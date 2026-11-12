"""Armazenamento S3 dos vídeos (com um cliente S3 falso em memória): upload, player com Range, link público, zip, exclusão, migração."""
import io
import zipfile
from app import armazenamento as arm, main
from tests.test_clipes import MP4, _setup, _saida, _status, _envelhece

DADOS = MP4 + bytes(range(200))


class _Corpo:
    def __init__(self, b): self.b = io.BytesIO(b)
    def read(self, n=-1): return self.b.read(n)
    def close(self): pass


class S3Falso:
    def __init__(self): self.obj, self.buckets = {}, set()
    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")
    def create_bucket(self, Bucket): self.buckets.add(Bucket)
    def upload_file(self, caminho, Bucket, Key, ExtraArgs=None):
        assert Bucket in self.buckets, "NoSuchBucket"
        self.obj[Key] = open(caminho, "rb").read()
    def get_object(self, Bucket, Key, Range=None):
        from botocore.exceptions import ClientError
        if Key not in self.obj:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        d = self.obj[Key]
        if Range:
            a, b = Range.removeprefix("bytes=").split("-")
            ini, fim = int(a or 0), int(b) if b else len(d) - 1
            return {"Body": _Corpo(d[ini:fim + 1]), "ContentLength": fim - ini + 1, "ContentRange": f"bytes {ini}-{fim}/{len(d)}"}
        return {"Body": _Corpo(d), "ContentLength": len(d)}
    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.obj:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
    def delete_object(self, Bucket, Key): self.obj.pop(Key, None)


def _ativa(monkeypatch, tmp_path):
    s3 = S3Falso()
    monkeypatch.setattr(arm, "ENDPOINT", "http://minio:9000")
    monkeypatch.setattr(arm, "_cliente", s3)
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    arm.garantir_bucket()
    return s3


def _clipe_pronto(client, h, antena, dest, epc="CC99"):
    cid = _saida(client, antena, dest, epc)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    assert client.put(f"/api/agente/clipes/{cid}/arquivo", content=DADOS, headers=h, auth=None).status_code == 200
    return cid


def test_upload_vai_para_o_bucket_e_nao_fica_no_disco(client, tmp_path, monkeypatch):
    s3 = _ativa(monkeypatch, tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _clipe_pronto(client, h, antena, dest)
    assert s3.obj[f"clipes/{cid}.mp4"] == DADOS
    assert list(tmp_path.iterdir()) == []  # nem .mp4 nem .part
    r = client.get(f"/api/clipes/{cid}/video")
    assert r.status_code == 200 and r.content == DADOS and r.headers["content-type"] == "video/mp4"
    p = client.get(f"/api/clipes/{cid}/video", headers={"Range": "bytes=4-11"})  # o player busca trechos
    assert p.status_code == 206 and p.content == DADOS[4:12] and p.headers["content-range"] == f"bytes 4-11/{len(DADOS)}"
    assert client.get(f"/api/clipes/{cid}/video", headers={"Range": "bytes=abc"}).status_code == 200  # Range inválido é ignorado


def test_link_publico_zip_e_exclusao_com_s3(client, tmp_path, monkeypatch):
    s3 = _ativa(monkeypatch, tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _clipe_pronto(client, h, antena, dest)
    pub = main.link_clipe(cid)[len(main.PUBLIC_URL):]
    assert client.get(pub, auth=None).content == DADOS
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/clipes-zip?ids={cid}").content))
    assert z.read(z.namelist()[0]) == DADOS
    assert client.delete(f"/api/clipes/{cid}").status_code == 200
    assert s3.obj == {} and client.get(pub, auth=None).status_code == 404


def test_falha_do_s3_no_upload_nao_marca_ok(client, tmp_path, monkeypatch):
    s3 = _ativa(monkeypatch, tmp_path)
    def quebra(*a, **k): raise OSError("S3 fora do ar")
    monkeypatch.setattr(s3, "upload_file", quebra)
    cam, antena, dest, h = _setup(client)
    cid = _saida(client, antena, dest)["clipe_id"]
    _envelhece(cid, 70); _status(client, h)
    assert client.put(f"/api/agente/clipes/{cid}/arquivo", content=DADOS, headers=h, auth=None).status_code == 503
    assert client.get("/api/saida").json()[0]["clipe_status"] == "enviado" and list(tmp_path.iterdir()) == []


def test_migracao_do_disco_para_o_s3_e_leitura_durante_a_transicao(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CLIPES_DIR", tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _clipe_pronto(client, h, antena, dest)  # S3 ainda desligado: vai para o disco
    assert (tmp_path / f"{cid}.mp4").read_bytes() == DADOS
    s3 = _ativa(monkeypatch, tmp_path)  # liga o S3
    assert client.get(f"/api/clipes/{cid}/video").content == DADOS  # ainda lê do disco
    main.purgar_clipes()
    assert s3.obj[f"clipes/{cid}.mp4"] == DADOS and not (tmp_path / f"{cid}.mp4").exists()
    assert client.get(f"/api/clipes/{cid}/video").content == DADOS  # agora do bucket


def test_retencao_apaga_tambem_no_bucket(client, tmp_path, monkeypatch):
    s3 = _ativa(monkeypatch, tmp_path)
    cam, antena, dest, h = _setup(client)
    cid = _clipe_pronto(client, h, antena, dest)
    with main.closing(main.db()) as con:
        con.execute("UPDATE clipes SET criado_em=criado_em-%s", (main.CLIPES_RETENCAO_DIAS * 86400 + 60,)); con.commit()
    main.purgar_clipes()
    assert s3.obj == {}
