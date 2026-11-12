"""Armazenamento dos vídeos das ocorrências: disco local (padrão) ou qualquer servidor S3 (MinIO, AWS, Wasabi, R2...).

Escolha pelo ambiente: com CLIPES_S3_ENDPOINT definido os vídeos vão para o bucket; sem ele, ficam em CLIPES_DIR.
Trocar de servidor S3 = mudar as variáveis CLIPES_S3_* (e copiar os objetos `clipes/<id>.mp4` para o novo bucket).
Arquivos que ainda estão no disco continuam sendo servidos, e `migrar_locais()` os sobe para o bucket.
"""
import os
from pathlib import Path

ENDPOINT = os.environ.get("CLIPES_S3_ENDPOINT", "").strip()
BUCKET = os.environ.get("CLIPES_S3_BUCKET", "cabine-clipes").strip()
ACCESS_KEY = os.environ.get("CLIPES_S3_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("CLIPES_S3_SECRET_KEY", "")
REGIAO = os.environ.get("CLIPES_S3_REGION", "us-east-1")
ESTILO_PATH = os.environ.get("CLIPES_S3_PATH_STYLE", "1") != "0"  # MinIO e a maioria dos S3 próprios usam path-style
_cliente = None


def ativo() -> bool:
    return bool(ENDPOINT)


def _chave(clipe_id: int) -> str:
    return f"clipes/{int(clipe_id)}.mp4"  # só o id numérico: nada vindo de usuário entra no caminho


def _s3():
    global _cliente
    if _cliente is None:
        import boto3
        from botocore.config import Config
        _cliente = boto3.client(
            "s3", endpoint_url=ENDPOINT, aws_access_key_id=ACCESS_KEY, aws_secret_access_key=SECRET_KEY, region_name=REGIAO,
            config=Config(s3={"addressing_style": "path" if ESTILO_PATH else "virtual"}, signature_version="s3v4",
                          retries={"max_attempts": 3}, connect_timeout=5, read_timeout=60))
    return _cliente


def garantir_bucket():
    """Cria o bucket se não existir (idempotente). Erros viram aviso: o app sobe mesmo com o S3 fora do ar."""
    if not ativo():
        return
    from botocore.exceptions import ClientError
    c = _s3()
    try:
        c.head_bucket(Bucket=BUCKET)
    except ClientError as e:
        if str(e.response.get("Error", {}).get("Code")) in ("404", "NoSuchBucket", "NotFound"):
            c.create_bucket(Bucket=BUCKET)
        else:
            raise


def guardar(clipe_id: int, arquivo: Path):
    """Sobe o mp4 para o bucket."""
    extra = {"ContentType": "video/mp4"}
    try:
        _s3().upload_file(str(arquivo), BUCKET, _chave(clipe_id), ExtraArgs=extra)
    except Exception as e:  # bucket ainda não existe (S3 estava fora na subida): cria e repete uma vez
        if "NoSuchBucket" not in str(e) and "404" not in str(e):
            raise
        garantir_bucket()
        _s3().upload_file(str(arquivo), BUCKET, _chave(clipe_id), ExtraArgs=extra)


def apagar(clipe_id: int):
    if ativo():
        _s3().delete_object(Bucket=BUCKET, Key=_chave(clipe_id))


def existe(clipe_id: int) -> bool:
    from botocore.exceptions import ClientError
    try:
        _s3().head_object(Bucket=BUCKET, Key=_chave(clipe_id))
        return True
    except ClientError:
        return False


def abrir(clipe_id: int, faixa: str | None = None):
    """Lê o objeto (opcionalmente uma faixa 'bytes=a-b'). Devolve (status, cabeçalhos, iterador de bytes)."""
    args = {"Bucket": BUCKET, "Key": _chave(clipe_id)}
    if faixa:
        args["Range"] = faixa
    r = _s3().get_object(**args)
    cab = {"Content-Length": str(r["ContentLength"]), "Accept-Ranges": "bytes"}
    if r.get("ContentRange"):
        cab["Content-Range"] = r["ContentRange"]
    corpo = r["Body"]

    def blocos():
        try:
            while b := corpo.read(1024 * 1024):
                yield b
        finally:
            corpo.close()
    return (206 if faixa and r.get("ContentRange") else 200), cab, blocos()


def migrar_locais(pasta: Path) -> int:
    """Sobe para o bucket os mp4 que ficaram no disco (de antes do S3) e apaga a cópia local. Devolve quantos migrou."""
    if not ativo() or not pasta.is_dir():
        return 0
    n = 0
    for f in sorted(pasta.glob("*.mp4")):
        try:
            guardar(int(f.stem), f)
            f.unlink()
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"migração do vídeo {f.name} para o S3 falhou: {e}", flush=True)
            break  # S3 fora do ar: tenta de novo no próximo ciclo
    return n
