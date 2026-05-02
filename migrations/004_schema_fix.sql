-- ChurchBooks AI - Schema Fix Migration
-- Run this in Supabase Dashboard > SQL Editor

-- ============================================================
-- 1. USERS TABLE - Add missing columns
-- ============================================================

-- Drop NOT NULL constraints temporarily
ALTER TABLE users ALTER COLUMN full_name DROP NOT NULL;
ALTER TABLE users ALTER COLUMN role DROP NOT NULL;

-- Add missing columns
ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_step INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ DEFAULT NOW();

-- ============================================================
-- 2. SESSIONS TABLE - Add missing columns
-- ============================================================

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS state TEXT DEFAULT 'UNKNOWN';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMPTZ DEFAULT NOW();

-- Backfill phone from sender_phone
UPDATE sessions SET phone = sender_phone WHERE phone IS NULL;

-- ============================================================
-- 3. ONBOARDING PROGRESS TABLE - Create if missing
-- ============================================================

CREATE TABLE IF NOT EXISTS onboarding_progress (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    step INTEGER DEFAULT 0,
    collected_data JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_onboarding_phone ON onboarding_progress(phone);

-- ============================================================
-- 4. UPDATE RLS POLICIES
-- ============================================================

-- Drop existing policies (will be recreated)
DROP POLICY IF EXISTS "sessions_phone_access" ON sessions;

-- Recreate with correct columns
CREATE POLICY "sessions_phone_access" ON sessions FOR ALL USING (sender_phone = current_setting('request.headers')::json->>'x-phone');
