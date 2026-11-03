from __future__ import annotations

import hashlib
import html
import re
import secrets
import sqlite3
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from app_config import (
    APP_BASE_URL,
    BACKUP_DIR,
    DB_PATH,
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW,
    MAIL_FROM,
    MAIL_OUTBOX_PATH,
    MAIL_REPLY_TO,
    SESSION_DAYS,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_USE_SSL,
    SMTP_USE_TLS,
    TEST_BACKUP_DIR,
)
from app_db import DBIntegrityError, get_db, logger


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_TICKET_STATUSES = ("Aberto", "Em analise", "Em atendimento", "Aguardando retorno", "Concluido", "Cancelado")
VALID_TABS = {"abertos", "andamento", "concluidos", "todos", "excluidos"}
PRIORITY_ORDER = {"Alta": 1, "Media": 2, "Baixa": 3}
ALLOWED_SORTS = {"recentes", "antigos", "prioridade_alta", "prioridade_baixa"}
DELETED_RETENTION_DAYS = 30
USER_INVITE_EXPIRATION_HOURS = 72
PASSWORD_RESET_EXPIRATION_HOURS = 2


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def format_datetime(value: str | None) -> str:
    if not value:
        return "-"
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value
    return dt.astimezone().strftime("%d/%m/%Y %H:%M")


def status_label(status: str) -> str:
    return {
        "Aberto": "Aberto",
        "Em analise": "Em análise",
        "Em atendimento": "Em atendimento",
        "Aguardando retorno": "Aguardando retorno",
        "Concluido": "Concluído",
        "Cancelado": "Cancelado",
    }.get(status, status)


def priority_label(priority: str) -> str:
    return {"Alta": "Alta", "Media": "Média", "Baixa": "Baixa"}.get(priority, priority)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded or "$" not in encoded:
        return False
    salt, current_hash = encoded.split("$", 1)
    candidate = hash_password(password, salt).split("$", 1)[1]
    return secrets.compare_digest(candidate, current_hash)


def normalize_tab(tab: str) -> str:
    return tab if tab in VALID_TABS else "abertos"


def list_categories() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM request_types ORDER BY name").fetchall()


