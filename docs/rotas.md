# Rotas

## Publicas

### `GET /`

Página principal pública com selecao de categoria.

Comportamento atual:

- exibe os cards `Automação de atividades` e `Suporte TI`
- cada card abre uma nova aba para o formulário dedicado

### `GET /novo-chamado`

Página pública dedicada ao formulário de abertura.

Parametros suportados:

- `category_id`

### `POST /abrir-chamado`

Recebe o formulário público, valida os campos e grava o chamado no banco.

Campos principais:

- `requester_name`
- `requester_email`
- `requester_department`
- `category_id`
- `priority`
- `title`
- `description`
- `csrf_token`

### `GET /acompanhar/{public_token}`

Consulta pública do chamado por token seguro.

Exibe:

- status
- prioridade
- categoria
- abertura
- ultima atualização
- histórico público de andamento

## TI

### `GET /ti/login`

Tela de login da equipe de TI.

Observação:

- a tela oferece a ação `Esqueci minha senha`

### `POST /ti/login`

Autentica o usuário do TI.

### `POST /ti/logout`

Encerra a sessão do usuário do TI.

### `GET /ti/setup`

Tela para criar o primeiro acesso do TI.

### `POST /ti/setup`

Cria o primeiro usuário interno do TI.

### `GET /ti`

Painel interno do TI.

Parametros suportados:

- `tab=abertos|andamento|concluidos|todos`
- `category_id`
- `assigned_to`
- `q`
- `sort=recentes|antigos|prioridade_alta|prioridade_baixa`

### `POST /ti/chamados/{id}/atualizar`

Atualiza um chamado do painel interno.

Campos usados:

- `status`
- `internal_note`
- `comment`
- `assigned_to_user_id`
- `active_tab`
- `category_id`
- `assigned_to`
- `q`
- `sort`
- `csrf_token`

Observação:

- `internal_note` permanece restrito ao painel TI
- `comment` representa a devolutiva visivel ao solicitante na consulta pública
- `assigned_to_user_id` deve apontar para um usuário TI ativo

### `GET /ti/usuários`

Tela para cadastrar e visualizar usuários do TI.

### `POST /ti/usuários`

Cria um novo usuário interno do TI e envia por e-mail um link para ele definir a própria senha.

### `POST /ti/usuários/{id}/atualizar`

Edita nome, e-mail, setor e status de ativação de um usuário TI.

Observação:

- a troca de senha de terceiros não é permitida

### `POST /ti/minha-senha`

Permite ao usuário autenticado alterar a própria senha.

Campos usados:

- `password`
- `password_confirm`

### `GET /ti/ativar/{token}`

Exibe a tela para o novo usuário definir a senha a partir do link enviado por e-mail.

### `POST /ti/ativar/{token}`

Conclui a ativação do novo usuário, gravando a senha definida por ele.

### `GET /ti/esqueci-minha-senha`

Exibe o formulário para solicitar o envio do link de redefinição de senha.

### `POST /ti/esqueci-minha-senha`

Recebe o e-mail do usuário e envia o link de redefinição quando houver um acesso ativo correspondente.

Campos usados:

- `email`

### `GET /ti/redefinir-senha/{token}`

Exibe a tela para cadastro da nova senha a partir do link de redefinição.

### `POST /ti/redefinir-senha/{token}`

Conclui a redefinição da senha do usuário.

## Tecnicas

### `GET /styles.css`

Arquivo CSS da aplicação.
