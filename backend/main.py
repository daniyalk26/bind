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
@app.websocket("/ws/streaming")
async def streaming_websocket_endpoint(ws: WebSocket, db: AsyncSession = Depends(get_session)):
    """
    Enhanced WebSocket endpoint for continuous voice conversation using Deepgram streaming.
    """
    await ws.accept()
    session_id = ws.query_params.get("session") or ws.headers.get("x-session-id") or ws.client.host
    user = await crud.get_or_create_user(db, session_id)
    
    # Create streaming conversation manager
    streaming_manager = StreamingConversationManager(dg_client)
    
    async def send(payload: dict):
        try:
            await ws.send_json(payload)
        except (WebSocketDisconnect, ConnectionClosedError):
            raise
    
    async def process_user_utterance(user_text: str):
        """Process complete user utterance and generate response"""
        try:
            # Send processing indicator
            await send({"type": "processing", "text": user_text})
            
            # Save user message
            await crud.save_message(db, user.id, "user", user_text)
            
            # Get current session state
            session = await crud.get_session(db, user.id)
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
                err_audio = await dg_client.tts_once(err_msg)
                if err_audio:
                    await send({
                        "type": "bot_audio", 
                        "content": base64.b64encode(err_audio).decode()
                    })
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
            except Exception as e:
                logger.warning(f"TTS failed: {e}")
            
            # Send progress update
            await send({
                "type": "state_update",
                "data": {
                    "current_state": next_state.value,
                    "progress": engine.calculate_progress(next_state)
                }
            })
            
        except Exception as e:
            logger.error(f"Error processing utterance: {e}")
            await send({"type": "error", "message": "Sorry, I encountered an error. Please try again."})
    
    # Send initial greeting with audio
    try:
        first_state = ConversationState.start
        first_text = await ai_client.generate_response(
            first_state.value,
            engine.get_prompt(first_state) + "\n\nKeep it very brief and conversational."
        )
        
        # Send greeting text
        await send({
            "type": "bot_message",
            "content": first_text,
            "data": {"state": first_state.value}
        })
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
            await send({"type": "error", "message": f"Audio generation failed: {str(e)}"})
        
        await crud.update_session_state(db, user.id, ConversationState.collecting_zip.value)
        
        # Start Deepgram streaming
        logger.info("Starting Deepgram streaming...")
        streaming_started = await dg_client.start_streaming_transcription(
            on_transcript=lambda text, is_final:
                streaming_manager.handle_transcript(text, is_final, ws),

            on_utterance_end=lambda:
                streaming_manager.handle_utterance_end(process_user_utterance),

            on_speech_started=lambda:
                streaming_manager.handle_speech_started(ws),

            on_error=lambda err:
                send({"type": "error", "message": f"Audio error: {err}"})
        )

        
        if not streaming_started:
            await send({"type": "error", "message": "Failed to start audio streaming"})
            await ws.close()
            return
        
        await send({"type": "ready", "message": "Streaming ready"})
        
        # Main message loop
        while True:
            message = await ws.receive_json()
            
            if message.get("type") == "audio_stream":
                # Forward audio to Deepgram
                audio_data = base64.b64decode(message["content"])
                
                # Process audio format if needed
                mime_type = message.get("mime_type", "audio/webm")
                sample_rate = message.get("sample_rate", 48000)
                
                processed_audio = audio_processor.process_browser_audio(
                    audio_data, 
                    mime_type=mime_type,
                    sample_rate=sample_rate
                )
                
                if processed_audio:
                    await dg_client.send_audio_stream(processed_audio)
                else:
                    logger.warning("Failed to process audio chunk")
                
            elif message.get("type") == "bot_finished_speaking":
                # Bot finished speaking, ready for next input
                streaming_manager.is_bot_speaking = False
                await send({"type": "bot_speaking", "status": False})
                
            elif message.get("type") == "stop":
                break
                
    except WebSocketDisconnect:
        logger.info(f"Streaming WS closed by client {session_id}")
    except Exception as exc:
        logger.exception(f"Streaming WS error: {exc}")
    finally:
        # Clean up
        await dg_client.stop_streaming_transcription()
        try:
            await ws.close()
        except:
            pass


# ────────────────────────── simple health-check
@app.get("/api/health")
async def health(): 
    return {"status": "healthy", "service": "Bind IQ Chatbot"}