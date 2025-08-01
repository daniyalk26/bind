"""
backend/deepgram_client.py  – Fixed for Deepgram SDK v3.x with proper async handling
─────────────────────────────────────────────────────
Updated with async callbacks and keep-alive mechanism
"""

from __future__ import annotations

import os
import logging
import asyncio
import base64
import time
from typing import Optional, Callable, Dict, Any

from deepgram import (
    DeepgramClient, 
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
    PrerecordedOptions,
    SpeakOptions,
)

log = logging.getLogger(__name__)


class DeepgramClientWrapper:
    """Façade around the Deepgram v3 SDK with streaming support."""

    def __init__(self) -> None:
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY env-var missing")
        
        # Initialize with options
        config = DeepgramClientOptions(
            api_key=api_key,
            options={"keepalive": "true"}
        )
        self._dg = DeepgramClient("", config)
        self._live_connection = None
        self._is_connected = False
        self._keep_alive_task = None
        self._last_audio_time = 0

    # ─────────────────────────────── STT (One-shot) ─────────────────────────────── #

    async def transcribe_once(self, wav_bytes: bytes) -> str:
        """One-shot transcription; returns empty string on failure."""
        opts = PrerecordedOptions(
            model="nova-2",
            language="en",
            punctuate=True,
        )
        try:
            # Updated API call for v3.x
            source = {"buffer": wav_bytes, "mimetype": "audio/wav"}
            res = await self._dg.listen.asyncrest.v("1").transcribe_file(
                source, opts
            )
            
            # Extract transcript from response
            if res and res.results and res.results.channels:
                return res.results.channels[0].alternatives[0].transcript
            return ""
            
        except Exception as exc:
            log.error("Deepgram STT error: %s", exc)
            return ""

    # ─────────────────────────────── TTS ─────────────────────────────── #

    async def tts_once(self, text: str) -> bytes:
        """Text-to-speech; returns b'' on failure so caller can skip audio."""
        opts = SpeakOptions(
            model="aura-asteria-en",
            encoding="mp3",
        )
        try:
            # Updated API call for v3.x
            response = await self._dg.speak.asyncrest.v("1").stream_memory(
                {"text": text},
                opts
            )
            
            # Handle different response types
            if hasattr(response, 'stream_memory'):
                # If it's a BytesIO object, read its content
                if hasattr(response.stream_memory, 'read'):
                    response.stream_memory.seek(0)  # Reset to beginning
                    return response.stream_memory.read()
                else:
                    return response.stream_memory
            elif hasattr(response, 'content'):
                return response.content
            elif hasattr(response, 'read'):
                # Direct BytesIO object
                response.seek(0)
                return response.read()
            else:
                # Try to read the response as bytes
                audio_buffer = b""
                if hasattr(response, 'aiter_bytes'):
                    async for chunk in response.aiter_bytes():
                        audio_buffer += chunk
                return audio_buffer
                
        except Exception as exc:
            log.error("Deepgram TTS error: %s", exc)
            return b""

    # ─────────────────────────────── Keep-Alive Task ─────────────────────────────── #

    async def _keep_alive_loop(self):
        """Send keep-alive messages to prevent Deepgram from closing idle connection"""
        try:
            while self._is_connected and self._live_connection:
                await asyncio.sleep(7)  # Send every 7 seconds (Deepgram timeout is 10s)
                
                # Check if we've sent audio recently
                time_since_audio = time.time() - self._last_audio_time
                if time_since_audio > 7:
                    try:
                        # Send keep-alive message
                        await self._live_connection.send({"type": "KeepAlive"})
                        log.debug("Sent keep-alive to Deepgram")
                    except Exception as e:
                        log.error(f"Failed to send keep-alive: {e}")
                        self._is_connected = False
                        break
        except asyncio.CancelledError:
            log.debug("Keep-alive task cancelled")
        except Exception as e:
            log.error(f"Keep-alive loop error: {e}")

    # ─────────────────────────────── Streaming STT ─────────────────────────────── #

    async def start_streaming_transcription(
        self,
        on_transcript: Callable[[str, bool], Any],
        on_utterance_end: Optional[Callable[[], Any]] = None,
        on_speech_started: Optional[Callable[[], Any]] = None,
        on_error: Optional[Callable[[str], Any]] = None
    ) -> bool:
        """
        Start a live streaming transcription session.
        """
        try:
            # Configure for real-time conversation
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                # Audio format settings
                encoding="opus",
                sample_rate=48000,
                # Formatting
                smart_format=True,
                punctuate=True,
                # Voice activity detection
                vad_events=True,
                # Interim results for real-time feedback
                interim_results=True,
                # Key setting: when to consider speech ended
                utterance_end_ms=1000,  # 1 second of silence = end of utterance
                channels=1,
                # Add endpointing for better turn detection
                endpointing=300,  # milliseconds
            )
            
            # Create live connection using the new API
            self._live_connection = self._dg.listen.asyncwebsocket.v("1")
            self._is_connected = False
            
            # Create async event handlers
            async def on_open(self, open_event, **kwargs):
                log.info("Deepgram connection opened")
                self._is_connected = True
            
            async def on_message(self, result, **kwargs):
                """Handle transcription results"""
                try:
                    if result and hasattr(result, 'channel'):
                        if result.channel and result.channel.alternatives:
                            transcript = result.channel.alternatives[0].transcript
                            is_final = result.is_final if hasattr(result, 'is_final') else False
                            
                            # Only process non-empty transcripts
                            if transcript.strip():
                                # Call the callback (it will handle its own async if needed)
                                if asyncio.iscoroutinefunction(on_transcript):
                                    await on_transcript(transcript, is_final)
                                else:
                                    on_transcript(transcript, is_final)
                except Exception as e:
                    log.error(f"Error handling transcript: {e}")
            
            async def on_utterance_end(self, result, **kwargs):
                """Handle end of utterance"""
                if on_utterance_end:
                    if asyncio.iscoroutinefunction(on_utterance_end):
                        await on_utterance_end()
                    else:
                        on_utterance_end()
                    
            async def on_speech_started(self, result, **kwargs):
                """Handle speech started event"""
                if on_speech_started:
                    if asyncio.iscoroutinefunction(on_speech_started):
                        await on_speech_started()
                    else:
                        on_speech_started()
                    
            async def on_error(self, error, **kwargs):
                """Handle any errors"""
                log.error(f"Deepgram streaming error: {error}")
                self._is_connected = False
                if on_error:
                    if asyncio.iscoroutinefunction(on_error):
                        await on_error(str(error))
                    else:
                        on_error(str(error))
            
            async def on_close(self, close_event, **kwargs):
                """Handle connection close"""
                log.info("Deepgram connection closed")
                self._is_connected = False
            
            # Register event handlers
            self._live_connection.on(LiveTranscriptionEvents.Open, on_open)
            self._live_connection.on(LiveTranscriptionEvents.Transcript, on_message)
            self._live_connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
            self._live_connection.on(LiveTranscriptionEvents.SpeechStarted, on_speech_started)
            self._live_connection.on(LiveTranscriptionEvents.Error, on_error)
            self._live_connection.on(LiveTranscriptionEvents.Close, on_close)
            
            # Start the connection
            result = await self._live_connection.start(options)
            
            if not result:
                log.error("Failed to start Deepgram connection")
                return False
            
            # Wait a bit to ensure connection is established
            await asyncio.sleep(0.5)
            
            if self._is_connected:
                # Start keep-alive task
                self._last_audio_time = time.time()
                self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())
                log.info("Deepgram streaming connection established successfully with keep-alive")
                return True
            else:
                log.error("Deepgram connection failed to establish")
                return False
            
        except Exception as e:
            log.error(f"Failed to start Deepgram streaming: {e}")
            if on_error:
                if asyncio.iscoroutinefunction(on_error):
                    await on_error(f"Connection failed: {e}")
                else:
                    on_error(f"Connection failed: {e}")
            return False
    
    async def send_audio_stream(self, audio_data: bytes) -> bool:
        """
        Send audio data to the streaming connection.
        
        Args:
            audio_data: Raw audio bytes (WebM/Opus from browser)
            
        Returns:
            True if sent successfully
        """
        if not self._live_connection or not self._is_connected:
            log.error("No active streaming connection")
            return False
            
        try:
            # Update last audio time
            self._last_audio_time = time.time()
            
            # Send the raw audio data
            await self._live_connection.send(audio_data)
            return True
        except Exception as e:
            log.error(f"Failed to send audio data: {e}")
            # Check if we need to reconnect
            if "ConnectionClosed" in str(e):
                self._is_connected = False
            return False
    
    async def stop_streaming_transcription(self):
        """Stop the streaming transcription session."""
        # Cancel keep-alive task
        if self._keep_alive_task and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass
        
        if self._live_connection:
            try:
                await self._live_connection.finish()
                log.info("Deepgram streaming connection closed")
            except Exception as e:
                log.error(f"Error closing streaming connection: {e}")
            finally:
                self._live_connection = None
                self._is_connected = False


