# Instalação

## Requisitos

- `Python 3.11+`

## Passos

1. abrir a pasta do projeto
2. opcionalmente definir variáveis de ambiente
3. iniciar o servidor:

```bash
python3 server.py
```

## Variáveis de ambiente

- `PORT`: porta HTTP
- `DB_BACKEND`: `sqlite` (default) ou `postgres`. Producao usa `postgres`
- `DATABASE_URL`: connection string PostgreSQL, obrigatoria quando `DB_BACKEND=postgres`
- `PG_POOL_MIN` / `PG_POOL_MAX`: tamanho do pool psycopg (padroes 2 e 5)
- `APP_DB_PATH`: caminho do banco SQLite (so vale com `DB_BACKEND=sqlite`)
- `SESSION_DAYS`: duração da sessão em dias
- `APP_ENV`: ambiente de execução
- `APP_BASE_URL`: URL base usada nos links públicos enviados por e-mail
- `APP_OUTBOX_PATH`: pasta de fallback para mensagens `.eml`
- `USE_SECURE_COOKIE`: ativa cookie `Secure`
- `LOGIN_RATE_LIMIT_ATTEMPTS`: limite de tentativas de login
- `LOGIN_RATE_LIMIT_WINDOW`: janela do rate limit
- `MAIL_FROM`: remetente das confirmações
- `MAIL_REPLY_TO`: reply-to opcional
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
- `SMTP_USE_TLS`, `SMTP_USE_SSL`

## O que acontece na inicialização

- o log local e configurado
- as migracoes de banco sao aplicadas
- sessões expiradas sao removidas
- um backup inicial do banco e gerado
- o sistema prepara o envio de confirmações por e-mail e usa `outbox/` como fallback se necessário
