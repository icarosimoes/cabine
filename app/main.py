"""Receptor de webhook do leitor Impinj (R700 Event Reporting) + painel ao vivo."""
import asyncio, base64, json, os, secrets, sqlite3, time, urllib.request
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

DB_PATH = os.environ.get("DB_PATH", "/data/impinj.db")
WEBHOOK_USER, WEBHOOK_PASS = os.environ["WEBHOOK_USER"], os.environ["WEBHOOK_PASS"]
UI_USER, UI_PASS = os.environ["UI_USER"], os.environ["UI_PASS"]
KUMA_PUSH_URL = os.environ.get("KUMA_PUSH_URL", "")
STALE_MINUTES = int(os.environ.get("STALE_MINUTES", "30"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
STATIC = Path(__file__).parent / "static"
security = HTTPBasic()


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with closing(db()) as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          received_at REAL NOT NULL,
          event_type TEXT,
          epc TEXT, tid TEXT, antenna TEXT, rssi REAL,
          seen_at TEXT,
          raw TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_ev_recv ON events(received_at);
        CREATE INDEX IF NOT EXISTS ix_ev_epc ON events(epc, received_at);

        CREATE TABLE IF NOT EXISTS destinos(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          tipo TEXT NOT NULL CHECK(tipo IN ('setor','lavanderia')),
          nome TEXT NOT NULL UNIQUE,
          ativo INTEGER NOT NULL DEFAULT 1,
          criado_em REAL NOT NULL);

        CREATE TABLE IF NOT EXISTS lotes(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          criado_em REAL NOT NULL,
          janela_segundos INTEGER NOT NULL,
          quantidade INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'aguardando' CHECK(status IN ('aguardando','transferido')));

        CREATE TABLE IF NOT EXISTS lote_itens(
          lote_id INTEGER NOT NULL REFERENCES lotes(id),
          epc TEXT NOT NULL,
          PRIMARY KEY(lote_id, epc));

        CREATE TABLE IF NOT EXISTS transferencias(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          lote_id INTEGER NOT NULL UNIQUE REFERENCES lotes(id),
          destino_id INTEGER NOT NULL REFERENCES destinos(id),
          observacao TEXT,
          criado_em REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_transf_destino ON transferencias(destino_id);
        CREATE INDEX IF NOT EXISTS ix_transf_criado ON transferencias(criado_em);
        """)
        con.commit()
        if not con.execute("SELECT 1 FROM destinos").fetchone():
            con.execute("INSERT INTO destinos(tipo,nome,ativo,criado_em) VALUES('setor','Governança',1,?)", (time.time(),))
            con.commit()


def auth(user, pw):
    def dep(c: HTTPBasicCredentials = Depends(security)):
        ok = secrets.compare_digest(c.username, user) & secrets.compare_digest(c.password, pw)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return dep


webhook_auth, ui_auth = auth(WEBHOOK_USER, WEBHOOK_PASS), auth(UI_USER, UI_PASS)


def first(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]


def parse_event(ev):
    """Extrai campos do evento do R700 de forma tolerante; o JSON bruto sempre é guardado."""
    if not isinstance(ev, dict):
        return dict(event_type=None, epc=None, tid=None, antenna=None, rssi=None, seen_at=None)
    etype = first(ev, "eventType", "type")
    body = next((v for k, v in ev.items() if isinstance(v, dict) and "tag" in k.lower()), None) \
        or next((v for v in ev.values() if isinstance(v, dict)), ev)
    epc = first(body, "epcHex", "epc") or first(ev, "epcHex", "epc")
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
                    con.execute("DELETE FROM events WHERE received_at<?", (cutoff,))
                    con.commit()
            await asyncio.to_thread(purge)


@asynccontextmanager
async def lifespan(_):
    init_db()
    t = asyncio.create_task(background())
    yield
    t.cancel()

import urllib.parse  # noqa: E402
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
    with closing(db()) as con:
        con.executemany("INSERT INTO events(received_at,event_type,epc,tid,antenna,rssi,seen_at,raw) VALUES(?,?,?,?,?,?,?,?)", rows)
        con.commit()
    return {"ok": True, "received": len(rows)}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(ui_auth)])
def summary():
    now = time.time()
    with closing(db()) as con:
        q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        last = q("SELECT MAX(received_at) FROM events")
        return {
            "now": now, "last_event": last,
            "events_1min": q("SELECT COUNT(*) FROM events WHERE received_at>?", now - 60),
            "events_1h": q("SELECT COUNT(*) FROM events WHERE received_at>?", now - 3600),
            "tags_1h": q("SELECT COUNT(DISTINCT epc) FROM events WHERE epc IS NOT NULL AND received_at>?", now - 3600),
            "tags_24h": q("SELECT COUNT(DISTINCT epc) FROM events WHERE epc IS NOT NULL AND received_at>?", now - 86400),
            "total": q("SELECT COUNT(*) FROM events"),
            "stale_minutes": STALE_MINUTES,
        }


@app.get("/api/tags", dependencies=[Depends(ui_auth)])
def tags(hours: float = 24, limit: int = 200):
    since = time.time() - hours * 3600
    with closing(db()) as con:
        rows = con.execute("""
          SELECT epc, COUNT(*) reads, MAX(received_at) last, MIN(received_at) first,
                 MAX(rssi) best_rssi, GROUP_CONCAT(DISTINCT antenna) antennas
          FROM events WHERE epc IS NOT NULL AND received_at>?
          GROUP BY epc ORDER BY last DESC LIMIT ?""", (since, min(limit, 1000))).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/events", dependencies=[Depends(ui_auth)])
def events(limit: int = 100, epc: str | None = None, after_id: int = 0, raw: bool = False):
    sql = "SELECT id,received_at,event_type,epc,tid,antenna,rssi,seen_at" + (",raw" if raw else "") + " FROM events WHERE id>?"
    args: list = [after_id]
    if epc:
        sql += " AND epc=?"; args.append(epc)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


class DestinoIn(BaseModel):
    tipo: str
    nome: str


class DestinoPatch(BaseModel):
    ativo: bool


class TransferenciaIn(BaseModel):
    destino_id: int
    observacao: str | None = None


@app.post("/api/lotes/registrar", dependencies=[Depends(ui_auth)])
def registrar_lote(janela_segundos: int = 15):
    janela_segundos = max(1, min(janela_segundos, 600))
    now = time.time()
    with closing(db()) as con:
        epcs = [r["epc"] for r in con.execute(
            "SELECT DISTINCT epc FROM events WHERE epc IS NOT NULL AND received_at>?",
            (now - janela_segundos,)).fetchall()]
        if not epcs:
            raise HTTPException(400, f"nenhuma tag lida nos últimos {janela_segundos}s")
        cur = con.execute(
            "INSERT INTO lotes(criado_em,janela_segundos,quantidade,status) VALUES(?,?,?,'aguardando')",
            (now, janela_segundos, len(epcs)))
        lote_id = cur.lastrowid
        con.executemany("INSERT INTO lote_itens(lote_id,epc) VALUES(?,?)", [(lote_id, e) for e in epcs])
        con.commit()
    return {"id": lote_id, "criado_em": now, "quantidade": len(epcs), "epcs": epcs, "status": "aguardando"}


@app.get("/api/lotes", dependencies=[Depends(ui_auth)])
def listar_lotes(status: str | None = None, limit: int = 100):
    sql = "SELECT * FROM lotes"
    args: list = []
    if status:
        sql += " WHERE status=?"; args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(min(limit, 500))
    with closing(db()) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


@app.get("/api/lotes/{lote_id}", dependencies=[Depends(ui_auth)])
def detalhe_lote(lote_id: int):
    with closing(db()) as con:
        lote = con.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
        epcs = [r["epc"] for r in con.execute("SELECT epc FROM lote_itens WHERE lote_id=?", (lote_id,)).fetchall()]
        transf = con.execute(
            """SELECT t.*, d.nome destino_nome, d.tipo destino_tipo FROM transferencias t
               JOIN destinos d ON d.id=t.destino_id WHERE t.lote_id=?""", (lote_id,)).fetchone()
    out = dict(lote)
    out["epcs"] = epcs
    out["transferencia"] = dict(transf) if transf else None
    return out


@app.post("/api/lotes/{lote_id}/transferir", dependencies=[Depends(ui_auth)])
def transferir_lote(lote_id: int, payload: TransferenciaIn):
    now = time.time()
    with closing(db()) as con:
        lote = con.execute("SELECT * FROM lotes WHERE id=?", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
        if lote["status"] == "transferido":
            raise HTTPException(409, "lote já foi transferido")
        destino = con.execute("SELECT * FROM destinos WHERE id=? AND ativo=1", (payload.destino_id,)).fetchone()
        if not destino:
            raise HTTPException(400, "destino inválido ou inativo")
        con.execute("INSERT INTO transferencias(lote_id,destino_id,observacao,criado_em) VALUES(?,?,?,?)",
                    (lote_id, payload.destino_id, (payload.observacao or "").strip() or None, now))
        con.execute("UPDATE lotes SET status='transferido' WHERE id=?", (lote_id,))
        con.commit()
    return {"ok": True, "lote_id": lote_id, "destino": destino["nome"], "criado_em": now}


@app.get("/api/destinos", dependencies=[Depends(ui_auth)])
def listar_destinos(tipo: str | None = None, ativo: bool | None = None):
    sql = "SELECT * FROM destinos"
    cond, args = [], []
    if tipo:
        cond.append("tipo=?"); args.append(tipo)
    if ativo is not None:
        cond.append("ativo=?"); args.append(1 if ativo else 0)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY tipo, nome"
    with closing(db()) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


@app.post("/api/destinos", dependencies=[Depends(ui_auth)])
def criar_destino(payload: DestinoIn):
    if payload.tipo not in ("setor", "lavanderia"):
        raise HTTPException(400, "tipo deve ser 'setor' ou 'lavanderia'")
    nome = payload.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    with closing(db()) as con:
        try:
            con.execute("INSERT INTO destinos(tipo,nome,ativo,criado_em) VALUES(?,?,1,?)",
                        (payload.tipo, nome, time.time()))
            con.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "já existe um destino com esse nome")
        row = con.execute("SELECT * FROM destinos WHERE nome=?", (nome,)).fetchone()
    return dict(row)


@app.patch("/api/destinos/{destino_id}", dependencies=[Depends(ui_auth)])
def atualizar_destino(destino_id: int, payload: DestinoPatch):
    with closing(db()) as con:
        con.execute("UPDATE destinos SET ativo=? WHERE id=?", (1 if payload.ativo else 0, destino_id))
        con.commit()
        row = con.execute("SELECT * FROM destinos WHERE id=?", (destino_id,)).fetchone()
    if not row:
        raise HTTPException(404, "destino não encontrado")
    return dict(row)


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
        cond.append("t.destino_id=?"); args.append(destino_id)
    if tipo:
        cond.append("d.tipo=?"); args.append(tipo)
    if desde:
        cond.append("t.criado_em>=?"); args.append(desde)
    if ate:
        cond.append("t.criado_em<=?"); args.append(ate)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY t.id DESC LIMIT ?"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


@app.get("/", dependencies=[Depends(ui_auth)])
def index():
    return FileResponse(STATIC / "index.html")
