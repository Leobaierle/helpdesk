PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS request_types (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department TEXT,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'ti' CHECK (role IN ('ti')),
  is_active INTEGER NOT NULL DEFAULT 1,
  activated_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  token TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  category_id INTEGER,
  responsible_area TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('Baixa', 'Media', 'Alta', 'Critica')),
  status TEXT NOT NULL CHECK (status IN ('Em triagem', 'Mapeamento', 'Em desenvolvimento', 'Aguardando validacao', 'Concluida')),
  due_date TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_id) REFERENCES request_types(id)
);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requester_name TEXT NOT NULL,
  requester_email TEXT NOT NULL,
  requester_department TEXT NOT NULL,
  requester_phone TEXT,
  category_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('Baixa', 'Media', 'Alta')),
  status TEXT NOT NULL DEFAULT 'Aberto' CHECK (status IN ('Aberto', 'Em analise', 'Em atendimento', 'Aguardando retorno', 'Concluido', 'Cancelado')),
  assigned_to_user_id INTEGER,
  internal_note TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_id) REFERENCES request_types(id),
  FOREIGN KEY (assigned_to_user_id) REFERENCES users(id)
);

INSERT OR IGNORE INTO request_types (id, name, description) VALUES
  (1, 'Automacao de atividades', 'Demandas para automatizacao de rotinas e processos internos.'),
  (2, 'Suporte TI', 'Solicitacoes tecnicas relacionadas a acessos, equipamentos e infraestrutura.');

INSERT OR IGNORE INTO activities (
  id,
  title,
  description,
  category_id,
  responsible_area,
  priority,
  status,
  due_date
) VALUES
  (1, 'Automacao de fechamento fiscal', 'Padronizacao do fluxo para reduzir tarefas manuais e acelerar conferencias mensais.', 1, 'Equipe Dev', 'Alta', 'Em desenvolvimento', '2026-04-18'),
  (2, 'Revisao de acessos de usuarios', 'Organizacao de permissoes e ajustes em perfis com inconsistencias reportadas.', 2, 'Suporte interno', 'Media', 'Aguardando validacao', '2026-04-12'),
  (3, 'Integracao de chamados com planilha central', 'Criacao do fluxo para consolidar solicitacoes e facilitar o acompanhamento do setor.', 1, 'Analise e Dev', 'Media', 'Mapeamento', '2026-04-22'),
  (4, 'Checklist de manutencao das estacoes', 'Levantamento de equipamentos, atualizacoes pendentes e necessidades de suporte preventivo.', 2, 'TI', 'Baixa', 'Em triagem', '2026-04-16');
