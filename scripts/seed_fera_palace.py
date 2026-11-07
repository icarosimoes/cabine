"""Cadastros do cliente Fera Palace Hotel (espelham o eTrace de referência, lidos em 2026-11-07).

  DATABASE_URL=postgresql://... python3 scripts/seed_fera_palace.py

Idempotente: destinos/antenas/acomodações são upsert por nome. Requer as tabelas (o app cria no start).
Inclui: Ambientes (com alerta), Antenas (reader -> ambiente) e Tipos de Acomodação com o enxoval por UH
(qtd por UH x fluxo = jogos em circulação). Estados e categorias já existem/vêm do inventário importado.
"""
import os, time
import psycopg

AMBIENTES = [  # nome, tipo, alerta (sino ligado no eTrace)
    ("Almoxarifado", "setor", 1), ("Apartamentos", "setor", 1), ("Descarte Inventário", "setor", 1),
    ("Enxoval Danificado", "setor", 1), ("Lavanderia", "lavanderia", 0), ("Lavanderia Relavar", "lavanderia", 0),
    ("Piscina", "setor", 1), ("Recepção", "setor", 0), ("Rouparia Geral", "setor", 1),
    ("Saída Serviço", "setor", 1), ("Secagem", "setor", 0),
]
ANTENAS = [  # nome, modelo, ambiente
    ("impinj15f21c", "ImpinjR700", "Rouparia Geral"),
    ("edgeR3015f21c-recepcao", "AcuraEdgeR30", "Recepção"),
    ("edgeR3015f21c-servico", "AcuraEdgeR30", "Saída Serviço"),
]
ACOMODACOES = {  # nome: (UHs, [(tipo, qtd por UH, fluxo)])
    "Solteiro": (27, [("Capa edredom solteiro", 2, 1.5), ("Edredom Solteiro", 2, 1),
                      ("Lençol solteiro", 2, 1.5), ("Protetor de colchão solteiro", 1, 1.1)]),
    "King": (81, [("Capa edredom king", 1, 3), ("Colcha Piquet king", 0.1, 1), ("Edredom King", 1, 1.1),
                  ("Fronha grande 3 abas", 2, 3), ("Fronha média 3 abas", 2, 3), ("Lençol king", 1, 3),
                  ("Piso banheiro", 1, 3), ("Protetor de colchão king", 1, 1.1),
                  ("Protetor de travesseiro grande", 2, 1.1), ("Protetor travesseiro médio", 2, 1.1),
                  ("Roupão", 2, 1.5), ("Toalha banho king", 2, 3), ("Toalha rosto", 2, 3)]),
    "Piscina": (1, [("Toalha Praia Lelis", 89, 1), ("Toalha piscina Carolina Herrera", 162, 1),
                    ("Toalha piscina listrada", 35, 1)]),
}

now = time.time()
with psycopg.connect(os.environ["DATABASE_URL"]) as con:
    for nome, tipo, alerta in AMBIENTES:
        con.execute("""INSERT INTO destinos(tipo,nome,ativo,alerta,criado_em) VALUES(%s,%s,1,%s,%s)
                       ON CONFLICT(nome) DO UPDATE SET tipo=excluded.tipo, alerta=excluded.alerta, ativo=1""",
                    (tipo, nome, alerta, now))
    for nome, modelo, amb in ANTENAS:
        did = con.execute("SELECT id FROM destinos WHERE nome=%s", (amb,)).fetchone()[0]
        con.execute("""INSERT INTO antenas(nome,modelo,destino_id,criado_em) VALUES(%s,%s,%s,%s)
                       ON CONFLICT(nome) DO UPDATE SET modelo=excluded.modelo, destino_id=excluded.destino_id""",
                    (nome, modelo, did, now))
    for nome, (uhs, itens) in ACOMODACOES.items():
        aid = con.execute("""INSERT INTO acomodacoes(nome,uhs,criado_em) VALUES(%s,%s,%s)
                             ON CONFLICT(nome) DO UPDATE SET uhs=excluded.uhs RETURNING id""", (nome, uhs, now)).fetchone()[0]
        con.execute("DELETE FROM acomodacao_itens WHERE acomodacao_id=%s", (aid,))
        with con.cursor() as cur:
            cur.executemany("INSERT INTO acomodacao_itens(acomodacao_id,tipo,qtd,fluxo) VALUES(%s,%s,%s,%s)",
                            [(aid, t, q, f) for t, q, f in itens])
    n = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("destinos", "antenas", "acomodacoes", "acomodacao_itens")]
print(f"destinos={n[0]} antenas={n[1]} acomodacoes={n[2]} itens_enxoval={n[3]}")
