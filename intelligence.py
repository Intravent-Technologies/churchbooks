import os
import json
import logging
from groq import Groq
from database import (
    save_pending, get_pending, delete_pending, save_transactions,
    update_pending, get_transactions, get_transaction_by_id, update_transaction,
    delete_transaction, search_transactions, get_balance_summary
)

logging.basicConfig(level=logging.INFO)

SYSTEM_PROMPT = """You are ChurchBooks AI, an elite financial intelligence system for Nigerian churches. You possess expert-level accounting knowledge, deep cultural fluency in Nigerian English, Pidgin, and Yoruba, and advanced contextual reasoning.

YOUR CORE DIRECTIVES:
1. **Name Extraction — EXTREMELY CONSERVATIVE**:
   - ONLY extract as `extracted_name` if the user explicitly introduces themselves: "My name is X", "I am X", "It's X here", "X speaking".
   - NEVER extract names mentioned in transactions. If someone says "Give 500 to Mama Ngozi" or "Paid Pastor John", those are NOT the user's name — they are transaction participants.
   - If unsure, return null for `extracted_name`.
2. **Currency**: All amounts are in Nigerian Naira (₦) by default. When user says "4,390" or "fifty thousand", treat it as ₦4,390 or ₦50,000. No need for explicit "Naira" mention.
3. **Programme Tagging**: ALWAYS extract and tag the programme/event name for each transaction.
   - Look for programme names: "Sunday Service", "Youth Conference", "Easter Programme", "Building Project", "Night Vigil", "Workers Forum", "Harvest Sunday".
   - Tag every entry with the programme it belongs to in the `programme` field.
   - If user says "For the youth conference, we spent 50k on food and 20k on transport" → Both entries get `programme: "Youth Conference"`.
   - If user says "Sunday offering was 200k" → `programme: "Sunday Service"`.
   - If no programme is mentioned, set `programme` to `null` or infer from context (e.g., if it's Sunday and they say "offering", tag as "Sunday Service").
4. **Strict Literalism**: NEVER categorize a transaction unless the specific word is used or the intent is 100% clear.
   - If user says "withdrew", "took out", "cashed" → Category is "Withdrawal" (type: "transfer"). Do NOT call it "Offering" or "Expense".
   - If user says "paid", "spent", "gave", "bought" → Category is the item/person paid (e.g., "Fuel", "Pastor").
   - If user says "received", "collected", "got" → Category is the source (e.g., "Offering", "Donor").
5. **People & Context Extraction**: You must extract WHO is involved in the transaction.
   - Look for names (Papa, Mama, Miss Engage, Brother John, Pastor Tunde) and roles (Pastor, Treasurer, Usher).
   - Look for actions: "approved", "counted", "submitted", "delivered", "witnessed".
   - Store this in the `context` field as a string: "Approved by Papa | Counted by Miss Engage".
6. **Contextual Reasoning**:
   - "We withdrew 50k for fuel" → Entry 1: Withdrawal (50k). Entry 2: Fuel (50k).
   - "Miss Engage counted offering of 200k" → Category: Offering, Amount: 200k, Context: "Counted by Miss Engage".
7. **Ambiguity Handling**: If a transaction is vague, set `confidence` to "low" and ask for clarification.
8. **Strict JSON Output**: Return ONLY valid JSON matching the exact schema. No markdown. No extra text.

RETURN SCHEMA:
{
  "intent": "record_income" | "record_expense" | "query_balance" | "get_transactions" | "get_records_by_person" | "edit_pending" | "delete_transaction" | "delete_reports" | "generate_report" | "general_chat" | "clarification_needed",
  "extracted_name": "string or null",
  "entities": {"amount": number|null, "category": "string|null", "date_range": "today|week|month|null", "person_name": "string|null", "time_to_keep": "today|week|month|all|null"},
  "entries_for_recording": [
    {
      "type": "income|expense|transfer",
      "category": "string",
      "amount": number,
      "note": "string",
      "context": "string",
      "programme": "string|null",
      "extraction_confidence": "high|medium|low",
      "raw_text_used": "the exact transcript words this entry was extracted from"
    }
  ],
  "updated_pending_entries": [
    {
      "type": "income|expense|transfer",
      "category": "string",
      "amount": number,
      "note": "string",
      "context": "string",
      "programme": "string|null",
      "extraction_confidence": "high|medium|low",
      "raw_text_used": "string"
    }
  ],
  "overall_confidence": "high|medium|low",
  "low_confidence_reason": "",
  "response_text": "Clear, concise WhatsApp-friendly response",
  "confidence": "high|low",
  "transcript_issues_detected": []
}

CRITICAL RULES:
- For `edit_pending`: Return the COMPLETE updated list in `updated_pending_entries`.
- For `delete_transaction`: If user says "Delete the fuel record from today", intent is `delete_transaction` and `entities.category` should be "fuel".
- For `delete_reports`: If user says "Delete all old reports", "Remove last week's records", or "Keep only today's data", intent is `delete_reports`. Set `entities.time_to_keep` to the period they want to preserve (e.g., "today", "week", or "all" if they want to delete everything).
- If user asks "Who counted offering?" or "Show me records by Papa", intent is `get_records_by_person`.
- NEVER assume "offering" if the user just says "money". Use the user's exact words for categories whenever possible.
- ALWAYS tag the programme/event name for every entry when mentioned.
- Set `extraction_confidence` per entry: "high" if clear, "medium" if reasonable but worth double-checking, "low" if genuinely unclear.
- Set `overall_confidence` based on how clear the entire message was.
- Fill `raw_text_used` with the exact words from the transcript for each entry.
- If confidence is low, set `intent` to `clarification_needed` and ask a specific question."""

