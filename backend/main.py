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




# ────────────────────────── env / logging
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    await ws.accept()
    # allow either ?session=… or x-session-id header
    session_id = ws.query_params.get("session") or ws.headers.get("x-session-id") or ws.client.host
    user       = await crud.get_or_create_user(db, session_id)

    async def send(payload: dict):
        try:
            await ws.send_json(payload)
        except (WebSocketDisconnect, ConnectionClosedError):
            raise

    # initial greeting
    first_state = ConversationState.start
    first_text  = await ai_client.generate_response(first_state.value, engine.get_prompt(first_state))
    await send({"type": "bot_message", "content": first_text, "data": {"state": first_state.value}})
    await crud.save_message(db, user.id, "assistant", first_text)
    await crud.update_session_state(db, user.id, ConversationState.collecting_zip.value)

    # ─────────────── conversation loop
    try:
        while True:
            frame = await ws.receive_json()

            # 1️⃣  Convert incoming frame → user_text
            if frame.get("type") == "user_audio":
                try:
                    pcm = base64.b64decode(frame["content"])
                except Exception:
                    continue
                user_text = await dg_client.transcribe_once(pcm)
            elif frame.get("type") == "user_message":
                user_text = frame.get("content", "").strip()
            else:
                continue

            if not user_text:
                continue

            await crud.save_message(db, user.id, "user", user_text)

            # 2️⃣  Validate / route
            session     = await crud.get_session(db, user.id)
            cur_state   = ConversationState(session.current_state)
            state_data  = json.loads(session.state_data)

            ok, parsed, err = engine.validate_input(cur_state, user_text)
            if not ok:
                err_msg = await ai_client.generate_error_response(cur_state.value, user_text, err)
                await send({"type": "bot_message", "content": err_msg})
                await crud.save_message(db, user.id, "assistant", err_msg)
                continue

            await _apply_valid_input(db, user, cur_state, parsed, state_data)
            next_state = engine.get_next_state(cur_state, parsed, state_data)

            # 3️⃣  Create assistant reply (OpenAI for wording)
            reply_text = await ai_client.generate_response(
                next_state.value,
                engine.get_prompt(next_state),
                user_name=user.full_name,
            )

            await crud.update_session_state(db, user.id, next_state.value, state_data)
            await crud.save_message(db, user.id, "assistant", reply_text)

            # 4️⃣  Synthesize voice  (Deepgram TTS)
            try:
                mp3_bytes = await dg_client.tts_once(reply_text)
                mp3_b64   = base64.b64encode(mp3_bytes).decode() if mp3_bytes else None
            except Exception as e:
                logger.warning("Deepgram TTS failed: %s", e)
                mp3_b64 = None

            # 5️⃣  Send packets back to browser
            await send({"type": "bot_message", "content": reply_text,
                        "data": {"state": next_state.value}})
            if mp3_b64:
                await send({"type": "bot_audio", "content": mp3_b64})
            await send({"type": "state_update",
                        "data": {"current_state": next_state.value,
                                 "progress": engine.calculate_progress(next_state)}})
    except WebSocketDisconnect:
        logger.info("WS closed by client %s", session_id)
    except Exception as exc:
        logger.exception("WS error: %s", exc)
        try: 
            await ws.close()
        except Exception: 
            pass


# ────────────────────────── NEW: Streaming WebSocket endpoint for continuous conversation
# backend/main.py - Fixed streaming endpoint section
# (Keep all the existing code, just replace the streaming_websocket_endpoint function)

# backend/main.py - Fixed streaming endpoint with proper error handling

# backend/main.py - Updated streaming endpoint with lazy Deepgram initialization

# Fixed streaming_websocket_endpoint for main.py
# Replace the existing streaming_websocket_endpoint function with this:

# Fixed streaming_websocket_endpoint for main.py
# Replace the existing streaming_websocket_endpoint function with this:

