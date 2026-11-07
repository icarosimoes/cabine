"""Importa o inventário (CSV do eTrace: Item;Localização;RfidTag;Data_Compra;Quantidade) para `itens`.

  CSV_PATH=/tmp/inv.csv DATABASE_URL=... python3 importar_inventario_csv.py

- Item -> `tipo`; RfidTag -> `epc` (hex maiúsculo). EPC inválido (fora de 16..32 hex) é ignorado e listado.
- 'Descarte Inventário' entra no estado 'Descartado'; os demais no estado padrão ('Em uso').
- Localização NÃO é importada: no Cabine ela é derivada de transferências reais, não é campo editável.
- Data_Compra não tem campo no modelo. Idempotente: EPC já cadastrada não é alterada.
"""
import csv, os, re, sys, time
import psycopg

linhas = list(csv.DictReader(open(os.environ["CSV_PATH"], encoding="latin-1").read().splitlines(), delimiter=";"))
col = list(linhas[0].keys())
ok, invalidos, vistos = [], [], set()
for r in linhas:
    epc, tipo, loc = r["RfidTag"].strip().upper(), r[col[0]].strip(), r[col[1]].strip()
    if not re.fullmatch(r"[0-9A-F]{16,32}", epc) or not tipo:
        invalidos.append((epc, tipo)); continue
    if epc in vistos:
        continue
    vistos.add(epc)
    ok.append((epc, tipo, "Descartado" if loc.startswith("Descarte") else None))

now = time.time()
with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    estados = {r[0]: r[1] for r in con.execute("SELECT nome,id FROM estados_inventario")}
    padrao = con.execute("SELECT id FROM estados_inventario WHERE padrao=1").fetchone()[0]
    antes = con.execute("SELECT COUNT(*) FROM itens").fetchone()[0]
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO itens(epc,tipo,estado_id,criado_em,atualizado_em) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(epc) DO NOTHING",
            [(e, t, estados.get(s, padrao), now, now) for e, t, s in ok])
    depois = con.execute("SELECT COUNT(*) FROM itens").fetchone()[0]
print(f"linhas csv: {len(linhas)}; validas: {len(ok)}; inseridas: {depois-antes}; itens antes={antes} depois={depois}", file=sys.stderr)
print("ignoradas:", invalidos, file=sys.stderr)
