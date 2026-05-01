import os
import hashlib
import secrets
import logging
from datetime import datetime
from supabase import create_client

logging.basicConfig(level=logging.INFO)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_KEY")
supabase = create_client(supabase_url, supabase_key)

def hash_pin(pin):
    """Hash a 4-digit PIN using SHA-256 + salt."""
    salt = os.environ.get("PIN_SALT", "churchbooks-default-salt-2026")
    return hashlib.sha256(f"{salt}{pin}".encode()).hexdigest()

def generate_verification_code():
    """Generate a 6-digit verification code."""
    return secrets.randbelow(900000) + 100000

def generate_church_slug(church_name):
    """Create URL-friendly slug from church name."""
    import re
    slug = church_name.lower().strip()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s]+', '-', slug)
    slug = slug.strip('-')
    # Add random suffix to avoid collisions
    suffix = secrets.token_hex(3)
    return f"{slug}-{suffix}"

def register_church(church_name, denomination, address, city, state, phone, email, pastor_name, pastor_phone, treasurer_name=None, treasurer_phone=None):
    """Register a new church with pastor (required) and optional treasurer."""
    try:
        # Create church record
        slug = generate_church_slug(church_name)
        church_data = {
            "church_name": church_name,
            "slug": slug,
            "denomination": denomination,
            "address": address,
            "city": city,
            "state": state,
            "phone": phone,
            "email": email
        }
        
        church_response = supabase.table("churches").insert(church_data).execute()
        if not church_response.data:
            return {"success": False, "error": "Failed to create church record"}
        
        church_id = church_response.data[0]["id"]
        
        # Create pastor account
        pastor_code = generate_verification_code()
        pastor_data = {
            "church_id": church_id,
            "full_name": pastor_name,
            "phone": pastor_phone,
            "role": "pastor",
            "metadata": {"verification_code": pastor_code, "verified": False}
        }
        
        pastor_response = supabase.table("users").insert(pastor_data).execute()
        if not pastor_response.data:
            # Rollback church
            supabase.table("churches").delete().eq("id", church_id).execute()
            return {"success": False, "error": "Failed to create pastor account"}
        
        result = {
            "success": True,
            "church_id": church_id,
            "church_name": church_name,
            "slug": slug,
            "pastor_code": pastor_code,
            "pastor_phone": pastor_phone
        }
        
        # Create treasurer if provided
        if treasurer_name and treasurer_phone:
            treasurer_code = generate_verification_code()
            treasurer_data = {
                "church_id": church_id,
                "full_name": treasurer_name,
                "phone": treasurer_phone,
                "role": "treasurer",
                "metadata": {"verification_code": treasurer_code, "verified": False}
            }
            
            supabase.table("users").insert(treasurer_data).execute()
            result["treasurer_code"] = treasurer_code
            result["treasurer_phone"] = treasurer_phone
        
        return result
        
    except Exception as e:
        logging.error(f"Registration error: {e}")
        return {"success": False, "error": str(e)}

def verify_user(phone, code):
    """Verify a user with their verification code and prompt PIN setup."""
    try:
        response = supabase.table("users").select("*").eq("phone", phone).limit(1).execute()
        
        if not response.data:
            return {"success": False, "error": "Phone number not found"}
        
        user = response.data[0]
        metadata = user.get("metadata", {})
        
        if str(metadata.get("verification_code")) != str(code):
            return {"success": False, "error": "Invalid verification code"}
        
        if metadata.get("verified"):
            return {"success": False, "error": "Account already verified"}
        
        # Mark as verified (pending PIN setup)
        metadata["verified"] = True
        metadata["verification_code"] = None
        
        supabase.table("users").update({"metadata": metadata}).eq("id", user["id"]).execute()
        
        return {
            "success": True,
            "user_id": user["id"],
            "name": user["full_name"],
            "role": user["role"],
            "church_id": user["church_id"],
            "needs_pin": not user.get("pin_hash")
        }
        
    except Exception as e:
        logging.error(f"Verification error: {e}")
        return {"success": False, "error": str(e)}

