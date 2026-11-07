import time
from contextlib import closing
from tests.conftest import ingest
from app import main


def main_db():
    return closing(main.db())


main_agregar, main_ciclo = main.agregar_horas, main.ciclo_sessao


def test_ingest_so_grava_com_leitura_armada(client):
    main.state["armado_ate"] = 0.0
    main.state["conf_ativa"], main.state["conf_ate"] = False, 9e12
    r = client.post("/ingest", auth=("w", "w"), json=[{"tagInventoryEvent": {"epc": "ZZ99"}}])
    assert r.json() == {"ok": True, "received": 1, "stored": 0}
    assert client.get("/api/events").json() == []
    assert client.get("/api/summary").json()["last_event"] is not None  # sinal do rádio segue visível
    main.state["conf_ate"] = 0.0


def test_epc_base64_vira_hex(client):
    client.post("/api/leitura/armar?segundos=60")
    r = client.post("/ingest", auth=("w", "w"), json=[{"tagInventoryEvent": {"epc": "MA9PVzrQBABQsTtx"}}])
    assert r.json()["received"] == 1
    assert client.get("/api/events").json()[0]["epc"] == "300F4F573AD0040050B13B71"


def test_ingest_exige_credencial(client):
    assert client.post("/ingest", json=[]).status_code == 401


def test_lote_transferencia_e_409(client):
    ingest(client, "AA11", "BB22")
    lote = client.post("/api/lotes/registrar?janela_segundos=60").json()
    assert lote["quantidade"] == 2
    d = client.get("/api/destinos").json()[0]
    ok = client.post(f"/api/lotes/{lote['id']}/transferir", json={"destino_id": d["id"]})
    assert ok.status_code == 200
    assert client.post(f"/api/lotes/{lote['id']}/transferir", json={"destino_id": d["id"]}).status_code == 409
    t = client.get("/api/transferencias").json()[0]
    assert t["usuario"] == "admin" and t["quantidade"] == 2


def test_lote_sem_leitura_da_400(client):
    assert client.post("/api/lotes/registrar?janela_segundos=5").status_code == 400


def test_saida_documentada_e_nao(client):
    ingest(client, "AA11")
    lote = client.post("/api/lotes/registrar").json()
    d = client.get("/api/destinos").json()[0]
    client.post(f"/api/lotes/{lote['id']}/transferir", json={"destino_id": d["id"]})
    doc = client.post("/api/saida", auth=("s", "s"), json={"epc": "aa11", "destino_id": d["id"]}).json()
    assert doc["documentado"] is True  # epc normalizado para hex maiúsculo
    assert client.post("/api/saida", auth=("s", "s"), json={"epc": "ZZ99", "destino_id": d["id"]}).json()["documentado"] is False
    assert client.post("/api/saida", auth=("s", "s"), json={"epc": " ", "destino_id": d["id"]}).status_code == 400
    assert len(client.get("/api/saida?documentado=false").json()) == 1


def test_catalogo_inventario_e_esperado(client):
    client.post("/api/itens", json={"epc": "e2001", "tipo": "Toalha rosto"})
    client.post("/api/itens", json={"epc": "E2002", "tipo": "Toalha rosto"})
    assert client.get("/api/inventario?estado=Em uso").json()["total"] == 2
    client.post("/api/acomodacoes", json={"nome": "King", "uhs": 10, "itens": [{"tipo": "Toalha rosto", "qtd": 2, "fluxo": 3}]})
    linha = client.get("/api/enxoval/esperado").json()["linhas"][0]
    assert linha == {"tipo": "Toalha rosto", "esperado": 60.0, "atual": 2, "diferenca": -58.0}


