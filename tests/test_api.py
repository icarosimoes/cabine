import json
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
    assert client.post("/api/saida", auth=("s", "s"), json={"epc": "CC99", "destino_id": d["id"]}).json()["documentado"] is False
    assert client.post("/api/saida", auth=("s", "s"), json={"epc": " ", "destino_id": d["id"]}).status_code == 400
    assert len(client.get("/api/saida?documentado=false").json()) == 1


def test_catalogo_inventario_e_esperado(client):
    client.post("/api/itens", json={"epc": "e20010", "tipo": "Toalha rosto"})
    client.post("/api/itens", json={"epc": "E20020", "tipo": "Toalha rosto"})
    assert client.get("/api/inventario?estado=Em uso").json()["total"] == 2
    client.post("/api/acomodacoes", json={"nome": "King", "uhs": 10, "itens": [{"tipo": "Toalha rosto", "qtd": 2, "fluxo": 3}]})
    linha = client.get("/api/enxoval/esperado").json()["linhas"][0]
    assert linha == {"tipo": "Toalha rosto", "esperado": 60.0, "atual": 2, "diferenca": -58.0}


def test_conferencia_fluxo(client):
    for e in ("A100", "A200", "A300"):
        client.post("/api/itens", json={"epc": e, "tipo": "Roupão"})
    c = client.post("/api/conferencias", json={"filtros": {"estado": "Em uso"}}).json()
    assert c["esperados"] == 3
    assert client.post("/api/conferencias", json={}).status_code == 409
    ingest(client, "A100", "A200")
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
    me = anon.get("/api/me").json()
    assert (me["usuario"], me["papel"]) == ("ana", "leitura") and "consultar" in me["permissoes"] and "catalogo_editar" not in me["permissoes"]
    assert anon.get("/api/summary").status_code == 200
    assert anon.post("/api/itens", json={"epc": "AB01", "tipo": "x"}).status_code == 403  # leitura não escreve
    assert anon.get("/api/usuarios").status_code == 403
    uid = next(u["id"] for u in client.get("/api/usuarios").json() if u["usuario"] == "ana")
    client.patch(f"/api/usuarios/{uid}", json={"papel": "operador"})
    assert anon.post("/api/itens", json={"epc": "AB01", "tipo": "x"}).status_code == 200  # papel vale na hora
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


def test_alerta_webhook_do_radio(client):
    main.state["alertas"].clear()
    now = time.time()
    c = main.cfg()
    def ant(st, code): return [{"id": 9, "nome": "r700", "ip": "1.1.1.1", "alerta": True, "online": True, "detalhe": "ok", "verificado_em": now,
                                "extra": json.dumps({"eventWebhookStatus": {"status": st, "httpStatusCode": code}})}]
    assert main.avaliar_agentes(now, ant("active", 200), [], c) == []
    m = main.avaliar_agentes(now, ant("active", 401), [], c)
    assert m[0][0] == "webhook_falha" and "FALHA" in m[0][1] and "401" in m[0][1]
    assert main.avaliar_agentes(now + 5, ant("active", 401), [], c) == []
    assert "voltou" in main.avaliar_agentes(now + 10, ant("active", 200), [], c)[0][1]
    main.state["alertas"].clear()


def test_antena_de_evasao_flag_config_e_alerta(client):
    body = {"nome": "saida", "modelo": "AcuraEdgeR30", "ip": "192.168.30.104", "porta": 8080, "alerta": False, "evasao": True}
    assert client.post("/api/antenas", json=body).status_code == 200
    a = client.get("/api/antenas").json()[0]
    assert a["evasao"] is True
    ag = client.post("/api/agentes", json={"nome": "h"}).json()
    cfg = client.get("/api/agente/config", headers={"X-Agent-Token": ag["token"]}, auth=None).json()
    assert cfg["antenas"][0]["evasao"] is True  # reflete no endpoint do agente
    assert client.put(f"/api/antenas/{a['id']}", json={**body, "evasao": False}).status_code == 200
    assert client.get("/api/antenas").json()[0]["evasao"] is False
    # alerta sai mesmo com "alerta" desligado quando é de evasão, com prefixo prioritário
    main.state["alertas"].clear()
    now = time.time()
    ant = [{"id": 5, "nome": "saida", "ip": "1.1.1.1", "alerta": False, "evasao": True, "online": False, "detalhe": "porta 8080 FECHADA", "verificado_em": now, "extra": None}]
    m = main.avaliar_agentes(now, ant, [], main.cfg())
    assert m and "EVASÃO" in m[0][1] and "OFFLINE" in m[0][1]
    main.state["alertas"].clear()
    ant[0]["evasao"] = False  # sem evasão e sem alerta: nada
    assert main.avaliar_agentes(now, ant, [], main.cfg()) == []
    main.state["alertas"].clear()


