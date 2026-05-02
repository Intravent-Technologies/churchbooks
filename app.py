import os
import tempfile
import logging
import traceback
import requests
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request, jsonify
from transcribe import transcribe_audio
from extract import extract_entries
from intelligence import analyze_message, extract_name_and_role, detect_unsupported_request
from personality import (
    craft_response, craft_smart_confirmation, craft_error, craft_help,
    craft_unsupported_response, extract_intent_natural, get_first_name
)
from financial_advisor import run_background_insights
from database import (
    clean_phone, validate_name, validate_role, validate_church_name,
    get_user_by_phone, create_user, update_user_name, update_user_role,
    update_user_church, complete_onboarding, update_last_seen,
    verify_user, get_user_display_name,
    find_church_by_name, create_church, get_church, get_church_pastor,
    get_or_create_session, update_session_state, update_session_context,
    set_pending_transaction, get_pending_transaction, clear_pending_transaction,
    is_session_active,
    get_onboarding_step, advance_onboarding, get_onboarding_data, complete_onboarding_progress,
    save_pending, update_pending, get_pending, delete_pending, save_transactions,
    get_transactions, delete_transaction_by_details, search_transactions_by_person,
    search_transactions, get_balance_summary, delete_old_transactions,
    log_unsupported_request, get_system_stats, increment_stat
)
from reports import generate_weekly_report
from scheduler import scheduler
from web_routes import web
from whatsapp_api import send_whatsapp_message, download_media

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-secret')
app.config['ADMIN_PHONE'] = os.environ.get('ADMIN_PHONE', '')

app.register_blueprint(web)

def format_naira(amount):
    return f"₦{int(amount):,}"

def safe_log_error(error, handler_name, phone=None):
    """Log error with privacy-safe phone number."""
    safe_phone = phone[-4:] if phone else "unknown"
    logging.error(
        f"[{handler_name}] Phone: ...{safe_phone} | Error: {error}\n{traceback.format_exc()}"
    )

def fallback_response(name):
    return (
        f"Something's not quite right on my end, {name} 🙏\n"
        f"Give me a moment and try again — I'll sort it out.\n"
        f"_Abby • Ledgr Chapel by Intravent_"
    )

def _reply(phone, message):
    """Send a WhatsApp message via Meta Cloud API."""
    send_whatsapp_message(phone, message)

def _transcribe_audio_from_media_id(media_id):
    """Download and transcribe audio from Meta media ID."""
    audio_path = None
    try:
        audio_path = download_media(media_id)
        if audio_path:
            return transcribe_audio(audio_path)
        return None
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

