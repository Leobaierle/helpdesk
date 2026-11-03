PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS user_password_resets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_password_resets_user_id ON user_password_resets(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_password_resets_token_expires ON user_password_resets(token, expires_at);
