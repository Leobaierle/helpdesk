PRAGMA foreign_keys = OFF;

UPDATE tickets
SET public_token = lower(hex(randomblob(12)))
WHERE public_token IS NULL OR trim(public_token) = '';

CREATE TABLE tickets_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requester_name TEXT NOT NULL,
  requester_email TEXT NOT NULL,
  requester_department TEXT NOT NULL,
  category_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('Baixa', 'Media', 'Alta')),
  status TEXT NOT NULL DEFAULT 'Aberto' CHECK (status IN ('Aberto', 'Em analise', 'Em atendimento', 'Aguardando retorno', 'Concluido', 'Cancelado')),
  assigned_to_user_id INTEGER,
  internal_note TEXT,
  public_token TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_id) REFERENCES request_types(id),
  FOREIGN KEY (assigned_to_user_id) REFERENCES users(id)
);

INSERT INTO tickets_new (
  id,
  requester_name,
  requester_email,
  requester_department,
  category_id,
  title,
  description,
  priority,
  status,
  assigned_to_user_id,
  internal_note,
  public_token,
  created_at,
  updated_at
)
SELECT
  id,
  requester_name,
  requester_email,
  requester_department,
  category_id,
  title,
  description,
  priority,
  status,
  assigned_to_user_id,
  internal_note,
  public_token,
  created_at,
  updated_at
FROM tickets;

DROP TABLE tickets;
ALTER TABLE tickets_new RENAME TO tickets;

DROP TABLE IF EXISTS activities;

CREATE UNIQUE INDEX IF NOT EXISTS idx_tickets_public_token ON tickets(public_token);
CREATE INDEX IF NOT EXISTS idx_tickets_status_created_at ON tickets(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickets_category_id ON tickets(category_id);
CREATE INDEX IF NOT EXISTS idx_tickets_assigned_to ON tickets(assigned_to_user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_updated_at ON tickets(updated_at DESC);

PRAGMA foreign_keys = ON;
