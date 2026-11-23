"""Web Push: chaves VAPID, assinatura por aparelho e envio junto com os alertas."""
import base64
from app import main


def _sub(n=1, endpoint=None):
    return {"endpoint": endpoint or f"https://push.example/{n}", "keys": {"p256dh": "BP" + "A" * 20, "auth": "x" * 16}}


def test_chave_vapid_estavel_e_valida(client):
    k1 = client.get("/api/push/chave").json()["chave"]
    assert client.get("/api/push/chave").json()["chave"] == k1  # gerada uma vez, persistida
    raw = base64.urlsafe_b64decode(k1 + "=" * (-len(k1) % 4))
    assert len(raw) == 65 and raw[0] == 4  # ponto P-256 não comprimido


def test_assinar_cancelar_e_validacao(client):
    assert client.post("/api/push/assinar", json=_sub(endpoint="http://inseguro/x")).status_code == 400
    assert client.post("/api/push/assinar", json=_sub(1)).status_code == 200
    assert client.post("/api/push/assinar", json=_sub(1)).status_code == 200  # idempotente
    assert client.post("/api/push/assinar", json=_sub(2)).status_code == 200
    n = lambda: len(main.db().execute("SELECT 1 FROM push_assinaturas").fetchall())
    assert n() == 2
    assert client.post("/api/push/cancelar", json=_sub(1)).status_code == 200 and n() == 1


def test_preferencias_de_alerta_por_aparelho(client):
    assert client.get("/api/push/preferencias", params={"endpoint": "https://push.example/9"}).status_code == 404
    assert client.put("/api/push/preferencias", json={"endpoint": "https://push.example/9", "recebe_monitor": False}).status_code == 404
    client.post("/api/push/assinar", json=_sub(1))
    p = client.get("/api/push/preferencias", params={"endpoint": "https://push.example/1"}).json()
    assert p == {"endpoint": "https://push.example/1", "recebe_sem_sinal": True, "recebe_falhas": True,
                 "recebe_evasao": True, "recebe_monitor": True}  # padrão: recebe tudo
    r = client.put("/api/push/preferencias", json={"endpoint": "https://push.example/1", "recebe_sem_sinal": False, "recebe_falhas": False})
    assert r.status_code == 200
    p2 = client.get("/api/push/preferencias", params={"endpoint": "https://push.example/1"}).json()
    assert p2["recebe_sem_sinal"] is False and p2["recebe_falhas"] is False and p2["recebe_evasao"] is True


def test_alerta_dispara_push_e_remove_assinatura_morta(client, monkeypatch):
    import pywebpush
    enviados = []

    class Resp:
        status_code = 410

    def falso(info, data=None, **kw):
        if info["endpoint"].endswith("/morta"):
            raise pywebpush.WebPushException("gone", response=Resp())
        enviados.append((info["endpoint"], data, kw["vapid_claims"]))
    monkeypatch.setattr(pywebpush, "webpush", falso)
    monkeypatch.setattr(main, "enviar_um", lambda c, n, t: None)
    client.post("/api/push/assinar", json=_sub(1))
    client.post("/api/push/assinar", json=_sub(endpoint="https://push.example/morta"))
    main.notificar("evasao", "🚨 Cabine RFID — EVASÃO: peça sem documento saindo por Rua às 10:00:00: Roupão (AB12CD34).")
    assert len(enviados) == 1 and enviados[0][0].endswith("/1")
    import json
    corpo = json.loads(enviados[0][1])
    assert corpo["title"] == "🚨 Evasão" and corpo["url"] == "/#evasao" and "Cabine RFID" not in corpo["body"] and "Roupão" in corpo["body"]
    assert enviados[0][2] == {"sub": main.PUBLIC_URL}
    restantes = [r["endpoint"] for r in main.db().execute("SELECT endpoint FROM push_assinaturas").fetchall()]
    assert restantes == ["https://push.example/1"]  # a que o serviço de push recusou (410) foi removida
    enviados.clear()
    main.notificar("teste", "x")  # teste de WhatsApp não vira push
    assert enviados == []
    assert client.post("/api/push/teste").json()["enviados"] == 1


def test_push_respeita_preferencia_por_tipo(client, monkeypatch):
    import pywebpush
    enviados = []
    monkeypatch.setattr(pywebpush, "webpush", lambda info, data=None, **kw: enviados.append(info["endpoint"]))
    client.post("/api/push/assinar", json=_sub(1))
    client.post("/api/push/assinar", json=_sub(2))
    client.put("/api/push/preferencias", json={"endpoint": "https://push.example/2", "recebe_sem_sinal": False})
    assert main.enviar_push("sem_sinal", "sem sinal") == 1 and enviados == ["https://push.example/1"]
    enviados.clear()
    assert main.enviar_push("evasao", "evasão") == 2  # tipo sem coluna (hardware): vai pra todos
    assert sorted(enviados) == ["https://push.example/1", "https://push.example/2"]
