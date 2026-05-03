import os
import logging
import requests
import tempfile
import base64

logging.basicConfig(level=logging.INFO)

WAHA_BASE_URL = os.environ.get("WAHA_BASE_URL", "https://waha-latest-g5ir.onrender.com")
WAHA_API_KEY = os.environ.get("WAHA_API_KEY", "churchbot-secret-key-2026")
WAHA_SESSION_NAME = os.environ.get("WAHA_SESSION_NAME", "churchbot")

def send_whatsapp_message(to_phone, body):
    """Send a WhatsApp text message via WAHA API."""
    try:
        clean_phone = to_phone.replace("whatsapp:", "").strip()
        if not clean_phone.startswith("+"):
            clean_phone = "+" + clean_phone

        url = f"{WAHA_BASE_URL.rstrip('/')}/api/sendText"
        headers = {
            "X-Api-Key": WAHA_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "session": WAHA_SESSION_NAME,
            "chatId": f"{clean_phone}@c.us",
            "text": body
        }
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code in [200, 201]:
            logging.info(f"Message sent to {clean_phone}")
            return True
        else:
            logging.error(f"WAHA error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logging.error(f"send_whatsapp_message error: {e}")
        return False

def download_media(message_id_or_url):
    """Download media from WAHA webhook payload."""
    try:
        if not message_id_or_url:
            return None

        # WAHA sends media as URL in webhook or as base64
        if message_id_or_url.startswith("data:"):
            header, data = message_id_or_url.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            ext = ".ogg" if "audio" in mime_type else ".bin"
            fd, path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(path, "wb") as f:
                f.write(base64.b64decode(data))
            logging.info(f"Downloaded base64 media: {os.path.getsize(path)} bytes")
            return path

        if message_id_or_url.startswith("http"):
            headers = {"X-Api-Key": WAHA_API_KEY}
            response = requests.get(message_id_or_url, headers=headers, timeout=30)
            if response.status_code == 200:
                fd, path = tempfile.mkstemp(suffix=".ogg")
                os.close(fd)
                with open(path, "wb") as f:
                    f.write(response.content)
                logging.info(f"Downloaded media: {os.path.getsize(path)} bytes")
                return path

        return None
    except Exception as e:
        logging.error(f"download_media error: {e}")
        return None
