from __future__ import annotations

import os
import sys
import tempfile
import threading
import http.client
import unittest
from datetime import timedelta
from email import message_from_string
from email.header import decode_header
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def decode_mime_header(value: str) -> str:
    parts = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8"))
        else:
            parts.append(chunk)
    return "".join(parts)


class Projeto1TestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["APP_DB_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        os.environ["APP_OUTBOX_PATH"] = str(Path(self.temp_dir.name) / "outbox")
        os.environ["APP_ENV"] = "test"

        import importlib

        self.app_config = importlib.import_module("app_config")
        importlib.reload(self.app_config)
        self.app_db = importlib.import_module("app_db")
        importlib.reload(self.app_db)
        self.app_services = importlib.import_module("app_services")
        importlib.reload(self.app_services)
        self.server_module = importlib.import_module("server")
        importlib.reload(self.server_module)
        self.app_db.ensure_schema()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def start_http_server(self):
        httpd = self.server_module.ThreadingHTTPServer(("127.0.0.1", 0), self.server_module.AppHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def request_http(self, method: str, path: str, body: str = "", headers: dict[str, str] | None = None):
        httpd, thread = self.start_http_server()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            payload = response.read().decode("utf-8", errors="replace")
            header_map = {key: value for key, value in response.getheaders()}
            set_cookies = [value for key, value in response.getheaders() if key.lower() == "set-cookie"]
            connection.close()
            return response.status, payload, header_map, set_cookies
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_abertura_de_chamado(self) -> None:
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Paulo Teste",
                "requester_email": "leo@example.com",
                "requester_department": "Financeiro",
                "category_id": "1",
                "priority": "Alta",
                "title": "Erro em rotina mensal",
                "description": "A rotina mensal falhou ao consolidar os dados do fechamento.",
            }
        )
        self.assertFalse(errors)
        self.assertIsNotNone(ticket_id)
        token = self.app_services.get_public_ticket_token_by_id(ticket_id)
        self.assertTrue(token)

    def test_notifica_operadores_ao_abrir_chamado(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Iago",
                "email": "iago@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        _, errors = self.app_services.create_user(
            {
                "name": "Paulo Teste",
                "email": "leo@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=1,
        )
        self.assertFalse(errors)
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Silva",
                "requester_email": "maria@example.com",
                "requester_department": "Financeiro",
                "category_id": "2",
                "priority": "Alta",
                "title": "Erro ao acessar o sistema",
                "description": "Nao consigo entrar no sistema interno desde esta manha.",
            }
        )
        self.assertFalse(errors)

        delivery = self.app_services.send_new_ticket_operator_notification(ticket_id)
        self.assertEqual(delivery, "outbox")

        outbox_files = sorted(Path(self.app_config.MAIL_OUTBOX_PATH).glob("*.eml"))
        self.assertEqual(len(outbox_files), 1)
        message = message_from_string(outbox_files[0].read_text(encoding="utf-8"))
        self.assertEqual(decode_mime_header(message["Subject"]), f"Novo chamado aberto #{ticket_id}")
        self.assertEqual(message["To"], "iago@example.com, leo@example.com")

        payload = message.get_payload()
        self.assertIn("Um novo chamado foi aberto na Central de chamados.", payload)
        self.assertIn("Solicitante: Maria Silva", payload)
        self.assertIn("Descrição: Nao consigo entrar no sistema interno desde esta manha.", payload)
        self.assertIn(f"{self.app_config.APP_BASE_URL}/ti", payload)

    def test_setup_login_e_sessao(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        self.assertIsNotNone(user)
        token = self.app_services.create_session(user["id"])
        session_user = self.app_services.get_session_user(token)
        self.assertEqual(session_user["email"], "ti@example.com")

    def test_http_setup_exige_auth_apos_primeiro_acesso(self) -> None:
        _, errors = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        self.assertFalse(errors)

        status_get, _, headers_get, _ = self.request_http("GET", "/ti/setup")
        self.assertEqual(status_get, 302)
        self.assertEqual(headers_get.get("Location"), "/ti/login")

        csrf_cookie = "helpdesk_csrf=dummy"
        body = (
            f"csrf_token=dummy&name=Suporte+2&email=suporte2%40example.com"
            "&department=TI&password=Senha456"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": csrf_cookie,
        }

        status_post, _, headers_post, _ = self.request_http("POST", "/ti/setup", body=body, headers=headers)
        self.assertEqual(status_post, 302)
        self.assertEqual(headers_post.get("Location"), "/ti/login")
        users = self.app_services.list_ti_users()
        self.assertEqual(len(users), 1)

    def test_atualizacao_de_chamado(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, errors, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Silva",
                "requester_email": "maria@example.com",
                "requester_department": "RH",
                "category_id": "2",
                "priority": "Media",
                "title": "Acesso ao sistema",
                "description": "Nao consigo acessar o sistema interno da empresa.",
            }
        )
        self.assertFalse(errors)
        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "internal_note": "Atendimento iniciado",
                "comment": "Contato inicial realizado com a usuaria.",
                "assigned_to_user_id": str(user["id"]),
            },
            user["id"],
        )
        self.assertTrue(success, message)
        ticket = self.app_services.get_public_ticket(self.app_services.get_public_ticket_token_by_id(ticket_id))
        self.assertEqual(ticket["status"], "Em atendimento")

    def test_designacao_de_chamado_para_outro_operador_ativo(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Iago",
                "email": "iago@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        operador_origem = self.app_services.authenticate_user("iago@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Paulo Teste",
                "email": "leo@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=operador_origem["id"],
        )
        self.assertFalse(errors)
        operador_destino = self.app_services.authenticate_user("leo@example.com", "Senha456")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Maria Silva",
                "requester_email": "maria@example.com",
                "requester_department": "RH",
                "category_id": "2",
                "priority": "Media",
                "title": "Acesso ao sistema",
                "description": "Nao consigo acessar o sistema interno da empresa.",
            }
        )

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em analise",
                "comment": "Chamado direcionado para o operador correto.",
                "assigned_to_user_id": str(operador_destino["id"]),
            },
            operador_origem["id"],
        )

        self.assertTrue(success, message)
        dashboard_rows, _, _ = self.app_services.get_dashboard_data(
            "andamento",
            {"category_id": "", "assigned_to": "", "q": "", "sort": "recentes"},
        )
        ticket_row = next(row for row in dashboard_rows if row["id"] == ticket_id)
        self.assertEqual(ticket_row["assigned_name"], "Paulo Teste")
        _, _, events_by_ticket = self.app_services.get_dashboard_data(
            "andamento",
            {"category_id": "", "assigned_to": "", "q": "", "sort": "recentes"},
        )
        self.assertIn(
            "Chamado designado para Paulo Teste.",
            [row["message"] for row in events_by_ticket[ticket_id]],
        )

    def test_criacao_e_edicao_de_usuario_ti(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Suporte 1",
                "email": "suporte@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=admin["id"],
        )
        self.assertFalse(errors)
        users = self.app_services.list_ti_users()
        suporte = next(row for row in users if row["email"] == "suporte@example.com")
        success, message = self.app_services.update_user(
            suporte["id"],
            {
                "name": "Suporte Interno",
                "email": "suporte@example.com",
                "department": "Service Desk",
                "password": "",
                "is_active": "1",
            },
            admin["id"],
        )
        self.assertTrue(success, message)

    def test_convite_de_usuario_ti_envia_link_para_definir_senha(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin@example.com", "Senha123")

        _, errors, delivery = self.app_services.create_invited_user(
            {
                "name": "Suporte Novo",
                "email": "novo@example.com",
                "department": "Service Desk",
            },
            invited_by_user_id=admin["id"],
        )

        self.assertFalse(errors)
        self.assertEqual(delivery, "outbox")
        outbox_files = sorted(Path(self.app_config.MAIL_OUTBOX_PATH).glob("*.eml"))
        self.assertEqual(len(outbox_files), 1)
        message = message_from_string(outbox_files[0].read_text(encoding="utf-8"))
        self.assertEqual(decode_mime_header(message["Subject"]), "Ativação do seu acesso ao Helpdesk")
        payload = message.get_payload()
        self.assertIn("novo@example.com", outbox_files[0].read_text(encoding="utf-8"))
        self.assertIn("/ti/ativar/", payload)

    def test_usuario_convidado_define_a_propria_senha_e_consegue_logar(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin@example.com", "Senha123")
        _, errors, _ = self.app_services.create_invited_user(
            {
                "name": "Suporte Novo",
                "email": "novo@example.com",
                "department": "Service Desk",
            },
            invited_by_user_id=admin["id"],
        )
        self.assertFalse(errors)

        with self.app_db.get_db() as conn:
            token = conn.execute(
                "SELECT token FROM user_invites ORDER BY id DESC LIMIT 1"
            ).fetchone()["token"]

        success, message = self.app_services.activate_user_invite(
            token,
            {
                "password": "SenhaNova123",
                "password_confirm": "SenhaNova123",
            },
        )

        self.assertTrue(success, message)
        invited = self.app_services.authenticate_user("novo@example.com", "SenhaNova123")
        self.assertIsNotNone(invited)

    def test_usuario_nao_pode_trocar_senha_de_terceiro(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        admin = self.app_services.authenticate_user("admin@example.com", "Senha123")
        _, errors = self.app_services.create_user(
            {
                "name": "Suporte 1",
                "email": "suporte@example.com",
                "department": "TI",
                "password": "Senha456",
            },
            current_user_id=admin["id"],
        )
        self.assertFalse(errors)
        suporte = self.app_services.authenticate_user("suporte@example.com", "Senha456")

        success, message = self.app_services.update_user(
            suporte["id"],
            {
                "name": "Suporte 1",
                "email": "suporte@example.com",
                "department": "TI",
                "password": "Senha789",
                "is_active": "1",
            },
            admin["id"],
        )

        self.assertFalse(success)
        self.assertIn("apenas a própria senha", message)
        self.assertIsNone(self.app_services.authenticate_user("suporte@example.com", "Senha789"))
        self.assertIsNotNone(self.app_services.authenticate_user("suporte@example.com", "Senha456"))

    def test_http_tela_de_usuarios_remove_troca_de_senha_de_terceiros(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )

        status_login_get, _, _, login_cookies = self.request_http("GET", "/ti/login")
        self.assertEqual(status_login_get, 200)
        csrf_cookie = next(value for value in login_cookies if value.startswith("helpdesk_csrf="))
        csrf_token = csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        login_body = f"csrf_token={csrf_token}&email=admin%40example.com&password=Senha123"
        login_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": csrf_cookie.split(";", 1)[0],
        }
        _, _, login_headers_map, login_set_cookies = self.request_http("POST", "/ti/login", login_body, login_headers)
        self.assertEqual(login_headers_map["Location"], "/ti")
        session_cookie = next(value for value in login_set_cookies if value.startswith("helpdesk_session="))
        new_csrf_cookie = next(value for value in login_set_cookies if value.startswith("helpdesk_csrf="))

        status_users, payload_users, _, _ = self.request_http(
            "GET",
            "/ti/usuarios",
            headers={
                "Cookie": f"{session_cookie.split(';', 1)[0]}; {new_csrf_cookie.split(';', 1)[0]}",
            },
        )

        self.assertEqual(status_users, 200)
        self.assertIn("Adicionar usuário TI", payload_users)
        self.assertIn("Alterar senha do usuário atual", payload_users)
        self.assertNotIn("Nova senha</span><input name=\"password\" type=\"password\" placeholder=\"Preencha apenas para trocar\"", payload_users)

    def test_login_exibe_esqueci_minha_senha_e_nao_exibe_criar_acesso(self) -> None:
        status, payload, _, _ = self.request_http("GET", "/ti/login")
        self.assertEqual(status, 200)
        self.assertIn("Esqueci minha senha", payload)
        self.assertNotIn("Criar acesso TI", payload)

    def test_esqueci_minha_senha_envia_link_de_redefinicao(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )

        _, errors, delivery = self.app_services.request_password_reset({"email": "admin@example.com"})
        self.assertFalse(errors)
        self.assertEqual(delivery, "outbox")

        outbox_files = sorted(Path(self.app_config.MAIL_OUTBOX_PATH).glob("*.eml"))
        self.assertEqual(len(outbox_files), 1)
        message = message_from_string(outbox_files[0].read_text(encoding="utf-8"))
        self.assertEqual(decode_mime_header(message["Subject"]), "Redefinição de senha do Helpdesk")
        self.assertIn("/ti/redefinir-senha/", message.get_payload())

    def test_usuario_consegue_redefinir_senha_por_token(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Admin TI",
                "email": "admin@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )

        _, _, _ = self.app_services.request_password_reset({"email": "admin@example.com"})
        with self.app_db.get_db() as conn:
            token = conn.execute(
                "SELECT token FROM user_password_resets ORDER BY id DESC LIMIT 1"
            ).fetchone()["token"]

        success, message = self.app_services.reset_user_password(
            token,
            {
                "password": "SenhaNova123",
                "password_confirm": "SenhaNova123",
            },
        )

        self.assertTrue(success, message)
        self.assertIsNotNone(self.app_services.authenticate_user("admin@example.com", "SenhaNova123"))

    def test_dashboard_publico_retorna_andamento_e_finalizados_com_operador(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Operador TI",
                "email": "operador@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        operador = self.app_services.authenticate_user("operador@example.com", "Senha123")

        _, _, andamento_id = self.app_services.create_ticket(
            {
                "requester_name": "Ana Souza",
                "requester_email": "ana@example.com",
                "requester_department": "Fiscal",
                "category_id": "1",
                "priority": "Alta",
                "title": "Automacao de planilha",
                "description": "Precisamos automatizar o fechamento da planilha de conferencia.",
            }
        )
        _, _, concluido_id = self.app_services.create_ticket(
            {
                "requester_name": "Bruno Lima",
                "requester_email": "bruno@example.com",
                "requester_department": "TI",
                "category_id": "2",
                "priority": "Media",
                "title": "Ajuste de acesso",
                "description": "Solicito revisao do acesso ao sistema interno para o novo colaborador.",
            }
        )

        self.app_services.update_ticket(
            andamento_id,
            {
                "status": "Em atendimento",
                "internal_note": "Fluxo iniciado",
                "comment": "Atendimento em execucao.",
                "assigned_to_user_id": str(operador["id"]),
            },
            operador["id"],
        )
        self.app_services.update_ticket(
            concluido_id,
            {
                "status": "Concluido",
                "internal_note": "Chamado resolvido",
                "comment": "Entrega concluida.",
                "assigned_to_user_id": str(operador["id"]),
            },
            operador["id"],
        )

        andamento, concluidos = self.app_services.get_public_dashboard_snapshot()

        self.assertTrue(any(row["id"] == andamento_id for row in andamento))
        self.assertTrue(any(row["id"] == concluido_id for row in concluidos))
        andamento_row = next(row for row in andamento if row["id"] == andamento_id)
        concluido_row = next(row for row in concluidos if row["id"] == concluido_id)
        self.assertEqual(andamento_row["assigned_name"], "Operador TI")
        self.assertEqual(concluido_row["assigned_name"], "Operador TI")

    def test_dashboard_publico_considera_status_em_analise_e_aguardando_retorno_como_andamento(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Operador TI",
                "email": "operador@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        operador = self.app_services.authenticate_user("operador@example.com", "Senha123")

        _, _, em_analise_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia",
                "requester_email": "julia@example.com",
                "requester_department": "Fiscal",
                "category_id": "1",
                "priority": "Media",
                "title": "Ticket em analise",
                "description": "Descricao suficiente para validar o status em analise.",
            }
        )
        _, _, aguardando_id = self.app_services.create_ticket(
            {
                "requester_name": "Renato",
                "requester_email": "renato@example.com",
                "requester_department": "Contabil",
                "category_id": "2",
                "priority": "Alta",
                "title": "Ticket aguardando retorno",
                "description": "Descricao suficiente para validar o status aguardando retorno.",
            }
        )

        self.app_services.update_ticket(
            em_analise_id,
            {"status": "Em analise", "internal_note": "Triagem interna", "comment": "", "assigned_to_user_id": str(operador["id"])},
            operador["id"],
        )
        self.app_services.update_ticket(
            aguardando_id,
            {
                "status": "Aguardando retorno",
                "internal_note": "Aguardando resposta",
                "comment": "Precisamos da sua confirmacao.",
                "assigned_to_user_id": str(operador["id"]),
            },
            operador["id"],
        )

        andamento, _ = self.app_services.get_public_dashboard_snapshot()
        andamento_ids = {row["id"] for row in andamento}
        self.assertIn(em_analise_id, andamento_ids)
        self.assertIn(aguardando_id, andamento_ids)

    def test_confirmacao_de_chamado_vai_para_outbox_quando_smtp_nao_esta_configurado(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Carlos Mendes",
                "requester_email": "carlos@example.com",
                "requester_department": "Contabil",
                "category_id": "2",
                "priority": "Baixa",
                "title": "Teste de confirmacao",
                "description": "Abrindo um chamado para validar a geracao da confirmacao por e-mail.",
            }
        )

        delivery_mode = self.app_services.send_ticket_confirmation(ticket_id)

        self.assertEqual(delivery_mode, "outbox")
        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        files = list(outbox_dir.glob("*.eml"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        subject = decode_mime_header(message_from_string(content)["Subject"])
        self.assertIn("Confirmação do chamado", subject)
        self.assertIn("carlos@example.com", content)
        self.assertIn(f"/acompanhar/{self.app_services.get_public_ticket_token_by_id(ticket_id)}", content)

    def test_resposta_ao_solicitante_fica_visivel_na_consulta_publica_sem_expor_resumo_interno(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Paula Lima",
                "requester_email": "paula@example.com",
                "requester_department": "Fiscal",
                "category_id": "2",
                "priority": "Media",
                "title": "Erro de acesso",
                "description": "Nao consigo acessar o portal interno.",
            }
        )

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Aguardando retorno",
                "internal_note": "Aguardando confirmacao do gestor para liberar perfil.",
                "comment": "Precisamos da confirmacao do seu gestor para liberar o acesso.",
                "assigned_to_user_id": str(user["id"]),
            },
            user["id"],
        )

        self.assertTrue(success, message)
        events = self.app_services.get_public_ticket_events(ticket_id)
        messages = [row["message"] for row in events]
        self.assertIn("Precisamos da confirmacao do seu gestor para liberar o acesso.", messages)
        self.assertNotIn("Resumo interno atualizado: Aguardando confirmacao do gestor para liberar perfil.", messages)

    def test_atualizacao_de_chamado_envia_email_ao_solicitante_com_resumo_da_alteracao(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Joao Pedro",
                "requester_email": "joao@example.com",
                "requester_department": "Operacoes",
                "category_id": "1",
                "priority": "Alta",
                "title": "Automacao de relatorio",
                "description": "Precisamos automatizar a consolidacao do relatorio semanal.",
            }
        )

        outbox_dir = Path(os.environ["APP_OUTBOX_PATH"])
        self.app_services.send_ticket_confirmation(ticket_id)
        before_files = set(outbox_dir.glob("*.eml"))

        success, message = self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "internal_note": "Analise interna iniciada.",
                "comment": "Ja iniciamos a analise da sua solicitacao.",
                "assigned_to_user_id": str(user["id"]),
            },
            user["id"],
        )

        self.assertTrue(success, message)
        after_files = set(outbox_dir.glob("*.eml"))
        new_files = list(after_files - before_files)
        self.assertEqual(len(new_files), 1)
        content = new_files[0].read_text(encoding="utf-8")
        subject = decode_mime_header(message_from_string(content)["Subject"])
        self.assertIn("Atualização do chamado", subject)
        self.assertIn("joao@example.com", content)
        self.assertIn("Status alterado de Aberto para Em atendimento.", content)
        self.assertIn("Resposta da equipe: Ja iniciamos a analise da sua solicitacao.", content)
        self.assertNotIn("Resumo interno do atendimento atualizado pela equipe.", content)
        self.assertNotIn("Analise interna iniciada.", content)

    def test_exclusao_de_chamado_move_para_aba_de_excluidos_sem_apagar_historico(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia Lopes",
                "requester_email": "julia@example.com",
                "requester_department": "Fiscal",
                "category_id": "2",
                "priority": "Media",
                "title": "Excluir ticket",
                "description": "Chamado criado para validar a exclusao.",
            }
        )
        self.app_services.update_ticket(
            ticket_id,
            {
                "status": "Em atendimento",
                "comment": "Atendimento iniciado.",
                "assigned_to_user_id": str(user["id"]),
            },
            user["id"],
        )
        public_token = self.app_services.get_public_ticket_token_by_id(ticket_id)

        success, message = self.app_services.delete_ticket(ticket_id, user["id"])

        self.assertTrue(success, message)
        self.assertEqual(self.app_services.get_public_ticket_token_by_id(ticket_id), public_token)
        self.assertIsNone(self.app_services.get_public_ticket(public_token))
        self.assertTrue(self.app_services.get_public_ticket_events(ticket_id))
        excluidos, summary, _ = self.app_services.get_dashboard_data(
            "excluidos",
            {"category_id": "", "assigned_to": "", "q": "", "sort": "recentes"},
        )
        self.assertEqual(summary["deleted_count"], 1)
        self.assertEqual(excluidos[0]["id"], ticket_id)

    def test_chamado_excluido_pode_ser_restaurado(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia Lopes",
                "requester_email": "julia@example.com",
                "requester_department": "Fiscal",
                "category_id": "2",
                "priority": "Media",
                "title": "Restaurar ticket",
                "description": "Chamado criado para validar restauracao.",
            }
        )
        public_token = self.app_services.get_public_ticket_token_by_id(ticket_id)

        success, _ = self.app_services.delete_ticket(ticket_id, user["id"])
        self.assertTrue(success)
        restored, message = self.app_services.restore_ticket(ticket_id, user["id"])

        self.assertTrue(restored, message)
        self.assertIsNotNone(self.app_services.get_public_ticket(public_token))

    def test_purge_remove_chamados_excluidos_apos_30_dias(self) -> None:
        _, _ = self.app_services.create_user(
            {
                "name": "Equipe TI",
                "email": "ti@example.com",
                "department": "TI",
                "password": "Senha123",
            },
            initial_setup=True,
        )
        user = self.app_services.authenticate_user("ti@example.com", "Senha123")
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Julia Lopes",
                "requester_email": "julia@example.com",
                "requester_department": "Fiscal",
                "category_id": "2",
                "priority": "Media",
                "title": "Expirar exclusao",
                "description": "Chamado criado para validar purge.",
            }
        )
        self.app_services.delete_ticket(ticket_id, user["id"])
        cutoff = self.app_services.isoformat(self.app_services.utc_now() - timedelta(days=31))
        with self.app_db.get_db() as conn:
            conn.execute("UPDATE tickets SET deleted_at = ? WHERE id = ?", (cutoff, ticket_id))

        removed = self.app_services.purge_deleted_tickets()

        self.assertEqual(removed, 1)
        self.assertIsNone(self.app_services.get_public_ticket_token_by_id(ticket_id))

    def test_backup_automatico_gera_copia_sqlite_consistente(self) -> None:
        _, _, ticket_id = self.app_services.create_ticket(
            {
                "requester_name": "Marina Costa",
                "requester_email": "marina@example.com",
                "requester_department": "Financeiro",
                "category_id": "1",
                "priority": "Baixa",
                "title": "Backup de teste",
                "description": "Validando a geracao de backup consistente do banco.",
            }
        )

        backup_path = Path(self.app_services.make_backup(test=True))

        self.assertTrue(backup_path.exists())

        import sqlite3

        with sqlite3.connect(backup_path) as conn:
            row = conn.execute("SELECT id, title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[1], "Backup de teste")

    def test_schema_limpo_remove_legado_do_fluxo_oficial(self) -> None:
        import sqlite3

        with sqlite3.connect(os.environ["APP_DB_PATH"]) as conn:
            ticket_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(tickets)").fetchall()
            }
            activities_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'activities'"
            ).fetchone()
            user_invites_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'user_invites'"
            ).fetchone()
            user_password_resets_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'user_password_resets'"
            ).fetchone()

        self.assertNotIn("requester_phone", ticket_columns)
        self.assertIn("public_token", ticket_columns)
        self.assertIsNone(activities_table)
        self.assertIsNotNone(user_invites_table)
        self.assertIsNotNone(user_password_resets_table)

    def test_rate_limit_de_login_fica_persistido_no_banco(self) -> None:
        self.assertFalse(self.app_services.is_login_rate_limited("127.0.0.1"))
        for _ in range(self.app_config.LOGIN_RATE_LIMIT_ATTEMPTS):
            self.app_services.register_login_attempt("127.0.0.1", success=False)
        self.assertTrue(self.app_services.is_login_rate_limited("127.0.0.1"))

    def test_http_get_home_retorna_dashboard_em_andamento(self) -> None:
        status, payload, _, _ = self.request_http("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Em andamento", payload)

    def test_http_404_e_405_sao_padronizados(self) -> None:
        status_404, payload_404, _, _ = self.request_http("GET", "/nao-existe")
        status_405, payload_405, _, _ = self.request_http("PUT", "/")

        self.assertEqual(status_404, 404)
        self.assertIn("Página não encontrada", payload_404)
        self.assertEqual(status_405, 405)
        self.assertIn("Método não permitido", payload_405)

    def test_http_abertura_de_chamado_com_csrf_funciona_no_fluxo_real(self) -> None:
        status_get, payload_get, _, set_cookies = self.request_http("GET", "/novo-chamado?category_id=1")
        self.assertEqual(status_get, 200)
        self.assertIn("Novo chamado", payload_get)

        csrf_cookie = next(value for value in set_cookies if value.startswith("helpdesk_csrf="))
        csrf_token = csrf_cookie.split(";", 1)[0].split("=", 1)[1]
        body = (
            f"csrf_token={csrf_token}&category_id=1&requester_name=Teste+HTTP&requester_email=http%40example.com"
            "&requester_department=Financeiro&priority=Alta&title=Chamado+HTTP&description=Descricao+completa+do+chamado+via+HTTP"
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": csrf_cookie.split(";", 1)[0],
        }

        status_post, payload_post, _, _ = self.request_http("POST", "/abrir-chamado", body=body, headers=headers)
        self.assertEqual(status_post, 200)
        self.assertIn("Chamado enviado com sucesso", payload_post)


if __name__ == "__main__":
    unittest.main()
