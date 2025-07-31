# backend/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from pathlib import Path
from websockets.exceptions import ConnectionClosedError
import backend.crud as crud
import backend.models as models
from backend.db import init_db, get_session
from backend.schemas import ConversationState
from backend.conversation_engine import ConversationEngine
from backend.openai_client import OpenAIClient
import json, logging, os, base64, asyncio
from dotenv import load_dotenv
from backend.deepgram_client import DeepgramClientWrapper, StreamingConversationManager
from backend.audio_utils import AudioProcessor
from starlette.websockets import WebSocketState
from datetime import datetime
from deepgram import DeepgramClient, DeepgramClientOptions

# ────────────────────────── env / logging
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ────────────────────────── Helper functions
async def safe_send(ws: WebSocket, payload: dict) -> bool:
    """
    Safely send JSON data to WebSocket, checking connection state first.
    Returns True if sent successfully, False otherwise.
    """
    try:
        # Check if WebSocket is still connected
        if hasattr(ws, 'client_state') and ws.client_state != WebSocketState.CONNECTED:
            logger.debug("WebSocket not connected, skipping send")
            return False
        
        # For older Starlette versions, check application_state
        if hasattr(ws, 'application_state') and ws.application_state != WebSocketState.CONNECTED:
            logger.debug("WebSocket not connected, skipping send")
            return False
            
        await ws.send_json(payload)
        return True
        
    except WebSocketDisconnect:
        logger.debug("Client disconnected during send")
        return False
    except ConnectionClosedError:
        logger.debug("Connection closed during send")
        return False
    except RuntimeError as e:
        # Handle "Cannot call send on a WebSocket that has not been accepted"
        if "has not been accepted" in str(e):
            logger.debug("WebSocket not yet accepted")
        else:
            logger.error(f"Runtime error during send: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during send: {e}")
        return False

async def safe_send_text(ws: WebSocket, text: str) -> bool:
    """
    Safely send text data to WebSocket, checking connection state first.
    Returns True if sent successfully, False otherwise.
    """
    try:
        # Check if WebSocket is still connected
        if hasattr(ws, 'client_state') and ws.client_state != WebSocketState.CONNECTED:
            logger.debug("WebSocket not connected, skipping send")
            return False
        
        # For older Starlette versions, check application_state
        if hasattr(ws, 'application_state') and ws.application_state != WebSocketState.CONNECTED:
            logger.debug("WebSocket not connected, skipping send")
            return False
            
        await ws.send_text(text)
        return True
        
    except WebSocketDisconnect:
        logger.debug("Client disconnected during send")
        return False
    except ConnectionClosedError:
        logger.debug("Connection closed during send")
        return False
    except RuntimeError as e:
        if "has not been accepted" in str(e):
            logger.debug("WebSocket not yet accepted")
        else:
            logger.error(f"Runtime error during send: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during send: {e}")
        return False

# ────────────────────────── FastAPI scaffolding
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("DB ready")
    yield
    logger.info("Shutting down")

