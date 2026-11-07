"""Copia os eventos brutos do SQLite antigo (/data/impinj.db) para o PostgreSQL novo.

Uso (dentro de um container com acesso ao volume antigo e à rede do Postgres):
  SQLITE_PATH=/old/impinj.db DATABASE_URL=postgresql://... python3 scripts/migrar_sqlite.py

Só migra a tabela `events` (era a única que existia em SQLite). Idempotente por (received_at, epc, raw):
rodar duas vezes não duplica. O app precisa ter subido ao menos uma vez (cria as tabelas).
"""
import os, sqlite3, sys
import psycopg

src = sqlite3.connect(f"file:{os.environ['SQLITE_PATH']}?mode=ro", uri=True)
rows = src.execute("SELECT received_at,event_type,epc,tid,antenna,rssi,seen_at,raw FROM events ORDER BY id").fetchall()
with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    antes = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    ja = {(r[0], r[1], r[2]) for r in con.execute("SELECT received_at, epc, raw FROM events")}
    novos = [r for r in rows if (r[0], r[2], r[7]) not in ja]
    with con.cursor() as cur:
        cur.executemany("INSERT INTO events(received_at,event_type,epc,tid,antenna,rssi,seen_at,raw) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", novos)
    depois = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
print(f"sqlite: {len(rows)} eventos; inseridos: {len(novos)}; postgres antes={antes} depois={depois}", file=sys.stderr)