@app.websocket("/ws/streaming")
async def streaming_websocket_endpoint(ws: WebSocket, db: AsyncSession = Depends(get_session)):
    """
    Enhanced WebSocket endpoint for continuous voice conversation using Deepgram streaming.
    """
    await ws.accept()
    session_id = ws.query_params.get("session") or ws.headers.get("x-session-id") or ws.client.host
    
    logger.info(f"New streaming connection: {session_id}")
    
    # Create a flag to track if we should process messages
    is_ready = False
    connection_alive = True
    
    # Handle potential duplicate sessions
    try:
        user = await crud.get_or_create_user(db, session_id)
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        try:
            await ws.send_json({"type": "error", "message": "Session initialization failed"})
            await ws.close()
        except:
            pass
        return
    
    # Create streaming conversation manager
    streaming_manager = StreamingConversationManager(dg_client)
    deepgram_connected = False
    audio_chunks_received = 0
    
    async def send(payload: dict):
        """Send message to WebSocket with error handling"""
        try:
            if ws.client_state == WebSocketState.CONNECTED:
                await ws.send_json(payload)
                return True
        except (WebSocketDisconnect, ConnectionClosedError) as e:
            logger.warning(f"WebSocket disconnected while sending: {e}")
            raise
        except Exception as e:
            logger.error(f"Error sending WebSocket message: {e}")
            return False
        return False
    
    async def process_user_utterance(user_text: str):
        """Process complete user utterance and generate response"""
        if not user_text.strip() or not connection_alive:
            return
            
        try:
            logger.info(f"Processing utterance: {user_text}")
            
            # Send processing indicator
            await send({"type": "processing", "text": user_text})
            
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
                await send({"type": "bot_message", "content": err_msg})
                await crud.save_message(db, user.id, "assistant", err_msg)
                
                # Generate error audio
                try:
                    err_audio = await dg_client.tts_once(err_msg)
                    if err_audio:
                        await send({
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
            await send({
                "type": "bot_message",
                "content": reply_text,
                "data": {"state": next_state.value}
            })
            
            # Mark bot as speaking
            streaming_manager.is_bot_speaking = True
            await send({"type": "bot_speaking", "status": True})
            
            # Generate and send audio
            try:
                mp3_bytes = await dg_client.tts_once(reply_text)
                if mp3_bytes:
                    await send({
                        "type": "bot_audio",
                        "content": base64.b64encode(mp3_bytes).decode()
                    })
                else:
                    logger.warning("No audio generated for response")
            except Exception as e:
                logger.error(f"TTS failed: {e}")
            
            # Send progress update
            await send({
                "type": "state_update",
                "data": {
                    "current_state": next_state.value,
                    "progress": engine.calculate_progress(next_state)
                }
            })
            
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected during utterance processing")
            raise
        except Exception as e:
            logger.error(f"Error processing utterance: {e}", exc_info=True)
            try:
                await send({"type": "error", "message": "Sorry, I encountered an error. Please try again."})
            except:
                pass
    
    try:
        # Add a small delay to ensure WebSocket is fully established
        await asyncio.sleep(0.1)
        
        # Send initial greeting with audio
        first_state = ConversationState.start
        first_text = await ai_client.generate_response(
            first_state.value,
            engine.get_prompt(first_state) + "\n\nKeep it very brief and conversational."
        )
        
        # Check if connection is still alive before sending
        if ws.client_state != WebSocketState.CONNECTED:
            logger.warning("WebSocket disconnected before sending greeting")
            return
        
        # Send greeting text
        success = await send({
            "type": "bot_message",
            "content": first_text,
            "data": {"state": first_state.value}
        })
        
        if not success:
            logger.error("Failed to send initial greeting")
            return
            
        await crud.save_message(db, user.id, "assistant", first_text)
        
        # Generate and send greeting audio
        try:
            greeting_audio = await dg_client.tts_once(first_text)
            if greeting_audio:
                await send({
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
        await send({"type": "ready", "message": "Streaming ready"})
        
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
                                send({"type": "error", "message": f"Audio error: {error}"})
                            )
                        )
                        
                        if not deepgram_connected:
                            logger.error("Deepgram connection failed")
                            await send({
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
                    await send({"type": "bot_speaking", "status": False})
                    
                elif message.get("type") == "keep_alive":
                    # Acknowledge keep-alive
                    logger.debug("Keep-alive received")
                    
                elif message.get("type") == "stop":
                    logger.info("Stop command received")
                    break
                    
            except asyncio.TimeoutError:
                logger.warning("WebSocket receive timeout - connection may be stale")
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
        try:
            await ws.close()
        except:
            pass

# ────────────────────────── simple health-check
@app.get("/api/health")
async def health(): 
    return {"status": "healthy", "service": "Bind IQ Chatbot"}


# Fixed test endpoint for main.py
# Add this endpoint to your main.py to check Deepgram version and test the API
from deepgram import DeepgramClient, DeepgramClientOptions

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
    
# Add this debug endpoint to your main.py to test basic WebSocket connectivity

@app.websocket("/ws/test")
async def test_websocket_endpoint(ws: WebSocket):
    """Simple test endpoint to verify WebSocket connectivity"""
    client_info = f"{ws.client.host}:{ws.client.port}"
    
    try:
        logger.info(f"Test WebSocket connection attempt from {client_info}")
        await ws.accept()
        logger.info(f"Test WebSocket accepted for {client_info}")
        
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

    # Add this endpoint to your main.py to check Deepgram version and test the API

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
        try:
            test_client = DeepgramClient(os.getenv("DEEPGRAM_API_KEY", ""))
            client_ok = True
        except Exception as e:
            client_error = str(e)
        
        # Get available attributes
        dg_attrs = []
        if client_ok:
            dg_attrs = [attr for attr in dir(test_client) if not attr.startswith('_')]
        
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
            "python_deepgram_module": str(deepgram.__file__) if hasattr(deepgram, '__file__') else "unknown"
        }
    except Exception as e:
        logger.error(f"Deepgram info failed: {e}")
        return {
            "status": "error",
            "error": str(e)
        }

# Add these debug endpoints to your main.py to test WebSocket connectivity
# Add these debug endpoints to your main.py to test WebSocket connectivity
from datetime import datetime

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
        
