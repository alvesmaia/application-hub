"""Domínio da Central de Serviços: listas fechadas, esquema do banco, SLA e a carga inicial
(espelho de ``hub-demo-apps/src/{servicos,db,seed}.rs``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

CATEGORIES = (
    "Tecnologia",
    "Software",
    "Acessos",
    "Financeiro",
    "Facilities",
    "Pessoas",
    "Compras",
    "Infraestrutura",
)
PRIORITIES = ("Baixa", "Normal", "Alta", "Crítica")
STATUSES = ("Aberto", "Em atendimento", "Aguardando", "Resolvido", "Fechado")
PAGE = 25
CLOSED = ("Resolvido", "Fechado")

TICKET_COLS = "id, protocol, title, requester, category, priority, status, opened_at, sla_due, version"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tickets (
  id bigserial PRIMARY KEY,
  protocol text NOT NULL UNIQUE,
  title text NOT NULL,
  requester text NOT NULL,
  category text NOT NULL,
  priority text NOT NULL,
  status text NOT NULL,
  assignee text,
  description text,
  opened_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  sla_due timestamptz NOT NULL,
  version int NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS tickets_status ON tickets (status, id);
CREATE INDEX IF NOT EXISTS tickets_category ON tickets (category, id);
CREATE INDEX IF NOT EXISTS tickets_report ON tickets (opened_at, category) INCLUDE (status, resolved_at, sla_due);
-- Chamados criados pelo app começam em 10 milhões: os ids 1..N ficam para o seed.
SELECT setval('tickets_id_seq', 10000000) WHERE (SELECT last_value FROM tickets_id_seq) < 10000000;
CREATE TABLE IF NOT EXISTS ticket_approvals (
  ticket_id bigint PRIMARY KEY,
  status text NOT NULL DEFAULT 'pending',
  requested_by text NOT NULL,
  requested_by_name text NOT NULL DEFAULT '',
  decided_by text,
  decided_at timestamptz,
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);
"""

INSERT_SQL = (
    "WITH n AS (SELECT nextval('tickets_id_seq') AS id) INSERT INTO tickets "
    "(id, protocol, title, requester, category, priority, status, description, sla_due) "
    "SELECT id, 'SD-' || to_char(now(), 'YYYY') || '-' || id, $1, $2, $3, $4, $7, $5, $6::timestamptz "
    "FROM n RETURNING protocol, id"
)
UPDATE_SQL = (
    "UPDATE tickets SET status = $3::text, updated_at = now(), resolved_at = CASE WHEN $3::text IN "
    "('Resolvido','Fechado') THEN coalesce(resolved_at, now()) ELSE NULL END, version = version + 1 "
    f"WHERE id = $1 AND version = $2 RETURNING {TICKET_COLS}"
)

TICKETS_BASE = 2_000_000
SEED_CHUNK = 50_000

# Abertura cresce com o id (o maior id é o mais recente: a lista em ORDER BY id DESC mostra o
# protocolo do ano corrente), 2 anos de histórico. Só os chamados dos últimos 3 dias ficam em
# aberto (1 em 50 de cada estado: contadores na casa das centenas); os antigos estão resolvidos
# ou fechados. ``{TOTAL}`` = total-alvo da carga.
SEED_SQL = """
INSERT INTO tickets (id, protocol, title, requester, category, priority, status, assignee, description,
  opened_at, updated_at, resolved_at, sla_due)
SELECT g,
  'SD-' || to_char(t.o, 'YYYY') || '-' || lpad(g::text, 7, '0'),
  (ARRAY['Acesso ao ambiente de produção','Instalação do pacote de design','Ajuste de centro de custo','Troca de notebook','Reset de senha do e-mail','Liberação de VPN','Compra de licença','Manutenção do ar-condicionado','Novo colaborador: kit de boas-vindas','Erro no sistema de reembolso'])[1 + (g * 7) % 10],
  (ARRAY['Mariana Costa','Rafael Mendes','Camila Rocha','Lucas Ferreira','John Doe','Ana Beatriz Lima','Pedro Henrique Alves','Juliana Martins'])[1 + (g * 11) % 8],
  (ARRAY['Tecnologia','Software','Acessos','Financeiro','Facilities','Pessoas','Compras','Infraestrutura'])[1 + (g * 3) % 8],
  t.p, t.st,
  (ARRAY['Equipe N1','Equipe N2','Infraestrutura','Facilities'])[1 + g % 4],
  'Chamado gerado pela carga inicial de demonstração.',
  t.o, t.o + interval '1 hour',
  CASE WHEN t.st IN ('Aberto','Em atendimento','Aguardando') THEN NULL
       ELSE t.o + ((1 + (g * 13) % 48) * interval '1 hour') END,
  t.o + CASE t.p WHEN 'Crítica' THEN interval '4 hours' WHEN 'Alta' THEN interval '8 hours'
                 WHEN 'Normal' THEN interval '24 hours' ELSE interval '72 hours' END
FROM generate_series($1::int8, $2::int8) AS g
CROSS JOIN LATERAL (SELECT
  now() - ((({TOTAL} - g)::float8 * 63072000 / {TOTAL}) * interval '1 second') AS o,
  (ARRAY['Baixa','Normal','Normal','Normal','Alta','Crítica'])[1 + (g * 5) % 6] AS p) t0
CROSS JOIN LATERAL (SELECT t0.o, t0.p,
  CASE WHEN t0.o > now() - interval '3 days' THEN
         CASE g % 50 WHEN 0 THEN 'Aberto' WHEN 1 THEN 'Em atendimento' WHEN 2 THEN 'Aguardando' ELSE 'Resolvido' END
       WHEN g % 3 = 0 THEN 'Fechado' ELSE 'Resolvido' END AS st) t
ON CONFLICT (id) DO NOTHING
"""

