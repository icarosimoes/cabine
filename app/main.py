"""Receptor de webhook do leitor Impinj (R700 Event Reporting) + painel ao vivo."""
import asyncio, base64, binascii, json, os, secrets, time, urllib.parse, urllib.request
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from contextlib import asynccontextmanager, closing
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_USER, WEBHOOK_PASS = os.environ["WEBHOOK_USER"], os.environ["WEBHOOK_PASS"]
UI_USER, UI_PASS = os.environ["UI_USER"], os.environ["UI_PASS"]
SAIDA_USER, SAIDA_PASS = os.environ["SAIDA_USER"], os.environ["SAIDA_PASS"]
KUMA_PUSH_URL = os.environ.get("KUMA_PUSH_URL", "")
STALE_MINUTES = int(os.environ.get("STALE_MINUTES", "30"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
SAIDA_JANELA_MINUTOS = int(os.environ.get("SAIDA_JANELA_MINUTOS", "60"))
STATIC = Path(__file__).parent / "static"
security = HTTPBasic()

ITENS_VIEW_SQL = """
SELECT i.epc, i.tipo, i.fabricante, i.tamanho, i.composicao, i.cor, i.criado_em, i.atualizado_em,
       d.nome AS localizacao, e.nome AS estado
FROM itens i
JOIN estados_inventario e ON e.id = i.estado_id
LEFT JOIN (
  SELECT li.epc, t.destino_id,
         ROW_NUMBER() OVER (PARTITION BY li.epc ORDER BY t.criado_em DESC) rn
  FROM lote_itens li JOIN transferencias t ON t.lote_id = li.lote_id
) ut ON ut.epc = i.epc AND ut.rn = 1
LEFT JOIN destinos d ON d.id = ut.destino_id
"""


def db():
    return psycopg.connect(DATABASE_URL, autocommit=False, row_factory=dict_row)


def init_db():
    with closing(db()) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          received_at DOUBLE PRECISION NOT NULL,
          event_type TEXT,
          epc TEXT, tid TEXT, antenna TEXT, rssi DOUBLE PRECISION,
          seen_at TEXT,
          raw TEXT NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_ev_recv ON events(received_at)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_ev_epc ON events(epc, received_at)")

        con.execute("""
        CREATE TABLE IF NOT EXISTS destinos(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tipo TEXT NOT NULL CHECK(tipo IN ('setor','lavanderia')),
          nome TEXT NOT NULL UNIQUE,
          ativo INTEGER NOT NULL DEFAULT 1,
          criado_em DOUBLE PRECISION NOT NULL)""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS lotes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          criado_em DOUBLE PRECISION NOT NULL,
          janela_segundos INTEGER NOT NULL,
          quantidade INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'aguardando' CHECK(status IN ('aguardando','transferido')))""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS lote_itens(
          lote_id INTEGER NOT NULL REFERENCES lotes(id),
          epc TEXT NOT NULL,
          PRIMARY KEY(lote_id, epc))""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS transferencias(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          lote_id INTEGER NOT NULL UNIQUE REFERENCES lotes(id),
          destino_id INTEGER NOT NULL REFERENCES destinos(id),
          observacao TEXT,
          criado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_transf_destino ON transferencias(destino_id)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_transf_criado ON transferencias(criado_em)")

        con.execute("""
        CREATE TABLE IF NOT EXISTS estados_inventario(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          nome TEXT NOT NULL UNIQUE,
          descricao TEXT,
          comportamento TEXT NOT NULL CHECK(comportamento IN ('ativo','inativo')) DEFAULT 'ativo',
          padrao INTEGER NOT NULL DEFAULT 0,
          criado_em DOUBLE PRECISION NOT NULL)""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS itens(
          epc TEXT PRIMARY KEY,
          tipo TEXT NOT NULL,
          fabricante TEXT, tamanho TEXT, composicao TEXT, cor TEXT,
          estado_id INTEGER NOT NULL REFERENCES estados_inventario(id),
          criado_em DOUBLE PRECISION NOT NULL, atualizado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_itens_tipo ON itens(tipo)")

        con.execute("""
        CREATE TABLE IF NOT EXISTS eventos_saida(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          epc TEXT NOT NULL,
          destino_id INTEGER NOT NULL REFERENCES destinos(id),
          detectado_em DOUBLE PRECISION NOT NULL,
          documentado INTEGER NOT NULL,
          transferencia_id INTEGER REFERENCES transferencias(id))""")
        con.execute("CREATE INDEX IF NOT EXISTS ix_saida_epc ON eventos_saida(epc, detectado_em)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_saida_destino ON eventos_saida(destino_id, detectado_em)")
        con.execute("ALTER TABLE destinos ADD COLUMN IF NOT EXISTS alerta INTEGER NOT NULL DEFAULT 0")

        con.execute("""
        CREATE TABLE IF NOT EXISTS antenas(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          nome TEXT NOT NULL UNIQUE,
          modelo TEXT,
          destino_id INTEGER REFERENCES destinos(id),
          criado_em DOUBLE PRECISION NOT NULL)""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS acomodacoes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          nome TEXT NOT NULL UNIQUE,
          uhs INTEGER NOT NULL DEFAULT 0,
          criado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS acomodacao_itens(
          acomodacao_id INTEGER NOT NULL REFERENCES acomodacoes(id) ON DELETE CASCADE,
          tipo TEXT NOT NULL,
          qtd DOUBLE PRECISION NOT NULL,
          fluxo DOUBLE PRECISION NOT NULL DEFAULT 1,
          PRIMARY KEY(acomodacao_id, tipo))""")
        con.commit()

        if not con.execute("SELECT 1 FROM estados_inventario").fetchone():
            now = time.time()
            with con.cursor() as cur:
                cur.executemany(
                    "INSERT INTO estados_inventario(nome,descricao,comportamento,padrao,criado_em) VALUES(%s,%s,%s,%s,%s)",
                    [("Em uso", "Peça em uso normal", "ativo", 1, now),
                     ("Em manutenção", "Temporariamente fora de uso", "ativo", 0, now),
                     ("Vendido", "Vendida, não volta mais", "inativo", 0, now),
                     ("Descartado", "Fim de vida útil", "inativo", 0, now)])
            con.commit()

        if not con.execute("SELECT 1 FROM destinos").fetchone():
            con.execute("INSERT INTO destinos(tipo,nome,ativo,criado_em) VALUES('setor','Governança',1,%s)", (time.time(),))
            con.commit()


def auth(user, pw):
    def dep(c: HTTPBasicCredentials = Depends(security)):
        ok = secrets.compare_digest(c.username, user) & secrets.compare_digest(c.password, pw)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return dep


webhook_auth, ui_auth = auth(WEBHOOK_USER, WEBHOOK_PASS), auth(UI_USER, UI_PASS)
saida_auth = auth(SAIDA_USER, SAIDA_PASS)


def first(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]


def epc_hex(valor):
    """EPC canônico = hex maiúsculo. O R700 manda `epcHex` (hex) ou `epc` (base64 dos mesmos bytes)."""
    if not valor:
        return None
    s = str(valor).strip()
    if len(s) % 4 == 0 and not (len(s) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in s) and len(s) >= 20):
        try:
            return base64.b64decode(s, validate=True).hex().upper()
        except (binascii.Error, ValueError):
            pass
    return s.upper()


def parse_event(ev):
    """Extrai campos do evento do R700 de forma tolerante; o JSON bruto sempre é guardado."""
    if not isinstance(ev, dict):
        return dict(event_type=None, epc=None, tid=None, antenna=None, rssi=None, seen_at=None)
    etype = first(ev, "eventType", "type")
    body = next((v for k, v in ev.items() if isinstance(v, dict) and "tag" in k.lower()), None) \
        or next((v for v in ev.values() if isinstance(v, dict)), ev)
    epc = first(body, "epcHex") or first(ev, "epcHex")
    if epc:
        epc = str(epc).strip().upper()
    else:
        epc = epc_hex(first(body, "epc") or first(ev, "epc"))
    rssi = first(body, "peakRssiCdbm")
    if rssi is not None:
        rssi = rssi / 100
    else:
        rssi = first(body, "peakRssi", "rssi")
    return dict(
        event_type=etype, epc=str(epc) if epc else None,
        tid=first(body, "tidHex", "tid"),
        antenna=str(first(body, "antennaName", "antennaPort", "antenna") or "") or None,
        rssi=rssi, seen_at=first(body, "lastSeenTime", "lastSeenTimestamp", "timestamp") or first(ev, "timestamp"))


state = {"last_push": None}


def push_kuma():
    if not KUMA_PUSH_URL:
        return
    with closing(db()) as con:
        last = con.execute("SELECT MAX(received_at) m FROM events").fetchone()["m"]
    age = None if last is None else (time.time() - last) / 60
    up = age is not None and age <= STALE_MINUTES
    msg = "sem eventos ainda" if age is None else f"ultimo evento ha {age:.0f} min"
    sep = "&" if "?" in KUMA_PUSH_URL else "?"
    url = f"{KUMA_PUSH_URL}{sep}status={'up' if up else 'down'}&msg={urllib.parse.quote(msg)}"
    try:
        urllib.request.urlopen(url, timeout=10).read()
        state["last_push"] = time.time()
    except Exception as e:  # noqa: BLE001
        print("push kuma falhou:", e)


async def background():
    n = 0
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(push_kuma)
        n += 1
        if n % 60 == 0:
            cutoff = time.time() - RETENTION_DAYS * 86400
            def purge():
                with closing(db()) as con:
                    con.execute("DELETE FROM events WHERE received_at<%s", (cutoff,))
                    con.commit()
            await asyncio.to_thread(purge)


def init_db_retry(tentativas=30):
    for i in range(tentativas):
        try:
            return init_db()
        except psycopg.OperationalError as e:
            print(f"postgres indisponível ({i + 1}/{tentativas}): {e}")
            time.sleep(2)
    init_db()


@asynccontextmanager
async def lifespan(_):
    await asyncio.to_thread(init_db_retry)
    t = asyncio.create_task(background())
    yield
    t.cancel()

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/ingest", dependencies=[Depends(webhook_auth)])
async def ingest(request: Request):
    body = await request.body()
    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(400, "JSON inválido")
    events = data if isinstance(data, list) else data.get("events", [data]) if isinstance(data, dict) else []
    now = time.time()
    rows = []
    for ev in events:
        p = parse_event(ev)
        rows.append((now, p["event_type"], p["epc"], p["tid"], p["antenna"], p["rssi"], p["seen_at"],
                     json.dumps(ev, ensure_ascii=False)))
    def gravar():
        with closing(db()) as con:
            with con.cursor() as cur:
                cur.executemany(
                    "INSERT INTO events(received_at,event_type,epc,tid,antenna,rssi,seen_at,raw) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    rows)
            con.commit()
    await asyncio.to_thread(gravar)
    return {"ok": True, "received": len(rows)}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(ui_auth)])
def summary():
    now = time.time()
    with closing(db()) as con:
        q = lambda sql, *a: list(con.execute(sql, a).fetchone().values())[0]
        last = q("SELECT MAX(received_at) FROM events")
        return {
            "now": now, "last_event": last,
            "events_1min": q("SELECT COUNT(*) FROM events WHERE received_at>%s", now - 60),
            "events_1h": q("SELECT COUNT(*) FROM events WHERE received_at>%s", now - 3600),
            "tags_1h": q("SELECT COUNT(DISTINCT epc) FROM events WHERE epc IS NOT NULL AND received_at>%s", now - 3600),
            "tags_24h": q("SELECT COUNT(DISTINCT epc) FROM events WHERE epc IS NOT NULL AND received_at>%s", now - 86400),
            "total": q("SELECT COUNT(*) FROM events"),
            "stale_minutes": STALE_MINUTES,
        }


@app.get("/api/tags", dependencies=[Depends(ui_auth)])
def tags(hours: float = 24, limit: int = 200):
    since = time.time() - hours * 3600
    with closing(db()) as con:
        rows = con.execute("""
          SELECT t.*, i.tipo FROM (
            SELECT epc, COUNT(*) reads, MAX(received_at) last, MIN(received_at) first,
                   MAX(rssi) best_rssi, STRING_AGG(DISTINCT antenna, ',') antennas
            FROM events WHERE epc IS NOT NULL AND received_at>%s
            GROUP BY epc ORDER BY last DESC LIMIT %s) t
          LEFT JOIN itens i ON i.epc = t.epc
          ORDER BY t.last DESC""", (since, min(limit, 1000))).fetchall()
    return rows


@app.get("/api/events", dependencies=[Depends(ui_auth)])
def events(limit: int = 100, epc: str | None = None, after_id: int = 0, raw: bool = False):
    sql = ("SELECT e.id,e.received_at,e.event_type,e.epc,e.tid,e.antenna,e.rssi,e.seen_at,i.tipo"
           + (",e.raw" if raw else "") + " FROM events e LEFT JOIN itens i ON i.epc=e.epc WHERE e.id>%s")
    args: list = [after_id]
    if epc:
        sql += " AND e.epc=%s"; args.append(epc)
    sql += " ORDER BY e.id DESC LIMIT %s"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


class DestinoIn(BaseModel):
    tipo: str
    nome: str
    alerta: bool = False


class DestinoPatch(BaseModel):
    ativo: bool | None = None
    alerta: bool | None = None


class TransferenciaIn(BaseModel):
    destino_id: int
    observacao: str | None = None


def classificar_epcs(con, epcs):
    if not epcs:
        return [], []
    placeholders = ",".join(["%s"] * len(epcs))
    cadastrados = {r["epc"]: r for r in con.execute(
        f"SELECT epc,tipo,fabricante,tamanho,composicao,cor FROM itens WHERE epc IN ({placeholders})", epcs).fetchall()}
    identificados = [cadastrados[e] for e in epcs if e in cadastrados]
    novos = [e for e in epcs if e not in cadastrados]
    return identificados, novos


@app.post("/api/lotes/registrar", dependencies=[Depends(ui_auth)])
def registrar_lote(janela_segundos: int = 15):
    janela_segundos = max(1, min(janela_segundos, 600))
    now = time.time()
    with closing(db()) as con:
        epcs = [r["epc"] for r in con.execute(
            "SELECT DISTINCT epc FROM events WHERE epc IS NOT NULL AND received_at>%s",
            (now - janela_segundos,)).fetchall()]
        if not epcs:
            raise HTTPException(400, f"nenhuma tag lida nos últimos {janela_segundos}s")
        lote_id = con.execute(
            "INSERT INTO lotes(criado_em,janela_segundos,quantidade,status) VALUES(%s,%s,%s,'aguardando') RETURNING id",
            (now, janela_segundos, len(epcs))).fetchone()["id"]
        with con.cursor() as cur:
            cur.executemany("INSERT INTO lote_itens(lote_id,epc) VALUES(%s,%s)", [(lote_id, e) for e in epcs])
        con.commit()
        identificados, novos = classificar_epcs(con, epcs)
    return {"id": lote_id, "criado_em": now, "quantidade": len(epcs), "epcs": epcs, "status": "aguardando",
            "identificados": identificados, "novos": novos}


@app.get("/api/lotes", dependencies=[Depends(ui_auth)])
def listar_lotes(status: str | None = None, limit: int = 100):
    sql = "SELECT * FROM lotes"
    args: list = []
    if status:
        sql += " WHERE status=%s"; args.append(status)
    sql += " ORDER BY id DESC LIMIT %s"; args.append(min(limit, 500))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


@app.get("/api/lotes/{lote_id}", dependencies=[Depends(ui_auth)])
def detalhe_lote(lote_id: int):
    with closing(db()) as con:
        lote = con.execute("SELECT * FROM lotes WHERE id=%s", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
        epcs = [r["epc"] for r in con.execute("SELECT epc FROM lote_itens WHERE lote_id=%s", (lote_id,)).fetchall()]
        transf = con.execute(
            """SELECT t.*, d.nome destino_nome, d.tipo destino_tipo FROM transferencias t
               JOIN destinos d ON d.id=t.destino_id WHERE t.lote_id=%s""", (lote_id,)).fetchone()
        identificados, novos = classificar_epcs(con, epcs)
    out = dict(lote)
    out["epcs"] = epcs
    out["identificados"] = identificados
    out["novos"] = novos
    out["transferencia"] = dict(transf) if transf else None
    return out


@app.post("/api/lotes/{lote_id}/transferir", dependencies=[Depends(ui_auth)])
def transferir_lote(lote_id: int, payload: TransferenciaIn):
    now = time.time()
    with closing(db()) as con:
        lote = con.execute("SELECT * FROM lotes WHERE id=%s", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
        if lote["status"] == "transferido":
            raise HTTPException(409, "lote já foi transferido")
        destino = con.execute("SELECT * FROM destinos WHERE id=%s AND ativo=1", (payload.destino_id,)).fetchone()
        if not destino:
            raise HTTPException(400, "destino inválido ou inativo")
        try:
            con.execute("INSERT INTO transferencias(lote_id,destino_id,observacao,criado_em) VALUES(%s,%s,%s,%s)",
                        (lote_id, payload.destino_id, (payload.observacao or "").strip() or None, now))
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "lote já foi transferido")
        con.execute("UPDATE lotes SET status='transferido' WHERE id=%s", (lote_id,))
        con.commit()
    return {"ok": True, "lote_id": lote_id, "destino": destino["nome"], "criado_em": now}


@app.get("/api/destinos", dependencies=[Depends(ui_auth)])
def listar_destinos(tipo: str | None = None, ativo: bool | None = None):
    sql = "SELECT * FROM destinos"
    cond, args = [], []
    if tipo:
        cond.append("tipo=%s"); args.append(tipo)
    if ativo is not None:
        cond.append("ativo=%s"); args.append(1 if ativo else 0)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY tipo, nome"
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


@app.post("/api/destinos", dependencies=[Depends(ui_auth)])
def criar_destino(payload: DestinoIn):
    if payload.tipo not in ("setor", "lavanderia"):
        raise HTTPException(400, "tipo deve ser 'setor' ou 'lavanderia'")
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    with closing(db()) as con:
        try:
            con.execute("INSERT INTO destinos(tipo,nome,ativo,alerta,criado_em) VALUES(%s,%s,1,%s,%s)",
                        (payload.tipo, nome, 1 if payload.alerta else 0, time.time()))
            con.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "já existe um destino com esse nome")
        row = con.execute("SELECT * FROM destinos WHERE nome=%s", (nome,)).fetchone()
    return row


@app.patch("/api/destinos/{destino_id}", dependencies=[Depends(ui_auth)])
def atualizar_destino(destino_id: int, payload: DestinoPatch):
    with closing(db()) as con:
        if payload.ativo is not None:
            con.execute("UPDATE destinos SET ativo=%s WHERE id=%s", (1 if payload.ativo else 0, destino_id))
        if payload.alerta is not None:
            con.execute("UPDATE destinos SET alerta=%s WHERE id=%s", (1 if payload.alerta else 0, destino_id))
        con.commit()
        row = con.execute("SELECT * FROM destinos WHERE id=%s", (destino_id,)).fetchone()
    if not row:
        raise HTTPException(404, "destino não encontrado")
    return row


@app.get("/api/transferencias", dependencies=[Depends(ui_auth)])
def listar_transferencias(destino_id: int | None = None, tipo: str | None = None,
                           desde: float | None = None, ate: float | None = None, limit: int = 200):
    sql = """SELECT t.id, t.lote_id, t.destino_id, d.nome destino_nome, d.tipo destino_tipo,
                    t.observacao, t.criado_em, l.quantidade
             FROM transferencias t
             JOIN destinos d ON d.id=t.destino_id
             JOIN lotes l ON l.id=t.lote_id"""
    cond, args = [], []
    if destino_id:
        cond.append("t.destino_id=%s"); args.append(destino_id)
    if tipo:
        cond.append("d.tipo=%s"); args.append(tipo)
    if desde:
        cond.append("t.criado_em>=%s"); args.append(desde)
    if ate:
        cond.append("t.criado_em<=%s"); args.append(ate)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY t.id DESC LIMIT %s"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


class ItemIn(BaseModel):
    epc: str
    tipo: str
    fabricante: str | None = None
    tamanho: str | None = None
    composicao: str | None = None
    cor: str | None = None


@app.post("/api/itens", dependencies=[Depends(ui_auth)])
def cadastrar_item(payload: ItemIn):
    epc, tipo = payload.epc.strip(), payload.tipo.strip()
    if not epc or not tipo:
        raise HTTPException(400, "epc e tipo são obrigatórios")
    now = time.time()
    with closing(db()) as con:
        estado_padrao = con.execute("SELECT id FROM estados_inventario WHERE padrao=1 LIMIT 1").fetchone()["id"]
        con.execute("""INSERT INTO itens(epc,tipo,fabricante,tamanho,composicao,cor,estado_id,criado_em,atualizado_em)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(epc) DO UPDATE SET tipo=excluded.tipo, fabricante=excluded.fabricante,
                          tamanho=excluded.tamanho, composicao=excluded.composicao, cor=excluded.cor,
                          atualizado_em=excluded.atualizado_em""",
                    (epc, tipo, payload.fabricante, payload.tamanho, payload.composicao, payload.cor,
                     estado_padrao, now, now))
        con.commit()
        row = con.execute(f"SELECT * FROM ({ITENS_VIEW_SQL}) v WHERE v.epc=%s", (epc,)).fetchone()
    return row


@app.get("/api/itens", dependencies=[Depends(ui_auth)])
def listar_itens(q: str | None = None, limit: int = 200):
    sql = f"SELECT * FROM ({ITENS_VIEW_SQL}) v"
    args: list = []
    if q:
        sql += " WHERE v.epc ILIKE %s OR v.tipo ILIKE %s"
        like = f"%{q}%"; args += [like, like]
    sql += " ORDER BY v.atualizado_em DESC LIMIT %s"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


@app.get("/api/itens/opcoes", dependencies=[Depends(ui_auth)])
def itens_opcoes():
    with closing(db()) as con:
        def distinct(col):
            return [r[col] for r in con.execute(
                f"SELECT DISTINCT {col} FROM itens WHERE {col} IS NOT NULL AND {col}<>'' ORDER BY {col}").fetchall()]
        resultado = {
            "tipos": distinct("tipo"), "fabricantes": distinct("fabricante"), "tamanhos": distinct("tamanho"),
            "composicoes": distinct("composicao"), "cores": distinct("cor"),
            "localizacoes": [r["nome"] for r in con.execute("SELECT nome FROM destinos ORDER BY nome").fetchall()],
            "estados": [r["nome"] for r in con.execute("SELECT nome FROM estados_inventario ORDER BY nome").fetchall()],
        }
    return resultado


class EstadoIn(BaseModel):
    nome: str
    descricao: str | None = None
    comportamento: str = "ativo"


class EstadoItemIn(BaseModel):
    estado_id: int


@app.get("/api/estados", dependencies=[Depends(ui_auth)])
def listar_estados():
    with closing(db()) as con:
        return con.execute("SELECT * FROM estados_inventario ORDER BY padrao DESC, nome").fetchall()


@app.post("/api/estados", dependencies=[Depends(ui_auth)])
def criar_estado(payload: EstadoIn):
    if payload.comportamento not in ("ativo", "inativo"):
        raise HTTPException(400, "comportamento deve ser 'ativo' ou 'inativo'")
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    with closing(db()) as con:
        try:
            con.execute("INSERT INTO estados_inventario(nome,descricao,comportamento,padrao,criado_em) VALUES(%s,%s,%s,0,%s)",
                        (nome, payload.descricao, payload.comportamento, time.time()))
            con.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "já existe um estado com esse nome")
        row = con.execute("SELECT * FROM estados_inventario WHERE nome=%s", (nome,)).fetchone()
    return row


@app.patch("/api/itens/{epc}/estado", dependencies=[Depends(ui_auth)])
def definir_estado_item(epc: str, payload: EstadoItemIn):
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM itens WHERE epc=%s", (epc,)).fetchone():
            raise HTTPException(404, "item não encontrado no catálogo")
        if not con.execute("SELECT 1 FROM estados_inventario WHERE id=%s", (payload.estado_id,)).fetchone():
            raise HTTPException(400, "estado inválido")
        con.execute("UPDATE itens SET estado_id=%s, atualizado_em=%s WHERE epc=%s",
                    (payload.estado_id, time.time(), epc))
        con.commit()
        row = con.execute(f"SELECT * FROM ({ITENS_VIEW_SQL}) v WHERE v.epc=%s", (epc,)).fetchone()
    return row


@app.get("/api/inventario", dependencies=[Depends(ui_auth)])
def inventario(localizacao: str | None = None, estado: str | None = None, tipo: str | None = None,
                fabricante: str | None = None, tamanho: str | None = None,
                composicao: str | None = None, cor: str | None = None):
    cond, args = [], []
    for campo, valor in (("localizacao", localizacao), ("estado", estado), ("tipo", tipo),
                          ("fabricante", fabricante), ("tamanho", tamanho),
                          ("composicao", composicao), ("cor", cor)):
        if valor:
            cond.append(f"v.{campo}=%s"); args.append(valor)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    with closing(db()) as con:
        resumo = con.execute(
            f"SELECT v.tipo, COUNT(*) quantidade FROM ({ITENS_VIEW_SQL}) v{where} GROUP BY v.tipo ORDER BY quantidade DESC",
            args).fetchall()
        total = list(con.execute(f"SELECT COUNT(*) FROM ({ITENS_VIEW_SQL}) v{where}", args).fetchone().values())[0]
    return {"total": total, "resumo": resumo}


class SaidaIn(BaseModel):
    epc: str
    destino_id: int


@app.post("/api/saida", dependencies=[Depends(saida_auth)])
def registrar_saida(payload: SaidaIn):
    payload.epc = payload.epc.strip()
    if not payload.epc:
        raise HTTPException(400, "epc é obrigatório")
    now = time.time()
    with closing(db()) as con:
        destino = con.execute("SELECT * FROM destinos WHERE id=%s", (payload.destino_id,)).fetchone()
        if not destino:
            raise HTTPException(400, "destino inválido")
        match = con.execute("""
            SELECT t.id FROM transferencias t
            JOIN lote_itens li ON li.lote_id = t.lote_id
            WHERE li.epc=%s AND t.destino_id=%s AND t.criado_em>=%s
            ORDER BY t.criado_em DESC LIMIT 1""",
            (payload.epc, payload.destino_id, now - SAIDA_JANELA_MINUTOS * 60)).fetchone()
        documentado = match is not None
        con.execute("""INSERT INTO eventos_saida(epc,destino_id,detectado_em,documentado,transferencia_id)
                        VALUES(%s,%s,%s,%s,%s)""",
                    (payload.epc, payload.destino_id, now, 1 if documentado else 0,
                     match["id"] if match else None))
        con.commit()
    return {"epc": payload.epc, "destino": destino["nome"], "documentado": documentado, "detectado_em": now}


@app.get("/api/saida", dependencies=[Depends(ui_auth)])
def listar_saidas(documentado: bool | None = None, destino_id: int | None = None,
                   desde: float | None = None, ate: float | None = None, limit: int = 200):
    sql = """SELECT s.id, s.epc, s.destino_id, d.nome destino_nome, s.detectado_em, s.documentado, s.transferencia_id
             FROM eventos_saida s JOIN destinos d ON d.id=s.destino_id"""
    cond, args = [], []
    if documentado is not None:
        cond.append("s.documentado=%s"); args.append(1 if documentado else 0)
    if destino_id:
        cond.append("s.destino_id=%s"); args.append(destino_id)
    if desde:
        cond.append("s.detectado_em>=%s"); args.append(desde)
    if ate:
        cond.append("s.detectado_em<=%s"); args.append(ate)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY s.id DESC LIMIT %s"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()



class AntenaIn(BaseModel):
    nome: str
    modelo: str | None = None
    destino_id: int | None = None


@app.get("/api/antenas", dependencies=[Depends(ui_auth)])
def listar_antenas():
    with closing(db()) as con:
        return con.execute("""SELECT a.id, a.nome, a.modelo, a.destino_id, d.nome destino_nome
                              FROM antenas a LEFT JOIN destinos d ON d.id=a.destino_id ORDER BY a.nome""").fetchall()


@app.post("/api/antenas", dependencies=[Depends(ui_auth)])
def salvar_antena(payload: AntenaIn):
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    with closing(db()) as con:
        if payload.destino_id and not con.execute("SELECT 1 FROM destinos WHERE id=%s", (payload.destino_id,)).fetchone():
            raise HTTPException(400, "destino inválido")
        con.execute("""INSERT INTO antenas(nome,modelo,destino_id,criado_em) VALUES(%s,%s,%s,%s)
                       ON CONFLICT(nome) DO UPDATE SET modelo=excluded.modelo, destino_id=excluded.destino_id""",
                    (nome, payload.modelo, payload.destino_id, time.time()))
        con.commit()
    return {"ok": True}


class AcomodacaoItemIn(BaseModel):
    tipo: str
    qtd: float
    fluxo: float = 1


class AcomodacaoIn(BaseModel):
    nome: str
    uhs: int = 0
    itens: list[AcomodacaoItemIn] = []


@app.get("/api/acomodacoes", dependencies=[Depends(ui_auth)])
def listar_acomodacoes():
    with closing(db()) as con:
        acs = con.execute("SELECT id,nome,uhs FROM acomodacoes ORDER BY nome").fetchall()
        itens = con.execute("SELECT acomodacao_id,tipo,qtd,fluxo FROM acomodacao_itens ORDER BY tipo").fetchall()
    for a in acs:
        a["itens"] = [{"tipo": i["tipo"], "qtd": i["qtd"], "fluxo": i["fluxo"]} for i in itens if i["acomodacao_id"] == a["id"]]
    return acs


@app.post("/api/acomodacoes", dependencies=[Depends(ui_auth)])
def salvar_acomodacao(payload: AcomodacaoIn):
    nome = payload.nome.strip()
    if not nome or payload.uhs < 0:
        raise HTTPException(400, "nome obrigatório e UHs não pode ser negativo")
    with closing(db()) as con:
        ac_id = con.execute("""INSERT INTO acomodacoes(nome,uhs,criado_em) VALUES(%s,%s,%s)
                               ON CONFLICT(nome) DO UPDATE SET uhs=excluded.uhs RETURNING id""",
                            (nome, payload.uhs, time.time())).fetchone()["id"]
        con.execute("DELETE FROM acomodacao_itens WHERE acomodacao_id=%s", (ac_id,))
        with con.cursor() as cur:
            cur.executemany("INSERT INTO acomodacao_itens(acomodacao_id,tipo,qtd,fluxo) VALUES(%s,%s,%s,%s)",
                            [(ac_id, i.tipo.strip(), i.qtd, i.fluxo) for i in payload.itens if i.tipo.strip()])
        con.commit()
    return {"ok": True, "id": ac_id}


@app.get("/api/enxoval/esperado", dependencies=[Depends(ui_auth)])
def enxoval_esperado(estado: str = "Em uso"):
    """Par de enxoval: esperado = Σ UHs × qtd por UH × fluxo (jogos em circulação), contra o atual no estado."""
    with closing(db()) as con:
        esperado = {r["tipo"]: r["esperado"] for r in con.execute(
            """SELECT ai.tipo, SUM(a.uhs * ai.qtd * ai.fluxo) esperado
               FROM acomodacao_itens ai JOIN acomodacoes a ON a.id=ai.acomodacao_id GROUP BY ai.tipo""").fetchall()}
        atual = {r["tipo"]: r["n"] for r in con.execute(
            f"SELECT v.tipo, COUNT(*) n FROM ({ITENS_VIEW_SQL}) v WHERE v.estado=%s GROUP BY v.tipo", (estado,)).fetchall()}
    tipos = sorted(set(esperado) | set(atual))
    linhas = [{"tipo": t, "esperado": round(esperado.get(t, 0), 1), "atual": atual.get(t, 0),
               "diferenca": round(atual.get(t, 0) - esperado.get(t, 0), 1)} for t in tipos]
    return {"estado": estado, "linhas": linhas}

@app.get("/", dependencies=[Depends(ui_auth)])
def index():
    return FileResponse(STATIC / "index.html")