def test_download_do_agente(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "AGENTE_DIST", tmp_path)
    assert client.get("/api/agente/download/cabine-agent.exe").status_code == 404  # não publicado
    (tmp_path / "instalar.cmd").write_text("echo ok")
    r = client.get("/api/agente/download/instalar.cmd")
    assert r.status_code == 200 and r.text == "echo ok"
    assert client.get("/api/agente/download/../main.py").status_code in (404, 422)
    assert client.get("/api/agente/download/outro.txt").status_code == 404
    assert client.get("/api/agente/download/instalar.cmd", auth=None).status_code in (401, 302, 307)


def test_rate_limit_ignora_xff_forjado_e_cobre_basic(client):
    # cliente forja o 1º valor do X-Forwarded-For; vale o último (o que o proxy acrescentou)
    for i in range(8):
        client.post("/api/login", json={"usuario": "admin", "senha": "x"}, headers={"x-forwarded-for": f"9.9.9.{i}, 5.5.5.5"})
    r = client.post("/api/login", json={"usuario": "admin", "senha": "admin-pass"}, headers={"x-forwarded-for": "1.1.1.1, 5.5.5.5"})
    assert r.status_code == 429
    # Basic Auth também conta e bloqueia
    for _ in range(8):
        client.get("/api/me", auth=("admin", "errada"), headers={"x-forwarded-for": "7.7.7.7"})
    assert client.get("/api/me", auth=("admin", "admin-pass"), headers={"x-forwarded-for": "7.7.7.7"}).status_code == 429
    # ingest (Basic do webhook) também
    for _ in range(8):
        client.post("/ingest", json=[], auth=("w", "errada"), headers={"x-forwarded-for": "8.8.8.8"})
    assert client.post("/ingest", json=[], auth=("w", "w"), headers={"x-forwarded-for": "8.8.8.8"}).status_code == 429


def test_falhas_sao_expurgadas():
    from app import main
    main._falhas.clear()
    main._falhas["velho"] = [time.time() - 10_000]
    main.bloqueado("novo")
    assert "velho" not in main._falhas


def test_dashboard_monitoramento_e_numeros(client):
    vazio = client.get("/api/dashboard").json()
    assert vazio["antenas"] == [] and vazio["resumo"]["antenas_total"] == 0 and vazio["conferencia"] is None
    client.post("/api/antenas", json={"nome": "r700", "modelo": "ImpinjR700", "ip": "10.0.0.1", "porta": 443, "alerta": True})
    client.post("/api/antenas", json={"nome": "acura", "modelo": "AcuraEdgeR30", "ip": "10.0.0.2", "porta": 80, "evasao": True})
    ids = {a["nome"]: a["id"] for a in client.get("/api/antenas").json()}
    d = client.get("/api/dashboard").json()
    assert d["resumo"]["antenas_sem_info"] == 2 and all(a["online"] is None for a in d["antenas"])  # nunca verificadas
    ag = client.post("/api/agentes", json={"nome": "hotel"}).json()
    H = {"X-Agent-Token": ag["token"]}
    client.post("/api/agente/status", headers=H, auth=None, json={"versao": "0.1.6", "host": "PC", "antenas": [
        {"id": ids["r700"], "online": True, "latencia_ms": 20, "detalhe": "running",
         "extra": {"firmware": "9.9", "activePreset": {"id": "etrace"}, "eventWebhookStatus": {"status": "active", "httpStatusCode": 200}, "senha": "x"}},
        {"id": ids["acura"], "online": False, "detalhe": "sem resposta"}]})
    d = client.get("/api/dashboard").json()
    por_nome = {a["nome"]: a for a in d["antenas"]}
    assert por_nome["r700"]["online"] is True and por_nome["acura"]["online"] is False and por_nome["acura"]["evasao"] is True
    assert por_nome["r700"]["extra"] == {"firmware": "9.9", "preset": "etrace", "webhook": {"status": "active", "http": 200}}  # só campos seguros
    assert d["resumo"]["antenas_online"] == 1 and d["resumo"]["problemas"] == ["acura"]
    assert d["agentes"][0]["online"] is True and d["agentes"][0]["versao"] == "0.1.6"
    # operação: inventário, lote e estados
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão"})
    ingest_e_lote(client)
    d = client.get("/api/dashboard").json()
    assert d["inventario"]["total"] == 1 and sum(e["qtd"] for e in d["inventario"]["por_estado"]) == 1
    assert d["cabine"]["lotes_aguardando"] == 1 and d["cabine"]["pecas_aguardando"] >= 1
    assert d["evasao"]["nao_documentadas_24h"] == 0
    assert client.get("/api/dashboard", auth=None).status_code == 401  # exige login