def test_conferencia_fluxo(client):
    for e in ("A1", "A2", "A3"):
        client.post("/api/itens", json={"epc": e, "tipo": "Roupão"})
    c = client.post("/api/conferencias", json={"filtros": {"estado": "Em uso"}}).json()
    assert c["esperados"] == 3
    assert client.post("/api/conferencias", json={}).status_code == 409
    ingest(client, "A1", "A2")
    d = client.get(f"/api/conferencias/{c['id']}").json()
    assert (d["conferidos"], d["nao_conferidos"], d["percentual"], d["usuario"]) == (2, 1, 67, "admin")
    assert client.post(f"/api/conferencias/{c['id']}/finalizar").json()["conferidos"] == 2
    assert client.post(f"/api/conferencias/{c['id']}/finalizar").status_code == 409
    assert client.post("/api/conferencias", json={"filtros": {"tipo": "Nada"}}).status_code == 400


def test_login_papeis_e_cookie(client):
    anon = client.__class__(client.app)
    assert anon.get("/api/summary").status_code == 401
    assert anon.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/api/usuarios", json={"usuario": "ana", "papel": "leitura", "senha": "senha-forte-1"}).status_code == 200
    assert client.post("/api/usuarios", json={"usuario": "ana"}).status_code == 409
    assert client.post("/api/usuarios", json={"usuario": "x", "senha": "curta"}).status_code == 400
    sem = client.post("/api/usuarios", json={"usuario": "bia", "papel": "operador"})
    assert sem.status_code == 200
    assert anon.post("/api/login", json={"usuario": "bia", "senha": "qualquer"}).status_code == 401  # sem senha não loga
    assert anon.post("/api/login", json={"usuario": "ana", "senha": "errada"}).status_code == 401
    assert anon.post("/api/login", json={"usuario": "ana", "senha": "senha-forte-1"}).status_code == 200
    assert anon.get("/api/me").json() == {"usuario": "ana", "nome": "ana", "papel": "leitura"}
    assert anon.get("/api/summary").status_code == 200
    assert anon.post("/api/itens", json={"epc": "Z1", "tipo": "x"}).status_code == 403  # leitura não escreve
    assert anon.get("/api/usuarios").status_code == 403
    uid = next(u["id"] for u in client.get("/api/usuarios").json() if u["usuario"] == "ana")
    client.patch(f"/api/usuarios/{uid}", json={"papel": "operador"})
    assert anon.post("/api/itens", json={"epc": "Z1", "tipo": "x"}).status_code == 200  # papel vale na hora
    client.patch(f"/api/usuarios/{uid}", json={"ativo": False})
    assert anon.get("/api/me").status_code == 401  # desativado derruba a sessão


def test_login_bloqueia_apos_tentativas(client):
    for _ in range(8):
        client.post("/api/login", json={"usuario": "admin", "senha": "x"})
    assert client.post("/api/login", json={"usuario": "admin", "senha": "admin-pass"}).status_code == 429


def test_resumo_por_hora_e_csv(client):
    ingest(client, "AA11", "AA11", "BB22")
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão"})
    with main_db() as con:
        main_agregar(con)
    h = client.get("/api/leituras-hora").json()
    assert h[0]["leituras"] == 3 and h[0]["tags"] == 2
    csv = client.get("/api/export/leituras.csv").text
    assert csv.startswith("﻿hora_local;epc;item;leituras") and "AA11;Roupão;2" in csv
    assert "AA11;Roupão" in client.get("/api/export/inventario.csv").text
    assert client.get("/api/export/transferencias.csv").status_code == 200


def test_sessao_automatica_vira_lote(client):
    import time as _t
    agora = _t.time()
    with main_db() as con:
        for epc, dt in (("S1", -100), ("S2", -90), ("S1", -80)):
            con.execute("INSERT INTO events(received_at,epc,raw) VALUES(%s,%s,'{}')", (agora + dt, epc))
        con.commit()
        main_ciclo(con, agora - 85, 30, agora - 200)   # leitura recente -> abre sessão
        assert con.execute("SELECT status FROM sessoes").fetchone()["status"] == "aberta"
        main_ciclo(con, agora, 30, agora - 200)        # 80s de silêncio -> encerra e cria lote
        s = con.execute("SELECT * FROM sessoes").fetchone()
    assert s["status"] == "encerrada" and s["tags"] == 2 and s["lote_id"]
    assert client.get("/api/lotes?status=aguardando").json()[0]["quantidade"] == 2