class StreamingConversationManager:
    """
    Manages the continuous conversation flow with Deepgram streaming.
    Handles turn-taking and conversation state with safe WebSocket handling.
    """
    
    def __init__(self, deepgram_wrapper: DeepgramClientWrapper):
        self.deepgram = deepgram_wrapper
        self.current_transcript = ""
        self.is_user_speaking = False
        self.is_bot_speaking = False
        self.conversation_active = False
        self._last_transcript_time = 0
        
    async def handle_transcript(self, text: str, is_final: bool, websocket):
        """Handle incoming transcription from Deepgram"""
        import time
        from starlette.websockets import WebSocketState
        from fastapi import WebSocketDisconnect
        
        self._last_transcript_time = time.time()
        
        # Check if WebSocket is still connected before sending
        if not hasattr(websocket, 'client_state') or websocket.client_state != WebSocketState.CONNECTED:
            log.debug("WebSocket not connected, skipping transcript send")
            return
        
        try:
            if is_final:
                # Append final transcript
                self.current_transcript += " " + text
                self.current_transcript = self.current_transcript.strip()
                
                # Send live transcript to frontend
                await websocket.send_json({
                    "type": "live_transcript",
                    "text": self.current_transcript,
                    "is_final": True
                })
            else:
                # Send interim results for real-time feedback
                temp_transcript = self.current_transcript + " " + text
                await websocket.send_json({
                    "type": "live_transcript",
                    "text": temp_transcript.strip(),
                    "is_final": False
                })
        except WebSocketDisconnect:
            log.debug("Client disconnected during transcript send")
        except Exception as e:
            log.error(f"Error handling transcript: {e}")
    
    async def handle_utterance_end(self, process_callback: Callable):
        """Handle when user stops speaking - trigger bot response"""
        if self.current_transcript and not self.is_bot_speaking:
            self.is_user_speaking = False
            user_text = self.current_transcript
            self.current_transcript = ""
            
            # Process the complete user utterance
            if asyncio.iscoroutinefunction(process_callback):
                await process_callback(user_text)
            else:
                process_callback(user_text)
    
    async def handle_speech_started(self, websocket):
        """Handle when user starts speaking"""
        from starlette.websockets import WebSocketState
        from fastapi import WebSocketDisconnect
        
        # Check if WebSocket is still connected before sending
        if not hasattr(websocket, 'client_state') or websocket.client_state != WebSocketState.CONNECTED:
            log.debug("WebSocket not connected, skipping speech started send")
            return
            
        try:
            self.is_user_speaking = True
            await websocket.send_json({
                "type": "user_speaking",
                "status": True
            })
            
            # If bot is speaking, we could implement interruption here
            if self.is_bot_speaking:
                log.info("User interrupted bot speech")
        except WebSocketDisconnect:
            log.debug("Client disconnected during speech started send")
        except Exception as e:
            log.error(f"Error handling speech started: {e}")