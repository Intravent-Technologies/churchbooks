import os
import re
import json
import logging
import hashlib
import secrets
from datetime import datetime, timedelta
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

# ============================================================
# CONVERSATION STATES
# ============================================================

class ConversationState:
    UNKNOWN = "UNKNOWN"
    ONBOARDING_1 = "ONBOARDING_1"
    ONBOARDING_2 = "ONBOARDING_2"
    ONBOARDING_3 = "ONBOARDING_3"
    ONBOARDING_4 = "ONBOARDING_4"
    ONBOARDING_4_VERIFY = "ONBOARDING_4_VERIFY"
    ACTIVE = "ACTIVE"
    AWAITING_YES_NO = "AWAITING_YES_NO"
    AWAITING_CLARIFY = "AWAITING_CLARIFY"
    AWAITING_CORRECT = "AWAITING_CORRECT"

# ============================================================
# PHONE UTILITIES
# ============================================================

def clean_phone(raw_phone):
    """Strip 'whatsapp:' prefix and return clean E.164 format."""
    phone = raw_phone.strip()
    if phone.startswith("whatsapp:"):
        phone = phone.replace("whatsapp:", "")
    return phone

# ============================================================
# VALIDATION
# ============================================================

def validate_name(name):
    """Validate name: 2+ words, letters/spaces/hyphens only, max 50 chars."""
    if not name or len(name.strip()) < 2:
        return False, "Name must be at least 2 characters."
    name = name.strip()
    if len(name) > 50:
        return False, "Name is too long. Maximum 50 characters."
    # Must have at least 2 words
    words = name.split()
    if len(words) < 2:
        return False, "Please provide your full name — for example: Grace Adeyemi"
    # Letters, spaces, hyphens only
    if not re.match(r'^[a-zA-Z\s\-]+$', name):
        return False, "Name should contain only letters, spaces, and hyphens."
    return True, ""

def validate_role(raw):
    """Validate role input. Returns canonical role string or None."""
    raw = raw.strip().lower()
    role_map = {
        "1": "pastor",
        "2": "treasurer",
        "3": "collector",
        "pastor": "pastor",
        "senior pastor": "pastor",
        "senior leader": "pastor",
        "treasurer": "treasurer",
        "collector": "collector",
    }
    return role_map.get(raw)

def validate_church_name(name):
    """Validate church name: minimum 3 characters."""
    if not name or len(name.strip()) < 3:
        return False, "Church name must be at least 3 characters."
    return True, ""

# ============================================================
# USER FUNCTIONS
# ============================================================

def get_user_by_phone(phone):
    """Get user object by phone. Returns None if not found."""
    try:
        clean = clean_phone(phone)
        response = supabase.table("users").select("*").eq("phone", clean).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error get_user_by_phone: {e}")
        return None

def create_user(phone):
    """Create a new user with onboarding_step=0."""
    try:
        clean = clean_phone(phone)
        now = datetime.utcnow().isoformat()
        payload = {
            "phone": clean,
            "first_name": None,
            "last_name": None,
            "full_name": None,
            "role": None,
            "church_id": None,
            "is_active": True,
            "is_verified": False,
            "onboarding_step": 0,
            "registered_at": now,
            "last_seen": now
        }
        response = supabase.table("users").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error create_user: {e}")
        return None

