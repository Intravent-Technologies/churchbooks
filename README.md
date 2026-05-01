# ChurchBooks AI

A WhatsApp-based AI financial assistant for Nigerian churches. Treasurers send voice notes with offerings, tithes, and expenses, and the system automatically records, categorizes, and reports them.

## 🚀 Setup Instructions

### 1. Clone the repo
```bash
git clone https://github.com/your-username/churchbooks.git
cd churchbooks
```

### 2. Create and configure `.env`
```bash
cp .env.example .env
```
Fill in all keys in `.env`:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`
- `GROQ_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `FLASK_SECRET_KEY`

### 3. How to get API keys
- **Twilio**: Create an account at twilio.com, enable WhatsApp Sandbox, and copy credentials.
- **Groq**: Sign up at console.groq.com, generate an API key.
- **Supabase**: Create a project, go to Settings → API, and copy Project URL and `service_role` key.
- Create the `transactions` and `pending_confirmations` tables in Supabase using the schema below.

### 4. Supabase Database Schema
Run these in Supabase SQL Editor:
```sql
CREATE TABLE transactions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  church_id text,
  sender_phone text,
  type text,
  category text,
  amount numeric,
  note text,
  raw_transcript text,
  confirmed boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE pending_confirmations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sender_phone text,
  entries jsonb,
  raw_transcript text,
  created_at timestamptz DEFAULT now()
);
```

### 5. Install dependencies & run locally
```bash
pip install -r requirements.txt
flask run
```

### 6. Set Twilio webhook URL
Point your Twilio WhatsApp Sandbox webhook to:
`https://your-ngrok-url/webhook`
(Use `ngrok http 5000` for local testing)

### 7. Deploy to Render.com (Free Tier)
1. Push code to GitHub.
2. Create a new Web Service on Render.
3. Connect your repo, set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app`
5. Add all `.env` variables in Render dashboard.
6. Update Twilio webhook URL to `https://your-render-app.onrender.com/webhook`

### 8. Test it
Send a voice note to your Twilio WhatsApp Sandbox number:
*"Sunday offering was 250,000. Tithe was 120,000. We spent 30,000 on fuel."*
Reply `YES` or `NO` to confirm or cancel. Send `REPORT` for your monthly summary.

---
_Built with ❤️ for Nigerian churches_