def test_alertas_whatsapp_transicoes(client, monkeypatch):
    monkeypatch.setattr(main, "INICIO", 0.0)
    main.state["alertas"].clear()
    t = 1_000_000.0
    assert main.avaliar_alertas(t, t - 3600, 0, main.CFG_PADRAO) and "sem enviar sinal" in main.avaliar_alertas(t + 7200 * 2, t - 3600, 0)[0][1]
    main.state["alertas"].clear()
    assert len(main.avaliar_alertas(t, t - 3600, 0, main.CFG_PADRAO)) == 1      # alerta
    assert main.avaliar_alertas(t + 60, t - 3540, 0) == []     # sem repetir
    assert "voltou" in main.avaliar_alertas(t + 120, t + 100, 0)[0][1]  # retomada
    assert "falharam" in main.avaliar_alertas(t + 200, t + 190, 5)[0][1]
    main.state["alertas"].clear()


def test_config_alertas_pela_ui(client):
    c = client.get("/api/config/alertas").json()
    assert c["wa_apikey_definida"] is False
    r = client.put("/api/config/alertas", json={"wa_instance": "inst", "wa_apikey": "segredo",
                                                "sem_sinal_seg": 45})
    assert r.status_code == 200 and r.json()["wa_apikey_definida"] and "segredo" not in r.text
    client.put("/api/config/alertas", json={"wa_instance": "inst", "sem_sinal_seg": 45})
    assert client.get("/api/config/alertas").json()["wa_apikey_definida"]  # vazio mantém a chave
    assert main.cfg()["sem_sinal_seg"] == "45"


def test_antena_cria_edita_e_valida(client):
    client.post("/api/destinos", json={"tipo": "setor", "nome": "Recepção"})
    did = client.get("/api/destinos").json()[0]["id"]
    body = {"nome": "edge1", "descricao": "RFID Recepção", "modelo": "AcuraEdgeR30", "destino_id": did,
            "modo": "automatico", "alerta": True, "ip": "192.168.30.32", "porta": 8080}
    assert client.post("/api/antenas", json=body).status_code == 200
    assert client.post("/api/antenas", json=body).status_code == 409
    a = client.get("/api/antenas").json()[0]
    assert a["ip"] == "192.168.30.32" and a["porta"] == 8080 and a["alerta"] is True
    r = client.put(f"/api/antenas/{a['id']}", json={**body, "nome": "edge1-b", "modo": "manual", "alerta": False, "porta": None})
    assert r.status_code == 200
    a = client.get("/api/antenas").json()[0]
    assert a["nome"] == "edge1-b" and a["modo"] == "manual" and a["alerta"] is False and a["porta"] is None
    assert client.put(f"/api/antenas/{a['id']}", json={**body, "modo": "x"}).status_code == 400
    assert client.put("/api/antenas/99999", json=body).status_code == 404


def test_destinatarios_alerta(client, monkeypatch):
    enviados = []
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: enviados.append(n) or None)
    assert client.post("/api/alertas/destinatarios", json={"nome": "Ana", "whatsapp": "(11) 99999-0001"}).status_code == 200
    client.post("/api/alertas/destinatarios", json={"nome": "Beto", "whatsapp": "5511999990002", "recebe_falhas": False})
    client.post("/api/alertas/destinatarios", json={"nome": "Inativo", "whatsapp": "5511999990003", "ativo": False})
    assert client.post("/api/alertas/destinatarios", json={"nome": "Dup", "whatsapp": "11999990001"}).status_code == 409
    assert client.post("/api/alertas/destinatarios", json={"nome": "Ruim", "whatsapp": "123"}).status_code == 400
    lista = client.get("/api/alertas/destinatarios").json()
    assert {d["whatsapp"] for d in lista} == {"5511999990001", "5511999990002", "5511999990003"}  # DDI 55 assumido
    main.notificar("falhas", "x")
    assert enviados == ["5511999990001"]  # Beto não recebe falhas, Inativo não recebe nada
    enviados.clear()
    main.notificar("sem_sinal", "y")
    assert sorted(enviados) == ["5511999990001", "5511999990002"]
    ana = next(d for d in lista if d["nome"] == "Ana")
    assert client.post(f"/api/alertas/destinatarios/{ana['id']}/teste").status_code == 200
    assert len(client.get("/api/alertas/envios").json()) == 4
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: "Evolution respondeu HTTP 401")
    assert client.post(f"/api/alertas/destinatarios/{ana['id']}/teste").status_code == 400
    assert client.delete(f"/api/alertas/destinatarios/{ana['id']}").status_code == 200
    assert len(client.get("/api/alertas/destinatarios").json()) == 2