def list_ti_users(active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM users"
    params: list[Any] = []
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def has_users() -> bool:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0


def validate_ticket_form(form: dict[str, str], categories: list[sqlite3.Row]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {
        "requester_name": form.get("requester_name", "").strip(),
        "requester_email": form.get("requester_email", "").strip().lower(),
        "requester_department": form.get("requester_department", "").strip(),
        "category_id": form.get("category_id", "").strip(),
        "priority": form.get("priority", "").strip(),
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
    }
    errors: dict[str, str] = {}
    if not normalized["requester_name"]:
        errors["requester_name"] = "Informe o nome do solicitante."
    elif len(normalized["requester_name"]) < 3:
        errors["requester_name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["requester_name"]) > 120:
        errors["requester_name"] = "O nome deve ter no máximo 120 caracteres."

    if not normalized["requester_email"]:
        errors["requester_email"] = "Informe o e-mail do solicitante."
    elif not EMAIL_RE.fullmatch(normalized["requester_email"]):
        errors["requester_email"] = "Informe um e-mail válido."

    if not normalized["requester_department"]:
        errors["requester_department"] = "Informe o setor do solicitante."
    elif len(normalized["requester_department"]) < 2:
        errors["requester_department"] = "O setor deve ter pelo menos 2 caracteres."
    elif len(normalized["requester_department"]) > 80:
        errors["requester_department"] = "O setor deve ter no máximo 80 caracteres."

    valid_category_ids = {str(row["id"]) for row in categories}
    if not normalized["category_id"]:
        errors["category_id"] = "Selecione a categoria do chamado."
    elif normalized["category_id"] not in valid_category_ids:
        errors["category_id"] = "A categoria selecionada é inválida."

    if normalized["priority"] not in {"Baixa", "Media", "Alta"}:
        errors["priority"] = "Selecione uma prioridade válida."

    if not normalized["title"]:
        errors["title"] = "Informe o título do chamado."
    elif len(normalized["title"]) < 5:
        errors["title"] = "O título deve ter pelo menos 5 caracteres."
    elif len(normalized["title"]) > 160:
        errors["title"] = "O título deve ter no máximo 160 caracteres."

    if not normalized["description"]:
        errors["description"] = "Descreva a solicitação."
    elif len(normalized["description"]) < 10:
        errors["description"] = "A descrição deve ter pelo menos 10 caracteres."
    elif len(normalized["description"]) > 4000:
        errors["description"] = "A descrição deve ter no máximo 4000 caracteres."

    return normalized, errors


def validate_user_form(form: dict[str, str], creating: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "department": form.get("department", "").strip() or "TI",
        "password": form.get("password", ""),
        "is_active": "1" if form.get("is_active", "1") == "1" else "0",
    }
    errors: dict[str, str] = {}
    if not normalized["name"]:
        errors["name"] = "Informe o nome do usuário."
    elif len(normalized["name"]) < 3:
        errors["name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["name"]) > 120:
        errors["name"] = "O nome deve ter no máximo 120 caracteres."

    if not normalized["email"]:
        errors["email"] = "Informe o e-mail do usuário."
    elif not EMAIL_RE.fullmatch(normalized["email"]):
        errors["email"] = "Informe um e-mail válido."

    if len(normalized["department"]) > 80:
        errors["department"] = "O setor deve ter no máximo 80 caracteres."

    if creating or normalized["password"]:
        password_error = validate_password_strength(normalized["password"])
        if password_error:
            errors["password"] = password_error

    return normalized, errors


def validate_invited_user_form(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {
        "name": form.get("name", "").strip(),
        "email": form.get("email", "").strip().lower(),
        "department": form.get("department", "").strip() or "TI",
    }
    errors: dict[str, str] = {}
    if not normalized["name"]:
        errors["name"] = "Informe o nome do usuário."
    elif len(normalized["name"]) < 3:
        errors["name"] = "O nome deve ter pelo menos 3 caracteres."
    elif len(normalized["name"]) > 120:
        errors["name"] = "O nome deve ter no máximo 120 caracteres."

    if not normalized["email"]:
        errors["email"] = "Informe o e-mail do usuário."
    elif not EMAIL_RE.fullmatch(normalized["email"]):
        errors["email"] = "Informe um e-mail válido."

    if len(normalized["department"]) > 80:
        errors["department"] = "O setor deve ter no máximo 80 caracteres."

    return normalized, errors


def validate_password_strength(password: str) -> str | None:
    if len(password) < 8:
        return "A senha deve ter pelo menos 8 caracteres."
    if password.lower() == password or password.upper() == password or not any(char.isdigit() for char in password):
        return "Use pelo menos 8 caracteres, com letras e números."
    return None


def validate_password_change_form(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {
        "password": form.get("password", ""),
        "password_confirm": form.get("password_confirm", ""),
    }
    errors: dict[str, str] = {}
    password_error = validate_password_strength(normalized["password"])
    if password_error:
        errors["password"] = password_error
    elif normalized["password"] != normalized["password_confirm"]:
        errors["password_confirm"] = "A confirmação de senha não confere."
    return normalized, errors


def validate_email_form(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    normalized = {"email": form.get("email", "").strip().lower()}
    errors: dict[str, str] = {}
    if not normalized["email"]:
        errors["email"] = "Informe o e-mail do usuário."
    elif not EMAIL_RE.fullmatch(normalized["email"]):
        errors["email"] = "Informe um e-mail válido."
    return normalized, errors


def record_ticket_event(
    conn: sqlite3.Connection,
    ticket_id: int,
    event_type: str,
    message: str,
    actor_user_id: int | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ticket_events (ticket_id, actor_user_id, event_type, message, old_status, new_status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (ticket_id, actor_user_id, event_type, message[:1000], old_status, new_status),
    )


def create_ticket(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str], int | None]:
    categories = list_categories()
    normalized, errors = validate_ticket_form(form, categories)
    if errors:
        return normalized, errors, None
    public_token = secrets.token_urlsafe(18)
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tickets (
              requester_name,
              requester_email,
              requester_department,
              category_id,
              title,
              description,
              priority,
              public_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["requester_name"],
                normalized["requester_email"],
                normalized["requester_department"],
                normalized["category_id"],
                normalized["title"],
                normalized["description"],
                normalized["priority"],
                public_token,
            ),
        )
        ticket_id = int(cursor.lastrowid)
        record_ticket_event(conn, ticket_id, "abertura", "Chamado aberto pelo solicitante.")
    return normalized, {}, ticket_id


def create_user(form: dict[str, str], current_user_id: int | None = None, initial_setup: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    normalized, errors = validate_user_form(form, creating=True)
    if errors:
        return normalized, errors
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (name, email, department, password_hash, role, is_active, activated_at, updated_at)
                VALUES (?, ?, ?, ?, 'ti', 1, ?, ?)
                """,
                (
                    normalized["name"],
                    normalized["email"],
                    normalized["department"],
                    hash_password(normalized["password"]),
                    isoformat(utc_now()),
                    isoformat(utc_now()),
                ),
            )
    except DBIntegrityError:
        errors["email"] = "Já existe usuário com esse e-mail."
    return normalized, errors


def create_invited_user(form: dict[str, str], invited_by_user_id: int) -> tuple[dict[str, str], dict[str, str], str | None]:
    normalized, errors = validate_invited_user_form(form)
    if errors:
        return normalized, errors, None
    now = isoformat(utc_now())
    temporary_password_hash = hash_password(secrets.token_urlsafe(32))
    try:
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (name, email, department, password_hash, role, is_active, activated_at, updated_at)
                VALUES (?, ?, ?, ?, 'ti', 1, NULL, ?)
                """,
                (
                    normalized["name"],
                    normalized["email"],
                    normalized["department"],
                    temporary_password_hash,
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
            token = issue_user_invite(conn, user_id, invited_by_user_id)
    except DBIntegrityError:
        errors["email"] = "Já existe usuário com esse e-mail."
        return normalized, errors, None
    delivery = send_user_activation_invite(user_id, token)
    return normalized, {}, delivery


def issue_user_invite(conn: sqlite3.Connection, user_id: int, invited_by_user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    conn.execute(
        """
        UPDATE user_invites
        SET used_at = ?
        WHERE user_id = ? AND used_at IS NULL
        """,
        (isoformat(now), user_id),
    )
    conn.execute(
        """
        INSERT INTO user_invites (user_id, token, expires_at, invited_by_user_id)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            token,
            isoformat(now + timedelta(hours=USER_INVITE_EXPIRATION_HOURS)),
            invited_by_user_id,
        ),
    )
    return token


def update_user(user_id: int, form: dict[str, str], current_user_id: int) -> tuple[bool, str]:
    normalized, errors = validate_user_form(form, creating=False)
    if errors:
        return False, next(iter(errors.values()))
    with get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return False, "Usuário não encontrado."
        if target["id"] != current_user_id and normalized["password"]:
            return False, "Cada usuário pode alterar apenas a própria senha."
        active_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
        if target["id"] == current_user_id and normalized["is_active"] == "0":
            return False, "Não é permitido desativar o próprio usuário."
        if target["is_active"] and normalized["is_active"] == "0" and active_count <= 1:
            return False, "Não é permitido desativar o único usuário ativo."
        try:
            if normalized["password"]:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, department = ?, is_active = ?, password_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["name"],
                        normalized["email"],
                        normalized["department"],
                        normalized["is_active"],
                        hash_password(normalized["password"]),
                        isoformat(utc_now()),
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET name = ?, email = ?, department = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized["name"],
                        normalized["email"],
                        normalized["department"],
                        normalized["is_active"],
                        isoformat(utc_now()),
                        user_id,
                    ),
                )
        except DBIntegrityError:
            return False, "Já existe usuário com esse e-mail."
    return True, "Usuário atualizado com sucesso."


def update_own_password(user_id: int, form: dict[str, str]) -> tuple[bool, str]:
    normalized, errors = validate_password_change_form(form)
    if errors:
        return False, next(iter(errors.values()))
    with get_db() as conn:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, "Usuário não encontrado."
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, activated_at = COALESCE(activated_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (
                hash_password(normalized["password"]),
                isoformat(utc_now()),
                isoformat(utc_now()),
                user_id,
            ),
        )
    return True, "Senha atualizada com sucesso."


def get_user_invite(token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT
              user_invites.id,
              user_invites.user_id,
              user_invites.token,
              user_invites.expires_at,
              user_invites.used_at,
              users.name,
              users.email,
              users.department,
              users.is_active
            FROM user_invites
            JOIN users ON users.id = user_invites.user_id
            WHERE user_invites.token = ?
            """,
            (token,),
        ).fetchone()


def issue_password_reset(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = utc_now()
    conn.execute(
        """
        UPDATE user_password_resets
        SET used_at = ?
        WHERE user_id = ? AND used_at IS NULL
        """,
        (isoformat(now), user_id),
    )
    conn.execute(
        """
        INSERT INTO user_password_resets (user_id, token, expires_at)
        VALUES (?, ?, ?)
        """,
        (
            user_id,
            token,
            isoformat(now + timedelta(hours=PASSWORD_RESET_EXPIRATION_HOURS)),
        ),
    )
    return token


def get_password_reset(token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT
              user_password_resets.id,
              user_password_resets.user_id,
              user_password_resets.token,
              user_password_resets.expires_at,
              user_password_resets.used_at,
              users.name,
              users.email,
              users.is_active
            FROM user_password_resets
            JOIN users ON users.id = user_password_resets.user_id
            WHERE user_password_resets.token = ?
            """,
            (token,),
        ).fetchone()


def request_password_reset(form: dict[str, str]) -> tuple[dict[str, str], dict[str, str], str]:
    normalized, errors = validate_email_form(form)
    if errors:
        return normalized, errors, "skipped"

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, name, email, is_active FROM users WHERE email = ?",
            (normalized["email"],),
        ).fetchone()
        if not user or not user["is_active"]:
            return normalized, {}, "skipped"
        token = issue_password_reset(conn, user["id"])

    delivery = send_password_reset_email(user["id"], token)
    return normalized, {}, delivery


def activate_user_invite(token: str, form: dict[str, str]) -> tuple[bool, str]:
    normalized, errors = validate_password_change_form(form)
    if errors:
        return False, next(iter(errors.values()))
    now = utc_now()
    with get_db() as conn:
        invite = conn.execute(
            """
            SELECT
              user_invites.id,
              user_invites.user_id,
              user_invites.expires_at,
              user_invites.used_at,
              users.is_active
            FROM user_invites
            JOIN users ON users.id = user_invites.user_id
            WHERE user_invites.token = ?
            """,
            (token,),
        ).fetchone()
        if not invite:
            return False, "Link de ativação inválido."
        if invite["used_at"]:
            return False, "Esse link de ativação já foi utilizado."
        if not invite["is_active"]:
            return False, "Esse usuário está inativo."
        if datetime.fromisoformat(invite["expires_at"].replace("Z", "+00:00")) <= now:
            return False, "Esse link de ativação expirou."
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, activated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                hash_password(normalized["password"]),
                isoformat(now),
                isoformat(now),
                invite["user_id"],
            ),
        )
        conn.execute("UPDATE user_invites SET used_at = ? WHERE id = ?", (isoformat(now), invite["id"]))
    return True, "Senha cadastrada com sucesso. Entre com seu e-mail e sua nova senha."


def reset_user_password(token: str, form: dict[str, str]) -> tuple[bool, str]:
    normalized, errors = validate_password_change_form(form)
    if errors:
        return False, next(iter(errors.values()))
    now = utc_now()
    with get_db() as conn:
        reset = conn.execute(
            """
            SELECT
              user_password_resets.id,
              user_password_resets.user_id,
              user_password_resets.expires_at,
              user_password_resets.used_at,
              users.is_active
            FROM user_password_resets
            JOIN users ON users.id = user_password_resets.user_id
            WHERE user_password_resets.token = ?
            """,
            (token,),
        ).fetchone()
        if not reset:
            return False, "Link de redefinição inválido."
        if reset["used_at"]:
            return False, "Esse link de redefinição já foi utilizado."
        if not reset["is_active"]:
            return False, "Esse usuário está inativo."
        if datetime.fromisoformat(reset["expires_at"].replace("Z", "+00:00")) <= now:
            return False, "Esse link de redefinição expirou."
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, activated_at = COALESCE(activated_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (
                hash_password(normalized["password"]),
                isoformat(now),
                isoformat(now),
                reset["user_id"],
            ),
        )
        conn.execute("UPDATE user_password_resets SET used_at = ? WHERE id = ?", (isoformat(now), reset["id"]))
    return True, "Senha redefinida com sucesso. Entre com seu e-mail e sua nova senha."


def cleanup_expired_sessions() -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (isoformat(utc_now()),))


def get_session_user(session_token: str | None) -> sqlite3.Row | None:
    if not session_token:
        return None
    cleanup_expired_sessions()
    with get_db() as conn:
        return conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ? AND users.is_active = 1
            """,
            (session_token, isoformat(utc_now())),
        ).fetchone()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = isoformat(utc_now() + timedelta(days=SESSION_DAYS))
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO sessions (user_id, token, expires_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, token, expires_at, isoformat(utc_now())),
        )
    return token


def delete_session(session_token: str | None) -> None:
    if not session_token:
        return
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (session_token,))


def authenticate_user(email: str, password: str) -> sqlite3.Row | None:
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    if not user or not user["is_active"] or not verify_password(password, user["password_hash"]):
        return None
    return user


def register_login_attempt(ip: str, success: bool) -> None:
    cutoff = isoformat(utc_now() - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW))
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE created_at <= ?", (cutoff,))
        if not success:
            conn.execute(
                "INSERT INTO login_attempts (ip_address, created_at) VALUES (?, ?)",
                (ip, isoformat(utc_now())),
            )


def is_login_rate_limited(ip: str) -> bool:
    cutoff = isoformat(utc_now() - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW))
    with get_db() as conn:
        conn.execute("DELETE FROM login_attempts WHERE created_at <= ?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE ip_address = ? AND created_at > ?",
            (ip, cutoff),
        ).fetchone()[0]
    return count >= LOGIN_RATE_LIMIT_ATTEMPTS


def get_ticket_filters(form: dict[str, str]) -> dict[str, str]:
    return {
        "category_id": form.get("category_id", "").strip(),
        "assigned_to": form.get("assigned_to", "").strip(),
        "q": form.get("q", "").strip(),
        "sort": form.get("sort", "recentes").strip() if form.get("sort", "recentes").strip() in ALLOWED_SORTS else "recentes",
    }


def build_dashboard_query(active_tab: str, filters: dict[str, str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if active_tab == "excluidos":
        clauses.append("tickets.deleted_at IS NOT NULL")
    else:
        clauses.append("tickets.deleted_at IS NULL")

    if active_tab == "abertos":
        clauses.append("tickets.status = 'Aberto'")
    elif active_tab == "andamento":
        clauses.append("tickets.status IN ('Em analise', 'Em atendimento', 'Aguardando retorno')")
    elif active_tab == "concluidos":
        clauses.append("tickets.status = 'Concluido'")
    else:
        clauses.append("1 = 1")

    if filters["category_id"]:
        clauses.append("tickets.category_id = ?")
        params.append(filters["category_id"])
    if filters["assigned_to"] == "none":
        clauses.append("tickets.assigned_to_user_id IS NULL")
    elif filters["assigned_to"]:
        clauses.append("tickets.assigned_to_user_id = ?")
        params.append(filters["assigned_to"])
    if filters["q"]:
        query = f"%{filters['q'].lower()}%"
        clauses.append(
            "(LOWER(tickets.requester_name) LIKE ? OR LOWER(tickets.title) LIKE ? OR CAST(tickets.id AS TEXT) LIKE ?)"
        )
        params.extend([query, query, query])

    if filters["sort"] == "antigos":
        order_by = "tickets.created_at ASC"
    elif filters["sort"] == "prioridade_alta":
        order_by = "CASE tickets.priority WHEN 'Alta' THEN 1 WHEN 'Media' THEN 2 ELSE 3 END, tickets.created_at DESC"
    elif filters["sort"] == "prioridade_baixa":
        order_by = "CASE tickets.priority WHEN 'Baixa' THEN 1 WHEN 'Media' THEN 2 ELSE 3 END, tickets.created_at DESC"
    else:
        order_by = "tickets.created_at DESC"
    return " AND ".join(clauses), params + [order_by]


def get_dashboard_data(active_tab: str, filters: dict[str, str]) -> tuple[list[sqlite3.Row], sqlite3.Row, dict[int, list[sqlite3.Row]]]:
    active_tab = normalize_tab(active_tab)
    where_clause, params = build_dashboard_query(active_tab, filters)
    order_by = params.pop()
    with get_db() as conn:
        tickets = conn.execute(
            f"""
            SELECT
              tickets.*,
              request_types.name AS category_name,
              users.name AS assigned_name,
              deleted_by_user.name AS deleted_by_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            LEFT JOIN users AS deleted_by_user ON deleted_by_user.id = tickets.deleted_by_user_id
            WHERE {where_clause}
            ORDER BY {order_by}
            """,
            params,
        ).fetchall()
        summary = conn.execute(
            """
            SELECT
              SUM(CASE WHEN deleted_at IS NULL AND status = 'Aberto' THEN 1 ELSE 0 END) AS open_count,
              SUM(CASE WHEN deleted_at IS NULL AND status IN ('Em analise', 'Em atendimento', 'Aguardando retorno') THEN 1 ELSE 0 END) AS progress_count,
              SUM(CASE WHEN deleted_at IS NULL AND status = 'Concluido' THEN 1 ELSE 0 END) AS done_count,
              SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END) AS total_count,
              SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted_count
            FROM tickets
            """
        ).fetchone()
        events_by_ticket: dict[int, list[sqlite3.Row]] = {}
        if tickets:
            placeholders = ",".join("?" for _ in tickets)
            event_rows = conn.execute(
                f"""
                SELECT ticket_events.*, users.name AS actor_name
                FROM ticket_events
                LEFT JOIN users ON users.id = ticket_events.actor_user_id
                WHERE ticket_id IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                """,
                [row["id"] for row in tickets],
            ).fetchall()
            for event in event_rows:
                events_by_ticket.setdefault(event["ticket_id"], []).append(event)
    return tickets, summary, events_by_ticket


def get_public_dashboard_snapshot(limit_per_column: int = 6) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    with get_db() as conn:
        in_progress = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.title,
              tickets.description,
              tickets.status,
              tickets.priority,
              tickets.updated_at,
              request_types.name AS category_name,
              users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.deleted_at IS NULL
              AND tickets.status IN ('Em analise', 'Em atendimento', 'Aguardando retorno')
            ORDER BY tickets.updated_at DESC, tickets.id DESC
            LIMIT ?
            """,
            (limit_per_column,),
        ).fetchall()
        completed = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.title,
              tickets.description,
              tickets.status,
              tickets.priority,
              tickets.updated_at,
              request_types.name AS category_name,
              users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.deleted_at IS NULL
              AND tickets.status = 'Concluido'
            ORDER BY tickets.updated_at DESC, tickets.id DESC
            LIMIT ?
            """,
            (limit_per_column,),
        ).fetchall()
    return in_progress, completed


def queue_email_to_outbox(message: EmailMessage, slug: str) -> Path:
    MAIL_OUTBOX_PATH.mkdir(parents=True, exist_ok=True)
    file_name = f"{utc_now().strftime('%Y%m%d-%H%M%S')}-{slug}.eml"
    file_path = MAIL_OUTBOX_PATH / file_name
    file_path.write_bytes(message.as_bytes())
    return file_path


def deliver_email(message: EmailMessage, slug: str) -> str:
    if not SMTP_HOST:
        queue_email_to_outbox(message, slug)
        return "outbox"

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as client:
                if SMTP_USERNAME:
                    client.login(SMTP_USERNAME, SMTP_PASSWORD)
                client.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as client:
                client.ehlo()
                if SMTP_USE_TLS:
                    client.starttls()
                    client.ehlo()
                if SMTP_USERNAME:
                    client.login(SMTP_USERNAME, SMTP_PASSWORD)
                client.send_message(message)
        return "smtp"
    except Exception:
        logger.exception("Falha ao enviar e-mail. Gravando copia no outbox.")
        queue_email_to_outbox(message, slug)
        return "outbox"


def send_ticket_confirmation(ticket_id: int) -> str:
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.title,
              tickets.description,
              tickets.priority,
              tickets.public_token,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket:
        return "skipped"

    public_link = f"{APP_BASE_URL}/acompanhar/{ticket['public_token']}"
    message = EmailMessage()
    message["Subject"] = f"Confirmação do chamado #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = ticket["requester_email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {ticket['requester_name']}.",
                "",
                f"Seu chamado #{ticket['id']} foi registrado com sucesso.",
                f"Categoria: {ticket['category_name']}",
                f"Prioridade: {priority_label(ticket['priority'])}",
                f"Título: {ticket['title']}",
                f"Resumo: {ticket['description']}",
                "",
                "Acompanhe o andamento pelo link abaixo:",
                public_link,
                "",
                "Esta é uma mensagem automática da Central de chamados da TI.",
            ]
        )
    )
    slug = f"ticket-confirmation-{ticket['id']}"
    return deliver_email(message, slug)


def send_new_ticket_operator_notification(ticket_id: int) -> str:
    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.requester_department,
              tickets.title,
              tickets.description,
              tickets.priority,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
        operators = conn.execute(
            """
            SELECT email
            FROM users
            WHERE is_active = 1
            ORDER BY name
            """
        ).fetchall()
    if not ticket:
        return "skipped"

    operator_emails = [row["email"] for row in operators if row["email"]]
    if not operator_emails:
        return "skipped"

    message = EmailMessage()
    message["Subject"] = f"Novo chamado aberto #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = ", ".join(operator_emails)
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                "Olá, equipe TI.",
                "",
                "Um novo chamado foi aberto na Central de chamados.",
                "",
                "Dados do chamado:",
                f"Número: #{ticket['id']}",
                f"Categoria: {ticket['category_name']}",
                f"Prioridade: {priority_label(ticket['priority'])}",
                f"Solicitante: {ticket['requester_name']}",
                f"E-mail do solicitante: {ticket['requester_email']}",
                f"Setor: {ticket['requester_department']}",
                f"Título: {ticket['title']}",
                f"Descrição: {ticket['description']}",
                "",
                "Acesse o painel para dar andamento ao atendimento:",
                f"{APP_BASE_URL}/ti",
                "",
                "Esta é uma mensagem automática da Central de chamados da TI.",
            ]
        )
    )
    slug = f"ticket-operator-notification-{ticket['id']}"
    return deliver_email(message, slug)


def send_user_activation_invite(user_id: int, token: str) -> str:
    with get_db() as conn:
        user = conn.execute(
            """
            SELECT id, name, email, department
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    if not user:
        return "skipped"

    activation_link = f"{APP_BASE_URL}/ti/ativar/{token}"
    message = EmailMessage()
    message["Subject"] = "Ativação do seu acesso ao Helpdesk"
    message["From"] = MAIL_FROM
    message["To"] = user["email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {user['name']}.",
                "",
                "Seu acesso à área interna do Helpdesk foi cadastrado.",
                f"Setor: {user['department'] or 'TI'}",
                "",
                "Para definir sua senha e ativar o acesso, use o link abaixo:",
                activation_link,
                "",
                f"O link expira em {USER_INVITE_EXPIRATION_HOURS} horas.",
                "",
                "Esta é uma mensagem automática da Central de chamados da TI.",
            ]
        )
    )
    slug = f"user-activation-{user['id']}"
    return deliver_email(message, slug)


def send_password_reset_email(user_id: int, token: str) -> str:
    with get_db() as conn:
        user = conn.execute(
            """
            SELECT id, name, email
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
    if not user:
        return "skipped"

    reset_link = f"{APP_BASE_URL}/ti/redefinir-senha/{token}"
    message = EmailMessage()
    message["Subject"] = "Redefinição de senha do Helpdesk"
    message["From"] = MAIL_FROM
    message["To"] = user["email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {user['name']}.",
                "",
                "Recebemos uma solicitação para redefinir a sua senha do Helpdesk.",
                "Se foi você, use o link abaixo para cadastrar uma nova senha:",
                reset_link,
                "",
                f"O link expira em {PASSWORD_RESET_EXPIRATION_HOURS} horas.",
                "Se você não solicitou a redefinição, ignore esta mensagem.",
                "",
                "Esta é uma mensagem automática da Central de chamados da TI.",
            ]
        )
    )
    slug = f"user-password-reset-{user['id']}"
    return deliver_email(message, slug)