app = FastAPI(title="Bind IQ Chatbot", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ────────────────────────── helpers
engine    = ConversationEngine()
ai_client = OpenAIClient()          # OpenAI -- for chat wording only
dg_client = DeepgramClientWrapper()      # Deepgram -- STT & TTS
audio_processor = AudioProcessor()   # Audio format conversion

async def _apply_valid_input(db, user, state, parsed, state_data):
    """Persist parsed answers exactly as in your old code."""
    if state == ConversationState.collecting_zip:
        await crud.update_user(db, user.id, zip_code=parsed)
    elif state == ConversationState.collecting_name:
        await crud.update_user(db, user.id, full_name=parsed)
    elif state == ConversationState.collecting_email:
        await crud.update_user(db, user.id, email=parsed)
    elif state == ConversationState.collecting_vehicle_info:
        state_data["current_vehicle"] = parsed
    elif state == ConversationState.collecting_vehicle_use:
        state_data["current_vehicle"]["vehicle_use"] = parsed
    elif state == ConversationState.collecting_blind_spot:
        state_data["current_vehicle"]["blind_spot_warning"] = parsed
    elif state == ConversationState.collecting_commute_days:
        state_data["current_vehicle"]["days_per_week"] = parsed
    elif state == ConversationState.collecting_commute_miles:
        state_data["current_vehicle"]["one_way_miles"] = parsed
        await crud.save_vehicle(db, user.id, state_data["current_vehicle"])
        state_data["current_vehicle"] = {}
    elif state == ConversationState.collecting_annual_mileage:
        state_data["current_vehicle"]["annual_mileage"] = parsed
        await crud.save_vehicle(db, user.id, state_data["current_vehicle"])
        state_data["current_vehicle"] = {}
    elif state == ConversationState.collecting_license_type:
        await crud.update_user(db, user.id, license_type=models.LicenseType(parsed))
    elif state == ConversationState.collecting_license_status:
        await crud.update_user(db, user.id, license_status=models.LicenseStatus(parsed))

# ────────────────────────── Original WebSocket endpoint (kept for compatibility)
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, db: AsyncSession = Depends(get_session)):
    # Generate unique session ID for this connection to avoid conflicts with streaming
    session_id = ws.query_params.get("session") or ws.headers.get("x-session-id") or ws.client.host
    # Add suffix to differentiate from streaming sessions
    session_id = f"{session_id}_chat"
    
    try:
        await ws.accept()
        logger.info(f"Chat WebSocket accepted: {session_id}")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {e}")
        return
    
    try:
        user = await crud.get_or_create_user(db, session_id)
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        await safe_send(ws, {"type": "error", "message": "Session initialization failed"})
        try:
            await ws.close()
        except:
            pass
        return

    # Wait a moment for connection to stabilize
    await asyncio.sleep(0.1)

    # Initial greeting
    first_state = ConversationState.start
    first_text = await ai_client.generate_response(first_state.value, engine.get_prompt(first_state))
    
    # Use safe_send for all messages
    success = await safe_send(ws, {"type": "bot_message", "content": first_text, "data": {"state": first_state.value}})
    if not success:
        logger.warning("Failed to send initial greeting - client disconnected")
        return
        
    await crud.save_message(db, user.id, "assistant", first_text)
    await crud.update_session_state(db, user.id, ConversationState.collecting_zip.value)

    # Conversation loop
    try:
        while True:
            try:
                frame = await asyncio.wait_for(ws.receive_json(), timeout=60.0)
            except asyncio.TimeoutError:
                # Send ping to check if connection is alive
                if not await safe_send(ws, {"type": "ping"}):
                    logger.warning("Ping failed, closing connection")
                    break
                continue

            # Convert incoming frame → user_text
            if frame.get("type") == "user_audio":
                try:
                    pcm = base64.b64decode(frame["content"])
                except Exception:
                    continue
                user_text = await dg_client.transcribe_once(pcm)
            elif frame.get("type") == "user_message":
                user_text = frame.get("content", "").strip()
            elif frame.get("type") == "pong":
                # Client responded to ping
                continue
            else:
                continue

            if not user_text:
                continue

            await crud.save_message(db, user.id, "user", user_text)

            # Validate / route
            session = await crud.get_session(db, user.id)
            cur_state = ConversationState(session.current_state)
            state_data = json.loads(session.state_data)

            ok, parsed, err = engine.validate_input(cur_state, user_text)
            if not ok:
                err_msg = await ai_client.generate_error_response(cur_state.value, user_text, err)
                await safe_send(ws, {"type": "bot_message", "content": err_msg})
                await crud.save_message(db, user.id, "assistant", err_msg)
                continue

            await _apply_valid_input(db, user, cur_state, parsed, state_data)
            next_state = engine.get_next_state(cur_state, parsed, state_data)

            # Create assistant reply
            reply_text = await ai_client.generate_response(
                next_state.value,
                engine.get_prompt(next_state),
                user_name=user.full_name,
            )

            await crud.update_session_state(db, user.id, next_state.value, state_data)
            await crud.save_message(db, user.id, "assistant", reply_text)

            # Synthesize voice
            try:
                mp3_bytes = await dg_client.tts_once(reply_text)
                mp3_b64 = base64.b64encode(mp3_bytes).decode() if mp3_bytes else None
            except Exception as e:
                logger.warning("Deepgram TTS failed: %s", e)
                mp3_b64 = None

            # Send packets back to browser
            await safe_send(ws, {"type": "bot_message", "content": reply_text, "data": {"state": next_state.value}})
            if mp3_b64:
                await safe_send(ws, {"type": "bot_audio", "content": mp3_b64})
            await safe_send(ws, {
                "type": "state_update",
                "data": {
                    "current_state": next_state.value,
                    "progress": engine.calculate_progress(next_state)
                }
            })
            
    except WebSocketDisconnect:
        logger.info("WS closed by client %s", session_id)
    except Exception as exc:
        logger.exception("WS error: %s", exc)
    finally:
        try:
            if hasattr(ws, 'client_state') and ws.client_state == WebSocketState.CONNECTED:
                await ws.close()
            elif hasattr(ws, 'application_state') and ws.application_state == WebSocketState.CONNECTED:
                await ws.close()
        except:
            pass