# Base de uma carga antiga (dezenas de milhares "aguardando", datas fora de ordem): refaz
# datas, estado e protocolo com a mesma regra da carga.
STALE_SQL = "SELECT count(*) > 20000 FROM tickets WHERE status IN ('Aberto','Em atendimento','Aguardando')"
REDATE_SQL = """
UPDATE tickets AS k SET
  protocol = 'SD-' || to_char(t.o, 'YYYY') || '-' || lpad(t.g::text, 7, '0'),
  status = t.st, opened_at = t.o, updated_at = t.o + interval '1 hour',
  resolved_at = CASE WHEN t.st IN ('Aberto','Em atendimento','Aguardando') THEN NULL
                     ELSE t.o + ((1 + (t.g * 13) % 48) * interval '1 hour') END,
  sla_due = t.o + CASE k.priority WHEN 'Crítica' THEN interval '4 hours' WHEN 'Alta' THEN interval '8 hours'
                                  WHEN 'Normal' THEN interval '24 hours' ELSE interval '72 hours' END
FROM (SELECT id AS g, o,
        CASE WHEN o > now() - interval '3 days' THEN
               CASE id % 50 WHEN 0 THEN 'Aberto' WHEN 1 THEN 'Em atendimento' WHEN 2 THEN 'Aguardando' ELSE 'Resolvido' END
             WHEN id % 3 = 0 THEN 'Fechado' ELSE 'Resolvido' END AS st
      FROM (SELECT id, now() - ((({TOTAL} - id)::float8 * 63072000 / {TOTAL}) * interval '1 second') AS o
            FROM tickets WHERE id <= {TOTAL}) x) t
WHERE k.id = t.g
"""


def pick(v: str, opts: tuple[str, ...]) -> str:
    """O próprio valor se for uma das opções; senão vazio."""
    return v if v in opts else ""


def sla_for(priority: str) -> timedelta:
    return {"Crítica": timedelta(hours=4), "Alta": timedelta(hours=8), "Normal": timedelta(hours=24)}.get(
        priority, timedelta(hours=72)
    )


def now() -> datetime:
    return datetime.now(UTC)


def sla_text(status: str, sla_due: datetime) -> str:
    if status in CLOSED:
        return "—"
    secs = (sla_due - now()).total_seconds()
    mins = int(secs / 60)  # trunca para zero, como o chrono
    if mins < 0:
        return "Vencido"
    return f"{int(secs / 3600):02d}:{mins % 60:02d}"


def priority_class(p: str) -> str:
    return "alta" if p in ("Alta", "Crítica") else "normal"


def status_class(s: str) -> str:
    return {"Aguardando": "waiting", "Resolvido": "done", "Fechado": "done"}.get(s, "")


def ticket_json(t: Any) -> dict[str, Any]:
    return {
        "id": t["id"],
        "protocol": t["protocol"],
        "title": t["title"],
        "requester": t["requester"],
        "category": t["category"],
        "priority": t["priority"],
        "status": t["status"],
        "opened_at": t["opened_at"].isoformat(),
        "sla_due": t["sla_due"].isoformat(),
        "version": t["version"],
    }