def send_ticket_update_notification(ticket_id: int, changes: list[str]) -> str:
    if not changes:
        return "skipped"

    with get_db() as conn:
        ticket = conn.execute(
            """
            SELECT
              tickets.id,
              tickets.requester_name,
              tickets.requester_email,
              tickets.title,
              tickets.description,
              tickets.priority,
              tickets.status,
              tickets.public_token,
              users.name AS assigned_name,
              request_types.name AS category_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if not ticket:
        return "skipped"

    public_link = f"{APP_BASE_URL}/acompanhar/{ticket['public_token']}"
    message = EmailMessage()
    message["Subject"] = f"Atualização do chamado #{ticket['id']}"
    message["From"] = MAIL_FROM
    message["To"] = ticket["requester_email"]
    if MAIL_REPLY_TO:
        message["Reply-To"] = MAIL_REPLY_TO
    message.set_content(
        "\n".join(
            [
                f"Olá, {ticket['requester_name']}.",
                "",
                f"Seu chamado #{ticket['id']} recebeu uma atualização.",
                "",
                "Dados do chamado:",
                f"Categoria: {ticket['category_name']}",
                f"Prioridade: {priority_label(ticket['priority'])}",
                f"Status atual: {status_label(ticket['status'])}",
                f"Título: {ticket['title']}",
                f"Responsável atual: {ticket['assigned_name'] or 'Equipe TI'}",
                "",
                "Alterações realizadas:",
                *[f"- {change}" for change in changes],
                "",
                "Acompanhe o andamento pelo link abaixo:",
                public_link,
                "",
                "Esta é uma mensagem automática da Central de chamados da TI.",
            ]
        )
    )
    slug = f"ticket-update-{ticket['id']}"
    return deliver_email(message, slug)


def update_ticket(ticket_id: int, form: dict[str, str], actor_user_id: int) -> tuple[bool, str]:
    status = form.get("status", "").strip()
    comment = form.get("comment", "").strip()
    assigned_to_raw = form.get("assigned_to_user_id", "").strip()
    assign_to_me = form.get("assign_to_me") == "1"
    if status not in VALID_TICKET_STATUSES:
        return False, "Status inválido para atualização do chamado."
    if status == "Aguardando retorno" and not comment:
        return False, "Inclua uma resposta ao solicitante ao colocar o chamado em aguardando retorno."
    with get_db() as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            return False, "Chamado não encontrado."
        assigned_to_user_id = ticket["assigned_to_user_id"]
        assigned_user_name = None
        change_messages: list[str] = []
        if assigned_to_raw:
            assigned_user = conn.execute(
                "SELECT id, name FROM users WHERE id = ? AND is_active = 1",
                (assigned_to_raw,),
            ).fetchone()
            if not assigned_user:
                return False, "Selecione um operador ativo para designar o atendimento."
            assigned_to_user_id = assigned_user["id"]
            assigned_user_name = assigned_user["name"]
        elif assign_to_me:
            assigned_to_user_id = actor_user_id
            assigned_user = conn.execute("SELECT id, name FROM users WHERE id = ?", (actor_user_id,)).fetchone()
            assigned_user_name = assigned_user["name"] if assigned_user else None
        changes_detected = (
            ticket["status"] != status
            or ticket["assigned_to_user_id"] != assigned_to_user_id
            or bool(comment)
        )
        if not changes_detected:
            return False, "Nenhuma alteração foi aplicada ao chamado."

        conn.execute(
            """
            UPDATE tickets
            SET status = ?, assigned_to_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, assigned_to_user_id, isoformat(utc_now()), ticket_id),
        )
        if ticket["status"] != status:
            change_messages.append(f"Status alterado de {status_label(ticket['status'])} para {status_label(status)}.")
            record_ticket_event(
                conn,
                ticket_id,
                "status",
                f"Status alterado de {status_label(ticket['status'])} para {status_label(status)}.",
                actor_user_id,
                ticket["status"],
                status,
            )
        if ticket["assigned_to_user_id"] != assigned_to_user_id:
            operator_name = assigned_user_name or "Equipe TI"
            change_messages.append(f"Chamado designado para {operator_name}.")
            record_ticket_event(
                conn,
                ticket_id,
                "atribuicao",
                f"Chamado designado para {operator_name}.",
                actor_user_id,
            )
        if comment:
            change_messages.append(f"Resposta da equipe: {comment}")
            record_ticket_event(conn, ticket_id, "comentario", comment, actor_user_id)
    send_ticket_update_notification(ticket_id, change_messages)
    return True, "Chamado atualizado."