# ────────────────────────── NEW: Streaming WebSocket endpoint for continuous conversation
@app.websocket("/ws/streaming")
async def streaming_websocket_endpoint(ws: WebSocket, db: AsyncSession = Depends(get_session)):
    """
    Enhanced WebSocket endpoint for continuous voice conversation using Deepgram streaming.
    """
    session_id = ws.query_params.get("session") or ws.headers.get("x-session-id") or ws.client.host
    logger.info(f"New streaming connection attempt: {session_id}")
    
    # Accept the connection first
    try:
        await ws.accept()
        logger.info(f"Streaming connection accepted: {session_id}")
    except Exception as e:
        logger.error(f"Failed to accept WebSocket: {e}")
        return
    
    # Create flags and state
    is_ready = False
    connection_alive = True
    
    # Handle potential duplicate sessions
    try:
        user = await crud.get_or_create_user(db, session_id)
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        await safe_send(ws, {"type": "error", "message": "Session initialization failed"})
        try:
            await ws.close()
        except:
            pass
        return
    
    # Create streaming conversation manager
    streaming_manager = StreamingConversationManager(dg_client)
    deepgram_connected = False
    audio_chunks_received = 0
    
    async def process_user_utterance(user_text: str):
        """Process complete user utterance and generate response"""
        if not user_text.strip() or not connection_alive:
            return
            
        try:
            logger.info(f"Processing utterance: {user_text}")
            
            # Send processing indicator
            await safe_send(ws, {"type": "processing", "text": user_text})
            
            # Save user message
            await crud.save_message(db, user.id, "user", user_text)
            
            # Get current session state
            session = await crud.get_session(db, user.id)
            if not session:
                logger.error("No session found for user")
                return
                
            cur_state = ConversationState(session.current_state)
            state_data = json.loads(session.state_data)
            
            # Validate input
            ok, parsed, err = engine.validate_input(cur_state, user_text)
            
            if not ok:
                # Handle validation error
                err_msg = await ai_client.generate_error_response(cur_state.value, user_text, err)
                await safe_send(ws, {"type": "bot_message", "content": err_msg})
                await crud.save_message(db, user.id, "assistant", err_msg)
                
                # Generate error audio
                try:
                    err_audio = await dg_client.tts_once(err_msg)
                    if err_audio:
                        await safe_send(ws, {
                            "type": "bot_audio", 
                            "content": base64.b64encode(err_audio).decode()
                        })
                except Exception as e:
                    logger.error(f"Error generating error audio: {e}")
                return
            
            # Apply valid input and get next state
            await _apply_valid_input(db, user, cur_state, parsed, state_data)
            next_state = engine.get_next_state(cur_state, parsed, state_data)
            
            # Generate conversational response
            reply_text = await ai_client.generate_response(
                next_state.value,
                engine.get_prompt(next_state) + "\n\nKeep your response brief and conversational (1-2 sentences).",
                user_name=user.full_name,
            )
            
            # Update state
            await crud.update_session_state(db, user.id, next_state.value, state_data)
            await crud.save_message(db, user.id, "assistant", reply_text)
            
            # Send text response immediately
            await safe_send(ws, {
                "type": "bot_message",
                "content": reply_text,
                "data": {"state": next_state.value}
            })
            
            # Mark bot as speaking
            streaming_manager.is_bot_speaking = True
            await safe_send(ws, {"type": "bot_speaking", "status": True})
            
            # Generate and send audio
            try:
                mp3_bytes = await dg_client.tts_once(reply_text)
                if mp3_bytes:
                    await safe_send(ws, {
                        "type": "bot_audio",
                        "content": base64.b64encode(mp3_bytes).decode()
                    })
                else:
                    logger.warning("No audio generated for response")
            except Exception as e:
                logger.error(f"TTS failed: {e}")
            
            # Send progress update
            await safe_send(ws, {
                "type": "state_update",
                "data": {
                    "current_state": next_state.value,
                    "progress": engine.calculate_progress(next_state)
                }
            })
            
        except Exception as e:
            logger.error(f"Error processing utterance: {e}", exc_info=True)
            await safe_send(ws, {"type": "error", "message": "Sorry, I encountered an error. Please try again."})
    
    try:
        # Add a small delay to ensure WebSocket is fully established
        await asyncio.sleep(0.2)
        
        # Check connection is still alive
        if not connection_alive:
            logger.warning("Connection closed before initialization")
            return
        
        # Send initial greeting with audio
        first_state = ConversationState.start
        first_text = await ai_client.generate_response(
            first_state.value,
            engine.get_prompt(first_state) + "\n\nKeep it very brief and conversational."
        )
        
        # Send greeting text
        success = await safe_send(ws, {
            "type": "bot_message",
            "content": first_text,
            "data": {"state": first_state.value}
        })
        
        if not success:
            logger.error("Failed to send initial greeting - client disconnected")
            return
            
        await crud.save_message(db, user.id, "assistant", first_text)
        
        # Generate and send greeting audio
        try:
            greeting_audio = await dg_client.tts_once(first_text)
            if greeting_audio:
                await safe_send(ws, {
                    "type": "bot_audio",
                    "content": base64.b64encode(greeting_audio).decode()
                })
            else:
                logger.warning("No audio generated for greeting")
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
        
        await crud.update_session_state(db, user.id, ConversationState.collecting_zip.value)
        
        # Mark as ready for streaming
        is_ready = True
        await safe_send(ws, {"type": "ready", "message": "Streaming ready"})
        
        # Main message loop
        while connection_alive:
            try:
                # Use receive_text with timeout to handle disconnections better
                raw_message = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
                message = json.loads(raw_message)
                
                if not is_ready and message.get("type") != "keep_alive":
                    logger.warning("Received message before ready state")
                    continue
                
                if message.get("type") == "audio_stream":
                    audio_chunks_received += 1
                    
                    # Initialize Deepgram on first audio chunk
                    if not deepgram_connected:
                        logger.info(f"First audio chunk received (chunk #{audio_chunks_received}), starting Deepgram streaming...")
                        deepgram_connected = await dg_client.start_streaming_transcription(
                            on_transcript=lambda text, is_final: asyncio.create_task(
                                streaming_manager.handle_transcript(text, is_final, ws)
                            ),
                            on_utterance_end=lambda: asyncio.create_task(
                                streaming_manager.handle_utterance_end(process_user_utterance)
                            ),
                            on_speech_started=lambda: asyncio.create_task(
                                streaming_manager.handle_speech_started(ws)
                            ),
                            on_error=lambda error: asyncio.create_task(
                                safe_send(ws, {"type": "error", "message": f"Audio error: {error}"})
                            )
                        )
                        
                        if not deepgram_connected:
                            logger.error("Deepgram connection failed")
                            await safe_send(ws, {
                                "type": "error",
                                "message": "Could not start audio transcription. Please try again."
                            })
                            break
                    
                    # Forward audio to Deepgram
                    try:
                        audio_data = base64.b64decode(message["content"])
                        
                        # Log every 10th chunk to avoid spam
                        if audio_chunks_received % 10 == 0:
                            logger.debug(f"Received audio chunk #{audio_chunks_received}, size: {len(audio_data)} bytes")
                        
                        # Send to Deepgram
                        success = await dg_client.send_audio_stream(audio_data)
                        if not success:
                            logger.warning(f"Failed to send audio chunk #{audio_chunks_received} to Deepgram")
                    except Exception as e:
                        logger.error(f"Error processing audio stream: {e}")
                    
                elif message.get("type") == "bot_finished_speaking":
                    # Bot finished speaking, ready for next input
                    streaming_manager.is_bot_speaking = False
                    await safe_send(ws, {"type": "bot_speaking", "status": False})
                    
                elif message.get("type") == "keep_alive":
                    # Acknowledge keep-alive
                    logger.debug("Keep-alive received")
                    # Send pong back
                    await safe_send(ws, {"type": "pong"})
                    
                elif message.get("type") == "stop":
                    logger.info("Stop command received")
                    break
                    
            except asyncio.TimeoutError:
                logger.warning("WebSocket receive timeout - sending ping")
                if not await safe_send(ws, {"type": "ping"}):
                    logger.warning("Ping failed, closing connection")
                    break
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON received: {e}")
                continue
            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected by client")
                break
            except Exception as e:
                logger.error(f"Error in message loop: {e}", exc_info=True)
                if "no close frame received" in str(e).lower():
                    break
                    
    except WebSocketDisconnect:
        logger.info(f"Streaming WS closed by client {session_id}")
    except Exception as exc:
        logger.exception(f"Streaming WS error: {exc}")
    finally:
        connection_alive = False
        logger.info(f"Cleaning up streaming connection for {session_id}. Received {audio_chunks_received} audio chunks.")
        
        # Clean up
        if deepgram_connected:
            try:
                await dg_client.stop_streaming_transcription()
            except Exception as e:
                logger.error(f"Error stopping Deepgram: {e}")
        
        # Safe close
        try:
            if hasattr(ws, 'client_state') and ws.client_state == WebSocketState.CONNECTED:
                await ws.close()
            elif hasattr(ws, 'application_state') and ws.application_state == WebSocketState.CONNECTED:
                await ws.close()
        except Exception as e:
            logger.debug(f"Error closing WebSocket: {e}")

