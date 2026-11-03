# Helpdesk de TI

> Projeto real, em produção, publicado aqui sem dados da empresa.

Sistema interno de chamados de TI para uma empresa do setor contábil. Qualquer colaborador abre um
chamado sem precisar de login. A equipe de TI atende por um painel autenticado, com histórico
completo de cada movimentação.

**Stack:** Python 3.12 (stdlib), PostgreSQL, HTML renderizado no servidor, Nginx, systemd, SMTP

## O problema

A equipe de TI recebia pedidos por canais dispersos, sem fila única, sem histórico e sem como o
solicitante saber em que pé estava o atendimento. A meta era um fluxo simples nas duas pontas:
abrir um chamado em poucos cliques e acompanhar o andamento sem criar mais uma conta.

## O que o sistema faz

**Para quem abre o chamado**
- Home pública para escolher a categoria (por exemplo "Suporte" ou "Automação de atividades") e abrir o formulário dedicado
- E-mail de confirmação ao abrir e a cada atualização do chamado
- Consulta pública do andamento por link com token seguro (`/acompanhar/{token}`), sem login

**Para a equipe de TI**
- Painel com abas (Abertos, Em andamento, Concluídos, Todos), filtros por categoria e responsável, busca textual e ordenação
- Atribuição de operador, mudança de status e comentários internos separados da resposta ao solicitante
- Regras de status aplicadas pelo sistema: *Concluído* exige resumo, *Cancelado* exige motivo, *Aguardando retorno* exige resposta ao solicitante
- Histórico auditável de cada alteração (`ticket_events`)
- Cadastro de usuários por convite: o novo usuário recebe um link por e-mail e define a própria senha, e o administrador nunca a conhece. Há também o fluxo "esqueci minha senha", por token com expiração

## Arquitetura

```mermaid
flowchart LR
    U[Solicitante] -->|abre / acompanha| N[Nginx]
    T[Equipe de TI] -->|painel autenticado| N
    N --> A[Aplicação Python<br/>ThreadingHTTPServer]
    A --> S[Camada de serviços<br/>regras de negócio]
    S --> DB[(PostgreSQL)]
    S -->|SMTP| M[E-mail]
    S -.->|falha no SMTP| O[Fila .eml em disco]
    DB -.->|leitura| G[Console interno<br/>via GraphQL]
```

A aplicação tem quatro módulos pequenos:

| Módulo | Responsabilidade |
|---|---|
| `server` | Rotas HTTP, renderização e camada web |
| `services` | Regras de negócio, autenticação, sessões, chamados e usuários |
| `db` | Conexão, migrações e camada de compatibilidade entre bancos |
| `config` | Configuração por variáveis de ambiente |

## Decisões técnicas

- **Sem framework web.** O escopo é pequeno, então usei só a biblioteca padrão do Python (`http.server`). Isso deixa o deploy leve e tem poucas dependências (`psycopg` e `psycopg-pool`). A separação em módulos deixa uma migração futura para Flask ou FastAPI simples.
- **Migração de SQLite para PostgreSQL sem reescrever o código.** Uma camada de compatibilidade traduz placeholders (`?` → `%s`), `lastrowid` (via `RETURNING id`) e as exceções de integridade dos dois bancos. As chamadas de serviço não mudaram. Um flag de ambiente (`DB_BACKEND`) permite voltar ao SQLite se for preciso.
- **E-mail que não perde mensagem.** Se o SMTP falha ou não está configurado, a mensagem é gravada como `.eml` numa pasta de saída em vez de ser descartada.
- **Notas internas nunca vazam.** O texto interno do TI não aparece nos e-mails nem na consulta pública. Só a resposta marcada para o solicitante sai.
- **Segurança básica feita direito:**
  - cookie de sessão `HttpOnly`, com suporte a `Secure`;
  - token CSRF (cookie mais campo oculto) em todo `POST`;
  - rate limiting de login por IP, com persistência no banco;
  - headers HTTP de segurança;
  - senhas só como hash;
  - tokens de convite e de redefinição com expiração.
- **Falha de segurança encontrada e corrigida.** Em auditoria própria, achei que a rota de setup inicial continuava aberta depois do primeiro usuário e permitia criar contas internas sem login. Passou a exigir sessão assim que existe algum usuário, com teste de regressão.
- **Migrações versionadas** aplicadas na inicialização, índices nas consultas principais e backup do banco.

## Qualidade

- Cerca de 4.200 linhas de Python, com 28 testes `unittest` cobrindo serviços, notificações, schema e as rotas HTTP principais (404/405, fluxo CSRF, abertura pública de chamado)
- Documentação em `docs/`: arquitetura, rotas, banco, instalação e testes

## Evolução

1. **Base funcional:** abertura pública, painel de TI, usuários internos, histórico de eventos.
2. **Comunicação:** e-mail ao solicitante em toda atualização e aviso aos operadores quando chega chamado novo.
3. **Banco:** migrações versionadas, depois migração de SQLite para PostgreSQL.
4. **Acesso:** convite por e-mail e redefinição de senha, sem senha inicial definida por terceiros.
5. **Integração:** os chamados também são lidos por um console interno, por um subgraph GraphQL.
6. **Interface:** redesenho visual da central de chamados.

## Rodando

```bash
python3 server.py                                   # http://localhost:8000 (SQLite, só biblioteca padrão)
python3 -m unittest discover -s tests -p 'test_*.py'
```

- formulário público: `/novo-chamado?category_id=1`
- primeiro acesso do TI: `/ti/setup`, depois `/ti/login`
- Para PostgreSQL: `pip install "psycopg[binary]>=3.1" "psycopg-pool>=3.2"` e `DB_BACKEND=postgres`

## Configuração por ambiente

Variáveis suportadas:

- `PORT`: porta HTTP. Padrão `8000`
- `DB_BACKEND`: `sqlite` (default) ou `postgres`. Em producao: `postgres`.
- `DATABASE_URL`: connection string PostgreSQL. Obrigatorio quando `DB_BACKEND=postgres`. Ex.: `postgresql://helpdesk_app:senha@db.example.internal:5432/helpdesk`
- `PG_POOL_MIN` / `PG_POOL_MAX`: tamanho do pool de conexoes psycopg. Padroes `2` e `5`.
- `APP_DB_PATH`: caminho do banco SQLite. Padrão `db/helpdesk.db` (so vale com `DB_BACKEND=sqlite`)
- `SESSION_DAYS`: duração da sessão do TI em dias. Padrão `7`
- `APP_ENV`: ambiente de execução. Ex.: `development`, `test`
- `APP_BASE_URL`: URL base usada nos links públicos enviados por e-mail. Padrão `http://localhost:8000`
- `APP_OUTBOX_PATH`: pasta usada para fallback/local capture dos e-mails
- `USE_SECURE_COOKIE`: ativa flag `Secure` nos cookies quando usar HTTPS
- `LOGIN_RATE_LIMIT_ATTEMPTS`: tentativas de login permitidas por janela
- `LOGIN_RATE_LIMIT_WINDOW`: janela do rate limit em segundos
- `MAIL_FROM`: remetente dos e-mails de confirmação
- `MAIL_REPLY_TO`: reply-to opcional
- `SMTP_HOST`: host SMTP
- `SMTP_PORT`: porta SMTP. Padrão `587`
- `SMTP_USERNAME`: usuário SMTP
- `SMTP_PASSWORD`: senha SMTP
- `SMTP_USE_TLS`: ativa STARTTLS. Padrão `1`
- `SMTP_USE_SSL`: ativa SMTP sobre SSL. Padrão `0`
