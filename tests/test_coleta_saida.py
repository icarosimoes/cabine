"""Coleta da evasão pelo agente (leitura da porta 8080 do Acura): POST /api/agente/saida."""
from app import main
from tests.test_clipes import _setup


def _com_destino(client):
    cam, antena, dest, h = _setup(client)
    body = {"nome": "saida", "evasao": True, "camera_id": cam, "destino_id": dest["id"]}
    assert client.put(f"/api/antenas/{antena['id']}", json=body).status_code == 200
    return antena, h


def test_agente_reporta_saida_nao_documentada_gera_evasao_e_clipe(client, monkeypatch):
    avisos = []
    monkeypatch.setattr(main, "avisar_evasao", lambda *a: avisos.append(a))
    client.post("/api/itens", json={"epc": "CC99", "tipo": "Roupão", "nome": "Roupão 301"})
    antena, h = _com_destino(client)
    r = client.post("/api/agente/saida", json={"antena_id": antena["id"], "epc": "cc99"}, headers=h, auth=None)
    assert r.status_code == 200 and r.json()["documentado"] is False and r.json()["clipe_id"]
    assert len(avisos) == 1
    # a mesma tag relida dentro da janela não vira outro evento
    r = client.post("/api/agente/saida", json={"antena_id": antena["id"], "epc": "CC99"}, headers=h, auth=None)
    assert r.json().get("duplicado") is True and len(avisos) == 1
    assert len(client.get("/api/saida").json()) == 1


def test_tag_fora_do_catalogo_e_ignorada(client):
    antena, h = _com_destino(client)
    r = client.post("/api/agente/saida", json={"antena_id": antena["id"], "epc": "E2801160600002054D1F9A3B"}, headers=h, auth=None)
    assert r.status_code == 200 and "ignorado" in r.json()
    assert client.get("/api/saida").json() == []


def test_antena_sem_localizacao_e_sem_token(client):
    cam, antena, dest, h = _setup(client)  # antena sem destino
    r = client.post("/api/agente/saida", json={"antena_id": antena["id"], "epc": "CC99"}, headers=h, auth=None)
    assert r.status_code == 409
    assert client.post("/api/agente/saida", json={"antena_id": antena["id"], "epc": "CC99"}, auth=None).status_code == 401
    assert client.post("/api/agente/saida", json={"antena_id": 9999, "epc": "CC99"}, headers=h, auth=None).status_code == 404