def test_destinatario_grupo(client, monkeypatch):
    enviados = []
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: enviados.append(n) or None)
    assert client.post("/api/alertas/destinatarios", json={"nome": "Grupo TI", "whatsapp": "120363123456789012@g.us"}).status_code == 200
    assert client.post("/api/alertas/destinatarios", json={"nome": "Ruim", "whatsapp": "abc@g.us"}).status_code == 400
    main.notificar("sem_sinal", "x")
    assert enviados == ["120363123456789012@g.us"]
    assert client.post("/api/alertas/grupos/atualizar").status_code == 400  # sem Evolution configurada
    assert client.get("/api/alertas/grupos").json()["status"] in ("vazio", "pronto", "erro")


def test_alerta_nao_dispara_logo_apos_restart(client):
    main.state["alertas"].clear()
    agora = 2_000_000.0
    # último evento no banco é de 1h atrás, mas o app acabou de subir (INICIO = agora - 5s)
    assert main.avaliar_alertas(agora, agora - 3600, 0, main.CFG_PADRAO, inicio=agora - 5) == []
    assert main.avaliar_alertas(agora + 70, agora - 3600, 0, main.CFG_PADRAO, inicio=agora - 5)[0][0] == "sem_sinal"
    main.state["alertas"].clear()


def test_movimentacao_cabine_completa(client, monkeypatch):
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Toalha piscina", "cor": "Branco"})
    client.post("/api/itens", json={"epc": "BB22", "tipo": "Toalha piscina"})
    client.post("/api/destinos", json={"tipo": "setor", "nome": "Piscina"})
    did = next(d["id"] for d in client.get("/api/destinos").json() if d["nome"] == "Piscina")
    ingest(client, "AA11", "BB22", "CC33")
    lote = client.post("/api/lotes/registrar?janela_segundos=60").json()
    assert len(lote["identificados"]) == 2 and lote["novos"] == ["CC33"]
    assert lote["identificados"][0]["estado"] and lote["identificados"][0]["localizacao"] is None
    # definir estado em lote
    est = next(e for e in client.get("/api/estados").json() if not e["padrao"])
    r = client.post(f"/api/lotes/{lote['id']}/estado", json={"estado_id": est["id"]}).json()
    assert r["itens"] == 2 and r["estado"] == est["nome"]
    assert client.post(f"/api/lotes/{lote['id']}/estado", json={"estado_id": 9999}).status_code == 400
    # divergência sem destinatário -> 400; com destinatário -> envia
    assert client.post(f"/api/lotes/{lote['id']}/divergencia", json={"esperado": 5}).status_code == 400
    enviados = []
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: enviados.append(t) or None)
    client.post("/api/alertas/destinatarios", json={"nome": "Ana", "whatsapp": "5511999990001"})
    assert client.post(f"/api/lotes/{lote['id']}/divergencia", json={"esperado": 5}).json()["enviados"] == 1
    assert "detectado 3, esperado 5" in enviados[0]
    # movimentar + comprovante + localização
    t = client.post(f"/api/lotes/{lote['id']}/transferir", json={"destino_id": did}).json()
    comp = client.get(f"/api/transferencias/{t['transferencia_id']}/comprovante").json()
    assert comp["destino"] == "Piscina" and comp["total"] == 3
    assert {i["tipo"]: i["qtd"] for i in comp["itens"]} == {"Toalha piscina": 2, "Sem cadastro": 1}
    assert client.get(f"/api/lotes/{lote['id']}").json()["identificados"][0]["localizacao"] == "Piscina"
    # lote transferido não pode ser descartado; lote novo pode (reiniciar leitura)
    assert client.delete(f"/api/lotes/{lote['id']}").status_code == 409
    ingest(client, "AA11")
    novo = client.post("/api/lotes/registrar?janela_segundos=60").json()
    assert client.delete(f"/api/lotes/{novo['id']}").status_code == 200
    assert client.get(f"/api/lotes/{novo['id']}").status_code == 404


