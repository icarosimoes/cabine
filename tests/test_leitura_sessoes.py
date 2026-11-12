"""Sessões de leitura da cabine: resultado, diagnóstico de 0 peças, sinalizada, reinício e reporte de problema."""
import time
from app import main


def _evento(client, epc):
    r = client.post("/ingest", json=[{"epc": epc, "event_type": "tagInventory", "antennaPort": 1}], auth=("w", "w"))
    assert r.status_code == 200


def _arma(client):
    r = client.post("/api/leitura/armar?segundos=60")
    return r.json()["sessao_id"]


def _lista(client, **q):
    return client.get("/api/leitura-sessoes", params=q).json()


def test_leitura_com_pecas_conclui_a_sessao(client):
    sid = _arma(client)
    _evento(client, "AA11")
    r = client.post(f"/api/lotes/registrar?janela_segundos=15&sessao_id={sid}")
    assert r.status_code == 200
    s = _lista(client)["sessoes"][0]
    assert s["status"] == "concluida" and s["pecas"] == 1 and s["lote_id"] == r.json()["id"] and s["radio_ok"] is True


def test_zero_pecas_com_radio_comunicando_e_sem_comunicacao(client):
    sid = _arma(client)
    main.state["sinais"] += 1  # o rádio mandou sinal (sem tags)
    assert client.post(f"/api/lotes/registrar?janela_segundos=15&sessao_id={sid}").status_code == 400
    s = _lista(client)["sessoes"][0]
    assert s["status"] == "vazia" and s["radio_ok"] is True and "comunicando" in s["causa"] and not s["sinalizada"]
    sid2 = _arma(client)  # agora sem nenhum sinal
    assert client.post(f"/api/lotes/registrar?janela_segundos=15&sessao_id={sid2}").status_code == 400
    s = _lista(client)["sessoes"][0]
    assert s["radio_ok"] is False and "Leitor/gateway/broker" in s["causa"]


def test_sinalizada_e_reiniciada_e_filtros(client):
    sid = _arma(client)
    _evento(client, "BB22")
    assert client.post(f"/api/lotes/registrar?janela_segundos=15&sessao_id={sid}").status_code == 200
    sid2 = _arma(client)  # 0 peças logo em seguida: sinalizada
    time.sleep(0.01)
    with main.closing(main.db()) as con:
        con.execute("UPDATE events SET received_at=received_at-100")
        con.commit()
    client.post(f"/api/lotes/registrar?janela_segundos=15&sessao_id={sid2}")
    sid3 = _arma(client)
    sid4 = _arma(client)  # reiniciou antes de concluir a anterior
    por_id = {s["id"]: s for s in _lista(client)["sessoes"]}
    assert por_id[sid2]["sinalizada"] is True and por_id[sid2]["status"] == "vazia"
    assert por_id[sid3]["status"] == "reiniciada" and por_id[sid4]["status"] == "andamento"
    assert [s["id"] for s in _lista(client, sinalizadas=True)["sessoes"]] == [sid2]
    assert {s["id"] for s in _lista(client, zero=True)["sessoes"]} == {sid2}
    assert _lista(client)["com_zero"] == 1


def test_reportar_e_resolver_problema(client):
    sid = _arma(client)
    assert client.post(f"/api/leitura-sessoes/{sid}/reportar", json={"mensagem": "x"}).status_code == 400
    assert client.post("/api/leitura-sessoes/9999/reportar", json={"mensagem": "antena não leu"}).status_code == 404
    assert client.post(f"/api/leitura-sessoes/{sid}/reportar", json={"mensagem": "antena não leu"}).status_code == 200
    assert client.get("/api/leitura-sessoes/reportes").json()["abertos"] == 1
    assert [s["id"] for s in _lista(client, reportadas=True)["sessoes"]] == [sid]
    assert client.post(f"/api/leitura-sessoes/{sid}/resolver").status_code == 200
    assert client.get("/api/leitura-sessoes/reportes").json()["abertos"] == 0
