import os
import json
import logging
from datetime import datetime, timedelta
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

def save_pending(sender_phone, entries, raw_transcript):
    try:
        supabase.table("pending_confirmations").delete().eq("sender_phone", sender_phone).execute()
        supabase.table("pending_confirmations").insert({
            "sender_phone": sender_phone,
            "entries": entries,
            "raw_transcript": raw_transcript
        }).execute()
    except Exception as e:
        logging.error(f"DB Error save_pending: {e}")
        raise

def update_pending(sender_phone, new_entries):
    try:
        supabase.table("pending_confirmations").update({"entries": new_entries}).eq("sender_phone", sender_phone).execute()
    except Exception as e:
        logging.error(f"DB Error update_pending: {e}")
        raise

def get_pending(sender_phone):
    try:
        response = supabase.table("pending_confirmations").select("*").eq("sender_phone", sender_phone).order("created_at", desc=True).limit(1).execute()
        if response.data:
            row = response.data[0]
            if isinstance(row.get("entries"), str):
                row["entries"] = json.loads(row["entries"])
            return row
        return None
    except Exception as e:
        logging.error(f"DB Error get_pending: {e}")
        raise

def delete_pending(sender_phone):
    try:
        supabase.table("pending_confirmations").delete().eq("sender_phone", sender_phone).execute()
    except Exception as e:
        logging.error(f"DB Error delete_pending: {e}")
        raise

def save_transactions(sender_phone, entries):
    try:
        payload = []
        for entry in entries:
            # Smart amount parsing
            amount_raw = entry.get("amount", 0)
            try:
                if isinstance(amount_raw, str):
                    amount = int(amount_raw.replace(",", ""))
                else:
                    amount = int(float(amount_raw))
            except ValueError:
                amount = 0
            
            # Append context to note for searching people later
            context = entry.get("context", "")
            final_note = entry.get("note", "")
            if context:
                final_note = f"{final_note} [Context: {context}]"
                
            payload.append({
                "church_id": sender_phone,
                "sender_phone": sender_phone,
                "type": entry.get("type", "other"),
                "category": entry.get("category", "Uncategorized"),
                "amount": amount,
                "note": final_note,
                "raw_transcript": entry.get("note", ""),
                "confirmed": True
            })
        if payload:
            response = supabase.table("transactions").insert(payload).execute()
            return response.data
        return []
    except Exception as e:
        logging.error(f"DB Error save_transactions: {e}")
        raise

def get_transactions(sender_phone, days=7, category=None):
    try:
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        query = supabase.table("transactions").select("*").eq("sender_phone", sender_phone).eq("confirmed", True).gte("created_at", start_date).order("created_at", desc=True)
        if category:
            query = query.ilike("category", f"%{category}%")
        response = query.execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error get_transactions: {e}")
        raise

def delete_transaction_by_details(sender_phone, category, date_hint=None):
    try:
        # Find the most recent transaction matching the category for this sender
        query = supabase.table("transactions").select("id").eq("sender_phone", sender_phone).eq("confirmed", True).ilike("category", f"%{category}%").order("created_at", desc=True).limit(1)
        response = query.execute()
        
        if response.data:
            txn_id = response.data[0]["id"]
            supabase.table("transactions").delete().eq("id", txn_id).execute()
            return True
        return False
    except Exception as e:
        logging.error(f"DB Error delete_transaction_by_details: {e}")
        raise

def get_transaction_by_id(sender_phone, transaction_id):
    try:
        response = supabase.table("transactions").select("*").eq("id", transaction_id).eq("sender_phone", sender_phone).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"DB Error get_transaction_by_id: {e}")
        raise

def update_transaction(transaction_id, updates):
    try:
        response = supabase.table("transactions").update(updates).eq("id", transaction_id).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error update_transaction: {e}")
        raise

def delete_transaction(transaction_id):
    try:
        supabase.table("transactions").delete().eq("id", transaction_id).execute()
    except Exception as e:
        logging.error(f"DB Error delete_transaction: {e}")
        raise

def search_transactions_by_person(sender_phone, person_name, days=30):
    try:
        # Search in the note/context field for the person's name
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("*").eq("sender_phone", sender_phone).eq("confirmed", True).gte("created_at", start_date).ilike("note", f"%{person_name}%").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error search_transactions_by_person: {e}")
        raise

