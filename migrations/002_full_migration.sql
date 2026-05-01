-- ChurchBooks AI - Complete Database Migration
-- Run this in Supabase Dashboard > SQL Editor
-- Created: 2026-04-30

-- ============================================================
-- 1. SESSIONS TABLE (Conversation Memory)
-- ============================================================
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

-- ============================================================
-- 2. CHURCHES TABLE (Account/Organization Level)
-- ============================================================
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

-- ============================================================
-- 3. USERS TABLE (Pastors & Treasurers)
-- ============================================================
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

-- ============================================================
-- 4. UPDATE EXISTING TABLES (Add church_id for multi-tenant)
-- ============================================================

-- Add church_id to transactions if not exists
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS church_id uuid REFERENCES churches(id);

-- Add church_id to pending_confirmations if not exists
ALTER TABLE pending_confirmations ADD COLUMN IF NOT EXISTS church_id uuid REFERENCES churches(id);

-- ============================================================
-- 5. RLS POLICIES (Security)
-- ============================================================

-- Enable RLS
ALTER TABLE churches ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

-- Churches: Anyone can view active churches (for landing page), only admins can modify
CREATE POLICY "churches_public_read" ON churches FOR SELECT USING (is_active = TRUE);

-- Users: Users can only see their own church's members
CREATE POLICY "users_read_own_church" ON users FOR SELECT USING (church_id IN (
    SELECT church_id FROM users WHERE phone = current_setting('request.headers')::json->>'x-phone'
));

-- Sessions: Phone-based access
CREATE POLICY "sessions_phone_access" ON sessions FOR ALL USING (sender_phone = current_setting('request.headers')::json->>'x-phone');

-- ============================================================
-- 6. CLEANUP FUNCTIONS
-- ============================================================
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

CREATE TRIGGER church_updated_at BEFORE UPDATE ON churches
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