def ingest_e_lote(client):
    with main_db() as con:
        lid = con.execute("INSERT INTO lotes(criado_em,janela_segundos,quantidade,status) VALUES(%s,15,1,'aguardando') RETURNING id", (time.time(),)).fetchone()["id"]
        con.execute("INSERT INTO lote_itens(lote_id,epc) VALUES(%s,'AA11')", (lid,))
        con.commit()


def test_perfil_painel_so_acessa_o_dashboard(client):
    assert client.post("/api/usuarios", json={"usuario": "gerente", "papel": "painel", "senha": "senha-forte-1"}).status_code == 200
    assert client.post("/api/usuarios", json={"usuario": "x", "papel": "inexistente"}).status_code == 400
    p = client.__class__(client.app)
    assert p.post("/api/login", json={"usuario": "gerente", "senha": "senha-forte-1"}).status_code == 200
    assert p.get("/api/me").json()["papel"] == "painel"
    for ok in ("/api/dashboard", "/api/summary", "/api/me"):
        assert p.get(ok).status_code == 200, ok
    for negado in ("/api/lotes", "/api/itens", "/api/antenas", "/api/destinos", "/api/tags", "/api/events", "/api/saida",
                   "/api/inventario", "/api/export/inventario.csv", "/api/conferencias", "/api/transferencias", "/api/usuarios",
                   "/api/agentes", "/api/alertas/destinatarios", "/api/config/alertas", "/api/leituras-hora", "/api/sessoes"):
        assert p.get(negado).status_code == 403, negado
    assert p.post("/api/lotes/registrar", json={}).status_code == 403
    assert p.post("/api/leitura/armar", json={}).status_code == 403
    # o papel vale na hora: promovido a leitura passa a enxergar o resto
    uid = next(u["id"] for u in client.get("/api/usuarios").json() if u["usuario"] == "gerente")
    client.patch(f"/api/usuarios/{uid}", json={"papel": "leitura"})
    assert p.get("/api/lotes").status_code == 200


def test_nenhuma_rota_get_da_api_abre_para_o_perfil_painel(client):
    """Rota nova em /api/* sem ui_auth/op_auth/admin_auth vira acesso indevido do perfil 'painel': este teste pega."""
    client.post("/api/usuarios", json={"usuario": "gerente", "papel": "painel", "senha": "senha-forte-1"})
    p = client.__class__(client.app)
    p.post("/api/login", json={"usuario": "gerente", "senha": "senha-forte-1"})
    permitidas = {"/api/dashboard", "/api/summary", "/api/me", "/api/login", "/api/logout"}
    abertas = []
    for r in client.app.routes:
        caminho = getattr(r, "path", "")
        if not caminho.startswith("/api/") or "GET" not in getattr(r, "methods", ()) or "{" in caminho or caminho in permitidas:
            continue
        if caminho.startswith("/api/agente/"):  # autenticação própria por token do agente
            continue
        if p.get(caminho).status_code != 403:
            abertas.append(caminho)
    assert abertas == [], f"rotas acessíveis ao perfil painel: {abertas}"


