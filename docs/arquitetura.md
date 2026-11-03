# Arquitetura

## Stack atual

- linguagem: `Python 3`
- servidor HTTP: `http.server`
- banco: `PostgreSQL` em producao (desde 2026-05-07) via `psycopg[binary]>=3.1` + `psycopg-pool>=3.2`. `SQLite` continua suportado em dev/teste/rollback (selecionavel via env `DB_BACKEND`)
- front-end: HTML renderizado pelo servidor + CSS local
- testes: `unittest`

## Estrutura principal

- `server.py`: rotas HTTP, renderização e camada web
- `app_config.py`: configuração por variáveis de ambiente
- `app_db.py`: camada de banco com flag `DB_BACKEND` (sqlite|postgres), shim que traduz `?`->`%s`, `cursor.lastrowid` via `RETURNING id`, e `DBIntegrityError` unificado
- `app_services.py`: regras de negócio, autenticação, sessões, chamados e usuários
- `db/migrations/`: migracoes SQL versionadas (SQLite)
- `db/migrations_pg/`: migracoes SQL versionadas (PostgreSQL)
- `tests/`: testes automatizados dos fluxos principais

## Modelo de funcionamento

O projeto roda como aplicação web local simples.

O servidor:

- aplica migracoes do banco na inicialização
- pública as rotas HTML e CSS
- valida formulários públicos e internos
- autentica usuários do TI com cookie de sessão
- aplica protecoes basicas de CSRF, headers HTTP e rate limiting no login

## Áreas do sistema

### Área pública

Responsável por:

- abrir chamados sem login
- validar os dados de entrada
- gerar token público de acompanhamento
- exibir consulta pública por rota segura

### Área interna do TI

Responsável por:

- autenticar usuários do setor
- listar chamados por abas
- filtrar por categoria, responsável e busca textual
- alterar status
- designar atendimento para operadores ativos do TI
- registrar resumo e comentarios internos
- manter usuários do TI

## Persistência e auditoria

O banco registra:

- chamados em `tickets`
- usuários internos em `users`
- sessões em `sessions`
- histórico de movimentações em `ticket_events`

As datas `updated_at` sao controladas pela aplicação nas alteracoes relevantes.

## Segurança atual

- cookie de sessão HTTP-only
- suporte a cookie `Secure` por variavel de ambiente
- token CSRF por cookie + campo oculto
- rate limiting simples no login por IP
- headers HTTP de seguranca

## Evolução

A aplicação continua sem framework web. A divisao atual em `server.py`, `app_db.py` e `app_services.py` reduz acoplamento e deixa a migracao futura para Flask, FastAPI ou Django mais direta.
