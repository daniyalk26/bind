# backend/audio_utils.py
"""
Audio format conversion utilities for handling browser audio formats.
"""

import io
import logging
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

# Optional: Use pydub for more robust conversion
try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False
    logger.warning("pydub not installed - audio conversion will be limited")


def convert_webm_to_pcm(webm_data: bytes, sample_rate: int = 16000) -> Optional[bytes]:
    """
    Convert WebM audio to 16-bit PCM format required by Deepgram.
    
    Args:
        webm_data: Raw WebM audio data
        sample_rate: Target sample rate (default 16kHz for Deepgram)
        
    Returns:
        PCM audio data or None if conversion fails
    """
    if not HAS_PYDUB:
        logger.error("pydub required for WebM conversion")
        return None
        
    try:
        # Load WebM audio
        audio = AudioSegment.from_file(io.BytesIO(webm_data), format="webm")
        
        # Convert to mono, 16kHz, 16-bit PCM
        audio = audio.set_channels(1)
        audio = audio.set_frame_rate(sample_rate)
        audio = audio.set_sample_width(2)  # 16-bit
        
        # Return raw PCM data
        return audio.raw_data
        
    except Exception as e:
        logger.error(f"WebM to PCM conversion failed: {e}")
        return None


def convert_float32_to_pcm16(float32_data: bytes) -> bytes:
    """
    Convert Float32 audio data to 16-bit PCM.
    Used when browser sends Float32Array audio data.
    
    Args:
        float32_data: Raw float32 audio samples
        
    Returns:
        16-bit PCM audio data
    """
    try:
        # Convert bytes to numpy array
        float_array = np.frombuffer(float32_data, dtype=np.float32)
        
        # Clip to prevent overflow
        float_array = np.clip(float_array, -1.0, 1.0)
        
        # Convert to 16-bit PCM
        pcm_array = (float_array * 32767).astype(np.int16)
        
        return pcm_array.tobytes()
        
    except Exception as e:
        logger.error(f"Float32 to PCM16 conversion failed: {e}")
        return b""


def resample_audio(audio_data: bytes, 
                   from_rate: int, 
                   to_rate: int, 
                   sample_width: int = 2) -> bytes:
    """
    Resample audio data to a different sample rate.
    
    Args:
        audio_data: Raw audio data
        from_rate: Original sample rate
        to_rate: Target sample rate
        sample_width: Bytes per sample (2 for 16-bit)
        
    Returns:
        Resampled audio data
    """
    if from_rate == to_rate:
        return audio_data
        
    try:
        # Convert to numpy array
        dtype = np.int16 if sample_width == 2 else np.int32
        audio_array = np.frombuffer(audio_data, dtype=dtype)
        
        # Simple resampling (for better quality, use scipy.signal.resample)
        duration = len(audio_array) / from_rate
        new_length = int(duration * to_rate)
        
        # Linear interpolation
        old_indices = np.arange(len(audio_array))
        new_indices = np.linspace(0, len(audio_array) - 1, new_length)
        resampled = np.interp(new_indices, old_indices, audio_array)
        
        return resampled.astype(dtype).tobytes()
        
    except Exception as e:
        logger.error(f"Audio resampling failed: {e}")
        return audio_data


class AudioProcessor:
    """
    Handles audio format detection and conversion for browser audio.
    """
    
    def __init__(self, target_sample_rate: int = 16000):
        self.target_sample_rate = target_sample_rate
        
    def process_browser_audio(self, 
                            audio_data: bytes, 
                            mime_type: Optional[str] = None,
                            sample_rate: Optional[int] = None) -> Optional[bytes]:
        """
        Process audio from browser into format suitable for Deepgram.
        
        Args:
            audio_data: Raw audio data from browser
            mime_type: MIME type if known (e.g., 'audio/webm')
            sample_rate: Sample rate if known
            
        Returns:
            16-bit PCM audio at 16kHz, or None if processing fails
        """
        try:
            # Handle different formats based on MIME type
            if mime_type and 'webm' in mime_type:
                return convert_webm_to_pcm(audio_data, self.target_sample_rate)
                
            elif mime_type and 'float32' in mime_type:
                # Browser might send float32 PCM
                pcm_data = convert_float32_to_pcm16(audio_data)
                if sample_rate and sample_rate != self.target_sample_rate:
                    pcm_data = resample_audio(pcm_data, sample_rate, self.target_sample_rate)
                return pcm_data
                
            else:
                # Assume it's already PCM, just resample if needed
                if sample_rate and sample_rate != self.target_sample_rate:
                    return resample_audio(audio_data, sample_rate, self.target_sample_rate)
                return audio_data
                
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            return None