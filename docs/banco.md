# Banco de Dados

> **Producao em PostgreSQL desde 2026-05-07** (database `helpdesk`, role `helpdesk_app`). O esquema descrito abaixo vale para ambos backends; tipos foram mantidos compativeis (timestamps como TEXT em formato ISO, booleans como INTEGER) para manter o codigo identico. Ver `[[../servicos/Helpdesk]]` no vault para detalhes operacionais e procedimento de cutover.

## Backends suportados

A aplicacao escolhe o backend em runtime via `DB_BACKEND`:

- `DB_BACKEND=postgres` (producao): conexao via `DATABASE_URL`, pool psycopg com 2-5 conexoes
- `DB_BACKEND=sqlite` (dev/teste/rollback): arquivo local em `APP_DB_PATH`

## Arquivos

### Modo SQLite

- `db/schema.sql`: representacao consolidada do schema atual (SQLite)
- `db/migrations/`: migracoes versionadas (SQLite, 7 arquivos)
- `db/helpdesk.db`: banco principal SQLite
- `db/backups/`: backups operacionais
- `db/backups_test/`: backups de ambiente de teste

### Modo PostgreSQL

- `db/migrations_pg/`: migracoes versionadas (PostgreSQL, consolidado em `001_initial.sql`)
- Backup em `/var/backups/postgres-helpdesk/`, gerado por `/etc/cron.daily/pg-backup-helpdesk`

## Tabelas principais

### `request_types`

Categorias dos chamados.

### `users`

Usuários internos do TI.

Campos principais:

- nome
- e-mail
- setor
- senha em hash
- papel
- status de ativação
- `updated_at`

### `sessions`

Sessoes autenticadas dos usuários do TI.

Campos principais:

- usuário
- token
- expiração
- `updated_at`

### `user_invites`

Convites de ativação para novos usuários do TI.

Campos principais:

- usuário convidado
- token único
- expiração do link
- data de uso
- usuário que gerou o convite

### `user_password_resets`

Solicitações de redefinição de senha dos usuários do TI.

Campos principais:

- usuário alvo
- token único
- expiração do link
- data de uso

### `login_attempts`

Registro de tentativas recentes de login por IP.

Campos principais:

- IP de origem
- data da tentativa

### `tickets`

Tabela central dos chamados.

Campos principais:

- dados do solicitante
- categoria
- título
- descrição
- prioridade
- status
- responsável do TI
- resumo interno
- token público de acompanhamento
- `created_at`
- `updated_at`

### `ticket_events`

Historico das movimentações do chamado.

Campos principais:

- chamado
- usuário autor
- tipo de evento
- mensagem
- status anterior
- novo status
- data do evento

## Indices aplicados

- status + data de criacao do chamado
- categoria do chamado
- responsável do chamado
- data de atualização do chamado
- token público do chamado
- token e expiração da sessão
- token e expiração do convite de ativação
- token e expiração da redefinição de senha
- histórico por chamado
- e-mail + status do usuário

## Migracoes

O projeto aplica migrações versionadas automaticamente ao iniciar.

Fluxo atual:

1. cria a tabela `schema_migrations` se necessário
2. identifica migracoes ainda não aplicadas
3. executa os arquivos SQL de `db/migrations/` em ordem alfabetica
4. registra a versao aplicada

## Auditoria

As datas `updated_at` sao atualizadas pela camada de aplicação quando um usuário ou chamado sofre alteração relevante.

## Limpeza de legado

O estado atual do banco não inclui mais:

- a tabela `activities`, que não fazia parte do fluxo oficial servido por `server.py`
- a coluna `requester_phone`, removida do fluxo público e agora retirada tambem do schema consolidado
