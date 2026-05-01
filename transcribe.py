import os
import logging
from groq import Groq

logging.basicConfig(level=logging.INFO)

def transcribe_audio(file_path):
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        with open(file_path, "rb") as f:
            # Using the TURBO model for much faster transcription (2x-3x speed)
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo",
                file=f,
                response_format="text",
                language="en",
                prompt="Financial terms, Nigerian Pidgin, Yoruba, church offering, tithe, expenses, amounts in Naira"
            )
        return transcription.strip()
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        raise