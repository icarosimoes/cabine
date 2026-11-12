"""Alerta de uptime baixo, status da Evolution, divergências gravadas e estações de controle."""
import time
from contextlib import closing
from app import main

CFG = dict(main.CFG_PADRAO, uptime_min="95", repetir_min="120")


def _antena(client, **kw):
    client.post("/api/antenas", json={"nome": "r700", "modelo": "ImpinjR700", "ip": "10.0.0.1", "porta": 443, "alerta": True, **kw})
    return client.get("/api/antenas").json()[0]["id"]


def _checagens(aid, ok, off, agora):
    with closing(main.db()) as con:
        for i in range(ok + off):
            con.execute("INSERT INTO monitor_checagens(tipo,ref_id,quando,online,latencia_ms) VALUES('antena',%s,%s,%s,10)",
                        (aid, agora - 3600 * 5 - i * 60, i >= off))
        con.commit()


def test_alerta_de_uptime_baixo_e_retomada(client):
    main.state["alertas"].clear()
    aid = _antena(client)
    agora = time.time()
    _checagens(aid, 80, 20, agora)  # 80% de 100 min
    with closing(main.db()) as con:
        ups = main.uptime_antenas(con, agora)
    antenas = [{"id": aid, "nome": "r700", "alerta": True, "evasao": False}]
    msgs = main.avaliar_uptime(agora, antenas, ups, CFG)
    assert len(msgs) == 1 and msgs[0][0] == "monitor" and "80.0%" in msgs[0][1]
    assert main.avaliar_uptime(agora + 60, antenas, ups, CFG) == []  # não repete antes do intervalo
    ups[aid] = {"total": 100, "ok": 99}
    assert "voltou" in main.avaliar_uptime(agora + 120, antenas, ups, CFG)[0][1]
    # pouca amostra (< 1 h) não avalia; mínimo 0 desliga; antena sem alerta é ignorada
    assert main.avaliar_uptime(agora, antenas, {aid: {"total": 10, "ok": 0}}, CFG) == []
    assert main.avaliar_uptime(agora, antenas, {aid: {"total": 100, "ok": 0}}, dict(CFG, uptime_min="0")) == []
    assert main.avaliar_uptime(agora, [{**antenas[0], "alerta": False}], {aid: {"total": 100, "ok": 0}}, CFG) == []
    main.state["alertas"].clear()


def test_config_uptime_validacao(client):
    r = client.get("/api/config/alertas").json()
    assert r["uptime_min"] == 95
    corpo = {"wa_url": "", "wa_instance": "", "wa_apikey": "", "ativo": True, "sem_sinal_seg": 60, "falhas": 5, "repetir_min": 120}
    assert client.put("/api/config/alertas", json={**corpo, "uptime_min": 101}).status_code == 400
    assert client.put("/api/config/alertas", json={**corpo, "uptime_min": 90}).json()["uptime_min"] == 90


def test_evolution_cai_e_volta(client, monkeypatch):
    main.state["alertas"].clear(); main.state.pop("evolution", None)
    estados = iter([{"estado": "open", "ok": True, "erro": None}, {"estado": "close", "ok": False, "erro": None},
                    {"estado": "close", "ok": False, "erro": None}, {"estado": "open", "ok": True, "erro": None}])
    monkeypatch.setattr(main, "consultar_evolution", lambda c: next(estados))
    t = 1_000_000.0
    assert main.avaliar_evolution(t, CFG) == []                     # conectada
    assert main.avaliar_evolution(t + 30, CFG) == []                # < 60 s: nem consulta
    cai = main.avaliar_evolution(t + 90, CFG)
    assert len(cai) == 1 and "desconectado" in cai[0][1] and "'close'" in cai[0][1]
    assert main.avaliar_evolution(t + 200, CFG) == []               # não repete
    volta = main.avaliar_evolution(t + 300, CFG)
    assert len(volta) == 1 and "voltou a conectar" in volta[0][1] and "3 min" in volta[0][1]
    monkeypatch.setattr(main, "consultar_evolution", lambda c: None)  # sem configuração: nada
    assert main.avaliar_evolution(t + 900, CFG) == [] and "evolution" not in main.state["alertas"]
    main.state["alertas"].clear(); main.state.pop("evolution", None)


def test_consultar_evolution_sem_config_e_com_erro(client, monkeypatch):
    assert main.consultar_evolution(dict(CFG, wa_apikey="", wa_instance="")) is None
    import io, json as _json

    class R:
        def __init__(s, d): s.d = d
        def read(s): return _json.dumps(s.d).encode()
    monkeypatch.setattr(main.urllib.request, "urlopen", lambda req, timeout=0: R({"instance": {"instanceName": "x", "state": "open"}}))
    assert main.consultar_evolution(dict(CFG, wa_apikey="k", wa_instance="x"))["ok"] is True
    def boom(req, timeout=0): raise OSError("recusou")
    monkeypatch.setattr(main.urllib.request, "urlopen", boom)
    r = main.consultar_evolution(dict(CFG, wa_apikey="k", wa_instance="x"))
    assert r["ok"] is False and r["estado"] == "inacessível" and "recusou" in r["erro"]


def test_divergencia_gravada_mesmo_sem_destinatario_e_no_dashboard(client, monkeypatch):
    from tests.conftest import ingest
    client.post("/api/itens", json={"epc": "AA11", "tipo": "Roupão"})
    client.post("/api/itens", json={"epc": "BB22", "tipo": "Toalha"})
    ingest(client, "AA11", "BB22", "CC33")
    lote = client.post("/api/lotes/registrar").json()
    r = client.post(f"/api/lotes/{lote['id']}/divergencia", json={"esperado": 5, "observacao": "faltou roupão"})
    assert r.status_code == 400 and "registrada" in r.json()["detail"]  # sem destinatário, mas gravou
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: None)
    client.post("/api/alertas/destinatarios", json={"nome": "x", "whatsapp": "5511999990000"})
    assert client.post(f"/api/lotes/{lote['id']}/divergencia", json={"esperado": 4}).json()["enviados"] == 1
    d = client.get("/api/divergencias").json()
    assert d["total"] == 2 and d["pecas_faltando"] == 3  # (5-3) + (4-3)
    assert {t["tipo"] for t in d["por_tipo"]} == {"Roupão", "Toalha", "(sem cadastro)"} and d["por_tipo"][0]["qtd"] == 2
    assert d["por_usuario"] == [{"usuario": "admin", "qtd": 2}]
    assert d["ultimas"][0]["esperado"] == 4
    assert client.get("/api/dashboard").json()["cabine"]["divergencias_30d"]["total"] == 2


def test_estacoes_de_controle_heartbeat(client):
    assert client.post("/api/estacoes/heartbeat", json={"nome": "  "}).status_code == 400
    assert client.post("/api/estacoes/heartbeat", json={"nome": "Portaria", "som": False, "intervalo_seg": 10}).status_code == 200
    e = client.get("/api/estacoes").json()
    assert len(e) == 1 and e[0]["nome"] == "Portaria" and e[0]["online"] and e[0]["som"] is False and e[0]["usuario"] == "admin"
    with closing(main.db()) as con:
        con.execute("UPDATE estacoes_controle SET visto_em=visto_em-600"); con.commit()
    assert client.get("/api/estacoes").json()[0]["online"] is False
    m = client.get("/api/monitoramento").json()
    assert m["estacoes"][0]["nome"] == "Portaria" and "whatsapp" in m and m["uptime_min"] == 95
    assert client.delete("/api/estacoes/Portaria").status_code == 200
    assert client.delete("/api/estacoes/Portaria").status_code == 404
