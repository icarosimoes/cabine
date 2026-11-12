import time
from contextlib import closing
from app import main

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _setup(client, evasao=True):
    cam = client.post("/api/cameras", json={"nome": "dvr-saida", "ip": "192.168.30.50", "porta": 80, "canal": 3}).json()["id"]
    ant = {"nome": "saida", "evasao": evasao, "camera_id": cam}
    assert client.post("/api/antenas", json=ant).status_code == 200
    antena = client.get("/api/antenas").json()[0]
    dest = client.get("/api/destinos").json()[0]
    ag = client.post("/api/agentes", json={"nome": "h"}).json()
    return cam, antena, dest, {"X-Agent-Token": ag["token"]}


def _saida(client, antena, dest, epc="CC99"):
    return client.post("/api/saida", auth=("s", "s"), json={"epc": epc, "destino_id": dest["id"], "antena_id": antena["id"]}).json()


def _status(client, h):
    return client.post("/api/agente/status", headers=h, auth=None, json={"antenas": []}).json()


def _envelhece(clipe_id, seg):
    with closing(main.db()) as con:
        con.execute("UPDATE clipes SET t0=t0-%s, inicio=inicio-%s, fim=fim-%s WHERE id=%s", (seg, seg, seg, clipe_id))
        con.commit()


def test_camera_crud_e_validacao(client):
    assert client.post("/api/cameras", json={"nome": "x", "ip": "1.1.1.1", "canal": 0}).status_code == 400
    assert client.post("/api/cameras", json={"nome": "x", "ip": "1.1.1.1", "gravacao": "nunca"}).status_code == 400
    assert client.post("/api/cameras", json={"nome": "x", "ip": "ip inválido!"}).status_code == 400
    cid = client.post("/api/cameras", json={"nome": "x", "ip": "1.1.1.1"}).json()["id"]
    assert client.post("/api/cameras", json={"nome": "x", "ip": "1.1.1.2"}).status_code == 409
    assert client.put(f"/api/cameras/{cid}", json={"nome": "x", "ip": "1.1.1.2", "canal": 2, "gravacao": "movimento"}).status_code == 200
    c = client.get("/api/cameras").json()[0]
    assert (c["ip"], c["canal"], c["gravacao"]) == ("1.1.1.2", 2, "movimento")
    assert client.post("/api/antenas", json={"nome": "a", "camera_id": 999}).status_code == 400
    assert client.delete(f"/api/cameras/{cid}").status_code == 200
    assert client.delete(f"/api/cameras/{cid}").status_code == 404


def test_saida_cria_clipe_e_agrupa_passagem(client):
    cam, antena, dest, h = _setup(client)
    r1 = _saida(client, antena, dest, "AA01")
    r2 = _saida(client, antena, dest, "AA02")  # mesma passagem: reaproveita o clipe
    assert r1["clipe_id"] and r1["clipe_id"] == r2["clipe_id"]
    ev = client.get("/api/saida").json()
    assert {e["clipe_status"] for e in ev} == {"aguardando"}
    c = client.get(f"/api/clipes/{r1['clipe_id']}").json()
    assert round(c["t0"] - c["inicio"]) == 10 and round(c["fim"] - c["t0"]) == 60  # 10 s antes, 60 s depois


def test_saida_sem_camera_nao_cria_clipe(client):
    dest = client.get("/api/destinos").json()[0]
    r = client.post("/api/saida", auth=("s", "s"), json={"epc": "AA01", "destino_id": dest["id"]}).json()
    assert r["clipe_id"] is None


def test_unica_camera_de_evasao_vale_sem_antena_id(client):
    cam, antena, dest, h = _setup(client)
    r = client.post("/api/saida", auth=("s", "s"), json={"epc": "AA01", "destino_id": dest["id"]}).json()
    assert r["clipe_id"]


def test_agente_so_recebe_depois_do_fim_da_gravacao(client):
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    assert _status(client, h)["clipes"] == []  # ainda gravando os 60 s seguintes
    _envelhece(cid, 70)
    pedido = _status(client, h)["clipes"]
    assert len(pedido) == 1 and pedido[0]["canal"] == 3 and pedido[0]["ip"] == "192.168.30.50"
    assert _status(client, h)["clipes"] == []  # não entrega duas vezes
    assert client.get(f"/api/clipes/{cid}").json()["status"] == "enviado"


