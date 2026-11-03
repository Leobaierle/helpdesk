from __future__ import annotations

import html
import sqlite3
import traceback
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app_config import APP_ENV, BASE_DIR, CSRF_COOKIE, PORT, SESSION_COOKIE, USE_SECURE_COOKIE
from app_db import configure_logging, ensure_schema, logger
from app_services import (
    VALID_TICKET_STATUSES,
    activate_user_invite,
    authenticate_user,
    build_cookie,
    cleanup_expired_sessions,
    create_session,
    create_invited_user,
    create_ticket,
    create_user,
    delete_session,
    delete_ticket,
    field_error,
    format_datetime,
    get_public_dashboard_snapshot,
    generate_csrf_token,
    get_dashboard_data,
    get_public_ticket,
    get_public_ticket_events,
    get_public_ticket_token_by_id,
    get_password_reset,
    get_session_user,
    get_user_invite,
    get_ticket_filters,
    has_users,
    input_value,
    is_login_rate_limited,
    list_categories,
    list_ti_users,
    make_backup,
    normalize_tab,
    parse_client_ip,
    purge_deleted_tickets,
    register_login_attempt,
    request_password_reset,
    reset_user_password,
    restore_ticket,
    send_ticket_confirmation,
    send_new_ticket_operator_notification,
    update_ticket,
    update_own_password,
    update_user,
)


def flash(kind: str, message: str) -> str:
    if not message:
        return ""
    role = "alert" if kind == "error" else "status"
    return f'<p class="flash flash--{kind}" role="{role}" aria-live="polite">{html.escape(message)}</p>'


ICON_PATHS = {
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"></path>',
    "headset": '<path d="M3 11h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5Zm0 0a9 9 0 1 1 18 0m0 0v5a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3Z"></path><path d="M21 16v2a4 4 0 0 1-4 4h-5"></path>',
    "server": '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"></rect><rect width="20" height="8" x="2" y="14" rx="2" ry="2"></rect><line x1="6" x2="6.01" y1="6" y2="6"></line><line x1="6" x2="6.01" y1="18" y2="18"></line>',
    "arrow-right": '<path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path>',
    "clock": '<circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline>',
    "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><path d="m9 11 3 3L22 4"></path>',
    "log-in": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path><polyline points="10 17 15 12 10 7"></polyline><line x1="15" x2="3" y1="12" y2="12"></line>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path>',
    "sun": '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path>',
}


def theme_toggle() -> str:
    """Botao de tema no canto superior direito. O rotulo e ajustado pelo script."""
    return (
        '<button class="theme-toggle" id="tema-toggle" type="button"'
        ' aria-label="Mudar para o tema escuro" aria-pressed="false"'
        ' title="Alternar entre tema claro e escuro">'
        f'<span class="theme-toggle__moon">{icon("moon", 16)}</span>'
        f'<span class="theme-toggle__sun">{icon("sun", 16)}</span>'
        "</button>"
    )


def icon(name: str, size: int = 20) -> str:
    """SVG inline do conjunto Lucide, como no design."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        f' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f'{ICON_PATHS[name]}</svg>'
    )


def category_icon(name: str) -> str:
    """Icone e tom do cartao de categoria, por nome da categoria."""
    normalized = name.strip().lower()
    if normalized.startswith(("automacao", "automação")):
        return f'<span class="option-card__icon option-card__icon--auto">{icon("zap")}</span>'
    if normalized.startswith(("tecnico", "técnico")):
        return f'<span class="option-card__icon option-card__icon--tecnico">{icon("server")}</span>'
    return f'<span class="option-card__icon">{icon("headset")}</span>'


def initials(name: str) -> str:
    parts = [part for part in name.split() if part]
    if not parts:
        return "—"
    return "".join(part[0].upper() for part in parts[:2])


def brand_logo(subtitle: str = "Central de TI") -> str:
    return f"""
    <a class="brand-lockup" href="/">
      <img class="brand-lockup__logo" src="/assets/mark.png" alt="Logo">
      <span class="brand-lockup__text">
        <strong>Helpdesk</strong>
        <span>{html.escape(subtitle)}</span>
      </span>
    </a>
    """


def html_page(title: str, content: str, user: sqlite3.Row | None = None, csrf_token: str = "") -> str:
    if user:
        topbar = f"""
        <header class="topbar">
          <div class="topbar__inner">
            <div class="topbar__brand">
              {brand_logo("Painel do TI")}
              <span class="topbar__env">{html.escape(APP_ENV)}</span>
            </div>
            <nav class="topbar__actions">
              <a class="nav-link" href="/ti">Chamados</a>
              <a class="nav-link" href="/ti/usuarios">Usuários TI</a>
              <span class="topbar__divider"></span>
              <span class="topbar__user">{html.escape(user["name"])}</span>
              <form method="post" action="/ti/logout">
                <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                <button class="button button--ghost" type="submit">Sair</button>
              </form>
              {theme_toggle()}
            </nav>
          </div>
        </header>
        """
    else:
        topbar = f"""
        <header class="topbar">
          <div class="topbar__inner">
            <div class="topbar__brand">
              {brand_logo()}
            </div>
            <nav class="topbar__actions">
              <a class="nav-link" href="/#categorias">Abrir chamado</a>
              <a class="nav-link" href="/#dashboard">Acompanhamento</a>
              <span class="topbar__divider"></span>
              <a class="button button--ghost" href="/ti/login">{icon("log-in", 16)} Acesso do TI</a>
              {theme_toggle()}
            </nav>
          </div>
        </header>
        """
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700;800&family=Open+Sans:wght@400;500;600;700&display=swap">
  <link rel="stylesheet" href="/styles.css">
  <script>
    /* Aplica o tema salvo antes da primeira pintura, para nao piscar claro. */
    (function () {{
      try {{
        var salvo = localStorage.getItem("helpdesk-tema");
        if (salvo === "dark" || salvo === "light") {{
          document.documentElement.setAttribute("data-theme", salvo);
        }}
      }} catch (erro) {{}}
    }})();
  </script>
</head>
<body>
  <a class="skip-link" href="#conteudo">Pular para o conteúdo</a>
  <div class="site-shell">
    {topbar}
    <div id="conteudo">{content}</div>
  </div>
  <script>
    (function () {{
      var botao = document.getElementById("tema-toggle");
      if (!botao) {{ return; }}

      function temaAtual() {{
        var escolhido = document.documentElement.getAttribute("data-theme");
        if (escolhido) {{ return escolhido; }}
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      }}

      function rotular(tema) {{
        botao.setAttribute(
          "aria-label",
          tema === "dark" ? "Mudar para o tema claro" : "Mudar para o tema escuro"
        );
        botao.setAttribute("aria-pressed", tema === "dark" ? "true" : "false");
      }}

      rotular(temaAtual());

      botao.addEventListener("click", function () {{
        var novo = temaAtual() === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", novo);
        try {{ localStorage.setItem("helpdesk-tema", novo); }} catch (erro) {{}}
        rotular(novo);
      }});
    }})();
  </script>
</body>
</html>"""