# ────────────────────────── API endpoints
@app.get("/api/health")
async def health(): 
    return {"status": "healthy", "service": "Bind IQ Chatbot"}

@app.get("/api/deepgram-info")
async def deepgram_info():
    """Get Deepgram SDK version and test basic functionality"""
    import deepgram
    import pkg_resources
    
    try:
        # Get version
        try:
            dg_version = pkg_resources.get_distribution("deepgram-sdk").version
        except:
            dg_version = "unknown"
        
        # Check if API key is set
        api_key_set = bool(os.getenv("DEEPGRAM_API_KEY"))
        api_key_prefix = os.getenv("DEEPGRAM_API_KEY", "")[:10] + "..." if api_key_set else "NOT SET"
        
        # Test client initialization
        client_ok = False
        client_error = None
        dg_attrs = []
        
        try:
            # Try new initialization method
            config = DeepgramClientOptions(
                api_key=os.getenv("DEEPGRAM_API_KEY", "test"),
                options={"keepalive": "true"}
            )
            test_client = DeepgramClient("", config)
            client_ok = True
            dg_attrs = [attr for attr in dir(test_client) if not attr.startswith('_')]
        except Exception as e:
            # Try old initialization method
            try:
                test_client = DeepgramClient(os.getenv("DEEPGRAM_API_KEY", ""))
                client_ok = True
                dg_attrs = [attr for attr in dir(test_client) if not attr.startswith('_')]
            except Exception as e2:
                client_error = f"New method: {str(e)}, Old method: {str(e2)}"
        
        return {
            "status": "ok",
            "deepgram_version": dg_version,
            "api_key_set": api_key_set,
            "api_key_prefix": api_key_prefix,
            "client_initialization": {
                "success": client_ok,
                "error": client_error
            },
            "available_attributes": dg_attrs,
            "has_listen": "listen" in dg_attrs,
            "has_speak": "speak" in dg_attrs,
            "has_transcription": "transcription" in dg_attrs,  # Old API
            "has_manage": "manage" in dg_attrs,
            "python_deepgram_module": str(deepgram.__file__) if hasattr(deepgram, '__file__') else "unknown"
        }
    except Exception as e:
        logger.error(f"Deepgram info failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "api_key_set": bool(os.getenv("DEEPGRAM_API_KEY"))
        }