def update_user_name(phone, first_name, last_name):
    """Save extracted name to user record."""
    try:
        clean = clean_phone(phone)
        full_name = f"{first_name} {last_name}".strip()
        supabase.table("users").update({
            "first_name": first_name,
            "last_name": last_name,
            "full_name": full_name
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_user_name: {e}")

def update_user_role(phone, role):
    """Save role to user record."""
    try:
        clean = clean_phone(phone)
        supabase.table("users").update({"role": role}).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_user_role: {e}")

def update_user_church(phone, church_id):
    """Link user to a church."""
    try:
        clean = clean_phone(phone)
        supabase.table("users").update({"church_id": church_id}).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_user_church: {e}")

def complete_onboarding(phone):
    """Mark onboarding as complete (step 5) and verified."""
    try:
        clean = clean_phone(phone)
        supabase.table("users").update({
            "onboarding_step": 5,
            "is_verified": True
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error complete_onboarding: {e}")

def update_last_seen(phone):
    """Update last_seen timestamp."""
    try:
        clean = clean_phone(phone)
        supabase.table("users").update({
            "last_seen": datetime.utcnow().isoformat()
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_last_seen: {e}")

def verify_user(phone):
    """Set user as verified."""
    try:
        clean = clean_phone(phone)
        supabase.table("users").update({
            "is_verified": True
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error verify_user: {e}")

def get_user_display_name(phone):
    """Return first_name for display. Falls back to 'Friend'."""
    try:
        user = get_user_by_phone(phone)
        if user and user.get("first_name"):
            return user["first_name"].capitalize()
        return "Friend"
    except Exception:
        return "Friend"

def get_user_by_phone_with_church(phone):
    """Get user with church details joined."""
    try:
        user = get_user_by_phone(phone)
        if user and user.get("church_id"):
            church = get_church(user["church_id"])
            user["church"] = church
        return user
    except Exception as e:
        logging.error(f"DB Error get_user_by_phone_with_church: {e}")
        return None

# ============================================================
# CHURCH FUNCTIONS
# ============================================================

def find_church_by_name(name):
    """Fuzzy match church by name. Returns church or None."""
    try:
        # Direct match first
        response = supabase.table("churches").select("*").ilike("name", f"%{name}%").execute()
        if response.data:
            # Return closest match (first result)
            return response.data[0]
        return None
    except Exception as e:
        logging.error(f"DB Error find_church_by_name: {e}")
        return None

def create_church(name, city="", state="", denomination="", pastor_phone=""):
    """Create a new church record."""
    try:
        now = datetime.utcnow().isoformat()
        payload = {
            "name": name.strip(),
            "city": city,
            "state": state,
            "denomination": denomination,
            "pastor_phone": clean_phone(pastor_phone) if pastor_phone else None,
            "is_active": True,
            "created_at": now
        }
        response = supabase.table("churches").insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error create_church: {e}")
        return None

def get_church(church_id):
    """Get church object by ID."""
    try:
        response = supabase.table("churches").select("*").eq("id", church_id).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error get_church: {e}")
        return None

def get_church_members(church_id):
    """Get all users in a church."""
    try:
        response = supabase.table("users").select("*").eq("church_id", church_id).eq("is_active", True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error get_church_members: {e}")
        return []

def get_church_pastor(church_id):
    """Get the pastor of a church."""
    try:
        response = supabase.table("users").select("*").eq("church_id", church_id).eq("role", "pastor").limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error get_church_pastor: {e}")
        return None

# ============================================================
# SESSION FUNCTIONS
# ============================================================

SESSION_ACTIVE_HOURS = 2
SESSION_SOFT_RESET_HOURS = 24

def get_or_create_session(phone):
    """Get existing session or create a new one."""
    try:
        clean = clean_phone(phone)
        now = datetime.utcnow().isoformat()
        response = supabase.table("sessions").select("*").eq("phone", clean).limit(1).execute()
        
        if response.data:
            session = response.data[0]
            # Parse expires_at
            expires_str = session.get("expires_at")
            if expires_str:
                expires_at = datetime.fromisoformat(expires_str.replace("Z", "+00:00")).replace(tzinfo=None)
                if datetime.utcnow() > expires_at:
                    # Session expired — soft reset
                    supabase.table("sessions").update({
                        "state": ConversationState.ACTIVE,
                        "pending_entries": None,
                        "pending_transaction_id": None,
                        "context": [],
                        "last_intent": None,
                        "last_message_at": now,
                        "expires_at": (datetime.utcnow() + timedelta(hours=SESSION_ACTIVE_HOURS)).isoformat()
                    }).eq("phone", clean).execute()
                    # Fetch updated
                    response = supabase.table("sessions").select("*").eq("phone", clean).limit(1).execute()
                    return response.data[0] if response.data else _create_session(clean, now)
            return session
        else:
            return _create_session(clean, now)
    except Exception as e:
        logging.error(f"DB Error get_or_create_session: {e}")
        clean = clean_phone(phone)
        now = datetime.utcnow().isoformat()
        return _create_session(clean, now)

def _create_session(phone, now):
    """Create a new session record."""
    expires = (datetime.utcnow() + timedelta(hours=SESSION_ACTIVE_HOURS)).isoformat()
    payload = {
        "phone": phone,
        "state": ConversationState.UNKNOWN,
        "pending_transaction_id": None,
        "pending_entries": None,
        "last_message_at": now,
        "last_intent": None,
        "context": [],
        "expires_at": expires
    }
    response = supabase.table("sessions").insert(payload).execute()
    return response.data[0] if response.data else payload

def update_session_state(phone, state):
    """Update session conversation state."""
    try:
        clean = clean_phone(phone)
        now = datetime.utcnow().isoformat()
        expires = (datetime.utcnow() + timedelta(hours=SESSION_ACTIVE_HOURS)).isoformat()
        supabase.table("sessions").update({
            "state": state,
            "last_message_at": now,
            "expires_at": expires
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_session_state: {e}")

def update_session_context(phone, user_msg, abby_response):
    """Append exchange to context window (max 3)."""
    try:
        clean = clean_phone(phone)
        session = get_or_create_session(phone)
        context = session.get("context", []) or []
        
        now = datetime.utcnow().isoformat()
        context.append({"role": "user", "content": user_msg, "timestamp": now})
        context.append({"role": "abby", "content": abby_response, "timestamp": now})
        
        # Keep only last 3 exchanges (6 entries)
        context = context[-6:]
        
        supabase.table("sessions").update({
            "context": context
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_session_context: {e}")

def set_pending_transaction(phone, entries):
    """Store pending transaction entries in session."""
    try:
        clean = clean_phone(phone)
        supabase.table("sessions").update({
            "pending_entries": entries,
            "state": ConversationState.AWAITING_YES_NO
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error set_pending_transaction: {e}")

def get_pending_transaction(phone):
    """Retrieve pending transaction entries from session."""
    try:
        clean = clean_phone(phone)
        response = supabase.table("sessions").select("pending_entries").eq("phone", clean).limit(1).execute()
        if response.data:
            entries = response.data[0].get("pending_entries")
            if isinstance(entries, str):
                return json.loads(entries)
            return entries
        return None
    except Exception as e:
        logging.error(f"DB Error get_pending_transaction: {e}")
        return None

def clear_pending_transaction(phone):
    """Remove pending transaction from session."""
    try:
        clean = clean_phone(phone)
        supabase.table("sessions").update({
            "pending_entries": None,
            "pending_transaction_id": None
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error clear_pending_transaction: {e}")

def is_session_active(phone):
    """Check if session is within active window (< 2 hours)."""
    try:
        clean = clean_phone(phone)
        response = supabase.table("sessions").select("last_message_at").eq("phone", clean).limit(1).execute()
        if response.data:
            last_msg = response.data[0].get("last_message_at")
            if last_msg:
                last_at = datetime.fromisoformat(last_msg.replace("Z", "+00:00")).replace(tzinfo=None)
                return (datetime.utcnow() - last_at).total_seconds() < (SESSION_ACTIVE_HOURS * 3600)
        return False
    except Exception as e:
        logging.error(f"DB Error is_session_active: {e}")
        return False

# ============================================================
# ONBOARDING FUNCTIONS
# ============================================================

def get_onboarding_step(phone):
    """Get current onboarding step from users table."""
    try:
        user = get_user_by_phone(phone)
        if user:
            return user.get("onboarding_step", 0)
        return 0
    except Exception as e:
        logging.error(f"DB Error get_onboarding_step: {e}")
        return 0

def advance_onboarding(phone, step, data=None):
    """Advance onboarding step and save collected data."""
    try:
        clean = clean_phone(phone)
        # Update user onboarding_step
        supabase.table("users").update({
            "onboarding_step": step
        }).eq("phone", clean).execute()
        
        # Update or create onboarding_progress
        response = supabase.table("onboarding_progress").select("*").eq("phone", clean).limit(1).execute()
        
        now = datetime.utcnow().isoformat()
        if response.data:
            # Update existing
            existing_data = response.data[0].get("collected_data", {}) or {}
            if data:
                existing_data.update(data)
            supabase.table("onboarding_progress").update({
                "step": step,
                "collected_data": existing_data
            }).eq("phone", clean).execute()
        else:
            # Create new
            supabase.table("onboarding_progress").insert({
                "phone": clean,
                "step": step,
                "collected_data": data or {},
                "started_at": now
            }).execute()
    except Exception as e:
        logging.error(f"DB Error advance_onboarding: {e}")

def get_onboarding_data(phone):
    """Get partial collected onboarding data."""
    try:
        clean = clean_phone(phone)
        response = supabase.table("onboarding_progress").select("*").eq("phone", clean).limit(1).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        logging.error(f"DB Error get_onboarding_data: {e}")
        return None

def complete_onboarding_progress(phone):
    """Mark onboarding_progress as completed."""
    try:
        clean = clean_phone(phone)
        now = datetime.utcnow().isoformat()
        supabase.table("onboarding_progress").update({
            "completed_at": now
        }).eq("phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error complete_onboarding_progress: {e}")

# ============================================================
# TRANSACTION FUNCTIONS (Existing — keep as-is)
# ============================================================

def save_pending(sender_phone, entries, raw_transcript):
    """Legacy: save to pending_confirmations table."""
    try:
        clean = clean_phone(sender_phone)
        supabase.table("pending_confirmations").delete().eq("sender_phone", clean).execute()
        supabase.table("pending_confirmations").insert({
            "sender_phone": clean,
            "entries": entries,
            "raw_transcript": raw_transcript
        }).execute()
    except Exception as e:
        logging.error(f"DB Error save_pending: {e}")

def update_pending(sender_phone, new_entries):
    try:
        clean = clean_phone(sender_phone)
        supabase.table("pending_confirmations").update({"entries": new_entries}).eq("sender_phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error update_pending: {e}")

def get_pending(sender_phone):
    try:
        clean = clean_phone(sender_phone)
        response = supabase.table("pending_confirmations").select("*").eq("sender_phone", clean).order("created_at", desc=True).limit(1).execute()
        if response.data:
            row = response.data[0]
            if isinstance(row.get("entries"), str):
                row["entries"] = json.loads(row["entries"])
            return row
        return None
    except Exception as e:
        logging.error(f"DB Error get_pending: {e}")
        return None

def delete_pending(sender_phone):
    try:
        clean = clean_phone(sender_phone)
        supabase.table("pending_confirmations").delete().eq("sender_phone", clean).execute()
    except Exception as e:
        logging.error(f"DB Error delete_pending: {e}")

def save_transactions(sender_phone, entries):
    try:
        clean = clean_phone(sender_phone)
        payload = []
        for entry in entries:
            amount_raw = entry.get("amount", 0)
            try:
                if isinstance(amount_raw, str):
                    amount = int(amount_raw.replace(",", ""))
                else:
                    amount = int(float(amount_raw))
            except ValueError:
                amount = 0
            
            context_parts = []
            customer = entry.get("customer_name") or entry.get("context", "")
            programme = entry.get("programme", "")
            
            if customer:
                context_parts.append(f"Context: {customer}")
            if programme:
                context_parts.append(f"Programme: {programme}")
            
            final_note = entry.get("note", "")
            if context_parts:
                final_note = f"{final_note} [{', '.join(context_parts)}]".strip()
            
            payload.append({
                "church_id": None,
                "sender_phone": clean,
                "type": entry.get("type", "other"),
                "category": entry.get("category", "Uncategorized"),
                "amount": amount,
                "note": final_note,
                "raw_transcript": entry.get("raw_text_used", entry.get("note", "")),
                "confirmed": True,
                "customer_name": customer or None,
                "programme": programme or None
            })
        if payload:
            response = supabase.table("transactions").insert(payload).execute()
            return response.data
        return []
    except Exception as e:
        logging.error(f"DB Error save_transactions: {e}")

def get_transactions(sender_phone, days=7, category=None):
    try:
        clean = clean_phone(sender_phone)
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        query = supabase.table("transactions").select("*").eq("sender_phone", clean).eq("confirmed", True).gte("created_at", start_date).order("created_at", desc=True)
        if category:
            query = query.ilike("category", f"%{category}%")
        response = query.execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error get_transactions: {e}")
        raise

def search_transactions_by_person(sender_phone, person_name, days=30):
    try:
        clean = clean_phone(sender_phone)
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("*").eq("sender_phone", clean).eq("confirmed", True).gte("created_at", start_date).ilike("note", f"%{person_name}%").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error search_transactions_by_person: {e}")
        raise

def search_transactions(sender_phone, query_text, days=30):
    try:
        clean = clean_phone(sender_phone)
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("*").eq("sender_phone", clean).eq("confirmed", True).gte("created_at", start_date).or_(f"category.ilike.%{query_text}%,note.ilike.%{query_text}%").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error search_transactions: {e}")
        raise

def get_balance_summary(sender_phone, days=30):
    try:
        clean = clean_phone(sender_phone)
        start_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("type, amount, category").eq("sender_phone", clean).eq("confirmed", True).gte("created_at", start_date).execute()
        
        income = {}
        expenses = {}
        total_income = 0
        total_expenses = 0
        
        for t in response.data:
            cat = t["category"].capitalize()
            amount = int(t["amount"])
            if t["type"] == "income":
                income[cat] = income.get(cat, 0) + amount
                total_income += amount
            else:
                expenses[cat] = expenses.get(cat, 0) + amount
                total_expenses += amount
                
        return {
            "total_income": total_income,
            "total_expenses": total_expenses,
            "net_balance": total_income - total_expenses,
            "income_breakdown": income,
            "expense_breakdown": expenses
        }
    except Exception as e:
        logging.error(f"DB Error get_balance_summary: {e}")
        raise

def get_all_active_phones():
    try:
        response = supabase.table("transactions").select("sender_phone").eq("confirmed", True).execute()
        phones = set()
        for row in response.data:
            phones.add(row["sender_phone"])
        return list(phones)
    except Exception as e:
        logging.error(f"DB Error get_all_active_phones: {e}")
        return []

def delete_transaction_by_details(sender_phone, category):
    try:
        clean = clean_phone(sender_phone)
        query = supabase.table("transactions").select("id").eq("sender_phone", clean).eq("confirmed", True).ilike("category", f"%{category}%").order("created_at", desc=True).limit(1)
        response = query.execute()
        if response.data:
            txn_id = response.data[0]["id"]
            supabase.table("transactions").delete().eq("id", txn_id).execute()
            return True
        return False
    except Exception as e:
        logging.error(f"DB Error delete_transaction_by_details: {e}")
        raise

def delete_old_transactions(sender_phone, keep_days=0):
    try:
        clean = clean_phone(sender_phone)
        if keep_days == 0:
            response = supabase.table("transactions").delete().eq("sender_phone", clean).execute()
            return len(response.data) if response.data else 0
        else:
            cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat()
            response = supabase.table("transactions").delete().eq("sender_phone", clean).lt("created_at", cutoff).execute()
            return len(response.data) if response.data else 0
    except Exception as e:
        logging.error(f"DB Error delete_old_transactions: {e}")
        return 0

# ============================================================
# FEATURE REQUEST INTELLIGENCE
# ============================================================

def log_unsupported_request(sender_phone, sender_name, raw_message, detected_intent, category, priority_signal):
    try:
        import rapidfuzz
        from rapidfuzz import fuzz
        
        existing = supabase.table("unsupported_requests").select("*").execute()
        best_match = None
        best_score = 0
        
        if existing.data:
            for row in existing.data:
                score = fuzz.partial_ratio(detected_intent.lower(), row.get("detected_intent", "").lower())
                if score > best_score:
                    best_score = score
                    best_match = row
        
        now = datetime.utcnow().isoformat()
        
        if best_match and best_score > 85:
            supabase.table("unsupported_requests").update({
                "frequency": best_match.get("frequency", 1) + 1,
                "last_asked": now
            }).eq("id", best_match["id"]).execute()
            return best_match["id"]
        else:
            payload = {
                "sender_phone": clean_phone(sender_phone),
                "sender_name": sender_name,
                "raw_message": raw_message,
                "detected_intent": detected_intent,
                "category": category,
                "priority_signal": priority_signal,
                "frequency": 1,
                "first_asked": now,
                "last_asked": now,
                "status": "noted"
            }
            response = supabase.table("unsupported_requests").insert(payload).execute()
            return response.data[0]["id"] if response.data else None
    except Exception as e:
        logging.error(f"DB Error log_unsupported_request: {e}")
        return None

def get_weekly_feature_requests():
    try:
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        response = supabase.table("unsupported_requests").select("*").gte("last_asked", seven_days_ago).order("frequency", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error get_weekly_feature_requests: {e}")
        return []

def get_system_stats():
    try:
        response = supabase.table("system_stats").select("*").limit(1).execute()
        if response.data:
            return response.data[0]
        return {
            "total_transactions_processed": 0,
            "total_voice_notes_transcribed": 0,
            "total_churches_active": 0,
            "total_features_built_from_requests": 0
        }
    except Exception as e:
        logging.error(f"DB Error get_system_stats: {e}")
        return {
            "total_transactions_processed": 0,
            "total_voice_notes_transcribed": 0,
            "total_churches_active": 0,
            "total_features_built_from_requests": 0
        }

def increment_stat(stat_name, count=1):
    try:
        existing = supabase.table("system_stats").select("*").limit(1).execute()
        now = datetime.utcnow().isoformat()
        
        if existing.data:
            current = existing.data[0].get(stat_name, 0) or 0
            supabase.table("system_stats").update({
                stat_name: current + count,
                "last_updated": now
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            payload = {stat_name: count, "last_updated": now}
            supabase.table("system_stats").insert(payload).execute()
    except Exception as e:
        logging.error(f"DB Error increment_stat: {e}")

def notify_feature_requestors(detected_intent, announcement_text):
    try:
        from reports import send_twilio_message
        
        response = supabase.table("unsupported_requests").select("sender_phone", "sender_name").eq("detected_intent", detected_intent).execute()
        phones_notified = set()
        for row in response.data:
            phone = row.get("sender_phone")
            name = row.get("sender_name", "Friend")
            if phone and phone not in phones_notified:
                phones_notified.add(phone)
                personalized = announcement_text.replace("[Name]", name.split()[0].capitalize())
                send_twilio_message(phone, personalized)
        return len(phones_notified)
    except Exception as e:
        logging.error(f"DB Error notify_feature_requestors: {e}")
        return 0

# ============================================================
# WAITLIST FUNCTIONS
# ============================================================

def add_to_waitlist(name, phone, role, current_tracking=None, will_pay=None, price_range=None, features=None, other_feature=None):
    try:
        clean = clean_phone(phone)
        existing = supabase.table("waitlist").select("id").eq("phone", clean).limit(1).execute()
        if existing.data:
            return {"success": False, "error": "This phone number is already on the waitlist."}

        payload = {
            "name": name,
            "phone": clean,
            "role": role,
            "current_tracking": current_tracking,
            "will_pay": will_pay,
            "price_range": price_range,
            "features": features,
            "other_feature": other_feature
        }
        response = supabase.table("waitlist").insert(payload).execute()
        if response.data:
            return {"success": True, "id": response.data[0].get("id")}
        return {"success": False, "error": "Failed to join waitlist."}
    except Exception as e:
        logging.error(f"Waitlist error: {e}")
        return {"success": False, "error": str(e)}

def get_all_waitlist_entries():
    try:
        response = supabase.table("waitlist").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"Get waitlist error: {e}")
        return []

def get_waitlist_count():
    try:
        response = supabase.table("waitlist").select("id", count="exact", head=True).execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"Get waitlist count error: {e}")
        return 0
