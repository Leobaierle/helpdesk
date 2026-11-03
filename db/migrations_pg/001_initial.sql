-- Migration consolidada Postgres (estado equivalente as 7 migrations SQLite aplicadas)
-- Filosofia: maxima compatibilidade mecanica com o codigo Python que usa SQLite.
-- - timestamps como TEXT em formato ISO 8601 (igual ao que o app ja produz com isoformat())
-- - booleans como INTEGER (evita conversao em ~10 call sites)
-- - DEFAULT em timestamps usa to_char(now() AT TIME ZONE 'UTC', ...) para bater com isoformat() Python

CREATE TABLE request_types (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE users (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department TEXT,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'ti' CHECK (role IN ('ti')),
  is_active INTEGER NOT NULL DEFAULT 1,
  activated_at TEXT,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00'),
  updated_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE sessions (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  token TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00'),
  updated_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE tickets (
  id BIGSERIAL PRIMARY KEY,
  requester_name TEXT NOT NULL,
  requester_email TEXT NOT NULL,
  requester_department TEXT NOT NULL,
  category_id BIGINT NOT NULL REFERENCES request_types(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('Baixa', 'Media', 'Alta')),
  status TEXT NOT NULL DEFAULT 'Aberto' CHECK (status IN ('Aberto', 'Em analise', 'Em atendimento', 'Aguardando retorno', 'Concluido', 'Cancelado')),
  assigned_to_user_id BIGINT REFERENCES users(id),
  internal_note TEXT,
  public_token TEXT NOT NULL UNIQUE,
  deleted_at TEXT,
  deleted_by_user_id BIGINT REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00'),
  updated_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE ticket_events (
  id BIGSERIAL PRIMARY KEY,
  ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  actor_user_id BIGINT REFERENCES users(id),
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  old_status TEXT,
  new_status TEXT,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE login_attempts (
  id BIGSERIAL PRIMARY KEY,
  ip_address TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE user_invites (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  invited_by_user_id BIGINT REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE TABLE user_password_resets (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
);

CREATE INDEX idx_tickets_status_created_at ON tickets(status, created_at DESC);
CREATE INDEX idx_tickets_category_id ON tickets(category_id);
CREATE INDEX idx_tickets_assigned_to ON tickets(assigned_to_user_id);
CREATE INDEX idx_tickets_updated_at ON tickets(updated_at DESC);
CREATE INDEX idx_tickets_deleted_at ON tickets(deleted_at DESC);
CREATE INDEX idx_sessions_token_expires ON sessions(token, expires_at);
CREATE INDEX idx_ticket_events_ticket_created ON ticket_events(ticket_id, created_at DESC);
CREATE INDEX idx_users_email_active ON users(email, is_active);
CREATE INDEX idx_login_attempts_ip_created_at ON login_attempts(ip_address, created_at DESC);
CREATE INDEX idx_user_invites_user_id ON user_invites(user_id, created_at DESC);
CREATE INDEX idx_user_invites_token_expires ON user_invites(token, expires_at);
CREATE INDEX idx_user_password_resets_user_id ON user_password_resets(user_id, created_at DESC);
CREATE INDEX idx_user_password_resets_token_expires ON user_password_resets(token, expires_at);

INSERT INTO request_types (id, name, description) VALUES
  (1, 'Automacao de atividades', 'Demandas para automatizacao de rotinas e processos internos.'),
  (2, 'Suporte TI', 'Solicitacoes tecnicas relacionadas a acessos, equipamentos e infraestrutura.')
ON CONFLICT (id) DO NOTHING;

-- Realinha sequences apos seeds com IDs explicitos
SELECT setval(pg_get_serial_sequence('request_types', 'id'), COALESCE((SELECT max(id) FROM request_types), 1));
