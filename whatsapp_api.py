import os
import logging
import requests

logging.basicConfig(level=logging.INFO)

META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "churchbooks_verify_2026")
META_BASE_URL = "https://graph.facebook.com/v18.0"

def send_whatsapp_message(to_phone, body):
    """Send a WhatsApp text message via Meta Cloud API."""
    try:
        url = f"{META_BASE_URL}/{META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone.replace("whatsapp:", "").strip(),
            "type": "text",
            "text": {"body": body}
        }
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            logging.info(f"Message sent to {to_phone}")
        else:
            logging.error(f"Meta API error: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as e:
        logging.error(f"send_whatsapp_message error: {e}")
        return False

def download_media(media_id):
    """Download media from Meta WhatsApp Cloud API."""
    try:
        # Step 1: Get media URL
        url = f"{META_BASE_URL}/{media_id}"
        headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logging.error(f"Failed to get media URL: {response.text}")
            return None
        
        media_url = response.json().get("url")
        mime_type = response.json().get("mime_type", "")
        
        # Step 2: Download media
        media_response = requests.get(media_url, headers=headers)
        if media_response.status_code == 200:
            import tempfile
            ext = ".ogg" if "audio" in mime_type else ".bin"
            fd, path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            with open(path, "wb") as f:
                f.write(media_response.content)
            return path
        return None
    except Exception as e:
        logging.error(f"download_media error: {e}")
        return None