def test_agente_status_comandos_e_alertas(client):
    client.post("/api/antenas", json={"nome": "edge1", "modelo": "ImpinjR700", "ip": "192.168.30.5", "porta": 80, "alerta": True})
    aid = client.get("/api/antenas").json()[0]["id"]
    # sem token / token inválido
    assert client.get("/api/agente/config", auth=None).status_code == 401
    assert client.get("/api/agente/config", headers={"X-Agent-Token": "x"}, auth=None).status_code == 401
    ag = client.post("/api/agentes", json={"nome": "hotel"}).json()
    H = {"X-Agent-Token": ag["token"]}
    assert client.post("/api/agentes", json={"nome": "hotel"}).status_code == 409
    cfg = client.get("/api/agente/config", headers=H, auth=None).json()
    assert cfg["antenas"][0]["ip"] == "192.168.30.5" and cfg["intervalo_seg"] > 0
    # comando: valida caminho/método/corpo
    assert client.post(f"/api/antenas/{aid}/comandos", json={"caminho": "/etc/passwd"}).status_code == 400
    assert client.post(f"/api/antenas/{aid}/comandos", json={"caminho": "/api/v1/../x"}).status_code == 400
    assert client.post(f"/api/antenas/{aid}/comandos", json={"metodo": "DELETE", "caminho": "/api/v1/system"}).status_code == 400
    assert client.post(f"/api/antenas/{aid}/comandos", json={"metodo": "PUT", "caminho": "/api/v1/system", "corpo": "{ruim"}).status_code == 400
    cid = client.post(f"/api/antenas/{aid}/comandos", json={"caminho": "/api/v1/system"}).json()["id"]
    r = client.post("/api/agente/status", headers=H, auth=None, json={"versao": "1.0", "host": "PC-HOTEL", "antenas": [
        {"id": aid, "online": True, "latencia_ms": 12, "detalhe": "ok", "extra": {"serial": "123"}}]}).json()
    assert [c["id"] for c in r["comandos"]] == [cid] and r["comandos"][0]["caminho"] == "/api/v1/system"
    assert client.post("/api/agente/status", headers=H, auth=None, json={"antenas": []}).json()["comandos"] == []  # não reenvia
    assert client.get(f"/api/comandos/{cid}").json()["status"] == "enviado"
    client.post(f"/api/agente/comandos/{cid}/resultado", headers=H, auth=None, json={"ok": True, "http_status": 200, "resposta": "{}"})
    assert client.get(f"/api/comandos/{cid}").json()["status"] == "ok"
    a = client.get("/api/antenas").json()[0]
    assert a["online"] is True and a["latencia_ms"] == 12
    # alertas: antena offline -> 1 aviso; volta -> aviso de retomada; agente sem contato
    main.state["alertas"].clear()
    now = time.time()
    c = main.cfg()
    def ant(online): return [{"id": aid, "nome": "edge1", "ip": "192.168.30.5", "alerta": True, "online": online, "detalhe": "timeout", "verificado_em": now}]
    msgs = main.avaliar_agentes(now, ant(False), [], c)
    assert len(msgs) == 1 and msgs[0][0] == "antena_offline" and "OFFLINE" in msgs[0][1]
    assert main.avaliar_agentes(now + 5, ant(False), [], c) == []
    assert "voltou" in main.avaliar_agentes(now + 10, ant(True), [], c)[0][1]
    msgs = main.avaliar_agentes(now + 1000, [], [{"id": 1, "nome": "hotel", "visto_em": now}], c)
    assert msgs[0][0] == "agente_offline" and "sem contato" in msgs[0][1]
    main.state["alertas"].clear()