# ============================================================
# WEBHOOK — Meta WhatsApp Cloud API
# ============================================================

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """Webhook verification (for platform setup)."""
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Handle incoming WhatsApp messages from Evolution API."""
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"status": "error", "message": "No JSON body"}), 400

    try:
        # Evolution API payload format
        event = body.get("event", "")
        data = body.get("data", {})

        if event != "messages.upsert":
            return jsonify({"status": "ignored"}), 200

        key = data.get("key", {})
        raw_phone = key.get("remoteJid", "")
        # Strip "@s.whatsapp.net" suffix
        phone = clean_phone(raw_phone.split("@")[0] if "@" in raw_phone else raw_phone)

        message = data.get("message", {})
        message_body = ""
        media_id = None
        media_type = ""

        # Text message
        if "conversation" in message:
            message_body = message["conversation"].strip()
        elif "extendedTextMessage" in message:
            message_body = message["extendedTextMessage"].get("text", "").strip()
        # Audio/voice note
        elif "audioMessage" in message:
            audio = message["audioMessage"]
            media_id = audio.get("url", "")
            media_type = "audio/ogg"
        # Document/image/video with audio
        elif "documentMessage" in message:
            doc = message["documentMessage"]
            if doc.get("mimetype", "").startswith("audio"):
                media_id = doc.get("url", "")
                media_type = doc.get("mimetype", "audio/ogg")
            else:
                message_body = f"[Document: {doc.get('fileName', 'file')}]"
        elif "imageMessage" in message:
            caption = message["imageMessage"].get("caption", "")
            if caption:
                message_body = caption.strip()

        if not phone:
            return jsonify({"status": "ignored"}), 200

        update_last_seen(phone)
        user = get_user_by_phone(phone)

        if not user:
            handle_unknown_user(phone, message_body)
        elif user.get("onboarding_step", 0) < 5:
            handle_onboarding(phone, message_body, media_id, media_type, user)
        else:
            handle_registered_user(phone, message_body, media_id, media_type, user)

        return jsonify({"status": "received"}), 200

    except Exception as e:
        safe_log_error(e, "webhook")
        return jsonify({"status": "error"}), 500

# ============================================================
# UNKNOWN USER — First contact ever
# ============================================================

def handle_unknown_user(phone, message_body):
    """Step 0 — First contact ever (state: UNKNOWN)"""
    try:
        user = create_user(phone)
        if not user:
            _reply(phone, "Welcome! I'm Abby, your church's personal finance assistant. Please try again in a moment 🙏")
            return

        advance_onboarding(phone, 1)
        update_session_state(phone, "ONBOARDING_1")

        reply = (
            "Hello! 👋 Welcome to Ledgr Chapel.\n\n"
            "I'm Abby, your church's personal finance assistant.\n"
            "I help churches record offerings, expenses, and generate\n"
            "reports — all through WhatsApp voice notes.\n\n"
            "To get started, what's your full name?"
        )
        _reply(phone, reply)
        update_session_context(phone, message_body, reply)

    except Exception as e:
        safe_log_error(e, "handle_unknown_user", phone)
        _reply(phone, "Hello! Welcome to Ledgr Chapel. Please try again in a moment 🙏")

# ============================================================
# ONBOARDING — Steps 1-4
# ============================================================

def handle_onboarding(phone, message_body, media_id, media_type, user):
    """Route to the correct onboarding step."""
    try:
        step = user.get("onboarding_step", 0)

        # Transcribe voice notes for ALL onboarding steps
        if media_id and media_type and "audio" in media_type:
            try:
                result = _transcribe_audio_from_media_id(media_id)
                if result and result.get("text"):
                    message_body = result.get("text", "").strip()
                else:
                    _reply(phone, "Sorry, I couldn't understand that voice note. Please try again or type your response 😊")
                    return
            except Exception as e:
                safe_log_error(e, "handle_onboarding_voice", phone)
                _reply(phone, "Sorry, I couldn't understand that voice note. Please try again or type your response 😊")
                return

        if step == 0:
            _onboarding_step_0(phone, message_body, user)
        elif step == 1:
            _onboarding_step_1(phone, message_body, user)
        elif step == 2:
            _onboarding_step_2(phone, message_body, user)
        elif step == 3:
            _onboarding_step_3(phone, message_body, user)
        elif step == 4:
            _onboarding_step_4(phone, message_body, user)
        else:
            complete_onboarding(phone)
            handle_registered_user(phone, message_body, None, None, user)

    except Exception as e:
        safe_log_error(e, "handle_onboarding", phone)
        _reply(phone, fallback_response("Friend"))

def _onboarding_step_0(phone, message_body, user):
    """Initial welcome — ask for name."""
    advance_onboarding(phone, 1)
    update_session_state(phone, "ONBOARDING_1")

    reply = (
        "Hello! 👋 Welcome to Ledgr Chapel.\n\n"
        "I'm Abby, your church's personal finance assistant.\n"
        "I help churches record offerings, expenses, and generate\n"
        "reports — all through WhatsApp voice notes.\n\n"
        "To get started, what's your full name?"
    )
    _reply(phone, reply)
    update_session_context(phone, message_body, reply)

def _onboarding_step_1(phone, message_body, user):
    """Waiting for name (state: ONBOARDING_1)"""
    valid, error_msg = validate_name(message_body)

    if not valid:
        _reply(phone, (
            f"I didn't quite catch that as a name 😊\n"
            f"Please reply with your full name — for example:\n"
            f"*Grace Adeyemi* or *Pastor James Okafor*"
        ))
        return

    words = message_body.strip().split()
    first_name = words[0]
    last_name = " ".join(words[1:])

    update_user_name(phone, first_name, last_name)
    advance_onboarding(phone, 2, {"first_name": first_name, "last_name": last_name})
    update_session_state(phone, "ONBOARDING_2")

    reply = (
        f"Great to meet you, {first_name}! 😊\n\n"
        f"What is your role in the church?\n"
        f"Please reply with one of these:\n\n"
        f"*1* — Pastor / Senior Leader\n"
        f"*2* — Treasurer\n"
        f"*3* — Collector (someone who collects offerings)"
    )
    _reply(phone, reply)
    update_session_context(phone, message_body, reply)

def _onboarding_step_2(phone, message_body, user):
    """Waiting for role (state: ONBOARDING_2)"""
    role = validate_role(message_body)

    if not role:
        _reply(phone, (
            f"Please reply with just *1*, *2*, or *3* to choose your role:\n\n"
            f"*1* — Pastor\n"
            f"*2* — Treasurer\n"
            f"*3* — Collector"
        ))
        return

    update_user_role(phone, role)
    advance_onboarding(phone, 3, {"role": role})
    update_session_state(phone, "ONBOARDING_3")

    reply = "Perfect! And what is the name of your church?"
    _reply(phone, reply)
    update_session_context(phone, message_body, reply)

def _onboarding_step_3(phone, message_body, user):
    """Waiting for church name (state: ONBOARDING_3)"""
    valid, error_msg = validate_church_name(message_body)

    if not valid:
        _reply(phone, "Please provide a church name with at least 3 characters.")
        return

    church_name = message_body.strip()
    role = user.get("role")
    first_name = user.get("first_name", "Friend")
    last_name = user.get("last_name", "")

    existing_church = find_church_by_name(church_name)

    if existing_church:
        update_user_church(phone, existing_church["id"])
        advance_onboarding(phone, 4, {"church_name": church_name, "church_id": existing_church["id"]})

        reply = (
            f"I found {existing_church.get('church_name', church_name)} already on Ledgr Chapel 🙏\n\n"
            f"I've sent a request to the church admin to verify "
            f"your membership. You'll be notified once approved.\n\n"
            f"Is that the right church?"
        )
        _reply(phone, reply)
        update_session_state(phone, "ONBOARDING_4_VERIFY")

    elif role == "pastor":
        new_church = create_church(church_name, pastor_phone=phone)
        if new_church:
            update_user_church(phone, new_church["id"])
            complete_onboarding(phone)
            complete_onboarding_progress(phone)
            update_session_state(phone, "ACTIVE")

            reply = (
                f"Welcome to Ledgr Chapel, Pastor {last_name}! 🎉\n\n"
                f"{church_name} is now registered.\n"
                f"You can start by inviting your treasurer — "
                f"just share this number with them and ask them to message me.\n\n"
                f"Send me a voice note anytime to log finances,\n"
                f"or type *HELP* to see everything I can do.\n\n"
                f"I'm here whenever you need me 🙏\n"
                f"_Abby • Ledgr Chapel by Intravent_"
            )
            _reply(phone, reply)
        else:
            _reply(phone, "Something went wrong creating your church record. Please try again 🙏")

    else:
        new_church = create_church(church_name, pastor_phone="")
        if new_church:
            update_user_church(phone, new_church["id"])
            advance_onboarding(phone, 4, {"church_name": church_name, "church_id": new_church["id"]})

            admin_phone = os.environ.get("ADMIN_PHONE")
            if admin_phone:
                try:
                    send_whatsapp_message(
                        admin_phone,
                        f"🔔 New church registration pending:\n\n"
                        f"Church: {church_name}\n"
                        f"User: {first_name} {last_name}\n"
                        f"Role: {role}\n"
                        f"Phone: {phone}\n\n"
                        f"Reply APPROVE {phone} to verify."
                    )
                except Exception:
                    pass

            reply = (
                f"I don't have {church_name} on record yet.\n\n"
                f"For security, a pastor or church admin needs to "
                f"confirm your membership before you can start logging.\n\n"
                f"I've flagged this for review. You'll receive a "
                f"message here once it's confirmed — usually within 24 hours 🙏"
            )
            _reply(phone, reply)
            update_session_state(phone, "ONBOARDING_4")
        else:
            _reply(phone, "Something went wrong. Please try again 🙏")

    update_session_context(phone, message_body, reply)

def _onboarding_step_4(phone, message_body, user):
    """Pending verification (state: ONBOARDING_4 or ONBOARDING_4_VERIFY)"""
    first_name = user.get("first_name", "Friend")

    msg_lower = message_body.lower().strip()
    if msg_lower.startswith("approve"):
        target_phone = msg_lower.replace("approve", "").strip()
        if target_phone and target_phone.startswith("+"):
            verify_user(target_phone)
            complete_onboarding(target_phone)
            complete_onboarding_progress(target_phone)

            try:
                approved_user = get_user_by_phone(target_phone)
                if approved_user:
                    approved_first = approved_user.get("first_name", "Friend")
                    church = get_church(approved_user.get("church_id"))
                    church_name = church.get("church_name", "your church") if church else "your church"

                    send_whatsapp_message(
                        target_phone,
                        f"Great news, {approved_first}! ✅\n"
                        f"You've been verified at {church_name}.\n"
                        f"You're all set on Ledgr Chapel!\n\n"
                        f"Send me a voice note to log your first record,\n"
                        f"or type *HELP* to see what I can do 😊\n"
                        f"_Abby • Ledgr Chapel by Intravent_"
                    )
                    _reply(phone, f"✅ {approved_first} has been approved and notified.")
                else:
                    _reply(phone, f"User with phone {target_phone} not found.")
            except Exception:
                _reply(phone, "Approval processed but notification failed. Check logs.")
            return

    reply = (
        f"You're almost set, {first_name} 😊\n"
        f"We're just waiting for your church admin to verify "
        f"your membership. I'll notify you as soon as it's done 🙏"
    )
    _reply(phone, reply)

# ============================================================
# REGISTERED USER — Normal flow
# ============================================================

def handle_registered_user(phone, message_body, media_id, media_type, user):
    """Fully registered user flow."""
    try:
        first_name = user.get("first_name", "Friend")

        session = get_or_create_session(phone)
        current_state = session.get("state", "ACTIVE")

        if _is_greeting(message_body) and current_state not in ["AWAITING_YES_NO", "AWAITING_CLARIFY", "AWAITING_CORRECT"]:
            return _handle_greeting(phone, message_body, first_name, session)

        if current_state == "AWAITING_YES_NO":
            return _handle_yes_no(phone, message_body, first_name, user, session)

        if current_state == "AWAITING_CLARIFY":
            return _handle_clarify(phone, message_body, first_name, user, session)

        if not is_session_active(phone) and current_state == "ACTIVE":
            clear_pending_transaction(phone)
            update_session_state(phone, "ACTIVE")

        if media_id and media_type and "audio" in media_type:
            return _handle_voice_note(phone, media_id, first_name, user, session)

        return _handle_text_message(phone, message_body, first_name, user, session)

    except Exception as e:
        safe_log_error(e, "handle_registered_user", phone)
        name = user.get("first_name", "Friend")
        _reply(phone, fallback_response(name))

def _is_greeting(text):
    """Detect greeting messages."""
    greetings = [
        "hello", "hi", "hey", "good morning", "good afternoon",
        "good evening", "morning", "afternoon", "evening",
        "hey abby", "hi abby", "hello abby", "good day",
        "howdy", "greetings", "hi there", "hey there"
    ]
    text_lower = text.lower().strip()
    return any(text_lower.startswith(g) or text_lower == g for g in greetings)

def _get_time_greeting(first_name):
    """Return greeting based on time of day."""
    now = datetime.utcnow()
    hour = now.hour

    if 5 <= hour < 12:
        return (
            f"Good morning, {first_name}! ☀️\n"
            f"Hope you're having a blessed day.\n"
            f"Ready to help whenever you are 😊\n"
            f"_Abby • Ledgr Chapel_"
        )
    elif 12 <= hour < 17:
        return (
            f"Good afternoon, {first_name}! 👋\n"
            f"What can I help you with today?\n"
            f"_Abby • Ledgr Chapel_"
        )
    elif 17 <= hour < 22:
        return (
            f"Good evening, {first_name} 😊\n"
            f"I'm here if you need anything.\n"
            f"_Abby • Ledgr Chapel_"
        )
    else:
        return (
            f"Still working hard, {first_name}? 😊\n"
            f"I'm here. What do you need?\n"
            f"_Abby • Ledgr Chapel_"
        )

def _handle_greeting(phone, message_body, first_name, session):
    """Handle greeting from registered user."""
    reply = _get_time_greeting(first_name)
    _reply(phone, reply)
    update_session_context(phone, message_body, reply)

def _handle_voice_note(phone, media_id, first_name, user, session):
    """Process voice note."""
    try:
        result = _transcribe_audio_from_media_id(media_id)

        if result is None:
            _reply(phone, (
                f"I couldn't read that audio format, {first_name} 😕 "
                f"Try sending the voice note directly in WhatsApp rather than as a file attachment 🙏"
            ))
            return

        if result.get("error") == "too_short":
            _reply(phone, (
                f"That voice note was too short for me to catch, {first_name} 😊 "
                f"Try again and hold the record button a little longer."
            ))
            return

        if result.get("error") == "too_long":
            _reply(phone, (
                f"That's a long one, {first_name}! "
                f"Voice notes work best under 5 minutes. "
                f"Try splitting it into two shorter notes 🙏"
            ))
            return

        transcript = result.get("text", "").lower()
        if not transcript:
            _reply(phone, (
                f"I couldn't catch any words in that voice note, {first_name} 😕 "
                f"Could you try again? Speak clearly and pause briefly between items 🙏"
            ))
            return

        increment_stat("total_voice_notes_transcribed")

        transcript_issues = []
        if result.get("confidence_scores"):
            for i, score in enumerate(result["confidence_scores"]):
                if score < -0.5:
                    seg_text = result["segments"][i].get("text", "") if result.get("segments") else ""
                    transcript_issues.append(f"unclear segment: '{seg_text.strip()}'")

        _process_transaction(phone, transcript, first_name, user, session, transcript_issues)

    except Exception as e:
        safe_log_error(e, "_handle_voice_note", phone)
        _reply(phone, fallback_response(first_name))

def _handle_text_message(phone, message_body, first_name, user, session):
    """Process text message — detect intent and route."""
    try:
        transcript = message_body.lower()
        _process_transaction(phone, transcript, first_name, user, session)
    except Exception as e:
        safe_log_error(e, "_handle_text_message", phone)
        _reply(phone, fallback_response(first_name))

def _process_transaction(phone, transcript, first_name, user, session, transcript_issues=None):
    """Core transaction processing — analyze, confirm, save."""
    try:
        pending = get_pending(phone)
        analysis = analyze_message(transcript, phone)
        intent = analysis.get("intent", "general_chat")
        confidence = analysis.get("confidence", "low")
        entities = analysis.get("entities", {})

        if confidence == "low":
            reply = craft_error(first_name)
            _reply(phone, reply)
            update_session_context(phone, transcript, reply)
            return

        if intent in ["record_income", "record_expense"]:
            entries = analysis.get("entries_for_recording", [])
            if not entries:
                _reply(phone, craft_error(first_name))
            else:
                save_pending(phone, entries, transcript)
                set_pending_transaction(phone, entries)
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in entries)
                overall_conf = analysis.get("overall_confidence", "high")
                low_reason = analysis.get("low_confidence_reason", "")
                issues = analysis.get("transcript_issues_detected", [])
                if transcript_issues:
                    issues.extend(transcript_issues)

                msg = craft_smart_confirmation(entries, first_name, net, overall_conf, low_reason, issues)
                _reply(phone, msg)
                update_session_context(phone, transcript, msg)
            return

        elif intent == "edit_pending" and pending:
            updated_entries = analysis.get("updated_pending_entries", pending.get("entries", []))
            if updated_entries:
                update_pending(phone, updated_entries)
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in updated_entries)
                msg = craft_smart_confirmation(updated_entries, first_name, net, "high")
                _reply(phone, msg)
            else:
                _reply(phone, f"Let me make sure I have this right, {first_name} — could you clarify which item to change?")
            return

        elif intent == "delete_transaction":
            category = entities.get("category")
            if category:
                success = delete_transaction_by_details(phone, category)
                if success:
                    _reply(phone, f"Got it, {first_name} ✅ Deleted the last {category} record.")
                else:
                    _reply(phone, f"Hmm, I couldn't find a record for {category} to delete, {first_name}.")
            else:
                _reply(phone, f"Please tell me which item to delete, {first_name} (e.g., 'Delete the fuel record').")
            return

        elif intent == "delete_reports":
            time_to_keep = entities.get("time_to_keep", "all")
            if time_to_keep == "today":
                delete_old_transactions(phone, keep_days=1)
                msg = f"Done ✅ All records older than today have been removed."
            elif time_to_keep == "week":
                delete_old_transactions(phone, keep_days=7)
                msg = f"Done ✅ All records older than this week have been removed."
            elif time_to_keep == "month":
                delete_old_transactions(phone, keep_days=30)
                msg = f"Done ✅ All records older than this month have been removed."
            else:
                delete_old_transactions(phone, keep_days=0)
                msg = f"Done ✅ All records have been cleared. Send a new voice note to start recording."
            _reply(phone, msg)
            return

        elif transcript.strip() == "yes":
            pending = get_pending(phone)
            if pending:
                saved = save_transactions(phone, pending["entries"])
                delete_pending(phone)
                clear_pending_transaction(phone)

                increment_stat("total_transactions_processed", len(pending["entries"]))
                run_background_insights(phone, phone, pending["entries"])
                _reply(phone, f"You're all caught up, {first_name} 🙌")
            else:
                _reply(phone, f"Nothing pending to save, {first_name}. Send a voice note to record transactions.")
            return

        elif transcript.strip() == "no":
            delete_pending(phone)
            clear_pending_transaction(phone)
            _reply(phone, f"No worries, {first_name} ❌ Cancelled. Send a new voice note whenever you're ready.")
            return

        elif transcript.strip().upper() == "help":
            _reply(phone, craft_help(first_name, user.get("role", "treasurer")))
            return

        elif intent == "query_balance":
            days = 30
            if entities.get("date_range") == "week": days = 7
            elif entities.get("date_range") == "today": days = 1

            summary = get_balance_summary(phone, days=days)
            msg = craft_response("question_balance", {
                "net": summary['net_balance'],
                "income": summary['total_income'],
                "expenses": summary['total_expenses'],
                "period": f"last {days} days"
            }, {"sender_phone": phone, "full_name": first_name})
            _reply(phone, msg)
            return

        elif intent in ["get_transactions", "get_records_by_person"]:
            person_name = entities.get("person_name")
            category = entities.get("category")
            days = 7 if entities.get("date_range") != "month" else 30

            if person_name:
                txns = search_transactions_by_person(phone, person_name, days=days)
            elif category:
                txns = search_transactions(phone, category, days=days)
            else:
                txns = get_transactions(phone, days=days)

            if not txns:
                _reply(phone, f"No records found for {person_name or category} in the last {days} days, {first_name}.")
            else:
                msg_lines = [f"Here's what I found, {first_name}:\n"]
                for i, t in enumerate(txns[:10], 1):
                    msg_lines.append(f"{i}. {t['category'].capitalize()}: {format_naira(t['amount'])} ({t['type']}) - {t['created_at'][:10]}")
                    if t.get("note"):
                        msg_lines.append(f"   ↳ {t['note']}")
                _reply(phone, "\n".join(msg_lines))
            return

        elif intent == "generate_report":
            report = generate_weekly_report(phone)
            _reply(phone, report)
            return

        elif transcript.strip().isdigit() and pending:
            clarification_amount = int(transcript.strip())
            updated = False
            for entry in pending.get("entries", []):
                if entry.get("extraction_confidence") in ["medium", "low"]:
                    if clarification_amount < 1000:
                        clarification_amount *= 1000
                    entry["amount"] = clarification_amount
                    entry["extraction_confidence"] = "high"
                    entry["raw_text_used"] = f"Clarified: {clarification_amount:,}"
                    updated = True
                    break

            if updated:
                update_pending(phone, pending["entries"])
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in pending["entries"])
                lines = [f"Got it — updating that to ₦{clarification_amount:,} ✅\n\nHere's the updated record:\n"]
                for e in pending["entries"]:
                    label = e['category'].capitalize()
                    amt = f"₦{int(e['amount']):,}"
                    programme = e.get('programme', '')
                    programme_tag = f" — {programme}" if programme else ""
                    lines.append(f"- {label}{programme_tag} — {amt}")
                lines.append("\nReply *YES* to save 🙏")
                _reply(phone, "\n".join(lines))
            else:
                _reply(phone, f"Thanks, {first_name}. Reply *YES* to save or *NO* to cancel.")
            return

        else:
            natural = extract_intent_natural(transcript)
            nat_intent = natural.get("intent", "other")

            if nat_intent == "other":
                try:
                    unsupported = detect_unsupported_request(transcript)
                    log_unsupported_request(
                        sender_phone=phone,
                        sender_name=first_name,
                        raw_message=transcript,
                        detected_intent=unsupported.get("detected_intent", ""),
                        category=unsupported.get("category", "other"),
                        priority_signal=unsupported.get("priority_signal", "low")
                    )
                    _reply(phone, craft_unsupported_response(first_name, unsupported["detected_intent"], unsupported["category"], user.get("role", "treasurer")))
                except Exception:
                    _reply(phone, craft_response("other", natural, {"sender_phone": phone, "full_name": first_name}))
            else:
                _reply(phone, craft_response(nat_intent, natural, {"sender_phone": phone, "full_name": first_name}))
            return

    except Exception as e:
        safe_log_error(e, "_process_transaction", phone)
        _reply(phone, fallback_response(first_name))

def _handle_yes_no(phone, message_body, first_name, user, session):
    """Handle YES/NO response after pending confirmation."""
    msg_lower = message_body.lower().strip()

    if msg_lower == "yes" or msg_lower == "y":
        pending = get_pending(phone)
        if pending:
            saved = save_transactions(phone, pending["entries"])
            delete_pending(phone)
            clear_pending_transaction(phone)

            increment_stat("total_transactions_processed", len(pending["entries"]))
            run_background_insights(phone, phone, pending["entries"])

            update_session_state(phone, "ACTIVE")
            _reply(phone, f"You're all caught up, {first_name} 🙌")
        else:
            _reply(phone, f"Nothing pending to save, {first_name}. Send a voice note to record transactions.")
    elif msg_lower == "no" or msg_lower == "n":
        delete_pending(phone)
        clear_pending_transaction(phone)
        update_session_state(phone, "ACTIVE")
        _reply(phone, f"No worries, {first_name} ❌ Cancelled. Send a new voice note whenever you're ready.")
    else:
        return _handle_text_message(phone, message_body, first_name, user, session)

def _handle_clarify(phone, message_body, first_name, user, session):
    """Handle clarification response."""
    return _handle_text_message(phone, message_body, first_name, user, session)

# ============================================================
# DEV RESET
# ============================================================

@app.route("/dev/reset", methods=["POST"])
def dev_reset():
    if os.environ.get("FLASK_ENV") != "development":
        logging.warning("Attempted access to /dev/reset in non-development environment.")
        return "Forbidden", 403

    try:
        from supabase import create_client
        sb = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

        sb.table("pending_confirmations").delete().gt("created_at", "1900-01-01").execute()
        sb.table("transactions").delete().gt("created_at", "1900-01-01").execute()
        sb.table("sessions").delete().gt("created_at", "1900-01-01").execute()
        sb.table("onboarding_progress").delete().gt("started_at", "1900-01-01").execute()
        sb.table("users").delete().gt("registered_at", "1900-01-01").execute()
        sb.table("churches").delete().gt("created_at", "1900-01-01").execute()

        logging.info("Development reset completed.")
        return "Development reset complete.", 200
    except Exception as e:
        logging.error(f"Development reset failed: {e}")
        return f"Reset failed: {str(e)}", 500

if __name__ == "__main__":
    scheduler.start()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
