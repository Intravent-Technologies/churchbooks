import os
import json
import logging
from groq import Groq

logging.basicConfig(level=logging.INFO)

SYSTEM_PROMPT = """You are a financial data extractor for Nigerian churches. You understand English, Pidgin, and Yoruba.

Extract all financial entries from the voice note. Return ONLY valid JSON.

Format:
{"entries":[{"type":"income or expense","category":"specific term used","amount":0,"note":""}],"confidence":"high or low"}

Rules:
- Use specific terms mentioned (e.g., "Rice", "Generator", "Love Seed")
- If amounts are unclear, set confidence to "low"
- Income: offering, tithe, donation, alms, seed
- Expense: fuel, transport, salary, food, maintenance, other"""

def extract_entries(transcript):
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript}
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=1024
        )
        raw_response = completion.choices[0].message.content.strip()
        if raw_response.startswith("```json"):
            raw_response = raw_response.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw_response)
        return data
    except Exception as e:
        logging.error(f"Extraction error: {e}")
        return {"entries": [], "confidence": "low"}