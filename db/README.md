# Banco do sistema

O projeto suporta dois backends, escolhidos via env var `DB_BACKEND`:

- **`postgres`** (producao desde 2026-05-07): banco em PostgreSQL (`db.example.internal:5432`), database `helpdesk`, role `helpdesk_app`. Conexao via `DATABASE_URL`.
- **`sqlite`** (dev/teste/rollback): arquivo local apontado por `APP_DB_PATH`.

Arquivos:

- `schema.sql`: representacao consolidada do schema (SQLite)
- `migrations/`: migrações SQLite versionadas (7 arquivos, aplicadas automaticamente em modo SQLite)
- `migrations_pg/`: migracoes PostgreSQL versionadas (consolidado em `001_initial.sql`, aplicadas em modo postgres)
- `helpdesk.db`: arquivo SQLite (vale apenas em modo `DB_BACKEND=sqlite`)
- `backups/`: backups operacionais (SQLite)
- `backups_test/`: backups de testes (SQLite)

Em modo postgres, o backup oficial e diario via cron: `/var/backups/postgres-helpdesk/`.

Tabelas:

- `request_types`: categorias dos chamados
- `users`: usuários internos do TI
- `sessions`: sessoes autenticadas do TI
- `login_attempts`: tentativas recentes de login por IP para rate limiting
- `tickets`: chamados abertos pelo formulário público
- `ticket_events`: histórico e comentários dos chamados

Modelo atual:

- qualquer colaborador pode abrir chamado sem login
- apenas a equipe do TI acessa a área interna com usuário e senha
- cada chamado pode receber status, responsável, resumo interno e respostas ao solicitante
- cada chamado possui token público para acompanhamento sem login
