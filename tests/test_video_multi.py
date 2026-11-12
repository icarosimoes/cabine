"""Vídeo da evasão: câmera ligada a um agente, aviso de WhatsApp com link assinado e download em lote."""
import io
import time
import zipfile
from app import main
from test_clipes import MP4, _setup, _saida, _status, _envelhece


def _destinatario(client, **kw):
    r = client.post("/api/alertas/destinatarios", json={"nome": "Segurança", "whatsapp": "5511999990000", **kw})
    assert r.status_code == 200


def _captura(monkeypatch):
    msgs = []
    monkeypatch.setattr(main, "enviar_um", lambda c, numero, texto: msgs.append((numero, texto)))
    return msgs


def test_camera_ligada_a_um_agente_so_e_buscada_por_ele(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    cam, antena, dest, h1 = _setup(client)
    ag2 = client.post("/api/agentes", json={"nome": "outro-hotel"}).json()
    h2 = {"X-Agent-Token": ag2["token"]}
    agentes = {a["nome"]: a["id"] for a in client.get("/api/agentes").json()}
    c = client.get("/api/cameras").json()[0]
    body = {"nome": c["nome"], "ip": c["ip"], "porta": 80, "canal": 3, "agente_id": agentes["outro-hotel"]}
    assert client.put(f"/api/cameras/{cam}", json={**body, "agente_id": 99999}).status_code == 400
    assert client.put(f"/api/cameras/{cam}", json=body).status_code == 200
    assert client.get("/api/cameras").json()[0]["agente"] == "outro-hotel"
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70)
    assert _status(client, h1)["clipes"] == []  # não é a câmera dele
    url = f"/api/agente/clipes/{cid}/arquivo"
    got = _status(client, h2)["clipes"]
    assert [x["id"] for x in got] == [cid]
    assert client.put(url, content=MP4, headers=h1, auth=None).status_code == 403  # outro agente não envia arquivo
    assert client.post(f"/api/agente/clipes/{cid}/erro", json={"mensagem": "x"}, headers=h1, auth=None).status_code == 200
    assert client.get("/api/saida").json()[0]["clipe_status"] == "enviado"  # erro de quem não pegou é ignorado
    assert client.put(url, content=MP4, headers=h2, auth=None).status_code == 200


def test_cameras_rtsp_no_config_do_agente_so_as_dele(client):
    ag1 = client.post("/api/agentes", json={"nome": "a1"}).json()
    ag2 = client.post("/api/agentes", json={"nome": "a2"}).json()
    ids = {a["nome"]: a["id"] for a in client.get("/api/agentes").json()}
    for nome, ag in (("c-livre", None), ("c-a1", ids["a1"]), ("c-a2", ids["a2"])):
        assert client.post("/api/cameras", json={"nome": nome, "ip": "10.0.0.9", "tipo": "rtsp", "agente_id": ag}).status_code == 200
    cfg = client.get("/api/agente/config", headers={"X-Agent-Token": ag1["token"]}, auth=None).json()
    assert sorted(c["nome"] for c in cfg["cameras"]) == ["c-a1", "c-livre"]


def test_aviso_de_evasao_e_link_do_video(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    msgs = _captura(monkeypatch)
    _destinatario(client)
    _destinatario(client, nome="Só sinal", whatsapp="5511988880000", recebe_evasao=False)
    client.post("/api/itens", json={"epc": "CC99", "tipo": "Roupão", "nome": "Roupão 301"})
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _saida(client, antena, dest, "DD00")  # mesma passagem: não repete o aviso
    assert len(msgs) == 1 and msgs[0][0] == "5511999990000"
    assert "EVASÃO" in msgs[0][1] and "Roupão 301" in msgs[0][1] and "Segurança" not in msgs[0][1]
    _envelhece(cid, 70); _status(client, h)
    assert client.put(f"/api/agente/clipes/{cid}/arquivo", content=MP4, headers=h, auth=None).status_code == 200
    assert len(msgs) == 2 and "Roupão 301" in msgs[1][1] and "2 peça" in msgs[1][1]
    link = msgs[1][1].split(": ")[-1].split(" ")[0]
    assert link.startswith(main.PUBLIC_URL + "/v/")
    path = link[len(main.PUBLIC_URL):]
    r = client.get(path, auth=None)  # sem login
    assert r.status_code == 200 and r.content == MP4
    assert client.get(path.replace("s=", "s=0"), auth=None).status_code == 403  # assinatura adulterada
    assert client.get(f"/v/{cid + 1}?" + path.split("?")[1], auth=None).status_code == 403  # vale só para o clipe dele
    assert client.get(f"/v/{cid}?e={int(time.time()) - 5}&s={main._assina_clipe(cid, int(time.time()) - 5)}", auth=None).status_code == 403  # expirado


def test_aviso_de_falha_do_video_e_documentado_nao_avisa(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    msgs = _captura(monkeypatch)
    _destinatario(client)
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    client.post(f"/api/agente/clipes/{cid}/erro", json={"mensagem": "DVR fora do ar"}, headers=h, auth=None)
    assert len(msgs) == 2 and "não foi possível obter o vídeo" in msgs[1][1] and "DVR fora do ar" in msgs[1][1]
    # peça documentada (transferência recente) não gera aviso
    msgs.clear()
    client.post("/api/itens", json={"epc": "EE11", "tipo": "x"})
    ingest_lote = client.post("/api/itens/cadastrar-lote", json={"epcs": ["EE11"], "tipo": "x"})
    assert ingest_lote.status_code == 200 and msgs == []


def test_download_em_lote_zip(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    cam, antena, dest, h = _setup(client)
    ids = []
    for i, epc in enumerate(("CC99", "DD00")):
        cid = _saida(client, antena, dest, epc)["clipe_id"]
        if cid in ids:  # mesma passagem divide o clipe: separa no tempo
            continue
        ids.append(cid)
    with main.closing(main.db()) as con:  # segundo clipe, de outra passagem
        con.execute("UPDATE clipes SET t0=t0-1000, inicio=inicio-1000, fim=fim-1000")
        con.commit()
    ids.append(_saida(client, antena, dest, "FF22")["clipe_id"])
    for cid in ids:
        _envelhece(cid, 70)
    _status(client, h)
    for cid in ids:
        assert client.put(f"/api/agente/clipes/{cid}/arquivo", content=MP4, headers=h, auth=None).status_code == 200
    r = client.get("/api/clipes-zip?ids=" + ",".join(map(str, ids)) + ",9999")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(z.namelist()) == len(ids) and all(n.endswith(".mp4") for n in z.namelist())
    assert z.read(z.namelist()[0]) == MP4
    assert client.get("/api/clipes-zip?ids=9999").status_code == 404
    assert client.get("/api/clipes-zip?ids=a,b").status_code == 400
    assert client.get("/api/clipes-zip?ids=" + ",".join(map(str, range(1, 60)))).status_code == 400
