#!/usr/bin/env python3
"""Popula destinos e catálogo com dados de exemplo (tipos/atributos vistos no eTrace),
simula leituras e transferências pra deixar Inventário e Movimentações com dados reais
pra demonstração. Seguro rodar mais de uma vez (cadastro é upsert; leituras/transferências
novas só se somam ao histórico, o que é esperado).

Uso:
  BASE_URL=http://localhost:8000 UI_USER=u UI_PASS=p WEBHOOK_USER=w WEBHOOK_PASS=p \
    python3 scripts/seed_exemplo.py
"""
import json
import os
import time
import urllib.request
from base64 import b64encode

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
UI_USER, UI_PASS = os.environ.get("UI_USER", "u"), os.environ.get("UI_PASS", "p")
WEBHOOK_USER, WEBHOOK_PASS = os.environ.get("WEBHOOK_USER", UI_USER), os.environ.get("WEBHOOK_PASS", UI_PASS)

# Tipos e atributos vistos nos prints de referência (eTrace / Fera Palace Hotel).
# Só "Toalha banho king" tinha os atributos completos visíveis no print da cabine;
# os demais ficam sem fabricante/tamanho/composição/cor (fica livre pra completar depois).
ITENS = [
    dict(epc="EX-CAPAKING-01", tipo="Capa edredom king"),
    dict(epc="EX-CAPASOLT-01", tipo="Capa edredom solteiro"),
    dict(epc="EX-COLCHA-01", tipo="Colcha Piquet king"),
    dict(epc="EX-TOALHABANHO-01", tipo="Toalha banho king", fabricante="Buddemeyer",
         tamanho="100x150", composicao="100% algodão", cor="Branco"),
    dict(epc="EX-TOALHABANHO-02", tipo="Toalha banho king", fabricante="Buddemeyer",
         tamanho="100x150", composicao="100% algodão", cor="Branco"),
    dict(epc="EX-TOALHAROSTO-01", tipo="Toalha rosto"),
    dict(epc="EX-PISOBANHEIRO-01", tipo="Piso banheiro"),
    dict(epc="EX-ROUPAO-01", tipo="Roupão"),
    dict(epc="EX-TOALHAPRAIA-01", tipo="Toalha Praia Lelis"),
]

# Localizações (setores) vistas como "Última localização" nos prints de Antenas de Controle.
DESTINOS = [("setor", "Apartamentos"), ("setor", "Rouparia Geral"), ("setor", "Piscina")]

# Dois grupos lidos e transferidos, pra Inventário/Movimentações mostrarem estados variados;
# o restante dos ITENS fica "Sem leitura" de propósito (mistura realista).
GRUPO_APARTAMENTOS = ["EX-TOALHABANHO-01", "EX-TOALHABANHO-02", "EX-TOALHAROSTO-01"]
GRUPO_ROUPARIA = ["EX-PISOBANHEIRO-01", "EX-ROUPAO-01"]


def auth_header(user, pw):
    return "Basic " + b64encode(f"{user}:{pw}".encode()).decode()


def call(method, path, auth, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                  headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    ui = auth_header(UI_USER, UI_PASS)
    wh = auth_header(WEBHOOK_USER, WEBHOOK_PASS)

    print("== destinos ==")
    destino_id = {}
    for tipo, nome in DESTINOS:
        status, body = call("POST", "/api/destinos", ui, {"tipo": tipo, "nome": nome})
        if status == 409:
            _, lst = call("GET", f"/api/destinos?tipo={tipo}", ui)
            body = next(d for d in lst if d["nome"] == nome)
        destino_id[nome] = body["id"]
        print(f"  {nome}: id={body['id']} (status {status})")

    print("== catálogo ==")
    for item in ITENS:
        status, body = call("POST", "/api/itens", ui, item)
        print(f"  {item['epc']} -> {item['tipo']} (status {status})")

    # janela curta e intervalo entre grupos maior que ela, pra cada lote pegar só as
    # tags do seu próprio grupo (senão um grupo lido logo depois do outro "herda" as
    # tags recentes do grupo anterior, já que a janela olha pro passado).
    JANELA = 2
    for grupo, destino_nome in ((GRUPO_APARTAMENTOS, "Apartamentos"), (GRUPO_ROUPARIA, "Rouparia Geral")):
        print(f"== leitura + transferência para {destino_nome} ==")
        events = [{"eventType": "tagInventory", "tagInventoryEvent": {"epcHex": epc}} for epc in grupo]
        call("POST", "/ingest", wh, {"events": events})
        status, lote = call("POST", f"/api/lotes/registrar?janela_segundos={JANELA}", ui, None)
        if status != 200:
            print(f"  falha ao registrar lote: {lote}")
            continue
        status, transf = call("POST", f"/api/lotes/{lote['id']}/transferir", ui,
                               {"destino_id": destino_id[destino_nome]})
        print(f"  lote #{lote['id']} ({lote['quantidade']} itens) -> {destino_nome} (status {status})")
        time.sleep(JANELA + 1)

    print("Pronto. Confira em /api/inventario e na tela 'Inventário'.")


if __name__ == "__main__":
    main()