def delete_ticket(ticket_id: int, actor_user_id: int) -> tuple[bool, str]:
    with get_db() as conn:
        ticket = conn.execute("SELECT id, deleted_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            return False, "Chamado não encontrado."
        if ticket["deleted_at"]:
            return False, "Chamado já está excluído."
        now = isoformat(utc_now())
        conn.execute(
            """
            UPDATE tickets
            SET deleted_at = ?, deleted_by_user_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, actor_user_id, now, ticket_id),
        )
        record_ticket_event(conn, ticket_id, "exclusao", "Chamado excluído da fila ativa.", actor_user_id)
    return True, "Chamado excluído."


def restore_ticket(ticket_id: int, actor_user_id: int) -> tuple[bool, str]:
    with get_db() as conn:
        ticket = conn.execute("SELECT id, deleted_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            return False, "Chamado não encontrado."
        if not ticket["deleted_at"]:
            return False, "Chamado não está excluído."
        conn.execute(
            """
            UPDATE tickets
            SET deleted_at = NULL, deleted_by_user_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            (isoformat(utc_now()), ticket_id),
        )
        record_ticket_event(conn, ticket_id, "restauracao", "Chamado restaurado para a fila ativa.", actor_user_id)
    return True, "Chamado restaurado."


def get_public_ticket(public_token: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT tickets.*, request_types.name AS category_name, users.name AS assigned_name
            FROM tickets
            JOIN request_types ON request_types.id = tickets.category_id
            LEFT JOIN users ON users.id = tickets.assigned_to_user_id
            WHERE tickets.public_token = ? AND tickets.deleted_at IS NULL
            """,
            (public_token,),
        ).fetchone()


def get_public_ticket_token_by_id(ticket_id: int) -> str | None:
    with get_db() as conn:
        row = conn.execute("SELECT public_token FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row["public_token"] if row else None


def get_public_ticket_events(ticket_id: int) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT created_at, event_type, message
            FROM ticket_events
            WHERE ticket_id = ? AND event_type IN ('abertura', 'status', 'comentario')
            ORDER BY created_at DESC, id DESC
            """,
            (ticket_id,),
        ).fetchall()


def purge_deleted_tickets(retention_days: int = DELETED_RETENTION_DAYS) -> int:
    cutoff = isoformat(utc_now() - timedelta(days=retention_days))
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM tickets WHERE deleted_at IS NOT NULL AND deleted_at <= ?", (cutoff,))
    return cursor.rowcount


def make_backup(test: bool = False) -> str:
    from app_config import DB_BACKEND
    if DB_BACKEND == "postgres":
        logger.info("make_backup pulado: DB_BACKEND=postgres usa pg_dump externo")
        return "skipped:postgres"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = TEST_BACKUP_DIR if test else BACKUP_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{DB_PATH.stem}-{stamp}.db"
    with get_db() as source_conn:
        target_conn = sqlite3.connect(target)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    return str(target)


def input_value(form_data: dict[str, str], field: str) -> str:
    return html.escape(form_data.get(field, ""))


def field_error(errors: dict[str, str], field: str) -> str:
    message = errors.get(field, "")
    if not message:
        return ""
    return f'<small class="field-error">{html.escape(message)}</small>'


def build_cookie(name: str, value: str, *, max_age: int | None = None, secure: bool = False, http_only: bool = True) -> str:
    parts = [f"{name}={value}", "Path=/", "SameSite=Lax"]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def parse_client_ip(handler: Any) -> str:
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if handler.client_address:
        return handler.client_address[0]
    return "unknown"