def set_pin(user_id, pin):
    """Set a 4-digit PIN for a verified user."""
    try:
        if not pin.isdigit() or len(pin) != 4:
            return {"success": False, "error": "PIN must be 4 digits"}
        
        pin_hash = hash_pin(pin)
        supabase.table("users").update({"pin_hash": pin_hash}).eq("id", user_id).execute()
        
        return {"success": True}
        
    except Exception as e:
        logging.error(f"Set PIN error: {e}")
        return {"success": False, "error": str(e)}

def login(phone, pin):
    """Authenticate user with phone + PIN."""
    try:
        response = supabase.table("users").select("*").eq("phone", phone).limit(1).execute()
        
        if not response.data:
            return {"success": False, "error": "Phone number not found"}
        
        user = response.data[0]
        
        if not user.get("pin_hash"):
            return {"success": False, "error": "PIN not set. Please set up your PIN first."}
        
        if user["pin_hash"] != hash_pin(pin):
            return {"success": False, "error": "Incorrect PIN"}
        
        if not user.get("is_active", True):
            return {"success": False, "error": "Account is deactivated"}
        
        # Update last login
        supabase.table("users").update({"last_login": datetime.now().isoformat()}).eq("id", user["id"]).execute()
        
        # Fetch church details
        church_response = supabase.table("churches").select("*").eq("id", user["church_id"]).limit(1).execute()
        church = church_response.data[0] if church_response.data else {}
        
        return {
            "success": True,
            "user": {
                "id": user["id"],
                "name": user["full_name"],
                "phone": user["phone"],
                "role": user["role"],
                "church_id": user["church_id"]
            },
            "church": {
                "id": church.get("id"),
                "name": church.get("church_name"),
                "slug": church.get("slug")
            }
        }
        
    except Exception as e:
        logging.error(f"Login error: {e}")
        return {"success": False, "error": str(e)}

def get_user_by_phone(phone):
    """Get user data by phone number."""
    try:
        response = supabase.table("users").select("*").eq("phone", phone).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"Get user error: {e}")
        return None

def get_church_by_slug(slug):
    """Get church details by slug."""
    try:
        response = supabase.table("churches").select("*").eq("slug", slug).limit(1).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        logging.error(f"Get church error: {e}")
        return None

def get_church_stats(church_id):
    """Get church statistics for dashboard."""
    try:
        txn_response = supabase.table("transactions").select("type, amount").eq("church_id", church_id).eq("confirmed", True).execute()
        
        total_income = 0
        total_expenses = 0
        txn_count = 0
        
        for t in txn_response.data:
            txn_count += 1
            amount = int(t.get("amount", 0))
            if t.get("type") == "income":
                total_income += amount
            else:
                total_expenses += amount
        
        return {
            "total_transactions": txn_count,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "net_balance": total_income - total_expenses
        }
        
    except Exception as e:
        logging.error(f"Get stats error: {e}")
        return {"total_transactions": 0, "total_income": 0, "total_expenses": 0, "net_balance": 0}

def add_to_waitlist(name, phone, role, current_tracking=None, will_pay=None, price_range=None, features=None, other_feature=None):
    """Add a person to the waitlist."""
    try:
        existing = supabase.table("waitlist").select("id").eq("phone", phone).limit(1).execute()
        if existing.data:
            return {"success": False, "error": "This phone number is already on the waitlist."}

        payload = {
            "name": name,
            "phone": phone,
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
    """Get all waitlist entries for admin dashboard."""
    try:
        response = supabase.table("waitlist").select("*").order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        logging.error(f"Get waitlist error: {e}")
        return []

def get_waitlist_count():
    """Get total number of waitlist entries."""
    try:
        response = supabase.table("waitlist").select("id", count="exact", head=True).execute()
        return response.count if response.count is not None else 0
    except Exception as e:
        logging.error(f"Get waitlist count error: {e}")
        return 0
