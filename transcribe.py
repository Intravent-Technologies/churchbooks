import os
import logging
from groq import Groq

logging.basicConfig(level=logging.INFO)

SUPPORTED_EXTENSIONS = ['.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm', '.ogg']

WHISPER_PROMPT = (
    "Nigerian church financial record. Amounts in Naira. "
    "Names like Emeka, Funke, Tunde, Adewale, Chinedu, Olumide, Blessing, Chioma. "
    "Words: offering, tithe, thanksgiving, welfare, generator, fuel, salary, "
    "first fruit, convention, building fund, love seed, seed offering."
)

def get_audio_format(file_path):
    """Check if the audio file format is supported by Whisper."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None
    return ext

def get_audio_duration(file_path):
    """Get audio duration in seconds. Returns None if unable to determine."""
    try:
        # Try using file size as rough estimate (opus ogg ~1KB/s)
        # This is a fallback - Whisper itself will handle most files fine
        file_size = os.path.getsize(file_path)
        # Rough estimate: ~1.5KB/s for WhatsApp opus, ~3KB/s for m4a
        estimated_duration = file_size / 2000  # conservative estimate
        return estimated_duration
    except Exception:
        return None

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
    if file_size < 2000:
        return {
            "text": None,
            "segments": [],
            "confidence_scores": [],
            "error": "too_short"
        }
    if file_size > 3000000:  # ~3MB generous upper bound
        return {
            "text": None,
            "segments": [],
            "confidence_scores": [],
            "error": "too_long"
        }

    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(file_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=f,
                response_format="verbose_json",
                language="en",
                prompt=WHISPER_PROMPT
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