def test_pwa_arquivos_publicos_e_manifest_valido(client):
    anon = client.__class__(client.app)  # sem login: o navegador busca estes arquivos sem cookie
    m = anon.get("/manifest.webmanifest")
    assert m.status_code == 200 and "manifest+json" in m.headers["content-type"]
    man = m.json()
    assert man["display"] == "standalone" and man["start_url"].startswith("/") and man["name"] and man["short_name"]
    tamanhos = {i["sizes"] for i in man["icons"]}
    assert {"192x192", "512x512"} <= tamanhos and any(i.get("purpose") == "maskable" for i in man["icons"])
    for i in man["icons"]:  # todo ícone declarado existe e é PNG
        r = anon.get(i["src"])
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n", i["src"]
    sw = anon.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/" and "no-cache" in sw.headers["cache-control"]
    assert anon.get("/offline.html").status_code == 200
    assert anon.get("/icons/nao-existe.png").status_code == 404
    assert anon.get("/icons/..%2Fmain.py").status_code == 404
    # as páginas apontam para o manifest e registram o service worker
    for pagina in (anon.get("/login").text, client.get("/").text):
        assert 'rel="manifest"' in pagina and "serviceWorker" in pagina and "viewport-fit=cover" in pagina
    assert anon.get("/api/dashboard").status_code == 401  # e a API continua protegida


def test_alerta_de_agente_nao_repete_apos_restart(client, monkeypatch):
    """Deploy/restart zera a memória: o estado dos alertas de antena/agente precisa vir do banco para não reenviar o aviso."""
    enviados = []
    monkeypatch.setattr(main, "notificar", lambda tipo, texto, c=None, destinatario_id=None: enviados.append(tipo))
    main.state["alertas"].clear(); main.state.pop("_salvo", None)
    client.post("/api/agentes", json={"nome": "hotel"})
    def contato(seg_atras):
        with main_db() as con:
            con.execute("UPDATE agentes SET visto_em=%s", (time.time() - seg_atras,)); con.commit()
    def linhas():
        with main_db() as con:
            return con.execute("SELECT COUNT(*) n FROM alerta_estado").fetchone()["n"]
    def n(tipo="agente_offline"):
        return enviados.count(tipo)
    contato(1000)
    main.checar_alertas()
    assert n() == 1 and linhas() == 1
    main.checar_alertas()
    assert n() == 1  # sem repetir dentro da janela
    main.state["alertas"].clear(); main.state.pop("_salvo", None)  # "restart"
    main.carregar_estado_alertas()
    main.checar_alertas()
    assert n() == 1 and linhas() == 1, "o restart reenviou o alerta"
    contato(1)  # agente voltou: manda a retomada uma vez e limpa o estado
    main.checar_alertas()
    assert n() == 2 and linhas() == 0
    main.checar_alertas()
    assert n() == 2
    main.state["alertas"].clear()


def test_estado_de_alerta_de_item_removido_e_descartado(client):
    main.state["alertas"].clear(); main.state.pop("_salvo", None)
    main.state["alertas"]["antena:999"] = time.time()
    main.salvar_estado_alertas([], [])
    assert "antena:999" not in main.state["alertas"]


def test_dois_agentes_na_mesma_antena_online_vence_e_cobertura_retomada(client, monkeypatch):
    enviados = []
    monkeypatch.setattr(main, "notificar", lambda tipo, texto, c=None, destinatario_id=None: enviados.append((tipo, texto)))
    main.state["alertas"].clear(); main.state.pop("_salvo", None)
    client.post("/api/antenas", json={"nome": "saida", "modelo": "AcuraEdgeR30", "ip": "10.0.0.9", "porta": 80, "alerta": True, "evasao": True})
    aid = client.get("/api/antenas").json()[0]["id"]
    ta = {"X-Agent-Token": client.post("/api/agentes", json={"nome": "governanca"}).json()["token"]}
    tb = {"X-Agent-Token": client.post("/api/agentes", json={"nome": "pcsom"}).json()["token"]}
    def rep(h, online):
        client.post("/api/agente/status", headers=h, auth=None, json={"versao": "1", "host": "pc", "antenas": [{"id": aid, "online": online, "detalhe": "x"}]})
    def estado():
        with main_db() as con:
            return con.execute("SELECT online, g.nome agente FROM antena_status s JOIN agentes g ON g.id=s.agente_id WHERE antena_id=%s", (aid,)).fetchone()
    def envelhece():
        with main_db() as con:
            con.execute("UPDATE antena_status SET verificado_em=%s", (time.time() - 1000,)); con.commit()
    def tipos():
        return [t for t, _ in enviados if t.startswith("antena")]
    # 1) vários agentes: o "online" de um agente em dia não é derrubado pelo "offline" de outro
    rep(ta, True); rep(tb, False)
    assert estado() == {"online": True, "agente": "governanca"}
    # 2) se quem via online some (PC desligado), o offline do outro passa a valer após o prazo
    envelhece(); rep(tb, False)
    assert estado() == {"online": False, "agente": "pcsom"}
    # 3) cobertura: antena fica sem nenhum agente reportando -> em silêncio, marca a perda
    envelhece(); main.state["alertas"].clear()
    main.checar_alertas()
    assert "cobertura:%d" % aid in main.state["alertas"] and tipos() == []
    # 4) algum agente volta a vê-la online -> avisa uma vez, citando o agente
    rep(tb, True)
    main.checar_alertas()
    assert tipos() == ["antena_online"] and "pcsom" in enviados[-1][1] and "ONLINE" in enviados[-1][1] and "EVASÃO" in enviados[-1][1]
    main.checar_alertas()
    assert tipos() == ["antena_online"]  # sem repetir
    main.state["alertas"].clear()


