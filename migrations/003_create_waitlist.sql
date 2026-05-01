-- Waitlist Table for ChurchBooks AI
-- Run in Supabase Dashboard > SQL Editor

CREATE TABLE IF NOT EXISTS waitlist (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL,
    current_tracking TEXT,
    will_pay TEXT,
    price_range TEXT,
    features TEXT,
    other_feature TEXT,
    status TEXT DEFAULT 'pending',
    invited_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_waitlist_phone ON waitlist(phone);
CREATE INDEX IF NOT EXISTS idx_waitlist_status ON waitlist(status);
