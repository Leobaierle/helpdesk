PRAGMA foreign_keys = ON;

ALTER TABLE users ADD COLUMN updated_at TEXT;
ALTER TABLE sessions ADD COLUMN updated_at TEXT;
ALTER TABLE tickets ADD COLUMN public_token TEXT;

CREATE TABLE IF NOT EXISTS ticket_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL,
  actor_user_id INTEGER,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (actor_user_id) REFERENCES users(id)
);

UPDATE users SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP);
UPDATE sessions SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP);
UPDATE tickets SET public_token = COALESCE(public_token, lower(hex(randomblob(12))));

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_public_token ON tickets(public_token);
CREATE INDEX IF NOT EXISTS idx_tickets_status_created_at ON tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_category_id ON tickets(category_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to_user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_updated_at ON tickets(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_token_expires ON sessions(token, expires_at);
CREATE INDEX IF NOT EXISTS idx_ticket_events_ticket_created ON ticket_events(ticket_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_email_active ON users(email, is_active);
