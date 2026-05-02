import os
import tempfile
import logging
import traceback
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
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

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-secret')
app.config['ADMIN_PHONE'] = os.environ.get('ADMIN_PHONE', '')

app.register_blueprint(web)

def format_naira(amount):
    return f"₦{int(amount):,}"

def download_audio(media_url):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    response = requests.get(media_url, auth=(account_sid, auth_token))
    if response.status_code == 200:
        with open(path, "wb") as f:
            f.write(response.content)
        return path
    raise Exception("Failed to download audio from Twilio")

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

# ============================================================
# WEBHOOK — Single Entry Point
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    resp = MessagingResponse()
    
    # 1. Extract basics from Twilio payload
    raw_phone = request.form.get("From", "")
    message_body = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    media_type = request.form.get("MediaContentType0", "")
    
    phone = clean_phone(raw_phone)
    
    try:
        # 2. ALWAYS check if user exists
        user = get_user_by_phone(phone)
        
        if user:
            update_last_seen(phone)
        
        # 3. Get or create session
        session = get_or_create_session(phone)
        session_state = session.get("state", "UNKNOWN") if session else "UNKNOWN"
        
        # 4. Route based on user existence and state
        if not user:
            return handle_unknown_user(resp, phone, message_body, session)
        
        if user.get("onboarding_step", 0) < 5:
            return handle_onboarding(resp, phone, message_body, media_url, media_type, user, session)
        
        # 5. Fully registered user — normal flow
        return handle_registered_user(
            resp, phone, message_body, media_url, media_type, user, session
        )
        
    except Exception as e:
        safe_log_error(e, "webhook", phone)
        name = get_user_display_name(phone)
        resp.message(fallback_response(name))
        return str(resp)

# ============================================================
# UNKNOWN USER — First contact ever
# ============================================================

def handle_unknown_user(resp, phone, message_body, session):
    """Step 0 — First contact ever (state: UNKNOWN)"""
    try:
        # Create user record
        user = create_user(phone)
        if not user:
            resp.message("Welcome! I'm Abby, your church's personal finance assistant. Please try again in a moment 🙏")
            return str(resp)
        
        # Set to step 1 (waiting for name)
        advance_onboarding(phone, 1)
        update_session_state(phone, "ONBOARDING_1")
        
        reply = (
            "Hello! 👋 Welcome to Ledgr Chapel.\n\n"
            "I'm Abby, your church's personal finance assistant.\n"
            "I help churches record offerings, expenses, and generate\n"
            "reports — all through WhatsApp voice notes.\n\n"
            "To get started, what's your full name?"
        )
        resp.message(reply)
        update_session_context(phone, message_body, reply)
        
    except Exception as e:
        safe_log_error(e, "handle_unknown_user", phone)
        resp.message(f"DEBUG ERROR: {str(e)}")
    
    return str(resp)

# ============================================================
# ONBOARDING — Steps 1-4
# ============================================================

def handle_onboarding(resp, phone, message_body, media_url, media_type, user, session):
    """Route to the correct onboarding step."""
    try:
        step = user.get("onboarding_step", 0)
        first_name = user.get("first_name") or get_user_display_name(phone)
        
        # Transcribe voice notes for ALL onboarding steps
        if media_url and media_type and "audio" in media_type:
            try:
                audio_path = download_audio(media_url)
                transcription = transcribe_audio(audio_path)
                message_body = transcription.get("text", "").strip()
                import os as _os
                if _os.path.exists(audio_path):
                    _os.remove(audio_path)
            except Exception as e:
                safe_log_error(e, "handle_onboarding_voice", phone)
                resp.message("Sorry, I couldn't understand that voice note. Please try again or type your response 😊")
                return str(resp)
        
        if step == 0:
            return _onboarding_step_0(resp, phone, message_body, user)
        elif step == 1:
            return _onboarding_step_1(resp, phone, message_body, user)
        elif step == 2:
            return _onboarding_step_2(resp, phone, message_body, user)
        elif step == 3:
            return _onboarding_step_3(resp, phone, message_body, user)
        elif step == 4:
            return _onboarding_step_4(resp, phone, message_body, user)
        else:
            complete_onboarding(phone)
            return handle_registered_user(resp, phone, message_body, None, None, user, session)
            
    except Exception as e:
        safe_log_error(e, "handle_onboarding", phone)
        resp.message(f"DEBUG ERROR: {str(e)}")
    
    return str(resp)

def _onboarding_step_0(resp, phone, message_body, user):
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
    resp.message(reply)
    update_session_context(phone, message_body, reply)
    return str(resp)