def error_page(title: str, message: str, user: sqlite3.Row | None = None, csrf_token: str = "") -> str:
    return html_page(
        title,
        f"""
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Erro</p>
                <h1>{html.escape(title)}</h1>
              </div>
            </div>
            {flash("error", message)}
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def ticket_badge(status: str) -> str:
    mapping = {
        "Aberto": "tag--amber",
        "Em analise": "tag--blue",
        "Em atendimento": "tag--green",
        "Aguardando retorno": "tag--slate",
        "Concluido": "tag--green",
        "Cancelado": "tag--slate",
    }
    return mapping.get(status, "tag--blue")


def status_label(status: str) -> str:
    return {
        "Aberto": "Aberto",
        "Em analise": "Em análise",
        "Em atendimento": "Em atendimento",
        "Aguardando retorno": "Aguardando retorno",
        "Concluido": "Concluído",
        "Cancelado": "Cancelado",
    }.get(status, status)


def priority_badge(priority: str) -> str:
    return {"Alta": "tag--amber", "Media": "tag--blue", "Baixa": "tag--slate"}.get(priority, "tag--slate")


def priority_label(priority: str) -> str:
    return {"Alta": "Alta", "Media": "Média", "Baixa": "Baixa"}.get(priority, priority)


def ticket_tab_label(tab: str) -> str:
    return {
        "abertos": "Abertos",
        "andamento": "Em andamento",
        "concluidos": "Concluídos",
        "todos": "Todos",
        "excluidos": "Excluídos",
    }.get(tab, "Todos")


def public_home(
    categories: list[sqlite3.Row],
    in_progress_tickets: list[sqlite3.Row],
    completed_tickets: list[sqlite3.Row],
) -> str:
    def short_text(value: str, limit: int = 120) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def dashboard_column(title: str, subtitle: str, rows: list[sqlite3.Row], empty_message: str, done: bool = False) -> str:
        if not rows:
            items = f'<p class="dashboard-empty">{html.escape(empty_message)}</p>'
        else:
            items = "".join(
                f"""
                <article class="dashboard-ticket">
                  <div class="dashboard-ticket__head">
                    <div class="dashboard-ticket__ids">
                      <span class="ticket-id">#{row["id"]}</span>
                      <span class="tag {priority_badge(row['priority'])}">{html.escape(priority_label(row['priority']))}</span>
                      <span class="ticket-cat">{html.escape(row["category_name"])}</span>
                    </div>
                    <span class="status-pill{' status-pill--done' if row['status'] == 'Concluido' else ''}">
                      <span class="status-pill__dot"></span>{html.escape(status_label(row["status"]))}
                    </span>
                  </div>
                  <h3>{html.escape(row["title"])}</h3>
                  <p class="dashboard-ticket__summary">{html.escape(short_text(row["description"]))}</p>
                  <div class="dashboard-ticket__foot">
                    <span class="operator">
                      <span class="avatar">{html.escape(initials(row["assigned_name"] or ""))}</span>
                      {html.escape(row["assigned_name"] or "Aguardando triagem")}
                    </span>
                    <span class="updated">{icon("clock", 13)}{html.escape(format_datetime(row["updated_at"]))}</span>
                  </div>
                </article>
                """
                for row in rows
            )
        return f"""
        <section class="dashboard-column">
          <div class="dashboard-column__head">
            <div class="dashboard-column__label">
              <span class="dashboard-column__icon{' dashboard-column__icon--done' if done else ''}">{icon("check-circle" if done else "clock", 16)}</span>
              <span class="dashboard-column__titles">
                <strong>{html.escape(title)}</strong>
                <span>{html.escape(subtitle)}</span>
              </span>
            </div>
            <span class="dashboard-column__count">{len(rows)}</span>
          </div>
          <div class="dashboard-column__list">
            {items}
          </div>
        </section>
        """

    return html_page(
        "Central de Chamados TI",
        f"""
        <header class="hero hero--home">
          <div class="hero__content">
            <div class="hero__text-group">
              <p class="eyebrow">Central de chamados</p>
              <h1>Central de chamados da TI<span class="accent">.</span></h1>
              <p class="hero__text">Registre sua solicitação para automação de atividades ou suporte de TI e acompanhe o andamento em tempo real.</p>
              <div class="hero__actions">
                <a class="button button--primary" href="#categorias">Abrir chamado {icon("arrow-right", 16)}</a>
                <a class="button button--ghost" href="#dashboard">Ver acompanhamento</a>
              </div>
            </div>
            <img class="hero__mark" src="/assets/mark.png" alt="" aria-hidden="true">
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card" id="categorias">
            <div class="section-heading section-heading--stacked">
              <div>
                <p class="section-heading__eyebrow">Categorias</p>
                <h2>Selecione o tipo de atendimento</h2>
                <p class="section-heading__text">Escolha a categoria que melhor descreve sua solicitação.</p>
              </div>
            </div>
            <div class="options-grid">
              {"".join(
                  f'''
                  <a
                    class="option-card option-card--button"
                    href="/novo-chamado?category_id={row["id"]}"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {category_icon(row["name"])}
                    <span>
                      <h3>{html.escape(row["name"])}</h3>
                      <p>{html.escape(row["description"] or "")}</p>
                    </span>
                    <span class="option-card__cta">Abrir chamado {icon("arrow-right", 14)}</span>
                  </a>
                  '''
                  for row in categories
              )}
            </div>
          </section>
          <section class="section-card" id="dashboard">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Dashboard</p>
                <h2>Acompanhamento operacional</h2>
              </div>
            </div>
            <div class="dashboard-grid">
              {dashboard_column("Em andamento", "Chamados ativos", in_progress_tickets, "Nenhum chamado encontrado.")}
              {dashboard_column("Finalizados", "Últimas entregas", completed_tickets, "Nenhum chamado encontrado.", done=True)}
            </div>
          </section>
          <footer class="site-footer">
            <span>© 2026 Helpdesk</span>
            <span>Central de chamados da TI</span>
          </footer>
        </main>
        """,
    )


def public_form_page(
    categories: list[sqlite3.Row],
    csrf_token: str,
    message: str = "",
    kind: str = "info",
    form_data: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
    created_ticket_id: int | None = None,
    public_token: str | None = None,
) -> str:
    form_data = form_data or {}
    errors = errors or {}
    selected_category_id = form_data.get("category_id", "")
    selected_category = next((row for row in categories if str(row["id"]) == selected_category_id), None)
    selected_category_badge = "tag--green" if selected_category and selected_category["name"].startswith("Automacao") else "tag--blue"
    success_cta = ""
    if created_ticket_id and public_token:
        success_cta = f"""
        <div class="success-panel">
          <strong>Chamado #{created_ticket_id} registrado.</strong>
          <p>Use o botão abaixo para acompanhar o andamento do seu chamado.</p>
          <a class="button button--ghost" href="/acompanhar/{html.escape(public_token)}">Consultar chamado</a>
        </div>
        """
    return html_page(
        "Novo chamado",
        f"""
        <main class="auth-shell auth-shell--wide">
          <section class="auth-card auth-card--form">
            <img class="form-card__mark" src="/assets/mark.png" alt="" aria-hidden="true">
            <div class="section-heading section-heading--stacked">
              <div>
                <p class="section-heading__eyebrow">Formulário</p>
                <h1>Novo chamado</h1>
              </div>
              <p class="section-heading__text">{
                html.escape(f"Tipo selecionado: {selected_category['name']}. Preencha seus dados e descreva a necessidade." if selected_category else "Preencha o formulário para registrar sua solicitação.")
              }</p>
            </div>
            <div class="category-hero">
              <span class="tag {selected_category_badge}">{html.escape(selected_category['name'] if selected_category else 'Tipo não selecionado')}</span>
              <strong>{html.escape(selected_category['description'] if selected_category and selected_category['description'] else 'Preencha os campos abaixo para registrar o chamado.')}</strong>
            </div>
            {flash(kind, message)}
            {success_cta}
            <form class="ticket-form" method="post" action="/abrir-chamado" novalidate>
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <input type="hidden" name="category_id" value="{html.escape(selected_category_id)}">
              <div class="field-grid">
                <label class="field {'field--error' if errors.get('requester_name') else ''}">
                  <span>Nome</span>
                  <input name="requester_name" type="text" value="{input_value(form_data, 'requester_name')}" required aria-invalid="{'true' if errors.get('requester_name') else 'false'}">
                  {field_error(errors, 'requester_name')}
                </label>
                <label class="field {'field--error' if errors.get('requester_email') else ''}">
                  <span>E-mail</span>
                  <input name="requester_email" type="email" value="{input_value(form_data, 'requester_email')}" required aria-invalid="{'true' if errors.get('requester_email') else 'false'}">
                  {field_error(errors, 'requester_email')}
                </label>
                <label class="field {'field--error' if errors.get('requester_department') else ''}">
                  <span>Setor</span>
                  <input name="requester_department" type="text" value="{input_value(form_data, 'requester_department')}" required aria-invalid="{'true' if errors.get('requester_department') else 'false'}">
                  {field_error(errors, 'requester_department')}
                </label>
                <label class="field {'field--error' if errors.get('priority') else ''}">
                  <span>Prioridade</span>
                  <select name="priority" required aria-invalid="{'true' if errors.get('priority') else 'false'}">
                    <option value="">Selecione</option>
                    <option value="Baixa" {"selected" if form_data.get("priority") == "Baixa" else ""}>Baixa</option>
                    <option value="Media" {"selected" if form_data.get("priority") == "Media" else ""}>Média</option>
                    <option value="Alta" {"selected" if form_data.get("priority") == "Alta" else ""}>Alta</option>
                  </select>
                  {field_error(errors, 'priority')}
                </label>
              </div>
              <div class="selected-category-line">
                <strong>Tipo de atendimento:</strong>
                <span>{html.escape(selected_category['name'] if selected_category else "Não selecionado")}</span>
                {field_error(errors, 'category_id')}
              </div>
              <label class="field {'field--error' if errors.get('title') else ''}">
                <span>Título do chamado</span>
                <input name="title" type="text" value="{input_value(form_data, 'title')}" required aria-invalid="{'true' if errors.get('title') else 'false'}">
                {field_error(errors, 'title')}
              </label>
              <label class="field {'field--error' if errors.get('description') else ''}">
                <span>Descrição</span>
                <textarea name="description" rows="6" required aria-invalid="{'true' if errors.get('description') else 'false'}">{input_value(form_data, 'description')}</textarea>
                {field_error(errors, 'description')}
              </label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Enviar chamado</button>
              </div>
            </form>
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def auth_page(title: str, subtitle: str, form_html: str, csrf_token: str, message: str = "", kind: str = "info") -> str:
    return html_page(
        title,
        f"""
        <main class="auth-shell">
          <section class="auth-card">
            <div class="eyebrow">Área interna TI</div>
            <h1>{html.escape(title)}</h1>
            <p class="hero__text auth-card__text">{html.escape(subtitle)}</p>
            {flash(kind, message)}
            {form_html}
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def public_ticket_page(ticket: sqlite3.Row, events: list[sqlite3.Row], csrf_token: str) -> str:
    allowed_events = "".join(
        f"""
        <li class="timeline-item">
          <strong>{html.escape(row['message'])}</strong>
          <span>{html.escape(format_datetime(row['created_at']))}</span>
        </li>
        """
        for row in events
    ) or '<li class="timeline-item"><strong>Nenhuma atualização registrada ainda.</strong></li>'
    return html_page(
        f"Chamado #{ticket['id']}",
        f"""
        <header class="hero hero--request">
          <div class="hero__content">
            <div class="eyebrow">Acompanhamento público</div>
            <h1>Chamado #{ticket['id']}</h1>
            <p class="hero__text">Consulte aqui o andamento do seu atendimento sem acessar a área interna do TI.</p>
          </div>
        </header>
        <main class="content-flow">
          <section class="section-card">
            <div class="ticket-grid ticket-grid--public">
              <div><strong>Status</strong><span><span class="tag {ticket_badge(ticket['status'])}">{html.escape(status_label(ticket['status']))}</span></span></div>
              <div><strong>Categoria</strong><span>{html.escape(ticket['category_name'])}</span></div>
              <div><strong>Prioridade</strong><span>{html.escape(priority_label(ticket['priority']))}</span></div>
              <div><strong>Abertura</strong><span>{html.escape(format_datetime(ticket['created_at']))}</span></div>
              <div><strong>Última atualização</strong><span>{html.escape(format_datetime(ticket['updated_at']))}</span></div>
              <div><strong>Responsável</strong><span>{html.escape(ticket['assigned_name'] or 'Equipe TI')}</span></div>
            </div>
            <h2>{html.escape(ticket['title'])}</h2>
            <p class="ticket-description">{html.escape(ticket['description'])}</p>
          </section>
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Andamento</p>
                <h2>Histórico visível</h2>
              </div>
            </div>
            <ul class="timeline">{allowed_events}</ul>
          </section>
        </main>
        """,
        csrf_token=csrf_token,
    )


def render_ti_dashboard(
    user: sqlite3.Row,
    tickets: list[sqlite3.Row],
    events_by_ticket: dict[int, list[sqlite3.Row]],
    summary: sqlite3.Row,
    active_tab: str,
    csrf_token: str,
    categories: list[sqlite3.Row],
    users: list[sqlite3.Row],
    filters: dict[str, str],
    message: str = "",
    kind: str = "info",
) -> str:
    tab_items = [
        ("abertos", summary["open_count"]),
        ("andamento", summary["progress_count"]),
        ("concluidos", summary["done_count"]),
        ("todos", summary["total_count"]),
        ("excluidos", summary["deleted_count"]),
    ]
    rows = []
    for row in tickets:
        options_status = "".join(
            f'<option value="{value}" {"selected" if row["status"] == value else ""}>{status_label(value)}</option>'
            for value in VALID_TICKET_STATUSES
        )
        history = "".join(
            f"""
            <li class="timeline-item">
              <strong>{html.escape(event['message'])}</strong>
              <span>{html.escape(event['actor_name'] or 'Sistema')} • {html.escape(format_datetime(event['created_at']))}</span>
            </li>
            """
            for event in events_by_ticket.get(row["id"], [])[:6]
        ) or '<li class="timeline-item"><strong>Nenhuma movimentação registrada.</strong></li>'
        if active_tab == "excluidos":
            action_block = f"""
              <form class="ticket-actions" method="post" action="/ti/chamados/{row['id']}/restaurar">
                <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                <input type="hidden" name="active_tab" value="{html.escape(active_tab)}">
                <input type="hidden" name="category_id" value="{html.escape(filters.get('category_id', ''))}">
                <input type="hidden" name="assigned_to" value="{html.escape(filters.get('assigned_to', ''))}">
                <input type="hidden" name="q" value="{html.escape(filters.get('q', ''))}">
                <input type="hidden" name="sort" value="{html.escape(filters.get('sort', 'recentes'))}">
                <p class="help-text">Este chamado fica preservado por até 30 dias antes da remoção definitiva.</p>
                <div class="form-actions">
                  <button class="button button--primary" type="submit">Restaurar chamado</button>
                </div>
              </form>
            """
            delete_meta = f"""
                <div><strong>Excluído por</strong><span>{html.escape(row['deleted_by_name'] or 'Sistema')}</span></div>
                <div><strong>Data da exclusão</strong><span>{html.escape(format_datetime(row['deleted_at']))}</span></div>
            """
        else:
            operator_options = '<option value="">Selecione um operador</option>' + "".join(
                f'<option value="{operator["id"]}" {"selected" if row["assigned_to_user_id"] == operator["id"] else ""}>{html.escape(operator["name"])}</option>'
                for operator in users
            )
            action_block = f"""
              <form class="ticket-actions" method="post" action="/ti/chamados/{row['id']}/atualizar">
                <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
                <input type="hidden" name="active_tab" value="{html.escape(active_tab)}">
                <input type="hidden" name="category_id" value="{html.escape(filters.get('category_id', ''))}">
                <input type="hidden" name="assigned_to" value="{html.escape(filters.get('assigned_to', ''))}">
                <input type="hidden" name="q" value="{html.escape(filters.get('q', ''))}">
                <input type="hidden" name="sort" value="{html.escape(filters.get('sort', 'recentes'))}">
                <label><span>Status</span><select name="status">{options_status}</select></label>
                <label><span>Resposta ao solicitante</span><textarea name="comment" rows="3" placeholder="Escreva a devolutiva que ficará visível na consulta pública do chamado"></textarea></label>
                <p class="help-text">Regra operacional: `Aguardando retorno` exige resposta ao solicitante.</p>
                <div class="form-actions">
                  <button class="button button--primary" type="submit">Salvar</button>
                  <details class="assign-disclosure">
                    <summary class="button button--ghost">Designar atendimento</summary>
                    <div class="assign-disclosure__panel">
                      <label>
                        <span>Operador</span>
                        <select name="assigned_to_user_id">{operator_options}</select>
                      </label>
                    </div>
                  </details>
                  <a class="button button--ghost" href="/acompanhar/{html.escape(row['public_token'])}" target="_blank" rel="noreferrer">Consulta pública</a>
                  <button class="button button--danger" type="submit" formaction="/ti/chamados/{row['id']}/excluir" onclick="return window.confirm('Tem certeza que deseja excluir este chamado?');">Excluir chamado</button>
                </div>
              </form>
            """
            delete_meta = ""
        rows.append(
            f"""
            <article class="ticket-row">
              <div class="ticket-row__head">
                <div>
                  <div class="ticket-row__badges">
                    <span class="tag {ticket_badge(row['status'])}">{html.escape(status_label(row['status']))}</span>
                    <span class="tag {priority_badge(row['priority'])}">Prioridade {html.escape(priority_label(row['priority']))}</span>
                  </div>
                  <h3>{html.escape(row['title'])}</h3>
                </div>
                <div class="ticket-row__meta">
                  <span>#{row['id']}</span>
                  <span>{html.escape(format_datetime(row['created_at']))}</span>
                </div>
              </div>
              <p class="ticket-description">{html.escape(row['description'])}</p>
              <div class="ticket-grid">
                <div><strong>Solicitante</strong><span>{html.escape(row['requester_name'])}</span></div>
                <div><strong>E-mail</strong><span>{html.escape(row['requester_email'])}</span></div>
                <div><strong>Setor</strong><span>{html.escape(row['requester_department'])}</span></div>
                <div><strong>Categoria</strong><span>{html.escape(row['category_name'])}</span></div>
                <div><strong>Responsável</strong><span>{html.escape(row['assigned_name'] or 'Não atribuído')}</span></div>
                <div><strong>Última atualização</strong><span>{html.escape(format_datetime(row['updated_at']))}</span></div>
                {delete_meta}
              </div>
              {action_block}
              <div class="ticket-history">
                <h4>Histórico</h4>
                <ul class="timeline">{history}</ul>
              </div>
            </article>
            """
        )
    tabs = "".join(
        f'<a class="tab-link {"tab-link--active" if active_tab == key else ""}" href="/ti?tab={key}&category_id={html.escape(filters.get("category_id", ""))}&assigned_to={html.escape(filters.get("assigned_to", ""))}&q={html.escape(filters.get("q", ""))}&sort={html.escape(filters.get("sort", "recentes"))}">{ticket_tab_label(key)} <span>{count or 0}</span></a>'
        for key, count in tab_items
    )
    category_options = '<option value="">Todas</option>' + "".join(
        f'<option value="{row["id"]}" {"selected" if filters.get("category_id") == str(row["id"]) else ""}>{html.escape(row["name"])}</option>'
        for row in categories
    )
    assigned_options = '<option value="">Todos</option><option value="none" ' + ('selected' if filters.get("assigned_to") == "none" else '') + '>Sem responsável</option>' + "".join(
        f'<option value="{row["id"]}" {"selected" if filters.get("assigned_to") == str(row["id"]) else ""}>{html.escape(row["name"])}</option>'
        for row in users
    )
    return html_page(
        "Painel TI",
        f"""
        <header class="hero hero--dashboard">
          <div class="hero__content">
            <div class="eyebrow">Painel TI</div>
            <div class="hero__topline">
              <span>Área restrita</span>
              <span>Usuário: {html.escape(user['name'])}</span>
            </div>
            <h1>Gestão de chamados.</h1>
            <p class="hero__text">Filtre a fila, altere o status, designe atendimentos e acompanhe o histórico de cada chamado.</p>
          </div>
          <aside class="hero__panel">
            <div class="hero__metric"><strong>{summary['open_count'] or 0}</strong><span>Chamados abertos</span></div>
            <div class="hero__metric"><strong>{summary['progress_count'] or 0}</strong><span>Em andamento</span></div>
            <div class="hero__metric"><strong>{summary['done_count'] or 0}</strong><span>Concluídos</span></div>
          </aside>
        </header>
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Filtros</p>
                <h2>Fila do TI</h2>
              </div>
            </div>
            {flash(kind, message)}
            <form class="filter-form" method="get" action="/ti">
              <input type="hidden" name="tab" value="{html.escape(active_tab)}">
              <label><span>Categoria</span><select name="category_id">{category_options}</select></label>
              <label><span>Responsável</span><select name="assigned_to">{assigned_options}</select></label>
              <label><span>Busca</span><input name="q" type="text" value="{html.escape(filters.get('q', ''))}" placeholder="Nome, título ou número"></label>
              <label><span>Ordenação</span>
                <select name="sort">
                  <option value="recentes" {"selected" if filters.get("sort") == "recentes" else ""}>Mais recentes</option>
                  <option value="antigos" {"selected" if filters.get("sort") == "antigos" else ""}>Mais antigos</option>
                  <option value="prioridade_alta" {"selected" if filters.get("sort") == "prioridade_alta" else ""}>Prioridade alta</option>
                  <option value="prioridade_baixa" {"selected" if filters.get("sort") == "prioridade_baixa" else ""}>Prioridade baixa</option>
                </select>
              </label>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Aplicar filtros</button>
                <a class="button button--ghost" href="/ti?tab={html.escape(active_tab)}">Limpar</a>
              </div>
            </form>
            <div class="tabs-bar">{tabs}</div>
            <div class="ticket-list">
              {''.join(rows) if rows else '<p class="section-heading__text">Nenhum chamado encontrado para os filtros atuais.</p>'}
            </div>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def render_ti_users(user: sqlite3.Row, users: list[sqlite3.Row], csrf_token: str, message: str = "", kind: str = "info") -> str:
    cards = "".join(
        f"""
        <article class="activity-card">
          <span class="tag tag--blue">{html.escape(row['role'])}</span>
          <h3>{html.escape(row['name'])}</h3>
          <p>{html.escape(row['email'])}</p>
          <dl class="meta-list">
            <div><dt>Setor</dt><dd>{html.escape(row['department'] or '-')}</dd></div>
            <div><dt>Status</dt><dd>{'Ativo' if row['is_active'] else 'Inativo'}</dd></div>
            <div><dt>Atualizado</dt><dd>{html.escape(format_datetime(row['updated_at']))}</dd></div>
          </dl>
          <form class="ticket-form user-edit-form" method="post" action="/ti/usuarios/{row['id']}/atualizar">
            <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
            <label><span>Nome</span><input name="name" type="text" value="{html.escape(row['name'])}" required></label>
            <label><span>E-mail</span><input name="email" type="email" value="{html.escape(row['email'])}" required></label>
            <label><span>Setor</span><input name="department" type="text" value="{html.escape(row['department'] or 'TI')}"></label>
            <input name="password" type="hidden" value="">
            <label><span>Status</span>
              <select name="is_active">
                <option value="1" {"selected" if row['is_active'] else ""}>Ativo</option>
                <option value="0" {"selected" if not row['is_active'] else ""}>Inativo</option>
              </select>
            </label>
            <div class="form-actions">
              <button class="button button--primary" type="submit">Salvar usuário</button>
            </div>
          </form>
        </article>
        """
        for row in users
    )
    return html_page(
        "Usuários TI",
        f"""
        <main class="content-flow">
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Usuários TI</p>
                <h2>Gerenciar acessos</h2>
              </div>
              <p class="section-heading__text">Cadastre nome, e-mail e setor. O novo usuário recebe um link por e-mail para definir a própria senha.</p>
            </div>
            {flash(kind, message)}
            <form class="ticket-form" method="post" action="/ti/usuarios">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <div class="field-grid">
                <label><span>Nome</span><input name="name" type="text" required></label>
                <label><span>E-mail</span><input name="email" type="email" required></label>
                <label><span>Setor</span><input name="department" type="text" value="TI"></label>
              </div>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Adicionar usuário TI</button>
              </div>
            </form>
          </section>
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Equipe</p>
                <h2>Acessos cadastrados</h2>
              </div>
            </div>
            <div class="activity-grid">{cards}</div>
          </section>
          <section class="section-card">
            <div class="section-heading">
              <div>
                <p class="section-heading__eyebrow">Minha senha</p>
                <h2>Alterar senha do usuário atual</h2>
              </div>
              <p class="section-heading__text">Por segurança, apenas cada usuário pode alterar a própria senha.</p>
            </div>
            <form class="ticket-form" method="post" action="/ti/minha-senha">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <div class="field-grid">
                <label><span>Nova senha</span><input name="password" type="password" required></label>
                <label><span>Confirmar nova senha</span><input name="password_confirm" type="password" required></label>
              </div>
              <div class="form-actions">
                <button class="button button--primary" type="submit">Atualizar minha senha</button>
              </div>
            </form>
          </section>
        </main>
        """,
        user,
        csrf_token,
    )


def activation_password_page(invite: sqlite3.Row | None, token: str, csrf_token: str, message: str = "", kind: str = "info") -> str:
    if not invite:
        return auth_page(
            "Ativação de acesso",
            "Esse link não é válido ou não está mais disponível.",
            "",
            csrf_token,
            "Não foi possível localizar um convite ativo para esse acesso.",
            "error",
        )

    form = f"""
    <form class="ticket-form" method="post" action="/ti/ativar/{html.escape(token)}">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
      <label><span>Nome</span><input type="text" value="{html.escape(invite['name'])}" disabled></label>
      <label><span>E-mail</span><input type="email" value="{html.escape(invite['email'])}" disabled></label>
      <label><span>Nova senha</span><input name="password" type="password" required></label>
      <label><span>Confirmar nova senha</span><input name="password_confirm" type="password" required></label>
      <div class="form-actions">
        <button class="button button--primary" type="submit">Definir senha</button>
        <a class="button button--ghost" href="/ti/login">Voltar ao login</a>
      </div>
    </form>
    """
    return auth_page(
        "Ativação de acesso",
        "Defina sua senha para concluir o acesso à área interna do Helpdesk.",
        form,
        csrf_token,
        message,
        kind,
    )


def forgot_password_page(csrf_token: str, message: str = "", kind: str = "info") -> str:
    form = f"""
    <form class="ticket-form" method="post" action="/ti/esqueci-minha-senha">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
      <label><span>E-mail</span><input name="email" type="email" required></label>
      <div class="form-actions">
        <button class="button button--primary" type="submit">Enviar link de redefinição</button>
        <a class="button button--ghost" href="/ti/login">Voltar ao login</a>
      </div>
    </form>
    """
    return auth_page(
        "Esqueci minha senha",
        "Informe o seu e-mail para receber o link de redefinição de senha.",
        form,
        csrf_token,
        message,
        kind,
    )


def reset_password_page(reset_row: sqlite3.Row | None, token: str, csrf_token: str, message: str = "", kind: str = "info") -> str:
    if not reset_row:
        return auth_page(
            "Redefinir senha",
            "Esse link não é válido ou não está mais disponível.",
            "",
            csrf_token,
            "Não foi possível localizar uma redefinição de senha ativa para esse acesso.",
            "error",
        )

    form = f"""
    <form class="ticket-form" method="post" action="/ti/redefinir-senha/{html.escape(token)}">
      <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
      <label><span>E-mail</span><input type="email" value="{html.escape(reset_row['email'])}" disabled></label>
      <label><span>Nova senha</span><input name="password" type="password" required></label>
      <label><span>Confirmar nova senha</span><input name="password_confirm" type="password" required></label>
      <div class="form-actions">
        <button class="button button--primary" type="submit">Redefinir senha</button>
        <a class="button button--ghost" href="/ti/login">Voltar ao login</a>
      </div>
    </form>
    """
    return auth_page(
        "Redefinir senha",
        "Cadastre uma nova senha para o seu acesso ao Helpdesk.",
        form,
        csrf_token,
        message,
        kind,
    )


class AppHandler(BaseHTTPRequestHandler):
    server_version = "Projeto1HTTP/1.0"

    def do_GET(self) -> None:
        try:
            self.route_get()
        except Exception:
            self.handle_unexpected_error("GET", self.path)

    def do_POST(self) -> None:
        try:
            self.route_post()
        except Exception:
            self.handle_unexpected_error("POST", self.path)

    def do_PUT(self) -> None:
        self.handle_method_not_allowed()

    def do_DELETE(self) -> None:
        self.handle_method_not_allowed()

    def do_PATCH(self) -> None:
        self.handle_method_not_allowed()

    def route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/styles.css":
            return self.serve_static("styles.css", "text/css; charset=utf-8")
        if path == "/assets/logo.png":
            return self.serve_static_binary("assets/logo.png", "image/png")
        if path == "/assets/mark.png":
            return self.serve_static_binary("assets/mark.png", "image/png")
        if path == "/":
            return self.handle_public_home()
        if path == "/novo-chamado":
            params = parse_qs(parsed.query)
            return self.handle_public_form(params)
        if path == "/ti/login":
            return self.handle_login_page()
        if path == "/ti/esqueci-minha-senha":
            return self.handle_forgot_password_page()
        if path == "/ti/setup":
            return self.handle_setup_page()
        if path == "/ti":
            params = parse_qs(parsed.query)
            return self.handle_ti_dashboard(params)
        if path == "/ti/usuarios":
            return self.handle_ti_users()
        if path.startswith("/ti/ativar/"):
            return self.handle_user_activation_page(path.strip("/").split("/", 2)[2])
        if path.startswith("/ti/redefinir-senha/"):
            return self.handle_reset_password_page(path.strip("/").split("/", 2)[2])
        if path.startswith("/acompanhar/"):
            return self.handle_public_ticket(path.strip("/").split("/", 1)[1])
        self.handle_not_found()

    def route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/abrir-chamado":
            return self.handle_ticket_submit()
        if path == "/ti/login":
            return self.handle_login_submit()
        if path == "/ti/logout":
            return self.handle_logout()
        if path == "/ti/esqueci-minha-senha":
            return self.handle_forgot_password_submit()
        if path == "/ti/setup":
            return self.handle_setup_submit()
        if path == "/ti/usuarios":
            return self.handle_ti_users_submit()
        if path == "/ti/minha-senha":
            return self.handle_own_password_submit()
        if path.startswith("/ti/ativar/"):
            return self.handle_user_activation_submit(path.strip("/").split("/", 2)[2])
        if path.startswith("/ti/redefinir-senha/"):
            return self.handle_reset_password_submit(path.strip("/").split("/", 2)[2])
        if path.startswith("/ti/usuarios/") and path.endswith("/atualizar"):
            return self.handle_ti_user_update(path)
        if path.startswith("/ti/chamados/") and path.endswith("/excluir"):
            return self.handle_ti_ticket_delete(path)
        if path.startswith("/ti/chamados/") and path.endswith("/restaurar"):
            return self.handle_ti_ticket_restore(path)
        if path.startswith("/ti/chamados/") and path.endswith("/atualizar"):
            return self.handle_ti_ticket_update(path)
        self.handle_not_found()

    def add_common_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'self'")

    def serve_static(self, file_name: str, content_type: str) -> None:
        with open(BASE_DIR / file_name, "r", encoding="utf-8") as file:
            body = file.read()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def serve_static_binary(self, file_name: str, content_type: str) -> None:
        with open(BASE_DIR / file_name, "rb") as file:
            body = file.read()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def parse_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        data = parse_qs(raw)
        return {key: values[0].strip() for key, values in data.items()}

    def get_cookie_value(self, key: str) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        jar = cookies.SimpleCookie()
        jar.load(header)
        value = jar.get(key)
        return value.value if value else None

    def ensure_csrf(self) -> tuple[str, str | None]:
        token = self.get_cookie_value(CSRF_COOKIE) or generate_csrf_token()
        cookie_header = None
        if self.get_cookie_value(CSRF_COOKIE) != token:
            cookie_header = build_cookie(CSRF_COOKIE, token, secure=USE_SECURE_COOKIE, http_only=False)
        return token, cookie_header

    def verify_csrf(self, form: dict[str, str]) -> bool:
        return bool(form.get("csrf_token") and self.get_cookie_value(CSRF_COOKIE) == form.get("csrf_token"))

    def respond_html(self, body: str, status: int = 200, set_cookie: str | list[str] | None = None) -> None:
        self.send_response(status)
        self.add_common_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if set_cookie:
            cookies_to_set = set_cookie if isinstance(set_cookie, list) else [set_cookie]
            for cookie in cookies_to_set:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def redirect(self, location: str, set_cookie: str | list[str] | None = None) -> None:
        self.send_response(302)
        self.add_common_headers()
        self.send_header("Location", location)
        if set_cookie:
            cookies_to_set = set_cookie if isinstance(set_cookie, list) else [set_cookie]
            for cookie in cookies_to_set:
                self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def current_user(self) -> sqlite3.Row | None:
        return get_session_user(self.get_cookie_value(SESSION_COOKIE))

    def require_auth(self) -> sqlite3.Row | None:
        user = self.current_user()
        if not user:
            self.redirect("/ti/login")
            return None
        return user

    def handle_not_found(self) -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(error_page("Página não encontrada", "A rota informada não existe no sistema.", csrf_token=csrf_token), 404, csrf_cookie)

    def handle_method_not_allowed(self) -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(error_page("Método não permitido", "Esse método HTTP não é aceito para esta rota.", csrf_token=csrf_token), 405, csrf_cookie)

    def log_exception(self, context: str) -> None:
        logger.error("%s\n%s", context, traceback.format_exc())

    def handle_unexpected_error(self, method: str, path: str) -> None:
        self.log_exception(f"Erro inesperado em {method} {path}")
        csrf_token, csrf_cookie = self.ensure_csrf()
        try:
            self.respond_html(error_page("Falha interna", "Ocorreu um erro inesperado. Tente novamente em instantes.", csrf_token=csrf_token), 500, csrf_cookie)
        except BrokenPipeError:
            pass

    def handle_public_home(self) -> None:
        in_progress_tickets, completed_tickets = get_public_dashboard_snapshot()
        self.respond_html(public_home(list_categories(), in_progress_tickets, completed_tickets))

    def handle_public_form(self, params: dict[str, list[str]] | None = None, message: str = "", kind: str = "info", form_data: dict[str, str] | None = None, errors: dict[str, str] | None = None, created_ticket_id: int | None = None, public_token: str | None = None) -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        form_data = dict(form_data or {})
        selected_category_id = (params or {}).get("category_id", [""])[0].strip()
        if selected_category_id and not form_data.get("category_id"):
            form_data["category_id"] = selected_category_id
        self.respond_html(public_form_page(list_categories(), csrf_token, message, kind, form_data, errors, created_ticket_id, public_token), set_cookie=csrf_cookie)

    def handle_ticket_submit(self) -> None:
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_public_form(message="Não foi possível validar a solicitação. Atualize a página e tente novamente.", kind="error", form_data=form)
        normalized, errors, ticket_id = create_ticket(form)
        if errors:
            return self.handle_public_form(message="Revise os campos destacados e tente novamente.", kind="error", form_data=normalized, errors=errors)
        public_token = get_public_ticket_token_by_id(ticket_id) if ticket_id else None
        if ticket_id:
            send_ticket_confirmation(ticket_id)
            send_new_ticket_operator_notification(ticket_id)
        self.handle_public_form(
            params={"category_id": [form.get("category_id", "")]},
            message="Chamado enviado com sucesso. O TI vai acompanhar a solicitação.",
            kind="success",
            form_data={},
            errors={},
            created_ticket_id=ticket_id,
            public_token=public_token,
        )

    def handle_login_page(self, message: str = "", kind: str = "info") -> None:
        if self.current_user():
            return self.redirect("/ti")
        csrf_token, csrf_cookie = self.ensure_csrf()
        form = f"""
        <form class="ticket-form" method="post" action="/ti/login">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          <label><span>E-mail</span><input name="email" type="email" required></label>
          <label><span>Senha</span><input name="password" type="password" required></label>
          <div class="form-actions">
            <button class="button button--primary" type="submit">Entrar</button>
            <a class="button button--ghost" href="/ti/esqueci-minha-senha">Esqueci minha senha</a>
          </div>
        </form>
        """
        self.respond_html(auth_page("Login TI", "Acesso interno para acompanhamento e atendimento dos chamados.", form, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_forgot_password_page(self, message: str = "", kind: str = "info") -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(forgot_password_page(csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_forgot_password_submit(self) -> None:
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_forgot_password_page("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        _, errors, delivery = request_password_reset(form)
        if errors:
            return self.handle_forgot_password_page(next(iter(errors.values())), "error")
        channel = "e-mail" if delivery == "smtp" else "outbox"
        self.handle_forgot_password_page(
            f"Se o e-mail informado estiver cadastrado e ativo, o link de redefinição foi enviado por {channel}.",
            "success",
        )

    def handle_login_submit(self) -> None:
        form = self.parse_form()
        ip = parse_client_ip(self)
        if not self.verify_csrf(form):
            return self.handle_login_page("Sessão do formulário expirada. Atualize a tela e tente novamente.", "error")
        if is_login_rate_limited(ip):
            logger.warning("Login bloqueado por rate limit para IP %s", ip)
            return self.handle_login_page("Não foi possível concluir o login neste momento. Aguarde alguns minutos e tente novamente.", "error")
        user = authenticate_user(form.get("email", "").lower(), form.get("password", ""))
        if not user:
            register_login_attempt(ip, success=False)
            return self.handle_login_page("Não foi possível concluir o login neste momento. Verifique suas credenciais.", "error")
        register_login_attempt(ip, success=True)
        token = create_session(user["id"])
        session_cookie = build_cookie(SESSION_COOKIE, token, secure=USE_SECURE_COOKIE)
        csrf_cookie = build_cookie(CSRF_COOKIE, generate_csrf_token(), secure=USE_SECURE_COOKIE, http_only=False)
        self.redirect("/ti", set_cookie=[session_cookie, csrf_cookie])

    def handle_logout(self) -> None:
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_login_page("Sessão expirada. Atualize a página e tente novamente.", "error")
        delete_session(self.get_cookie_value(SESSION_COOKIE))
        self.redirect(
            "/ti/login",
            set_cookie=[
                build_cookie(SESSION_COOKIE, "", max_age=0, secure=USE_SECURE_COOKIE),
                build_cookie(CSRF_COOKIE, "", max_age=0, secure=USE_SECURE_COOKIE, http_only=False),
            ],
        )

    def handle_setup_page(self, message: str = "", kind: str = "info") -> None:
        if has_users() and not self.require_auth():
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        has_existing_users = has_users()
        title = "Criar acesso TI" if has_existing_users else "Primeiro acesso TI"
        subtitle = "Crie um novo usuário interno do setor." if has_existing_users else "Crie o primeiro usuário interno do setor."
        button_label = "Criar acesso TI" if has_existing_users else "Criar primeiro acesso"
        form = f"""
        <form class="ticket-form" method="post" action="/ti/setup">
          <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
          <label><span>Nome</span><input name="name" type="text" required></label>
          <label><span>E-mail</span><input name="email" type="email" required></label>
          <label><span>Setor</span><input name="department" type="text" value="TI"></label>
          <label><span>Senha</span><input name="password" type="password" required></label>
          <div class="form-actions">
            <button class="button button--primary" type="submit">{button_label}</button>
          </div>
        </form>
        """
        self.respond_html(auth_page(title, subtitle, form, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_setup_submit(self) -> None:
        if has_users() and not self.require_auth():
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_setup_page("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        is_first_user = not has_users()
        _, errors = create_user(form, initial_setup=is_first_user)
        if errors:
            return self.handle_setup_page(next(iter(errors.values())), "error")
        if is_first_user:
            return self.handle_login_page("Primeiro usuário TI criado. Entre com suas credenciais.", "success")
        self.handle_login_page("Usuário TI criado. Entre com suas credenciais.", "success")

    def handle_reset_password_page(self, token: str, message: str = "", kind: str = "info") -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        reset_row = get_password_reset(token)
        self.respond_html(reset_password_page(reset_row, token, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_reset_password_submit(self, token: str) -> None:
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_reset_password_page(token, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        success, message = reset_user_password(token, form)
        if success:
            return self.handle_login_page(message, "success")
        self.handle_reset_password_page(token, message, "error")

    def handle_ti_dashboard(self, params: dict[str, list[str]], message: str = "", kind: str = "info") -> None:
        user = self.require_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        active_tab = normalize_tab(params.get("tab", ["abertos"])[0])
        filters = get_ticket_filters({
            "category_id": params.get("category_id", [""])[0],
            "assigned_to": params.get("assigned_to", [""])[0],
            "q": params.get("q", [""])[0],
            "sort": params.get("sort", ["recentes"])[0],
        })
        tickets, summary, events_by_ticket = get_dashboard_data(active_tab, filters)
        self.respond_html(
            render_ti_dashboard(user, tickets, events_by_ticket, summary, active_tab, csrf_token, list_categories(), list_ti_users(active_only=True), filters, message, kind),
            set_cookie=csrf_cookie,
        )

    def handle_ti_ticket_update(self, path: str) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = update_ticket(ticket_id, form, user["id"])
        params = {
            "tab": [normalize_tab(form.get("active_tab", "todos"))],
            "category_id": [form.get("category_id", "")],
            "assigned_to": [form.get("assigned_to", "")],
            "q": [form.get("q", "")],
            "sort": [form.get("sort", "recentes")],
        }
        self.handle_ti_dashboard(params, msg, "success" if success else "error" if "expirada" not in msg and "Nenhuma" not in msg else "info")

    def handle_ti_ticket_delete(self, path: str) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = delete_ticket(ticket_id, user["id"])
        params = {
            "tab": [normalize_tab(form.get("active_tab", "todos"))],
            "category_id": [form.get("category_id", "")],
            "assigned_to": [form.get("assigned_to", "")],
            "q": [form.get("q", "")],
            "sort": [form.get("sort", "recentes")],
        }
        self.handle_ti_dashboard(params, msg, "success" if success else "error")

    def handle_ti_ticket_restore(self, path: str) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            ticket_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_dashboard({"tab": [normalize_tab(form.get("active_tab", "todos"))]}, "Chamado inválido.", "error")
        success, msg = restore_ticket(ticket_id, user["id"])
        params = {
            "tab": [normalize_tab(form.get("active_tab", "todos"))],
            "category_id": [form.get("category_id", "")],
            "assigned_to": [form.get("assigned_to", "")],
            "q": [form.get("q", "")],
            "sort": [form.get("sort", "recentes")],
        }
        self.handle_ti_dashboard(params, msg, "success" if success else "error")

    def handle_ti_users(self, message: str = "", kind: str = "info") -> None:
        user = self.require_auth()
        if not user:
            return
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(render_ti_users(user, list_ti_users(), csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_ti_users_submit(self) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_users("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        _, errors, delivery = create_invited_user(form, invited_by_user_id=user["id"])
        if errors:
            return self.handle_ti_users(next(iter(errors.values())), "error")
        channel = "e-mail" if delivery == "smtp" else "outbox"
        self.handle_ti_users(f"Usuário TI criado com sucesso. O link para definir a senha foi enviado por {channel}.", "success")

    def handle_ti_user_update(self, path: str) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_users("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        try:
            user_id = int(path.strip("/").split("/")[2])
        except ValueError:
            return self.handle_ti_users("Usuário inválido.", "error")
        success, message = update_user(user_id, form, user["id"])
        self.handle_ti_users(message, "success" if success else "error")

    def handle_own_password_submit(self) -> None:
        user = self.require_auth()
        if not user:
            return
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_ti_users("Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        success, message = update_own_password(user["id"], form)
        self.handle_ti_users(message, "success" if success else "error")

    def handle_user_activation_page(self, token: str, message: str = "", kind: str = "info") -> None:
        csrf_token, csrf_cookie = self.ensure_csrf()
        invite = get_user_invite(token)
        self.respond_html(activation_password_page(invite, token, csrf_token, message, kind), set_cookie=csrf_cookie)

    def handle_user_activation_submit(self, token: str) -> None:
        form = self.parse_form()
        if not self.verify_csrf(form):
            return self.handle_user_activation_page(token, "Sessão do formulário expirada. Atualize a página e tente novamente.", "error")
        success, message = activate_user_invite(token, form)
        if success:
            return self.handle_login_page(message, "success")
        self.handle_user_activation_page(token, message, "error")

    def handle_public_ticket(self, public_token: str) -> None:
        ticket = get_public_ticket(public_token)
        if not ticket:
            return self.handle_not_found()
        csrf_token, csrf_cookie = self.ensure_csrf()
        self.respond_html(public_ticket_page(ticket, get_public_ticket_events(ticket["id"]), csrf_token), set_cookie=csrf_cookie)

def main() -> None:
    configure_logging()
    ensure_schema()
    cleanup_expired_sessions()
    purged_count = purge_deleted_tickets()
    backup_path = make_backup(test=APP_ENV == "test")
    logger.info("Backup de inicialização gerado em %s", backup_path)
    if purged_count:
        logger.info("Chamados excluídos removidos definitivamente: %s", purged_count)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AppHandler)
    print(f"Servidor iniciado em http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