def test_retomada_de_cobertura_nao_duplica_com_o_aviso_de_offline(client, monkeypatch):
    enviados = []
    monkeypatch.setattr(main, "notificar", lambda tipo, texto, c=None, destinatario_id=None: enviados.append(tipo))
    main.state["alertas"].clear(); main.state.pop("_salvo", None)
    now = time.time(); c = main.cfg()
    def ant(online, visto): return [{"id": 7, "nome": "r", "ip": "1.1.1.1", "alerta": True, "evasao": False, "online": online, "detalhe": "", "verificado_em": visto, "extra": None, "agente": "x"}]
    main.avaliar_agentes(now, ant(False, now), [], c)              # offline em dia: alerta de offline
    main.avaliar_agentes(now + 500, ant(False, now), [], c)        # sem agente por >2min: marca cobertura
    msgs = main.avaliar_agentes(now + 510, ant(True, now + 510), [], c)  # volta online
    assert [t for t, _ in msgs] == ["antena_offline"] and "voltou" in msgs[0][1]  # só a retomada normal, sem duplicar
    main.state["alertas"].clear()


def test_epc_manual_exige_hex_par_e_data_de_compra(client):
    for ruim in ("Z1", "ABC", "ZZ99", "12"):
        assert client.post("/api/itens", json={"epc": ruim, "tipo": "x"}).status_code == 400
    assert client.post("/api/itens", json={"epc": "ab01", "tipo": "x", "data_compra": "31/02/2025"}).status_code == 400
    r = client.post("/api/itens", json={"epc": "ab01", "tipo": "x", "data_compra": "05/03/2025"})
    assert r.status_code == 200 and r.json()["epc"] == "AB01" and r.json()["data_compra"] == "2025-03-05"
    assert client.post("/api/saida", auth=("s", "s"), json={"epc": "ZZ99", "destino_id": 1}).status_code == 400
    csv = client.get("/api/export/inventario.csv").text.splitlines()
    assert "data_compra" in csv[0] and "2025-03-05" in csv[1]


def test_editar_lote_so_muda_campos_enviados(client):
    for e in ("AA11", "BB22", "CC33"):
        client.post("/api/itens", json={"epc": e, "tipo": "Roupão", "cor": "Branco", "tamanho": "M"})
    r = client.post("/api/itens/editar-lote", json={"epcs": ["aa11", "BB22", "DD44"], "cor": "Azul", "data_compra": "2025-01-10"})
    assert r.json() == {"alterados": 2, "nao_encontrados": 1}
    por_epc = {i["epc"]: i for i in client.get("/api/itens?limit=10").json()}
    assert por_epc["AA11"]["cor"] == "Azul" and por_epc["AA11"]["tamanho"] == "M" and por_epc["AA11"]["data_compra"] == "2025-01-10"
    assert por_epc["CC33"]["cor"] == "Branco"
    assert client.post("/api/itens/editar-lote", json={"epcs": ["AA11"], "tamanho": ""}).status_code == 200
    assert client.post("/api/itens/editar-lote", json={"epcs": ["AA11"], "tipo": " "}).status_code == 400
    assert client.post("/api/itens/editar-lote", json={"epcs": ["AA11"]}).status_code == 400
    assert client.post("/api/itens/editar-lote", json={"epcs": ["AA11"], "estado_id": 99999}).status_code == 400