def _onboarding_step_1(resp, phone, message_body, user):
    """Waiting for name (state: ONBOARDING_1)"""
    valid, error_msg = validate_name(message_body)
    
    if not valid:
        resp.message(
            f"I didn't quite catch that as a name 😊\n"
            f"Please reply with your full name — for example:\n"
            f"*Grace Adeyemi* or *Pastor James Okafor*"
        )
        return str(resp)
    
    # Extract first and last name
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
    resp.message(reply)
    update_session_context(phone, message_body, reply)
    return str(resp)

def _onboarding_step_2(resp, phone, message_body, user):
    """Waiting for role (state: ONBOARDING_2)"""
    role = validate_role(message_body)
    
    if not role:
        resp.message(
            f"Please reply with just *1*, *2*, or *3* to choose your role:\n\n"
            f"*1* — Pastor\n"
            f"*2* — Treasurer\n"
            f"*3* — Collector"
        )
        return str(resp)
    
    update_user_role(phone, role)
    advance_onboarding(phone, 3, {"role": role})
    update_session_state(phone, "ONBOARDING_3")
    
    reply = "Perfect! And what is the name of your church?"
    resp.message(reply)
    update_session_context(phone, message_body, reply)
    return str(resp)

def _onboarding_step_3(resp, phone, message_body, user):
    """Waiting for church name (state: ONBOARDING_3)"""
    valid, error_msg = validate_church_name(message_body)
    
    if not valid:
        resp.message("Please provide a church name with at least 3 characters.")
        return str(resp)
    
    church_name = message_body.strip()
    role = user.get("role")
    first_name = user.get("first_name", "Friend")
    last_name = user.get("last_name", "")
    
    # Check if church already exists
    existing_church = find_church_by_name(church_name)
    
    if existing_church:
        # Link to existing church
        update_user_church(phone, existing_church["id"])
        advance_onboarding(phone, 4, {"church_name": church_name, "church_id": existing_church["id"]})
        
        reply = (
            f"I found {existing_church.get('church_name', church_name)} already on Ledgr Chapel 🙏\n\n"
            f"I've sent a request to the church admin to verify "
            f"your membership. You'll be notified once approved.\n\n"
            f"Is that the right church?"
        )
        resp.message(reply)
        update_session_state(phone, "ONBOARDING_4_VERIFY")
        
    elif role == "pastor":
        # Pastor creating a new church — self-verify
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
            resp.message(reply)
        else:
            resp.message("Something went wrong creating your church record. Please try again 🙏")
            
    else:
        # Treasurer or collector — needs admin approval
        new_church = create_church(church_name, pastor_phone="")
        if new_church:
            update_user_church(phone, new_church["id"])
            advance_onboarding(phone, 4, {"church_name": church_name, "church_id": new_church["id"]})
            
            # Notify admin
            admin_phone = os.environ.get("ADMIN_PHONE")
            if admin_phone:
                try:
                    from reports import send_twilio_message
                    send_twilio_message(
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
            resp.message(reply)
            update_session_state(phone, "ONBOARDING_4")
        else:
            resp.message("Something went wrong. Please try again 🙏")
    
    update_session_context(phone, message_body, resp.messages[0].body if resp.messages else "")
    return str(resp)

def _onboarding_step_4(resp, phone, message_body, user):
    """Pending verification (state: ONBOARDING_4 or ONBOARDING_4_VERIFY)"""
    first_name = user.get("first_name", "Friend")
    
    # Check if this is an approval message from a pastor
    msg_lower = message_body.lower().strip()
    if msg_lower.startswith("approve"):
        # Pastor approving a user
        target_phone = msg_lower.replace("approve", "").strip()
        if target_phone and target_phone.startswith("+"):
            verify_user(target_phone)
            complete_onboarding(target_phone)
            complete_onboarding_progress(target_phone)
            
            # Notify the approved user
            admin_phone = os.environ.get("ADMIN_PHONE")
            try:
                from reports import send_twilio_message
                approved_user = get_user_by_phone(target_phone)
                if approved_user:
                    approved_first = approved_user.get("first_name", "Friend")
                    church = get_church(approved_user.get("church_id"))
                    church_name = church.get("church_name", "your church") if church else "your church"
                    
                    send_twilio_message(
                        target_phone,
                        f"Great news, {approved_first}! ✅\n"
                        f"You've been verified at {church_name}.\n"
                        f"You're all set on Ledgr Chapel!\n\n"
                        f"Send me a voice note to log your first record,\n"
                        f"or type *HELP* to see what I can do 😊\n"
                        f"_Abby • Ledgr Chapel by Intravent_"
                    )
                    resp.message(f"✅ {approved_first} has been approved and notified.")
                else:
                    resp.message(f"User with phone {target_phone} not found.")
            except Exception:
                resp.message("Approval processed but notification failed. Check logs.")
            
            return str(resp)
    
    # User messaging while pending
    reply = (
        f"You're almost set, {first_name} 😊\n"
        f"We're just waiting for your church admin to verify "
        f"your membership. I'll notify you as soon as it's done 🙏"
    )
    resp.message(reply)
    return str(resp)

# ============================================================
# REGISTERED USER — Normal flow
# ============================================================

def handle_registered_user(resp, phone, message_body, media_url, media_type, user, session):
    """Fully registered user flow."""
    try:
        first_name = user.get("first_name", "Friend")
        role = user.get("role", "member")
        church = None
        if user.get("church_id"):
            church = get_church(user["church_id"])
        
        # Get session state
        session = get_or_create_session(phone)
        current_state = session.get("state", "ACTIVE")
        
        # Check if this is a greeting
        if _is_greeting(message_body) and current_state not in ["AWAITING_YES_NO", "AWAITING_CLARIFY", "AWAITING_CORRECT"]:
            return _handle_greeting(resp, phone, message_body, first_name, session)
        
        # Route by session state first
        if current_state == "AWAITING_YES_NO":
            return _handle_yes_no(resp, phone, message_body, first_name, user, session)
        
        if current_state == "AWAITING_CLARIFY":
            return _handle_clarify(resp, phone, message_body, first_name, user, session)
        
        # Check session age for soft reset greeting
        if not is_session_active(phone) and current_state == "ACTIVE":
            # Soft reset — clear pending
            clear_pending_transaction(phone)
            update_session_state(phone, "ACTIVE")
        
        # Route by message type
        if media_url and media_type and "audio" in media_type:
            return _handle_voice_note(resp, phone, media_url, first_name, user, session)
        
        # Text message — detect intent
        return _handle_text_message(resp, phone, message_body, first_name, user, session, church)
        
    except Exception as e:
        safe_log_error(e, "handle_registered_user", phone)
        name = user.get("first_name", "Friend")
        resp.message(fallback_response(name))
    
    return str(resp)

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

def _handle_greeting(resp, phone, message_body, first_name, session):
    """Handle greeting from registered user."""
    reply = _get_time_greeting(first_name)
    resp.message(reply)
    update_session_context(phone, message_body, reply)
    return str(resp)

def _handle_voice_note(resp, phone, media_url, first_name, user, session):
    """Process voice note."""
    try:
        result = transcribe_audio_from_url(media_url)
        
        if result is None:
            resp.message(
                f"I couldn't read that audio format, {first_name} 😕 "
                f"Try sending the voice note directly in WhatsApp rather than as a file attachment 🙏"
            )
            return str(resp)
        
        if result.get("error") == "too_short":
            resp.message(
                f"That voice note was too short for me to catch, {first_name} 😊 "
                f"Try again and hold the record button a little longer."
            )
            return str(resp)
        
        if result.get("error") == "too_long":
            resp.message(
                f"That's a long one, {first_name}! "
                f"Voice notes work best under 5 minutes. "
                f"Try splitting it into two shorter notes 🙏"
            )
            return str(resp)
        
        transcript = result.get("text", "").lower()
        if not transcript:
            resp.message(
                f"I couldn't catch any words in that voice note, {first_name} 😕 "
                f"Could you try again? Speak clearly and pause briefly between items 🙏"
            )
            return str(resp)
        
        increment_stat("total_voice_notes_transcribed")
        
        transcript_issues = []
        if result.get("confidence_scores"):
            for i, score in enumerate(result["confidence_scores"]):
                if score < -0.5:
                    seg_text = result["segments"][i].get("text", "") if result.get("segments") else ""
                    transcript_issues.append(f"unclear segment: '{seg_text.strip()}'")
        
        # Process transcript as text
        return _process_transaction(resp, phone, transcript, first_name, user, session, transcript_issues)
        
    except Exception as e:
        safe_log_error(e, "_handle_voice_note", phone)
        resp.message(fallback_response(first_name))
    
    return str(resp)

def transcribe_audio_from_url(media_url):
    """Download and transcribe audio."""
    audio_path = None
    try:
        audio_path = download_audio(media_url)
        return transcribe_audio(audio_path)
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

def _handle_text_message(resp, phone, message_body, first_name, user, session, church):
    """Process text message — detect intent and route."""
    try:
        transcript = message_body.lower()
        return _process_transaction(resp, phone, transcript, first_name, user, session)
    except Exception as e:
        safe_log_error(e, "_handle_text_message", phone)
        resp.message(fallback_response(first_name))
    return str(resp)

def _process_transaction(resp, phone, transcript, first_name, user, session, transcript_issues=None):
    """Core transaction processing — analyze, confirm, save."""
    try:
        pending = get_pending(phone)
        analysis = analyze_message(transcript, phone)
        intent = analysis.get("intent", "general_chat")
        confidence = analysis.get("confidence", "low")
        entities = analysis.get("entities", {})
        
        if confidence == "low":
            reply = craft_error(first_name)
            resp.message(reply)
            update_session_context(phone, transcript, reply)
            return str(resp)
        
        # Record income/expense
        if intent in ["record_income", "record_expense"]:
            entries = analysis.get("entries_for_recording", [])
            if not entries:
                resp.message(craft_error(first_name))
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
                resp.message(msg)
                update_session_context(phone, transcript, msg)
            
            return str(resp)
        
        # Edit pending
        elif intent == "edit_pending" and pending:
            updated_entries = analysis.get("updated_pending_entries", pending.get("entries", []))
            if updated_entries:
                update_pending(phone, updated_entries)
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in updated_entries)
                msg = craft_smart_confirmation(updated_entries, first_name, net, "high")
                resp.message(msg)
            else:
                resp.message(f"Let me make sure I have this right, {first_name} — could you clarify which item to change?")
            return str(resp)
        
        # Delete transaction
        elif intent == "delete_transaction":
            category = entities.get("category")
            if category:
                success = delete_transaction_by_details(phone, category)
                if success:
                    resp.message(f"Got it, {first_name} ✅ Deleted the last {category} record.")
                else:
                    resp.message(f"Hmm, I couldn't find a record for {category} to delete, {first_name}.")
            else:
                resp.message(f"Please tell me which item to delete, {first_name} (e.g., 'Delete the fuel record').")
            return str(resp)
        
        # Delete reports
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
            resp.message(msg)
            return str(resp)
        
        # YES/NO flow
        elif transcript.strip() == "yes":
            pending = get_pending(phone)
            if pending:
                saved = save_transactions(phone, pending["entries"])
                delete_pending(phone)
                clear_pending_transaction(phone)
                
                increment_stat("total_transactions_processed", len(pending["entries"]))
                run_background_insights(phone, phone, pending["entries"])
                resp.message(f"You're all caught up, {first_name} 🙌")
            else:
                resp.message(f"Nothing pending to save, {first_name}. Send a voice note to record transactions.")
            return str(resp)
        
        elif transcript.strip() == "no":
            delete_pending(phone)
            clear_pending_transaction(phone)
            resp.message(f"No worries, {first_name} ❌ Cancelled. Send a new voice note whenever you're ready.")
            return str(resp)
        
        # HELP
        elif transcript.strip().upper() == "help":
            from personality import craft_help
            resp.message(craft_help(first_name, user.get("role", "treasurer")))
            return str(resp)
        
        # Query balance
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
            resp.message(msg)
            return str(resp)
        
        # Get transactions
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
                resp.message(f"No records found for {person_name or category} in the last {days} days, {first_name}.")
            else:
                msg_lines = [f"Here's what I found, {first_name}:\n"]
                for i, t in enumerate(txns[:10], 1):
                    msg_lines.append(f"{i}. {t['category'].capitalize()}: {format_naira(t['amount'])} ({t['type']}) - {t['created_at'][:10]}")
                    if t.get("note"):
                        msg_lines.append(f"   ↳ {t['note']}")
                resp.message("\n".join(msg_lines))
            return str(resp)
        
        # Generate report
        elif intent == "generate_report":
            report = generate_weekly_report(phone)
            resp.message(report)
            return str(resp)
        
        # Number clarification (medium confidence)
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
                resp.message("\n".join(lines))
            else:
                resp.message(f"Thanks, {first_name}. Reply *YES* to save or *NO* to cancel.")
            return str(resp)
        
        # General chat / unsupported request
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
                    resp.message(craft_unsupported_response(first_name, unsupported["detected_intent"], unsupported["category"], user.get("role", "treasurer")))
                except Exception:
                    resp.message(craft_response("other", natural, {"sender_phone": phone, "full_name": first_name}))
            else:
                resp.message(craft_response(nat_intent, natural, {"sender_phone": phone, "full_name": first_name}))
            
            return str(resp)
            
    except Exception as e:
        safe_log_error(e, "_process_transaction", phone)
        resp.message(fallback_response(first_name))
    
    return str(resp)

def _handle_yes_no(resp, phone, message_body, first_name, user, session):
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
            resp.message(f"You're all caught up, {first_name} 🙌")
        else:
            resp.message(f"Nothing pending to save, {first_name}. Send a voice note to record transactions.")
    elif msg_lower == "no" or msg_lower == "n":
        delete_pending(phone)
        clear_pending_transaction(phone)
        update_session_state(phone, "ACTIVE")
        resp.message(f"No worries, {first_name} ❌ Cancelled. Send a new voice note whenever you're ready.")
    else:
        # Not yes/no — treat as normal message
        return _handle_text_message(resp, phone, message_body, first_name, user, session, None)
    
    return str(resp)

def _handle_clarify(resp, phone, message_body, first_name, user, session):
    """Handle clarification response."""
    # TODO: Implement clarification flow
    return _handle_text_message(resp, phone, message_body, first_name, user, session, None)

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
