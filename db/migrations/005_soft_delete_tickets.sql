ALTER TABLE tickets ADD COLUMN deleted_at TEXT;
ALTER TABLE tickets ADD COLUMN deleted_by_user_id INTEGER REFERENCES users(id);

CREATE INDEX IF NOT EXISTS idx_tickets_deleted_at ON tickets(deleted_at DESC);