def search_transactions(sender_phone, query_text, days=30):
    try:
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("*").eq("sender_phone", sender_phone).eq("confirmed", True).gte("created_at", start_date).or_(f"category.ilike.%{query_text}%,note.ilike.%{query_text}%").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error search_transactions: {e}")
        raise

def get_balance_summary(sender_phone, days=30):
    try:
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        response = supabase.table("transactions").select("type, amount, category").eq("sender_phone", sender_phone).eq("confirmed", True).gte("created_at", start_date).execute()
        
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

def get_monthly_transactions(sender_phone):
    try:
        now = datetime.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        response = supabase.table("transactions").select("*").eq("sender_phone", sender_phone).eq("confirmed", True).gte("created_at", start_of_month).execute()
        return response.data
    except Exception as e:
        logging.error(f"DB Error get_monthly_transactions: {e}")
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

# --- Session Management (Persistent DB-backed memory) ---

def upsert_session(sender_phone, intent=None, transaction_id=None, context_entry=None, metadata=None):
    """Create or update a session with new context. Appends to context array (max 10 entries)."""
    try:
        # Fetch existing session
        existing = supabase.table("sessions").select("*").eq("sender_phone", sender_phone).limit(1).execute()
        
        now = datetime.now().isoformat()
        context = []
        current_metadata = {}
        
        if existing.data:
            row = existing.data[0]
            context = row.get("context", []) or []
            current_metadata = row.get("metadata", {}) or {}
        
        # Append new context entry if provided
        if context_entry:
            context.append(context_entry)
            # Keep only last 10 exchanges to prevent bloat
            context = context[-10:]
        
        # Merge metadata
        if metadata:
            current_metadata.update(metadata)
        
        payload = {
            "sender_phone": sender_phone,
            "last_active": now,
            "context": context,
            "metadata": current_metadata
        }
        
        if intent:
            payload["last_intent"] = intent
        if transaction_id:
            payload["last_transaction_id"] = str(transaction_id)
        
        if existing.data:
            supabase.table("sessions").update(payload).eq("sender_phone", sender_phone).execute()
        else:
            supabase.table("sessions").insert(payload).execute()
            
    except Exception as e:
        logging.error(f"DB Error upsert_session: {e}")

def get_session(sender_phone):
    """Retrieve session data. Returns None if not found or expired (>2 hours)."""
    try:
        response = supabase.table("sessions").select("*").eq("sender_phone", sender_phone).limit(1).execute()
        
        if not response.data:
            return None
            
        row = response.data[0]
        last_active = datetime.fromisoformat(row["last_active"])
        
        # Handle timezone-aware timestamps from Supabase
        if last_active.tzinfo is not None:
            last_active = last_active.replace(tzinfo=None)
        
        # Expire after 2 hours
        if (datetime.now() - last_active).total_seconds() > 7200:
            delete_session(sender_phone)
            return None
            
        # Parse context if stored as string
        context = row.get("context", [])
        if isinstance(context, str):
            try:
                context = json.loads(context)
            except:
                context = []
        
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except:
                metadata = {}
            
        return {
            "sender_phone": sender_phone,
            "last_intent": row.get("last_intent"),
            "last_transaction_id": row.get("last_transaction_id"),
            "context": context,
            "metadata": metadata,
            "last_active": row["last_active"]
        }
    except Exception as e:
        logging.error(f"DB Error get_session: {e}")
        return None

def delete_session(sender_phone):
    """Delete a session (used on expiry or explicit reset)."""
    try:
        supabase.table("sessions").delete().eq("sender_phone", sender_phone).execute()
    except Exception as e:
        logging.error(f"DB Error delete_session: {e}")

def delete_all_sessions():
    """Wipe all sessions (used in dev reset)."""
    try:
        supabase.table("sessions").delete().gt("created_at", "1900-01-01").execute()
    except Exception as e:
        logging.error(f"DB Error delete_all_sessions: {e}")

def delete_old_transactions(sender_phone, keep_days=0):
    """Delete transactions older than keep_days. If keep_days=0, delete all."""
    try:
        if keep_days == 0:
            # Delete all transactions for this phone
            response = supabase.table("transactions").delete().eq("sender_phone", sender_phone).execute()
            return len(response.data) if response.data else 0
        else:
            # Delete transactions older than keep_days
            cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat()
            response = supabase.table("transactions").delete().eq("sender_phone", sender_phone).lt("created_at", cutoff).execute()
            return len(response.data) if response.data else 0
    except Exception as e:
        logging.error(f"DB Error delete_old_transactions: {e}")
        return 0