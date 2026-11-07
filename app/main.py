"""Receptor de webhook do leitor Impinj (R700 Event Reporting) + painel ao vivo."""
import asyncio, base64, json, os, secrets, sqlite3, time, urllib.request
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

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
        """)
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


@app.get("/", dependencies=[Depends(ui_auth)])
def index():
    return FileResponse(STATIC / "index.html")