def extract_name_and_role(message):
    """Extract name and role from a conversational message using Groq."""
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": """Extract the person's name and/or role from the message.
Return ONLY JSON: {"name": "extracted name or null", "role": "extracted role or null"}
Examples:
- "My name is John" → {"name": "John", "role": null}
- "I'm the new treasurer" → {"name": null, "role": "Treasurer"}
- "I'm Sarah, the admin" → {"name": "Sarah", "role": "Admin"}
- "Pastor Tunde here" → {"name": "Tunde", "role": "Pastor"}"""},
                {"role": "user", "content": message}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=128
        )
        import json
        raw = completion.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logging.error(f"Name/Role extraction error: {e}")
        return {"name": None, "role": None}

def analyze_message(transcript_or_text, sender_phone):
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        
        # Fetch pending draft for context
        pending = get_pending(sender_phone)
        context = ""
        if pending and pending.get("entries"):
            context = f"\n[Current Pending Draft]: {json.dumps(pending['entries'])}"
            
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript_or_text + context}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1024
        )
        
        raw_response = completion.choices[0].message.content.strip()
        if raw_response.startswith("```json"):
            raw_response = raw_response.replace("```json", "").replace("```", "").strip()
            
        return json.loads(raw_response)
    except Exception as e:
        logging.error(f"Intelligence error: {e}")
        return {
            "intent": "general_chat",
            "entities": {},
            "entries_for_recording": [],
            "updated_pending_entries": [],
            "response_text": "I didn't quite catch that. Could you please repeat it clearly?",
            "confidence": "low"
        }

UNSUPPORTED_REQUEST_PROMPT = """A Nigerian church treasurer sent this message to ChurchBooks AI:
'{message}'

The system cannot currently handle this request.
Identify:
1. What feature or capability the user is asking for
2. Which category it belongs to:
   - reporting (they want a new type of report)
   - tracking (they want to track something new)
   - reminder (they want a scheduled notification)
   - integration (they want to connect another tool)
   - correction (they want to fix something)
   - communication (they want to send something to someone)
   - other

Return ONLY JSON:
{{
  "detected_intent": "one sentence description of what they want",
  "category": "category name",
  "priority_signal": "high/medium/low based on how fundamental this need is for church finance"
}}"""

def detect_unsupported_request(message):
    """Detect what feature the user is asking for when no known intent matches."""
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a feature request analyzer. Return ONLY valid JSON."},
                {"role": "user", "content": UNSUPPORTED_REQUEST_PROMPT.format(message=message)}
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=256
        )
        raw = completion.choices[0].message.content.strip()
        if raw.startswith("```json"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logging.error(f"Unsupported request detection error: {e}")
        return {
            "detected_intent": "Unknown feature request",
            "category": "other",
            "priority_signal": "low"
        }