@app.get("/api/test-deepgram")
async def test_deepgram():
    """Test endpoint to verify Deepgram configuration"""
    try:
        # Test TTS
        test_text = "Hello, this is a test of the Deepgram text to speech system."
        audio_bytes = await dg_client.tts_once(test_text)
        
        # Test if we got audio back
        tts_working = len(audio_bytes) > 0 if audio_bytes else False
        audio_size = len(audio_bytes) if audio_bytes else 0
        
        # Check if API key is set
        api_key_set = bool(os.getenv("DEEPGRAM_API_KEY"))
        
        return {
            "status": "ok",
            "deepgram_api_key_set": api_key_set,
            "tts_test": {
                "working": tts_working,
                "audio_size": audio_size
            },
            "message": "Deepgram configuration verified" if tts_working else "Deepgram test failed"
        }
    except Exception as e:
        logger.error(f"Deepgram test failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "deepgram_api_key_set": bool(os.getenv("DEEPGRAM_API_KEY"))
        }

# ────────────────────────── Debug WebSocket endpoints
@app.websocket("/ws/echo")
async def echo_websocket(ws: WebSocket):
    """Simple echo WebSocket for testing - no dependencies"""
    await ws.accept()
    client_info = f"{ws.client.host}:{ws.client.port}"
    logger.info(f"Echo WebSocket connected: {client_info}")
    
    try:
        # Send welcome message
        await ws.send_json({
            "type": "welcome",
            "message": "Echo WebSocket connected successfully!",
            "client": client_info
        })
        
        # Echo loop
        while True:
            data = await ws.receive_json()
            logger.info(f"Echo received: {data}")
            
            # Echo back with timestamp
            await ws.send_json({
                "type": "echo",
                "original": data,
                "timestamp": str(asyncio.get_event_loop().time()),
                "server_time": datetime.now().isoformat()
            })
            
            if data.get("type") == "close":
                break
                
    except WebSocketDisconnect:
        logger.info(f"Echo WebSocket disconnected: {client_info}")
    except Exception as e:
        logger.error(f"Echo WebSocket error: {e}")

