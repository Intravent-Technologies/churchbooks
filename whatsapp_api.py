import os
import logging
import requests
import tempfile

logging.basicConfig(level=logging.INFO)

EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.environ.get("EVOLUTION_INSTANCE_NAME", "churchbot")

def send_whatsapp_message(to_phone, body):
    """Send a WhatsApp text message via Evolution API."""
    try:
        if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
            logging.error("Evolution API not configured")
            return False

        clean_phone = to_phone.replace("whatsapp:", "").strip()
        url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
        headers = {
            "apikey": EVOLUTION_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "number": clean_phone,
            "text": body,
            "delay": 1200,
            "linkPreview": False,
            "mentionsEveryOne": False
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code in [200, 201]:
            logging.info(f"Message sent to {clean_phone}")
            return True
        else:
            logging.error(f"Evolution API error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"send_whatsapp_message error: {e}")
        return False

def download_media(media_id_or_url):
    """Download media from Evolution API webhook payload."""
    try:
        if not media_id_or_url:
            return None

        # Evolution API sends base64 or URL in webhook
        if media_id_or_url.startswith("data:"):
            # Base64 encoded audio
            import base64
            header, data = media_id_or_url.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            ext = ".ogg" if "audio" in mime_type else ".bin"
            fd, path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            logging.info(f"Downloaded base64 media: {os.path.getsize(path)} bytes")
            return path

        if media_id_or_url.startswith("http"):
            # URL — download directly
            response = requests.get(media_id_or_url, timeout=30)
            if response.status_code == 200:
                fd, path = tempfile.mkstemp(suffix=".ogg")
                os.close(fd)
                with open(path, "wb") as f:
                    f.write(response.content)
                logging.info(f"Downloaded media from URL: {os.path.getsize(path)} bytes")
                return path

        return None
    except Exception as e:
        logging.error(f"download_media error: {e}")
        return None
