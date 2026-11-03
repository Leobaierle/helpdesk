# Testes

## Suite automatizada

Executar:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Cobertura atual

- abertura de chamado
- setup inicial e autenticação
- criacao e validacao de sessão
- atualização de chamado
- criacao e edicao de usuário TI
- notificacoes por e-mail
- backup consistente do SQLite (modo `DB_BACKEND=sqlite`; em modo postgres a suite usa SQLite isolado)
- validacao do schema consolidado
- rotas HTTP principais, incluindo formulário público e respostas 404/405

## Estratégia

Os testes usam banco temporario por variavel `APP_DB_PATH`, aplicam as migracoes e validam:

- fluxos principais na camada de servicos
- rotas HTTP reais com servidor em porta efemera
- comportamento de cookies e protecoes basicas de formulários
