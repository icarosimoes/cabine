import os
import pytest

os.environ.setdefault("WEBHOOK_USER", "w"); os.environ.setdefault("WEBHOOK_PASS", "w")
os.environ.setdefault("UI_USER", "admin"); os.environ.setdefault("UI_PASS", "admin-pass")
os.environ.setdefault("SAIDA_USER", "s"); os.environ.setdefault("SAIDA_PASS", "s")
if "DATABASE_URL" not in os.environ:
    pytest.exit("defina DATABASE_URL apontando para um Postgres DESCARTÁVEL (os testes apagam as tabelas)", 2)

from fastapi.testclient import TestClient  # noqa: E402
import psycopg  # noqa: E402
from app import main  # noqa: E402

TABELAS = ["conferencia_itens", "conferencias", "eventos_saida", "transferencias", "lote_itens", "lotes",
           "acomodacao_itens", "acomodacoes", "antenas", "itens", "estados_inventario", "destinos", "usuarios", "events"]


@pytest.fixture()
def client():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        for t in TABELAS:
            con.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    main.init_db()
    main._falhas.clear()
    with TestClient(main.app) as c:
        c.auth = ("admin", "admin-pass")
        yield c


def ingest(c, *epcs):
    c.post("/api/leitura/armar?segundos=60")
    r = c.post("/ingest", auth=("w", "w"), json=[{"eventType": "tagInventory", "tagInventoryEvent": {"epcHex": e}} for e in epcs])
    assert r.status_code == 200
