import os
import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PROJECT_REF = SUPABASE_URL.split("//")[1].split(".")[0]

# Check existing tables
print(f"🔍 Checking existing tables in {PROJECT_REF}...")

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Query information_schema to see what tables exist
response = requests.get(
    f"{SUPABASE_URL}/rest/v1/information_schema.tables",
    headers={**headers, "Accept": "application/json"},
    params={"select": "table_name", "table_schema": "eq.public"}
)

if response.status_code == 200:
    tables = [row["table_name"] for row in response.json()]
    print(f"✅ Existing tables: {', '.join(tables) if tables else '(none)'}")
else:
    print(f"⚠️ Could not query tables: {response.status_code}")
    print(f"Response: {response.text}")
    tables = []

# Check which tables need to be created
needed = ["sessions", "churches", "users"]
missing = [t for t in needed if t not in tables]

print(f"\n📋 Tables needed: {', '.join(needed)}")
print(f"📋 Tables missing: {', '.join(missing) if missing else '(all exist)'}")

# Check if transactions/pending_confirmations exist
has_transactions = "transactions" in tables
has_pending = "pending_confirmations" in tables

print(f"\n📋 transactions table: {'✅ exists' if has_transactions else '❌ missing'}")
print(f"📋 pending_confirmations table: {'✅ exists' if has_pending else '❌ missing'}")

# Generate migration SQL that only creates missing tables
migration_sql = ""

if "sessions" in missing:
    migration_sql += """
-- SESSIONS TABLE
CREATE TABLE IF NOT EXISTS sessions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    sender_phone TEXT NOT NULL,
    last_intent TEXT,
    last_transaction_id uuid,
    last_active TIMESTAMPTZ DEFAULT NOW(),
    context JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_sessions_phone ON sessions(sender_phone);
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active DESC);
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "sessions_phone_access" ON sessions FOR ALL USING (sender_phone = current_setting('request.headers')::json->>'x-phone');

"""

if "churches" in missing:
    migration_sql += """
-- CHURCHES TABLE
CREATE TABLE IF NOT EXISTS churches (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    church_name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    denomination TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    phone TEXT,
    email TEXT,
    logo_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE,
    plan TEXT DEFAULT 'free',
    metadata JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_churches_slug ON churches(slug);
CREATE INDEX IF NOT EXISTS idx_churches_phone ON churches(phone);
ALTER TABLE churches ENABLE ROW LEVEL SECURITY;
CREATE POLICY "churches_public_read" ON churches FOR SELECT USING (is_active = TRUE);

"""

if "users" in missing:
    migration_sql += """
-- USERS TABLE
CREATE TABLE IF NOT EXISTS users (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    church_id uuid REFERENCES churches(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    email TEXT,
    role TEXT NOT NULL CHECK (role IN ('pastor', 'treasurer', 'admin')),
    pin_hash TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_church ON users(church_id);
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users_read_own_church" ON users FOR SELECT USING (church_id IN (
    SELECT church_id FROM users WHERE phone = current_setting('request.headers')::json->>'x-phone'
));

"""

# Add church_id to existing tables if they exist
if has_transactions:
    migration_sql += "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS church_id uuid REFERENCES churches(id);\n"

if has_pending:
    migration_sql += "ALTER TABLE pending_confirmations ADD COLUMN IF NOT EXISTS church_id uuid REFERENCES churches(id);\n"

# Functions & triggers
migration_sql += """
-- CLEANUP FUNCTIONS
CREATE OR REPLACE FUNCTION cleanup_stale_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM sessions WHERE last_active < NOW() - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;

-- Auto-set updated_at on churches
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS church_updated_at ON churches;
CREATE TRIGGER church_updated_at BEFORE UPDATE ON churches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

if missing:
    print(f"\n📝 Generated migration SQL for {len(missing)} table(s): {', '.join(missing)}")
    
    # Try to apply via Supabase Management API
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    
    if access_token:
        print("\n🚀 Applying migration via Management API...")
        mgmt_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        mgmt_response = requests.post(
            f"https://api.supabase.com/v1/projects/{PROJECT_REF}/sql",
            headers=mgmt_headers,
            json={"query": migration_sql}
        )
        
        if mgmt_response.status_code in [200, 201, 204]:
            print("✅ Migration applied successfully!")
        else:
            print(f"⚠️ Management API response: {mgmt_response.status_code}")
            print(mgmt_response.text)
            print("\n📋 Please run this SQL manually in Supabase Dashboard > SQL Editor:")
            print(migration_sql)
    else:
        print("\n⚠️ SUPABASE_ACCESS_TOKEN not set in .env")
        print("\n📋 Please run this SQL manually in Supabase Dashboard > SQL Editor:")
        print("\n" + "="*60)
        print(migration_sql)
        print("="*60)
        
        print("\n💡 To automate: Add SUPABASE_ACCESS_TOKEN to .env")
        print("   Get it from: https://supabase.com/dashboard/account/tokens")
else:
    print("\n✅ All tables already exist! No migration needed.")
