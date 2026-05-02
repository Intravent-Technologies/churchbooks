import os
import tempfile
import logging
import requests
import traceback
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from transcribe import transcribe_audio
from extract import extract_entries
from intelligence import analyze_message, extract_name_and_role
from personality import (
    craft_response, craft_confirmation, craft_error, craft_help,
    craft_smart_confirmation, craft_unsupported_response, craft_stats_response,
    append_insight, extract_intent_natural, get_first_name,
    craft_onboarding_welcome, craft_onboarding_name_saved, craft_onboarding_complete
)
from financial_advisor import run_background_insights
from auth import get_user_by_phone
from database import (
    save_pending, update_pending, get_pending, delete_pending, save_transactions,
    get_transactions, get_transaction_by_id, update_transaction,
    delete_transaction_by_details, search_transactions_by_person,
    search_transactions, get_balance_summary,
    upsert_session, get_session, delete_session, delete_all_sessions,
    log_unsupported_request, get_system_stats, increment_stat
)
from reports import generate_monthly_report, generate_weekly_report
from scheduler import scheduler
from web_routes import web

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'fallback-secret')

# Register web routes (landing page, auth, dashboard)
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

@app.route("/webhook", methods=["POST"])
def webhook():
    resp = MessagingResponse()
    
    sender_phone = request.form.get("From")
    body = request.form.get("Body", "").strip()
    media_url = request.form.get("MediaUrl0")
    
    # Check if user is registered
    registered_user = get_user_by_phone(sender_phone)
    
    # Load or create persistent session
    session = get_session(sender_phone)
    is_new_user = session is None
    
    if is_new_user:
        session = {"context": [], "metadata": {}}
        
    metadata = session.get("metadata", {})
    
    # If registered, use their details; otherwise use session metadata or default
    if registered_user:
        name = registered_user["full_name"].split()[0].capitalize()
        role = registered_user.get("role", "member")
        is_registered = True
        onboarding_complete = True
    else:
        name = get_first_name(sender_phone, metadata.get("name"))
        role = metadata.get("role", "member")
        is_registered = False
        onboarding_complete = metadata.get("onboarding_complete", False)
    
    # --- ONBOARDING FLOW (only for unregistered users) ---
    if not is_registered and not onboarding_complete:
        onboarding_step = metadata.get("onboarding_step", "welcome")
        
        if onboarding_step == "welcome":
            # First message ever — ask for name
            resp.message(craft_onboarding_welcome())
            upsert_session(sender_phone, intent="onboarding_start", 
                         context_entry={"role": "assistant", "content": "onboarding_welcome", "timestamp": datetime.now().isoformat()},
                         metadata={"onboarding_step": "ask_name"})
            return str(resp)
            
        elif onboarding_step == "ask_name":
            # User replied — extract name
            extracted = extract_name_and_role(body)
            user_name = extracted.get("name") or body.strip().capitalize()
            user_role = extracted.get("role")
            
            # Save name to metadata
            new_meta = {"name": user_name, "onboarding_step": "ask_role"}
            if user_role:
                new_meta["role"] = user_role
                new_meta["onboarding_step"] = "complete"
                
            session["context"].append({"role": "user", "content": body, "timestamp": datetime.now().isoformat()})
            session["context"].append({"role": "assistant", "content": "onboarding_name_saved", "timestamp": datetime.now().isoformat()})
            
            if new_meta["onboarding_step"] == "complete":
                resp.message(craft_onboarding_complete(user_name, user_role))
                new_meta["onboarding_complete"] = True
            else:
                resp.message(craft_onboarding_name_saved(user_name))
                
            upsert_session(sender_phone, intent="onboarding_name_provided",
                         context_entry=session["context"][-1], metadata=new_meta)
            return str(resp)
            
        elif onboarding_step == "ask_role":
            # User replied with role
            extracted = extract_name_and_role(body)
            user_role = extracted.get("role") or body.strip().capitalize()
            user_name = metadata.get("name", name)
            
            session["context"].append({"role": "user", "content": body, "timestamp": datetime.now().isoformat()})
            session["context"].append({"role": "assistant", "content": "onboarding_complete", "timestamp": datetime.now().isoformat()})
            
            resp.message(craft_onboarding_complete(user_name, user_role))
            
            upsert_session(sender_phone, intent="onboarding_complete",
                         context_entry=session["context"][-1],
                         metadata={"name": user_name, "role": user_role, "onboarding_step": "complete", "onboarding_complete": True})
            return str(resp)
    
    # --- REGISTERED USER WELCOME (first WhatsApp contact) ---
    if is_registered and not metadata.get("welcomed"):
        welcome_msg = (
            f"Welcome to ChurchBooks, {name}! 🙏✨\n\n"
            f"I'm Abby, your AI assistant. I'm all set up and ready to help.\n\n"
            f"Just send me a voice note after service to record transactions,\n"
            f"or ask me things like _'Show me this week's expenses'_ or _'What's our balance?_'\n\n"
            f"Type *HELP* anytime to see what I can do. Let's keep those books clean! 📖"
        )
        resp.message(welcome_msg)
        upsert_session(sender_phone, intent="registered_welcome",
                     context_entry={"role": "assistant", "content": "registered_welcome", "timestamp": datetime.now().isoformat()},
                     metadata={"welcomed": True, "name": name, "role": role})
        return str(resp)

    # --- NORMAL FLOW (after onboarding or for registered users) ---
    try:
        transcript = body.lower()
        transcript_issues = []
        
        if media_url:
            audio_path = None
            try:
                audio_path = download_audio(media_url)
                result = transcribe_audio(audio_path)
                
                if result is None:
                    resp.message(f"I couldn't read that audio format, {name} 😕 Try sending the voice note directly in WhatsApp rather than as a file attachment 🙏")
                    return str(resp)
                
                if result.get("error") == "too_short":
                    resp.message(f"That voice note was too short for me to catch, {name} 😊 Try again and hold the record button a little longer.")
                    return str(resp)
                
                if result.get("error") == "too_long":
                    resp.message(f"That's a long one, {name}! Voice notes work best under 5 minutes. Try splitting it into two shorter notes 🙏")
                    return str(resp)
                
                transcript = result.get("text", "").lower()
                if not transcript:
                    resp.message(f"I couldn't catch any words in that voice note, {name} 😕 Could you try again? Speak clearly and pause briefly between items 🙏")
                    return str(resp)
                
                # Track voice note transcription stat
                from database import increment_stat
                increment_stat("total_voice_notes_transcribed")
                
                # Check for low-confidence segments
                if result.get("confidence_scores"):
                    for i, score in enumerate(result["confidence_scores"]):
                        if score < -0.5:
                            seg_text = result["segments"][i].get("text", "") if result.get("segments") else ""
                            transcript_issues.append(f"unclear segment: '{seg_text.strip()}'")
            finally:
                if audio_path and os.path.exists(audio_path):
                    os.remove(audio_path)
        
        # Store conversation in session context
        session["context"].append({"role": "user", "content": transcript, "timestamp": datetime.now().isoformat()})
        
        pending = get_pending(sender_phone)
        analysis = analyze_message(transcript, sender_phone)
        intent = analysis.get("intent", "general_chat")
        confidence = analysis.get("confidence", "low")
        entities = analysis.get("entities", {})
        
        # Only update name if user explicitly introduces themselves (via AI analysis)
        # NEVER extract names from transaction context — those are other people
        extracted_name = analysis.get("extracted_name")
        if extracted_name and metadata.get("name", "").lower() != extracted_name.lower():
            metadata["name"] = extracted_name
            name = extracted_name.split()[0].capitalize()
        
        if confidence == "low":
            resp.message(craft_error(name))
            session["context"].append({"role": "assistant", "content": "error", "timestamp": datetime.now().isoformat()})
            upsert_session(sender_phone, intent="error", context_entry=session["context"][-1])
            return str(resp)
            
        if intent in ["record_income", "record_expense"]:
            entries = analysis.get("entries_for_recording", [])
            if not entries:
                resp.message(craft_error(name))
            else:
                save_pending(sender_phone, entries, transcript)
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in entries)
                overall_conf = analysis.get("overall_confidence", "high")
                low_reason = analysis.get("low_confidence_reason", "")
                issues = analysis.get("transcript_issues_detected", [])
                msg = craft_smart_confirmation(entries, name, net, overall_conf, low_reason, issues)
                resp.message(msg)
                session["context"].append({"role": "assistant", "content": "pending_confirmation", "timestamp": datetime.now().isoformat()})
                upsert_session(sender_phone, intent=intent, context_entry=session["context"][-1], metadata={"last_entries": entries, "overall_confidence": overall_conf})
        
        elif intent == "edit_pending" and pending:
            updated_entries = analysis.get("updated_pending_entries", pending.get("entries", []))
            if updated_entries:
                update_pending(sender_phone, updated_entries)
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in updated_entries)
                resp.message(craft_confirmation(updated_entries, name, net))
            else:
                resp.message(f"Let me make sure I have this right, {name} — could you clarify which item to change?")
                
        elif intent == "edit_pending" and not pending:
            resp.message(f"You don't have any pending drafts, {name}. Send a voice note first to start recording.")

        elif intent == "delete_pending_item" and pending:
             updated_entries = analysis.get("updated_pending_entries", [])
             if updated_entries:
                 update_pending(sender_phone, updated_entries)
                 net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in updated_entries)
                 resp.message(craft_confirmation(updated_entries, name, net))
             else:
                 delete_pending(sender_phone)
                 resp.message(f"Just to keep things clean, {name} — draft cleared. Send a new voice note whenever you're ready.")

        elif intent == "delete_transaction":
            category = analysis.get("entities", {}).get("category")
            if category:
                success = delete_transaction_by_details(sender_phone, category)
                if success:
                    resp.message(f"Got it, {name} ✅ Deleted the last {category} record.")
                else:
                    resp.message(f"Hmm, I couldn't find a record for {category} to delete, {name}.")
            else:
                resp.message(f"Please tell me which item to delete, {name} (e.g., 'Delete the fuel record').")

        # --- NUMBER CLARIFICATION (for medium confidence entries) ---
        elif body.strip().isdigit() and pending and pending.get("entries"):
            clarification_amount = int(body.strip())
            # Find the first medium/low confidence entry and update it
            updated = False
            for entry in pending["entries"]:
                if entry.get("extraction_confidence") in ["medium", "low"]:
                    old_amount = int(entry["amount"])
                    # If user typed a small number like 15 or 50, interpret as thousands
                    if clarification_amount < 1000:
                        clarification_amount *= 1000
                    entry["amount"] = clarification_amount
                    entry["extraction_confidence"] = "high"
                    entry["raw_text_used"] = f"Clarified: {clarification_amount:,}"
                    updated = True
                    break
            
            if updated:
                update_pending(sender_phone, pending["entries"])
                net = sum(int(e['amount']) if e['type'] == 'income' else -int(e['amount']) for e in pending["entries"])
                lines = [f"Got it — updating that to ₦{clarification_amount:,} ✅\n\nHere's the updated record:\n"]
                for e in pending["entries"]:
                    label = e['category'].capitalize()
                    amt = f"₦{int(e['amount']):,}"
                    programme = e.get('programme', '')
                    programme_tag = f" — {programme}" if programme else ""
                    corrected = " (corrected)" if e.get("extraction_confidence") == "high" and e.get("raw_text_used", "").startswith("Clarified") else ""
                    lines.append(f"- {label}{programme_tag} — {amt}{corrected}")
                
                income_total = sum(int(e['amount']) for e in pending["entries"] if e['type'] == 'income')
                expense_total = sum(int(e['amount']) for e in pending["entries"] if e['type'] == 'expense')
                if income_total > 0 and expense_total > 0:
                    net_display = f"₦{net:,}" if net >= 0 else f"-₦{abs(net):,}"
                    lines.append(f"\nNet: ₦{income_total:,} income, ₦{expense_total:,} expenses ({net_display})")
                elif income_total > 0:
                    lines.append(f"\nTotal income: ₦{income_total:,}")
                elif expense_total > 0:
                    lines.append(f"\nTotal expenses: ₦{expense_total:,}")
                
                lines.append("\nReply *YES* to save 🙏")
                resp.message("\n".join(lines))
            else:
                resp.message(f"Thanks, {name}. Reply *YES* to save the record or *NO* to cancel.")

        # --- YES/NO FLOW ---
        elif body.strip().lower() == "yes":
            if pending:
                saved = save_transactions(sender_phone, pending["entries"])
                delete_pending(sender_phone)
                
                # Update session with confirmation
                session["context"].append({"role": "assistant", "content": "confirmed", "timestamp": datetime.now().isoformat()})
                last_txn_id = saved[0]["id"] if saved else None
                upsert_session(sender_phone, intent="confirmed", transaction_id=last_txn_id, 
                             context_entry=session["context"][-1],
                             metadata={"name": name})
                
                # Trigger financial insights in background
                run_background_insights(sender_phone, sender_phone, pending["entries"])
                resp.message(f"You're all caught up, {name} 🙌")
            else:
                resp.message(f"Nothing pending to save, {name}. Send a voice note to record transactions.")
                
        elif body.strip().lower() == "no":
            delete_pending(sender_phone)
            session["context"].append({"role": "assistant", "content": "cancelled", "timestamp": datetime.now().isoformat()})
            upsert_session(sender_phone, intent="cancelled", context_entry=session["context"][-1])
            resp.message(f"No worries, {name} ❌ Cancelled. Send a new voice note whenever you're ready.")
            
        elif body.strip().upper() == "HELP":
            resp.message(craft_help(name))
            
        # --- QUERIES & SEARCH ---
        elif intent == "query_balance":
            days = 30
            if entities.get("date_range") == "week": days = 7
            elif entities.get("date_range") == "today": days = 1
            
            summary = get_balance_summary(sender_phone, days=days)
            msg = craft_response("question_balance", {
                "net": summary['net_balance'],
                "income": summary['total_income'],
                "expenses": summary['total_expenses'],
                "period": f"last {days} days"
            }, {"sender_phone": sender_phone, "full_name": name})
            resp.message(msg)
            upsert_session(sender_phone, intent="query_balance", metadata={"name": name})
            
        elif intent == "get_transactions" or intent == "get_records_by_person":
            person_name = entities.get("person_name")
            category = entities.get("category")
            days = 7 if entities.get("date_range") != "month" else 30
            
            if person_name:
                txns = search_transactions_by_person(sender_phone, person_name, days=days)
            elif category:
                txns = search_transactions(sender_phone, category, days=days)
            else:
                txns = get_transactions(sender_phone, days=days)
            
            if not txns:
                resp.message(f"No records found for {person_name or category} in the last {days} days, {name}.")
            else:
                msg_lines = [f"Here's what I found, {name}:\n"]
                for i, t in enumerate(txns[:10], 1):
                    msg_lines.append(f"{i}. {t['category'].capitalize()}: {format_naira(t['amount'])} ({t['type']}) - {t['created_at'][:10]}")
                    if t.get("note"):
                        msg_lines.append(f"   ↳ {t['note']}")
                resp.message("\n".join(msg_lines))
            upsert_session(sender_phone, intent=intent, metadata={"name": name})
                
        elif intent == "generate_report":
            report = generate_weekly_report(sender_phone)
            resp.message(report)
            upsert_session(sender_phone, intent="generate_report", metadata={"name": name})

        # --- DELETE REPORTS ---
        elif intent == "delete_reports":
            time_to_keep = entities.get("time_to_keep", "all")
            from database import delete_old_transactions

            if time_to_keep == "today":
                deleted = delete_old_transactions(sender_phone, keep_days=1)
                msg = f"Done ✅ All records older than today have been removed. Kept today's data."
            elif time_to_keep == "week":
                deleted = delete_old_transactions(sender_phone, keep_days=7)
                msg = f"Done ✅ All records older than this week have been removed."
            elif time_to_keep == "month":
                deleted = delete_old_transactions(sender_phone, keep_days=30)
                msg = f"Done ✅ All records older than this month have been removed."
            else:
                # Default: delete all
                deleted = delete_old_transactions(sender_phone, keep_days=0)
                msg = f"Done ✅ All records have been cleared. Send a new voice note to start recording."

            resp.message(msg)
            upsert_session(sender_phone, intent="delete_reports", metadata={"name": name})
            
        # --- NATURAL LANGUAGE INTENT HANDLING ---
        elif intent == "general_chat":
            natural = extract_intent_natural(transcript)
            nat_intent = natural.get("intent", "other")
            
            if nat_intent == "other":
                # Log as unsupported request
                try:
                    from intelligence import detect_unsupported_request
                    unsupported = detect_unsupported_request(transcript)
                    log_unsupported_request(
                        sender_phone=sender_phone,
                        sender_name=name,
                        raw_message=transcript,
                        detected_intent=unsupported.get("detected_intent", ""),
                        category=unsupported.get("category", "other"),
                        priority_signal=unsupported.get("priority_signal", "low")
                    )
                    resp.message(craft_unsupported_response(name, unsupported["detected_intent"], unsupported["category"], metadata.get("role", "treasurer")))
                except Exception:
                    resp.message(craft_response("other", natural, {"sender_phone": sender_phone, "full_name": name}))
            elif nat_intent == "confusion":
                resp.message(craft_response("confusion", natural, {"sender_phone": sender_phone, "full_name": name}))
            else:
                resp.message(craft_response(nat_intent, natural, {"sender_phone": sender_phone, "full_name": name}))
            
            upsert_session(sender_phone, intent=f"chat_{nat_intent}", metadata={"name": name})
            
        else:
            ai_response = analysis.get("response_text", "")
            if ai_response:
                resp.message(ai_response)
            else:
                resp.message(f"Thanks for reaching out, {name} 😊\nI'm right here if you need anything.\n\n_Abby • ChurchBooks AI by Intravent_")
            upsert_session(sender_phone, intent=intent, metadata={"name": name})
                
    except Exception as e:
        logging.error(f"Webhook error: {e}", exc_info=True)
        resp.message(f"So sorry, {name} — something went wrong on our end. Please try again in a moment.")
        
    return str(resp)

@app.route("/dev/reset", methods=["POST"])
def dev_reset():
    """Development-only endpoint to wipe all data and sessions."""
    if os.environ.get("FLASK_ENV") != "development":
        logging.warning("Attempted access to /dev/reset in non-development environment.")
        return "Forbidden: Development reset disabled in production.", 403

    try:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_KEY")
        from supabase import create_client
        sb = create_client(sb_url, sb_key)
        
        # Delete all rows using a universal match condition
        sb.table("pending_confirmations").delete().gt("created_at", "1900-01-01").execute()
        sb.table("transactions").delete().gt("created_at", "1900-01-01").execute()
        
        # Attempt to clear optional tables if they exist
        try:
            sb.table("audit_log").delete().gt("created_at", "1900-01-01").execute()
        except:
            pass
            
        # Clear persistent sessions
        delete_all_sessions()
        
        logging.info("Development reset completed successfully.")
        return "Development reset complete. All data wiped.", 200
        
    except Exception as e:
        logging.error(f"Development reset failed: {e}")
        return f"Reset failed: {str(e)}", 500

if __name__ == "__main__":
    scheduler.start()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)