"""Converte EPCs gravados em base64 (formato `epc` do R700) para hex maiúsculo em `events`. Idempotente.
Uso (no container do app): python3 /tmp/normalizar_epc.py   (usa DATABASE_URL)"""
import os, sys
sys.path.insert(0, "/srv")
import psycopg
from app.main import epc_hex

with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    distintos = [r[0] for r in con.execute("SELECT DISTINCT epc FROM events WHERE epc IS NOT NULL")]
    mudou = [(epc_hex(e), e) for e in distintos if epc_hex(e) != e]
    with con.cursor() as cur:
        cur.executemany("UPDATE events SET epc=%s WHERE epc=%s", mudou)
    print(f"epcs distintos: {len(distintos)}; convertidos: {len(mudou)}", file=sys.stderr)