def test_upload_do_clipe_e_reproducao(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    url = f"/api/agente/clipes/{cid}/arquivo"
    assert client.put(url, content=MP4, headers=h, auth=None).status_code == 409  # ainda não foi pedido
    _envelhece(cid, 70); _status(client, h)
    assert client.put(url, content=b"nao e mp4 nao e mp4", headers=h, auth=None).status_code == 400
    assert client.put(url, content=MP4).status_code == 401  # sem token do agente
    assert client.put(url, content=MP4, headers=h, auth=None).status_code == 200
    assert client.get(f"/api/saida").json()[0]["clipe_status"] == "ok"
    v = client.get(f"/api/clipes/{cid}/video")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4" and v.content == MP4
    assert not list(tmp_path.glob("*.part"))


def test_upload_acima_do_limite_e_rejeitado(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path); monkeypatch.setattr(main, "CLIPE_MAX_MB", 0)
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    assert client.put(f"/api/agente/clipes/{cid}/arquivo", content=MP4, headers=h, auth=None).status_code == 413
    assert not list(tmp_path.iterdir())


def test_erro_do_agente_aparece_no_evento(client):
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    client.post(f"/api/agente/clipes/{cid}/erro", json={"mensagem": "sem gravação nesse intervalo"}, headers=h, auth=None)
    ev = client.get("/api/saida").json()[0]
    assert ev["clipe_status"] == "erro" and "sem gravação" in ev["clipe_erro"]
    # nova passagem depois de um clipe com erro abre um clipe novo
    assert _saida(client, antena, dest)["clipe_id"] != cid


def test_clipe_preso_e_expirado(client):
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    with closing(main.db()) as con:
        con.execute("UPDATE clipes SET enviado_em=enviado_em-%s WHERE id=%s", (main.CLIPE_TIMEOUT_SEG + 5, cid)); con.commit()
    _status(client, h)
    assert client.get(f"/api/clipes/{cid}").json()["status"] == "erro"


def test_clipe_de_teste_fica_pronto_na_hora(client):
    cam, antena, dest, h = _setup(client)
    cid = client.post(f"/api/cameras/{cam}/teste").json()["clipe_id"]
    assert [c["id"] for c in _status(client, h)["clipes"]] == [cid]
    # clipe de teste não é reaproveitado por uma evasão
    assert _saida(client, antena, dest)["clipe_id"] != cid


def test_retencao_apaga_clipe_e_arquivo(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    (tmp_path / f"{cid}.mp4").write_bytes(MP4)
    with closing(main.db()) as con:
        con.execute("UPDATE clipes SET criado_em=criado_em-%s", (main.CLIPES_RETENCAO_DIAS * 86400 + 60,)); con.commit()
    main.purgar_clipes()
    assert not (tmp_path / f"{cid}.mp4").exists()
    assert client.get("/api/saida").json()[0]["clipe_id"] is None  # evento fica, o vídeo some


def test_camera_modo_rtsp_validacao_e_config_do_agente(client):
    base = {"nome": "Mibo saída", "ip": "192.168.30.70", "canal": 1}
    assert client.post("/api/cameras", json={**base, "tipo": "onvif"}).status_code == 400
    assert client.post("/api/cameras", json={**base, "tipo": "dvr", "rtsp_path": "/x"}).status_code == 400  # caminho só no modo rtsp
    assert client.post("/api/cameras", json={**base, "tipo": "rtsp", "rtsp_path": "/a/../b"}).status_code == 400
    assert client.post("/api/cameras", json={**base, "tipo": "rtsp", "rtsp_path": "/x y;rm"}).status_code == 400
    cid = client.post("/api/cameras", json={**base, "tipo": "rtsp"}).json()["id"]
    client.post("/api/cameras", json={"nome": "DVR", "ip": "192.168.30.60"})  # dvr: fora da lista de gravação
    ag = client.post("/api/agentes", json={"nome": "h"}).json()
    cfg = client.get("/api/agente/config", headers={"X-Agent-Token": ag["token"]}, auth=None).json()
    assert [(c["id"], c["nome"], c["rtsp_path"]) for c in cfg["cameras"]] == [(cid, "Mibo saída", None)]
    assert client.put(f"/api/cameras/{cid}", json={**base, "tipo": "rtsp", "rtsp_path": "/live/ch{canal}"}).status_code == 200
    assert client.get("/api/cameras").json()[1]["rtsp_path"] == "/live/ch{canal}"


def test_clipe_rtsp_carrega_tipo_e_teste_pede_o_passado_recente(client):
    cid = client.post("/api/cameras", json={"nome": "Mibo", "ip": "1.1.1.1", "tipo": "rtsp"}).json()["id"]
    ag = client.post("/api/agentes", json={"nome": "h"}).json()
    h = {"X-Agent-Token": ag["token"]}
    cl = client.post(f"/api/cameras/{cid}/teste").json()["clipe_id"]
    pedido = _status(client, h)["clipes"][0]
    assert (pedido["id"], pedido["tipo"], pedido["camera_id"]) == (cl, "rtsp", cid)
    assert pedido["fim"] <= time.time() and 25 <= pedido["fim"] - pedido["inicio"] <= 35  # só o que o buffer já tem


def test_camera_hikvision_tipo_e_pedido_ao_agente(client):
    base = {"nome": "Hik saída", "ip": "192.168.30.80", "canal": 2, "tipo": "hikvision"}
    assert client.post("/api/cameras", json={**base, "rtsp_path": "/x"}).status_code == 400  # caminho só no modo buffer
    cid = client.post("/api/cameras", json=base).json()["id"]
    cfg = client.get("/api/agente/config", headers={"X-Agent-Token": client.post("/api/agentes", json={"nome": "h"}).json()["token"]}, auth=None).json()
    assert cfg["cameras"] == []  # playback no aparelho: o agente não precisa gravar buffer
    ag = client.post("/api/agentes", json={"nome": "h2"}).json()
    h = {"X-Agent-Token": ag["token"]}
    client.post(f"/api/cameras/{cid}/teste")
    pedido = _status(client, h)["clipes"][0]
    assert (pedido["tipo"], pedido["canal"], pedido["porta"]) == ("hikvision", 2, 80)
    assert pedido["fim"] - pedido["inicio"] >= 60  # janela cheia, como no DVR
