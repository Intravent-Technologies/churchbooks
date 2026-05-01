import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

sb_url = os.environ.get("SUPABASE_URL")
sb_key = os.environ.get("SUPABASE_KEY")
sb = create_client(sb_url, sb_key)

# Run migration
migration_path = os.path.join(os.path.dirname(__file__), "migrations", "001_create_sessions.sql")
with open(migration_path, "r") as f:
    sql = f.read()

# Supabase Python client doesn't support raw SQL execution directly
# We need to use the REST API for DDL operations
import requests

headers = {
    "apikey": sb_key,
    "Authorization": f"Bearer {sb_key}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# Execute via Supabase SQL endpoint
response = requests.post(
    f"{sb_url}/rest/v1/",
    headers={**headers, "Content-Type": "text/plain"},
    data=sql
)

if response.status_code in [200, 201, 204]:
    print("✅ Migration applied successfully!")
else:
    print(f"⚠️ Migration response: {response.status_code}")
    print(response.text)

# Verify table exists
try:
    result = sb.table("sessions").select("*").limit(1).execute()
    print(f"✅ Sessions table verified. Current rows: {len(result.data)}")
except Exception as e:
    print(f"❌ Sessions table not found: {e}")