@app.websocket("/ws/test")
async def test_websocket_endpoint(ws: WebSocket):
    """Simple test endpoint to verify WebSocket connectivity"""
    client_info = f"{ws.client.host}:{ws.client.port}"
    
    try:
        logger.info(f"Test WebSocket connection attempt from {client_info}")
        await ws.accept()
        logger.info(f"Test WebSocket accepted for {client_info}")
        
        # Add small delay to ensure connection is stable
        await asyncio.sleep(0.1)
        
        # Send immediate test message
        await ws.send_json({
            "type": "test",
            "message": "WebSocket connection successful!",
            "timestamp": str(asyncio.get_event_loop().time())
        })
        
        # Keep connection alive and echo messages
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                logger.info(f"Test WS received: {data}")
                
                # Echo the message back
                await ws.send_json({
                    "type": "echo",
                    "original": data,
                    "timestamp": str(asyncio.get_event_loop().time())
                })
                
                if data.get("type") == "close":
                    break
                    
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                await ws.send_json({"type": "ping"})
                
    except WebSocketDisconnect:
        logger.info(f"Test WebSocket disconnected by client {client_info}")
    except Exception as e:
        logger.error(f"Test WebSocket error for {client_info}: {e}", exc_info=True)
    finally:
        logger.info(f"Test WebSocket closed for {client_info}")