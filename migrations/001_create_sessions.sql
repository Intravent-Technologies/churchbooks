-- Session memory for contextual conversations
-- Enables Abby to remember last intents, transactions, and conversation context
-- TTL: 2 hours (managed by application logic)

CREATE TABLE IF NOT EXISTS sessions (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    sender_phone TEXT NOT NULL,
    last_intent TEXT,
    last_transaction_id uuid,
    last_active TIMESTAMPTZ DEFAULT NOW(),
    context JSONB DEFAULT '[]'::jsonb,  -- Array of last exchanges [{role, content, timestamp}]
    metadata JSONB DEFAULT '{}'::jsonb  -- Extended user data (name, role, preferences)
);

-- Index for fast phone lookup
CREATE INDEX IF NOT EXISTS idx_sessions_phone ON sessions(sender_phone);

-- Index for active session queries
CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(last_active DESC);

-- Auto-cleanup function (optional, can also be handled by app logic)
-- Deletes sessions inactive for more than 24 hours
CREATE OR REPLACE FUNCTION cleanup_stale_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM sessions WHERE last_active < NOW() - INTERVAL '24 hours';
END;
$$ LANGUAGE plpgsql;
