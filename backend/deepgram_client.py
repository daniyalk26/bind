"""
backend/deepgram_client.py  – Deepgram SDK v3 wrapper with streaming support
─────────────────────────────────────────────────────
Provides both one-shot and streaming capabilities:

    • transcribe_once(wav_bytes)  -> str
    • tts_once(text)             -> bytes  (MP3)
    • StreamingTranscriber       -> Real-time streaming transcription

The wrapper handles all SDK-level errors and provides safe fall-backs.
"""

from __future__ import annotations

import os
import logging
import asyncio
from typing import Optional, Callable, Dict, Any

from deepgram import (
    DeepgramClient, 
    DeepgramError, 
    SpeakOptions, 
    PrerecordedOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

log = logging.getLogger(__name__)


class DeepgramClientWrapper:
    """Façade around the Deepgram v3 SDK with streaming support."""

    def __init__(self) -> None:
        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY env-var missing")
        self._dg = DeepgramClient(api_key)
        self._live_connection = None

    # ─────────────────────────────── STT (One-shot) ─────────────────────────────── #

    async def transcribe_once(self, wav_bytes: bytes) -> str:
        """One-shot transcription; returns empty string on failure."""
        opts = PrerecordedOptions(
            model="nova-2",
            language="en",
            punctuate=True,
        )
        try:
            res = await self._dg.transcription.prerecorded.transcribe_bytes(
                wav_bytes, opts
            )
            return res.results.channels[0].alternatives[0].transcript
        except DeepgramError as exc:
            log.error("Deepgram STT error: %s", exc)
            return ""

    # ─────────────────────────────── TTS ─────────────────────────────── #

    async def tts_once(self, text: str) -> bytes:
        """Text-to-speech; returns b'' on failure so caller can skip audio."""
        opts = SpeakOptions(
            model="aura-asteria-en",   # fast 16-kHz female voice
            encoding="mp3",
        )
        try:
            # Using the correct v3 API structure
            response = await self._dg.speak.asyncrest.v("1").stream(
                {"text": text},
                opts
            )
            
            # Get the stream response properly
            audio_data = getattr(response, 'stream', None)
            if audio_data:
                return audio_data.getvalue()
            
            # Alternative method if stream doesn't work
            return response.content if hasattr(response, 'content') else b""
                
        except DeepgramError as exc:
            log.error("Deepgram TTS error: %s", exc)
            return b""

    # ─────────────────────────────── Streaming STT ─────────────────────────────── #

    async def start_streaming_transcription(
        self,
        on_transcript: Callable[[str, bool], None],
        on_utterance_end: Optional[Callable[[], None]] = None,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ) -> bool:
        """
        Start a live streaming transcription session.
        
        Args:
            on_transcript: Called with (text, is_final) when transcription is received
            on_utterance_end: Called when user stops speaking (end of utterance detected)
            on_speech_started: Called when speech is first detected
            on_error: Called on any errors
            
        Returns:
            True if connection established successfully
        """
        try:
            # Configure for real-time conversation
            options = LiveOptions(
                model="nova-2",
                language="en-US",
                # Formatting
                smart_format=True,
                punctuate=True,
                # Voice activity detection
                vad_events=True,
                # Interim results for real-time feedback
                interim_results=True,
                # Key setting: when to consider speech ended
                utterance_end_ms=1000,  # 1 second of silence = end of utterance
                # Audio format
                encoding="linear16",
                sample_rate=16000,
                channels=1,
            )
            
            # Create live transcription connection
            self._live_connection = self._dg.listen.asynclive.v("1")
            
            # Set up event handlers - Note: no 'self' parameter in these closures
            # ── event callbacks ─────────────────────────────────────────
            async def _on_message(*args, **kwargs):
                """Transcript / interim results"""
                result = args[0] if args else kwargs.get("result")
                if not result:        # nothing to do
                    return
                transcript = result.channel.alternatives[0].transcript
                is_final   = result.is_final
                if transcript.strip():
                    # let the caller deal with it; no nested Tasks here
                    await on_transcript(transcript, is_final)

            async def _on_utterance_end(*_args, **_kw):
                if on_utterance_end:
                    await on_utterance_end()

            async def _on_speech_started(*_args, **_kw):
                if on_speech_started:
                    await on_speech_started()

            async def _on_error(*_args, **kw):
                msg = str(kw.get("error", "unknown error"))
                log.error(f"Deepgram streaming error: {msg}")
                if on_error:
                    await on_error(msg)

            
            # Register event handlers
            self._live_connection.on(LiveTranscriptionEvents.Transcript, _on_message)
            self._live_connection.on(LiveTranscriptionEvents.UtteranceEnd, _on_utterance_end)
            self._live_connection.on(LiveTranscriptionEvents.SpeechStarted, _on_speech_started)
            self._live_connection.on(LiveTranscriptionEvents.Error, _on_error)
            
            # Start the connection
            await self._live_connection.start(options)
            log.info("Deepgram streaming connection established")
            
            return True
            
        except Exception as e:
            log.error(f"Failed to start Deepgram streaming: {e}")
            if on_error:
                await on_error(f"Connection failed: {e}")
            return False
    
    async def send_audio_stream(self, audio_data: bytes) -> bool:
        """
        Send audio data to the streaming connection.
        
        Args:
            audio_data: Raw audio bytes (16-bit PCM, 16kHz, mono)
            
        Returns:
            True if sent successfully
        """
        if not self._live_connection:
            log.error("No active streaming connection")
            return False
            
        try:
            await self._live_connection.send(audio_data)
            return True
        except Exception as e:
            log.error(f"Failed to send audio data: {e}")
            return False
    
    async def stop_streaming_transcription(self):
        """Stop the streaming transcription session."""
        if self._live_connection:
            try:
                await self._live_connection.finish()
                log.info("Deepgram streaming connection closed")
            except Exception as e:
                log.error(f"Error closing streaming connection: {e}")
            finally:
                self._live_connection = None


class StreamingConversationManager:
    """
    Manages the continuous conversation flow with Deepgram streaming.
    Handles turn-taking and conversation state.
    """
    
    def __init__(self, deepgram_wrapper: DeepgramClientWrapper):
        self.deepgram = deepgram_wrapper
        self.current_transcript = ""
        self.is_user_speaking = False
        self.is_bot_speaking = False
        self.conversation_active = False
        
    async def handle_transcript(self, text: str, is_final: bool, websocket):
        """Handle incoming transcription from Deepgram"""
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
    
    async def handle_utterance_end(self, process_callback: Callable):
        """Handle when user stops speaking - trigger bot response"""
        if self.current_transcript and not self.is_bot_speaking:
            self.is_user_speaking = False
            user_text = self.current_transcript
            self.current_transcript = ""
            
            # Process the complete user utterance
            await process_callback(user_text)
    
    async def handle_speech_started(self, websocket):
        """Handle when user starts speaking"""
        self.is_user_speaking = True
        await websocket.send_json({
            "type": "user_speaking",
            "status": True
        })
        
        # If bot is speaking, we could implement interruption here
        if self.is_bot_speaking:
            log.info("User interrupted bot speech")
            # You could stop bot audio playback here