def test_leitor_manual_consulta_e_cadastro_em_lote(client):
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão"})
    c = client.post("/api/itens/consulta", json={"epcs": ["aa11", "BB22", "xx", "BB22"]}).json()
    assert [i["epc"] for i in c["identificados"]] == ["AA11"] and c["novos"] == ["BB22"] and c["invalidos"] == ["XX"]
    r = client.post("/api/itens/cadastrar-lote", json={"epcs": ["AA11", "BB22", "CC33", "xx"], "tipo": "Lençol"}).json()
    assert r == {"cadastrados": 2, "ja_existiam": 1, "invalidos": ["XX"]}
    tipos = {i["epc"]: i["tipo"] for i in client.get("/api/itens?limit=10").json()}
    assert tipos == {"AA11": "Roupão", "BB22": "Lençol", "CC33": "Lençol"}  # existente não é alterado
    assert client.post("/api/itens/cadastrar-lote", json={"epcs": [], "tipo": "x"}).status_code == 400
    assert client.post("/api/itens/cadastrar-lote", json={"epcs": ["AA11"], "tipo": " "}).status_code == 400


def test_revisao_do_inventario_e_csv_da_conferencia(client):
    import psycopg, os
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:  # item legado fora do padrão (importado do eTrace)
        pad = con.execute("SELECT id FROM estados_inventario WHERE padrao=1").fetchone()[0]
        con.execute("INSERT INTO itens(epc,tipo,estado_id,criado_em,atualizado_em) VALUES('LEGADO1','x',%s,1,1)", (pad,))
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão"})
    ingest(client, "AA11", "EE55", "EE55")
    rev = client.get("/api/inventario/revisao").json()
    assert rev["total_fora_do_padrao"] == 2  # LEGADO1 e AA11 (4 chars, não 24)
    assert {i["epc"] for i in rev["epcs_fora_do_padrao"]} == {"LEGADO1", "AA11"}
    assert [(s["epc"], s["leituras"]) for s in rev["lidas_sem_cadastro"]] == [("EE55", 2)]
    c = client.post("/api/conferencias", json={}).json()
    r = client.get(f"/api/conferencias/{c['id']}/export.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    assert r.text.lstrip("\ufeff").startswith("epc") and "AA11" in r.text
    assert client.get("/api/conferencias/99999/export.csv").status_code == 404


def test_auditoria_de_logins_so_admin_e_sem_senha(client):
    client.post("/api/usuarios", json={"usuario": "ana", "nome": "Ana", "papel": "leitura", "senha": "senha-forte-1"})
    anon = client.__class__(client.app)
    assert anon.post("/api/login", json={"usuario": "ana", "senha": "errada-123"}, headers={"user-agent": "teste-ua"}).status_code == 401
    assert anon.post("/api/login", json={"usuario": "ana", "senha": "senha-forte-1"}).status_code == 200
    r = client.get("/api/auditoria/logins").json()
    assert r["resumo"] == {"ok": 1, "falha": 1, "bloqueado": 0}
    assert [(l["usuario"], l["resultado"]) for l in r["logins"]] == [("ana", "ok"), ("ana", "falha")]
    assert r["logins"][1]["navegador"] == "teste-ua"
    assert "errada-123" not in str(r) and "senha-forte-1" not in str(r)
    assert client.get("/api/auditoria/logins?resultado=falha").json()["resumo"]["ok"] == 0
    assert client.get("/api/auditoria/logins?usuario=zzz").json()["logins"] == []
    assert "ana;falha" in client.get("/api/auditoria/logins.csv").text
    assert anon.get("/api/auditoria/logins").status_code == 403  # leitura não vê auditoria
    for _ in range(main.LIMITE_FALHAS):
        anon.post("/api/login", json={"usuario": "x", "senha": "y"})
    main._falhas.clear()  # o IP do teste (também do admin) está bloqueado
    assert client.get("/api/auditoria/logins?resultado=bloqueado").json()["resumo"]["bloqueado"] >= 1


def test_perfis_criar_editar_excluir_e_admin_fixo(client):
    r = client.get("/api/perfis").json()
    assert [p["nome"] for p in r["perfis"]][0] == "admin" and r["perfis"][0]["fixo"] is True
    assert set(r["perfis"][0]["permissoes"]) == {p["id"] for p in r["permissoes"]}  # admin tem todas
    assert {p["nome"] for p in r["perfis"]} >= {"operador", "leitura", "painel"}
    for ruim in ({"nome": "x", "permissoes": []}, {"nome": "admin", "permissoes": []}, {"nome": "Novo", "permissoes": ["voar"]}):
        assert client.post("/api/perfis", json=ruim).status_code in (400, 409)
    assert client.post("/api/perfis", json={"nome": "Supervisor", "descricao": "d", "permissoes": ["dashboard", "consultar", "excluir_lotes"]}).status_code == 200
    assert client.post("/api/perfis", json={"nome": "supervisor", "permissoes": []}).status_code == 409  # nome igual sem diferenciar caixa
    assert client.put("/api/perfis/Supervisor", json={"permissoes": ["dashboard", "consultar"]}).status_code == 200
    assert client.put("/api/perfis/admin", json={"permissoes": []}).status_code == 403
    assert client.delete("/api/perfis/admin").status_code == 403
    assert client.put("/api/perfis/naoexiste", json={"permissoes": []}).status_code == 404
    client.post("/api/usuarios", json={"usuario": "sup", "papel": "Supervisor", "senha": "senha-forte-1"})
    assert client.delete("/api/perfis/Supervisor").status_code == 409  # em uso
    uid = next(u["id"] for u in client.get("/api/usuarios").json() if u["usuario"] == "sup")
    client.patch(f"/api/usuarios/{uid}", json={"papel": "leitura"})
    assert client.delete("/api/perfis/Supervisor").status_code == 200
    assert client.post("/api/usuarios", json={"usuario": "z", "papel": "Supervisor"}).status_code == 400  # perfil inexistente
    assert client.delete("/api/perfis/painel").status_code == 200  # perfis padrão também podem ser excluídos
    main.init_db()  # reiniciar não recria o perfil excluído
    assert "painel" not in [p["nome"] for p in client.get("/api/perfis").json()["perfis"]]


def test_permissoes_granulares_por_rota(client):
    client.post("/api/perfis", json={"nome": "Cadastrista", "permissoes": ["consultar", "catalogo_editar"]})
    client.post("/api/usuarios", json={"usuario": "cad", "papel": "Cadastrista", "senha": "senha-forte-1"})
    c = client.__class__(client.app)
    assert c.post("/api/login", json={"usuario": "cad", "senha": "senha-forte-1"}).status_code == 200
    assert c.post("/api/itens", json={"epc": "AB01", "tipo": "x"}).status_code == 200  # catalogo_editar
    assert c.get("/api/itens").status_code == 200  # consultar
    assert c.get("/api/dashboard").status_code == 403  # sem a permissão dashboard
    assert c.post("/api/lotes/registrar").status_code == 403  # sem operar_cabine
    assert c.delete("/api/lotes/1").status_code == 403  # sem excluir_lotes
    assert c.get("/api/export/inventario.csv").status_code == 403  # sem exportar
    assert c.post("/api/destinos", json={"nome": "x", "tipo": "hospede"}).status_code == 403  # sem cadastros
    assert c.get("/api/auditoria/logins").status_code == 403 and c.get("/api/perfis").status_code == 403
    assert c.get("/api/me").status_code == 200  # qualquer logado


def test_quem_gerencia_usuarios_nao_escala_privilegio(client):
    client.post("/api/perfis", json={"nome": "RH", "permissoes": ["usuarios", "consultar"]})
    client.post("/api/usuarios", json={"usuario": "rh", "papel": "RH", "senha": "senha-forte-1"})
    c = client.__class__(client.app)
    c.post("/api/login", json={"usuario": "rh", "senha": "senha-forte-1"})
    assert c.post("/api/perfis", json={"nome": "Poderoso", "permissoes": ["auditoria"]}).status_code == 403  # não tem 'auditoria'
    assert c.post("/api/perfis", json={"nome": "Basico", "permissoes": ["consultar"]}).status_code == 200  # subconjunto do que ele tem
    assert c.post("/api/usuarios", json={"usuario": "novo", "papel": "admin", "senha": "senha-forte-1"}).status_code == 403
    assert c.post("/api/usuarios", json={"usuario": "novo", "papel": "Basico", "senha": "senha-forte-1"}).status_code == 200
    uid_admin_like = next(u["id"] for u in c.get("/api/usuarios").json() if u["usuario"] == "novo")
    assert c.patch(f"/api/usuarios/{uid_admin_like}", json={"papel": "operador"}).status_code == 403  # operador tem mais poder
