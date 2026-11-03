from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "db"
LOG_DIR = BASE_DIR / "logs"
MIGRATIONS_DIR = DB_DIR / "migrations"
MIGRATIONS_PG_DIR = DB_DIR / "migrations_pg"
BACKUP_DIR = DB_DIR / "backups"
TEST_BACKUP_DIR = DB_DIR / "backups_test"
OUTBOX_DIR = BASE_DIR / "outbox"
TEST_OUTBOX_DIR = BASE_DIR / "outbox_test"
DEFAULT_DB_PATH = DB_DIR / "helpdesk.db"

APP_ENV = os.getenv("APP_ENV", "development").strip().lower() or "development"
PORT = int(os.getenv("PORT", "8000"))
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
DB_PATH = Path(os.getenv("APP_DB_PATH", str(DEFAULT_DB_PATH)))
MAIL_OUTBOX_PATH = Path(os.getenv("APP_OUTBOX_PATH", str(TEST_OUTBOX_DIR if APP_ENV == "test" else OUTBOX_DIR)))
USE_SECURE_COOKIE = os.getenv("USE_SECURE_COOKIE", "0").strip().lower() in {"1", "true", "yes", "on"}

LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
LOGIN_RATE_LIMIT_WINDOW = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "300"))

APP_BASE_URL = (os.getenv("APP_BASE_URL", f"http://localhost:{PORT}").strip().rstrip("/")) or f"http://localhost:{PORT}"
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@helpdesk.local").strip() or "no-reply@helpdesk.local"
MAIL_REPLY_TO = os.getenv("MAIL_REPLY_TO", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1").strip().lower() in {"1", "true", "yes", "on"}
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "0").strip().lower() in {"1", "true", "yes", "on"}

SESSION_COOKIE = "helpdesk_session"
CSRF_COOKIE = "helpdesk_csrf"

# Backend de banco de dados: 'sqlite' (default) ou 'postgres'
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()
if DB_BACKEND not in {"sqlite", "postgres"}:
    raise ValueError(f"DB_BACKEND deve ser 'sqlite' ou 'postgres', recebido: {DB_BACKEND!r}")

# Postgres
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PG_POOL_MIN = int(os.getenv("PG_POOL_MIN", "2"))
PG_POOL_MAX = int(os.getenv("PG_POOL_MAX", "5"))
