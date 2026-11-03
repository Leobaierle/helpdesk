from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app_config import (
    BACKUP_DIR,
    DATABASE_URL,
    DB_BACKEND,
    DB_PATH,
    LOG_DIR,
    MIGRATIONS_DIR,
    MIGRATIONS_PG_DIR,
    PG_POOL_MAX,
    PG_POOL_MIN,
    TEST_BACKUP_DIR,
)


LOG_PATH = LOG_DIR / "app.log"
logger = logging.getLogger("helpdesk")


# --------------------------------------------------------------------------
# Erro unificado de integridade
# --------------------------------------------------------------------------

class IntegrityError(Exception):
    """Backend-agnostic integrity error wrapper levantada pelo shim Postgres."""


# Tupla pronta para except clauses cobrir ambos backends sem importar psycopg
# (psycopg.errors.IntegrityError e mapeada em runtime para IntegrityError pelo shim)
DBIntegrityError: tuple[type[BaseException], ...] = (sqlite3.IntegrityError, IntegrityError)


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


def ensure_directories() -> None:
    if DB_BACKEND == "sqlite":
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    TEST_BACKUP_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Backend Postgres (lazy import)
# --------------------------------------------------------------------------

_pg_pool = None  # type: ignore[var-annotated]
_psycopg = None  # type: ignore[var-annotated]


def _ensure_pg_imports() -> None:
    global _psycopg
    if _psycopg is not None:
        return
    import psycopg  # type: ignore
    _psycopg = psycopg


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        _ensure_pg_imports()
        from psycopg_pool import ConnectionPool  # type: ignore
        if not DATABASE_URL:
            raise RuntimeError("DB_BACKEND=postgres exige DATABASE_URL no ambiente")
        _pg_pool = ConnectionPool(
            DATABASE_URL,
            min_size=PG_POOL_MIN,
            max_size=PG_POOL_MAX,
            open=True,
        )
    return _pg_pool


# --------------------------------------------------------------------------
# Tradutor SQL: ?  ->  %s
# --------------------------------------------------------------------------

def _translate_placeholders(sql: str) -> str:
    """Converte ? para %s em SQL, ignorando ocorrencias dentro de strings."""
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Shim de cursor / connection para Postgres com API compativel SQLite
# --------------------------------------------------------------------------

class _RowDictWrapper(dict):
    """Permite acesso por indice, igual sqlite3.Row."""
    __slots__ = ("_keys",)

    def __init__(self, mapping: dict[str, Any]):
        super().__init__(mapping)
        self._keys = tuple(mapping.keys())

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)

    def keys(self):  # type: ignore[override]
        return self._keys


class _PgCursorShim:
    def __init__(self, raw_cursor, lastrowid: int | None = None) -> None:
        self._cur = raw_cursor
        self._lastrowid = lastrowid

    def fetchone(self):
        row = self._cur.fetchone()
        return _RowDictWrapper(row) if row else None

    def fetchall(self):
        return [_RowDictWrapper(r) for r in self._cur.fetchall()]

    def __iter__(self):
        for row in self._cur:
            yield _RowDictWrapper(row)

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


_INSERT_RE_PREFIX = "INSERT"


class _PgConnShim:
    def __init__(self, raw_conn) -> None:
        self._conn = raw_conn

    def execute(self, sql: str, params: Any = ()) -> _PgCursorShim:
        translated = _translate_placeholders(sql)
        cur = self._conn.cursor()
        try:
            stripped = translated.lstrip()
            upper = stripped.upper()
            if upper.startswith(_INSERT_RE_PREFIX) and "RETURNING" not in upper:
                # Tenta capturar lastrowid via RETURNING id (idempotente para callers que nao usam)
                returning_sql = translated.rstrip(";\n\t ") + " RETURNING id"
                try:
                    cur.execute(returning_sql, params)
                    row = cur.fetchone()
                    last = row.get("id") if isinstance(row, dict) and row else None
                    return _PgCursorShim(cur, lastrowid=last)
                except _psycopg.errors.UndefinedColumn:  # type: ignore[attr-defined]
                    self._conn.rollback()
                    cur = self._conn.cursor()
                    cur.execute(translated, params)
                    return _PgCursorShim(cur)
            cur.execute(translated, params)
            return _PgCursorShim(cur)
        except _psycopg.errors.IntegrityError as exc:  # type: ignore[attr-defined]
            raise IntegrityError(str(exc)) from exc

    def executescript(self, sql: str) -> None:
        # psycopg suporta multiplos statements em uma execute, mas vamos aplicar dentro de transacao
        self._conn.execute(sql)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        # No Postgres a conexao e devolvida ao pool; fechamento real fica com o pool
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            try:
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        else:
            self._conn.rollback()
        # devolve ao pool
        pool = _get_pg_pool()
        pool.putconn(self._conn)


# --------------------------------------------------------------------------
# API publica: get_db()
# --------------------------------------------------------------------------

def get_db():
    """Retorna conexao de banco. Compatibilidade SQLite-like em ambos backends."""
    if DB_BACKEND == "postgres":
        pool = _get_pg_pool()
        raw = pool.getconn()
        # garante autocommit OFF para semantica transacional dentro do `with`
        if raw.autocommit:
            raw.autocommit = False
        # row factory: dict para cada row
        from psycopg.rows import dict_row  # type: ignore
        raw.row_factory = dict_row
        return _PgConnShim(raw)
    # SQLite default
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn




# --------------------------------------------------------------------------
# Migracoes
# --------------------------------------------------------------------------

def ensure_schema() -> None:
    ensure_directories()
    if DB_BACKEND == "postgres":
        _ensure_pg_imports()
        from psycopg.rows import dict_row  # type: ignore
        pool = _get_pg_pool()
        with pool.connection() as conn:
            conn.row_factory = dict_row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                  version TEXT PRIMARY KEY,
                  applied_at TEXT NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS+00:00')
                )
                """
            )
            applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for migration_path in sorted(Path(MIGRATIONS_PG_DIR).glob("*.sql")):
                version = migration_path.name
                if version in applied:
                    continue
                conn.execute(migration_path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
        return

    # SQLite (comportamento original)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for migration_path in sorted(Path(MIGRATIONS_DIR).glob("*.sql")):
            version = migration_path.name
            if version in applied:
                continue
            conn.executescript(migration_path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
