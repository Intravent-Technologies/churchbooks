import os
import re
import json
import logging
import tempfile
import subprocess
from groq import Groq

logging.basicConfig(level=logging.INFO)

SUPPORTED_EXTENSIONS = ['.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm', '.ogg']

# Optimized prompt for Nigerian church financial context
WHISPER_PROMPT = (
    "You are transcribing Nigerian church financial records. "
    "Speakers have Nigerian accents and may mix English with Nigerian Pidgin. "
    "Key terms: offering, tithe, thanksgiving, welfare, building fund, "
    "first fruit, seed offering, convention, workers forum, night vigil, "
    "Sunday service, youth conference, harvest, pastor, treasurer. "
    "Money amounts: 'fifty thousand' means 50,000. 'two fifty' means 250,000. "
    "Names like Emeka, Funke, Tunde, Adewale, Chinedu, Olumide, Blessing, Chioma, "
    "Ngozi, Adebayo, Folake, Kemi, Segun, Bisi, Nkechi, Chuka, Ifeoma. "
    "Transcribe exactly what is said, do not correct grammar."
)

def convert_to_wav(input_path):
    """Convert any audio file to 16kHz mono WAV for optimal Whisper accuracy."""
    try:
        # Try ffmpeg first (best quality)
        output_path = tempfile.mktemp(suffix=".wav")
        
        result = subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-ar', '16000',    # 16kHz sample rate (optimal for Whisper)
            '-ac', '1',        # Mono
            '-codec:a', 'pcm_s16le',  # 16-bit PCM
            '-b:a', '128k',    # Bitrate
            '-af', 'highpass=f=200,lowpass=f=3000,volume=2.0',  # Voice band + boost
            output_path
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0 and os.path.exists(output_path):
            logging.info(f"Converted audio to WAV: {os.path.getsize(output_path)} bytes")
            return output_path
            
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    # Fallback: return original path (let Groq try)
    logging.warning("ffmpeg not available, using original audio format")
    return input_path

def get_audio_format(file_path):
    """Check if the audio file format is supported by Whisper."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext if ext in SUPPORTED_EXTENSIONS else None

def transcribe_audio(file_path):
    """Transcribe audio with maximum accuracy for Nigerian/world accents.
    
    Returns a dict with:
      - text: full transcript string
      - segments: list of segment dicts with confidence scores
      - confidence_scores: list of avg_logprob values per segment
    """
    # Format check
    audio_format = get_audio_format(file_path)
    if audio_format is None:
        logging.warning(f"Unsupported audio format: {os.path.splitext(file_path)[1]}")
        return None

    file_size = os.path.getsize(file_path)
    logging.info(f"Audio file: {audio_format}, size: {file_size} bytes")

    # Duration pre-check (rough estimate via file size)
    # WhatsApp opus: ~1-2KB/s. 1 second min = ~2KB. 5 min max = ~600KB (generous)
    if file_size < 1000:  # Lowered from 2000 to allow very short notes
        return {
            "text": None,
            "segments": [],
            "confidence_scores": [],
            "error": "too_short"
        }
    if file_size > 5000000:  # 5MB generous upper bound
        return {
            "text": None,
            "segments": [],
            "confidence_scores": [],
            "error": "too_long"
        }

    # Convert to WAV for optimal Whisper accuracy
    wav_path = convert_to_wav(file_path)
    use_wav = wav_path != file_path
    
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(wav_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=f,
                response_format="verbose_json",
                temperature=0.0,      # Deterministic output
                prompt=WHISPER_PROMPT
                # No language specified - auto-detect for better Nigerian Pidgin support
            )

        # Build return dict
        segments = []
        confidence_scores = []
        
        if hasattr(transcription, 'segments') and transcription.segments:
            for seg in transcription.segments:
                seg_info = {
                    "text": seg.text,
                    "start": seg.get("start", 0) if hasattr(seg, "get") else getattr(seg, "start", 0),
                    "end": seg.get("end", 0) if hasattr(seg, "get") else getattr(seg, "end", 0),
                    "avg_logprob": seg.get("avg_logprob", 0) if hasattr(seg, "get") else getattr(seg, "avg_logprob", 0),
                }
                segments.append(seg_info)
                confidence_scores.append(seg_info["avg_logprob"])

        return {
            "text": transcription.text,
            "segments": segments,
            "confidence_scores": confidence_scores
        }

    except Exception as e:
        logging.error(f"STT error: {e}")
        return None
    finally:
        # Clean up temporary WAV file if we created one
        if use_wav and os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except Exception:
                pass
