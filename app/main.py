"""Receptor de webhook do leitor Impinj (R700 Event Reporting) + painel ao vivo."""
import asyncio, base64, csv, io, binascii, hashlib, hmac, json, os, secrets, time, urllib.parse, urllib.request
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from contextlib import asynccontextmanager, closing
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_USER, WEBHOOK_PASS = os.environ["WEBHOOK_USER"], os.environ["WEBHOOK_PASS"]
UI_USER, UI_PASS = os.environ["UI_USER"], os.environ["UI_PASS"]
SAIDA_USER, SAIDA_PASS = os.environ["SAIDA_USER"], os.environ["SAIDA_PASS"]
KUMA_PUSH_URL = os.environ.get("KUMA_PUSH_URL", "")
STALE_MINUTES = int(os.environ.get("STALE_MINUTES", "30"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))  # só dos eventos brutos; leituras_hora fica pra sempre
TZ_OFFSET = int(os.environ.get("TZ_OFFSET_HOURS", "-3")) * 3600  # America/Sao_Paulo (só para os CSV)
SESSAO_AUTO = os.environ.get("SESSAO_AUTO", "0") == "1"  # sessões de leitura automáticas (opt-in)
SESSAO_OCIOSO = int(os.environ.get("SESSAO_OCIOSO_SEGUNDOS", "30"))
SAIDA_JANELA_MINUTOS = int(os.environ.get("SAIDA_JANELA_MINUTOS", "60"))
STATIC = Path(__file__).parent / "static"
security = HTTPBasic()
SESSION_SECRET = (os.environ.get("SESSION_SECRET") or hashlib.sha256((UI_PASS + "|cabine-sessao").encode()).hexdigest()).encode()
SESSION_HORAS = int(os.environ.get("SESSION_HORAS", "12"))
COOKIE = "cabine_sessao"
PAPEIS = {"leitura": 1, "operador": 2, "admin": 3}

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
        CREATE TABLE IF NOT EXISTS usuarios(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          usuario TEXT NOT NULL UNIQUE,
          nome TEXT,
          senha_hash TEXT,
          papel TEXT NOT NULL DEFAULT 'operador' CHECK(papel IN ('leitura','operador','admin')),
          ativo INTEGER NOT NULL DEFAULT 1,
          criado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("ALTER TABLE transferencias ADD COLUMN IF NOT EXISTS usuario TEXT")
        con.execute("""
        CREATE TABLE IF NOT EXISTS leituras_hora(
          hora BIGINT NOT NULL, epc TEXT NOT NULL, leituras INTEGER NOT NULL, melhor_rssi DOUBLE PRECISION,
          PRIMARY KEY(hora, epc))""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS sessoes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          inicio DOUBLE PRECISION NOT NULL, fim DOUBLE PRECISION,
          status TEXT NOT NULL DEFAULT 'aberta' CHECK(status IN ('aberta','encerrada')),
          tags INTEGER NOT NULL DEFAULT 0, lote_id INTEGER REFERENCES lotes(id))""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS conferencias(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          filtros_desc TEXT NOT NULL,
          iniciada_em DOUBLE PRECISION NOT NULL,
          finalizada_em DOUBLE PRECISION,
          status TEXT NOT NULL DEFAULT 'andamento' CHECK(status IN ('andamento','finalizada')),
          esperados INTEGER NOT NULL,
          conferidos INTEGER)""")
        con.execute("ALTER TABLE conferencias ADD COLUMN IF NOT EXISTS usuario TEXT")
        con.execute("""
        CREATE TABLE IF NOT EXISTS conferencia_itens(
          conferencia_id INTEGER NOT NULL REFERENCES conferencias(id) ON DELETE CASCADE,
          epc TEXT NOT NULL,
          tipo TEXT NOT NULL,
          conferido_em DOUBLE PRECISION,
          PRIMARY KEY(conferencia_id, epc))""")

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


webhook_auth = auth(WEBHOOK_USER, WEBHOOK_PASS)
saida_auth = auth(SAIDA_USER, SAIDA_PASS)

# ---- Login com usuários e papéis (leitura < operador < admin) ------------------------------------
# Aceita cookie de sessão (tela /login) ou HTTP Basic (scripts/curl). O par UI_USER/UI_PASS do ambiente
# continua valendo como admin (não depende do banco). Usuários do banco sem senha cadastrada não logam.
_basic_opt = HTTPBasic(auto_error=False)
_falhas: dict[str, list[float]] = {}


def hash_senha(senha: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${salt.hex()}${hashlib.scrypt(senha.encode(), salt=salt, n=2**14, r=8, p=1).hex()}"


def confere_senha(senha: str, guardado: str | None) -> bool:
    try:
        _, salt, h = (guardado or "").split("$")
        novo = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(novo, h)
    except ValueError:
        return False


def autentica(usuario: str, senha: str):
    if secrets.compare_digest(usuario, UI_USER) & secrets.compare_digest(senha, UI_PASS):
        return {"usuario": UI_USER, "nome": UI_USER, "papel": "admin", "env": True}
    with closing(db()) as con:
        u = con.execute("SELECT usuario,nome,papel,senha_hash FROM usuarios WHERE usuario=%s AND ativo=1", (usuario,)).fetchone()
    if u and u["senha_hash"] and confere_senha(senha, u["senha_hash"]):
        return {"usuario": u["usuario"], "nome": u["nome"] or u["usuario"], "papel": u["papel"], "env": False}


def emite_token(u: dict) -> str:
    corpo = base64.urlsafe_b64encode(json.dumps({"u": u["usuario"], "e": u["env"], "x": int(time.time()) + SESSION_HORAS * 3600}).encode()).decode()
    return corpo + "." + hmac.new(SESSION_SECRET, corpo.encode(), hashlib.sha256).hexdigest()


def valida_token(tok: str):
    try:
        corpo, sig = tok.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(SESSION_SECRET, corpo.encode(), hashlib.sha256).hexdigest()):
            return None
        d = json.loads(base64.urlsafe_b64decode(corpo))
        if d["x"] < time.time():
            return None
        if d["e"]:
            return {"usuario": UI_USER, "nome": UI_USER, "papel": "admin", "env": True}
        with closing(db()) as con:  # papel e "ativo" valem na hora, não só no login
            u = con.execute("SELECT usuario,nome,papel FROM usuarios WHERE usuario=%s AND ativo=1", (d["u"],)).fetchone()
        return {"usuario": u["usuario"], "nome": u["nome"] or u["usuario"], "papel": u["papel"], "env": False} if u else None
    except Exception:  # noqa: BLE001
        return None


def usuario_opt(request: Request, c: HTTPBasicCredentials | None = Depends(_basic_opt)):
    tok = request.cookies.get(COOKIE)
    u = valida_token(tok) if tok else None
    if not u and c:
        u = autentica(c.username, c.password)
    return u


def ui_auth(u=Depends(usuario_opt)):
    if not u:
        raise HTTPException(401, "não autenticado")
    return u


def exige(papel: str):
    def dep(u=Depends(ui_auth)):
        if PAPEIS[u["papel"]] < PAPEIS[papel]:
            raise HTTPException(403, f"ação exige papel '{papel}'")
        return u
    return dep


op_auth, admin_auth = exige("operador"), exige("admin")


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


def epc_manual(valor):
    """EPC digitado/importado (API, CSV): já é hex; só normaliza espaços e caixa. Nunca tenta base64."""
    return str(valor).strip().upper() if valor else None


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


state = {"last_push": None, "ultimo_sinal": None, "armado_ate": 0.0, "conf_ate": 0.0, "conf_ativa": False}


def gravando() -> bool:
    """Eventos do leitor só vão pro banco durante uma leitura solicitada (janela armada ou conferência em andamento)."""
    now = time.time()
    if now < state["armado_ate"]:
        return True
    if now > state["conf_ate"]:  # reconsulta a conferência no máximo a cada 5s
        with closing(db()) as con:
            state["conf_ativa"] = bool(con.execute("SELECT 1 FROM conferencias WHERE status='andamento'").fetchone())
        state["conf_ate"] = now + 5
    return state["conf_ativa"]


def push_kuma():
    if not KUMA_PUSH_URL:
        return
    with closing(db()) as con:
        last = con.execute("SELECT MAX(received_at) m FROM events").fetchone()["m"]
    last = max(x for x in (last, state["ultimo_sinal"], 0) if x is not None) or None
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


def agregar_horas(con):
    """Resumo por hora (EPC x hora) recalculando as últimas 2h; sobrevive à purga dos eventos brutos."""
    ult = con.execute("SELECT MAX(hora) h FROM leituras_hora").fetchone()["h"]
    desde = (ult - 7200) if ult is not None else 0
    con.execute("""INSERT INTO leituras_hora(hora,epc,leituras,melhor_rssi)
                   SELECT (FLOOR(received_at/3600)*3600)::bigint, epc, COUNT(*), MAX(rssi)
                   FROM events WHERE epc IS NOT NULL AND received_at>=%s GROUP BY 1,2
                   ON CONFLICT(hora,epc) DO UPDATE SET leituras=excluded.leituras, melhor_rssi=excluded.melhor_rssi""", (desde,))
    con.commit()


def ciclo_sessao(con, now, ocioso, desde):
    """Sessão = rajada de leituras separada por `ocioso` segundos de silêncio; ao encerrar vira um lote 'aguardando'."""
    aberta = con.execute("SELECT * FROM sessoes WHERE status='aberta' ORDER BY id DESC LIMIT 1").fetchone()
    ultimo = con.execute("SELECT MAX(received_at) m FROM events WHERE epc IS NOT NULL").fetchone()["m"]
    if aberta:
        if ultimo is None or now - ultimo >= ocioso:
            fim = max(ultimo or aberta["inicio"], aberta["inicio"])
            epcs = [r["epc"] for r in con.execute(
                "SELECT DISTINCT epc FROM events WHERE epc IS NOT NULL AND received_at>=%s AND received_at<=%s",
                (aberta["inicio"], fim)).fetchall()]
            lote_id = None
            if epcs:
                lote_id = con.execute("INSERT INTO lotes(criado_em,janela_segundos,quantidade,status) VALUES(%s,%s,%s,'aguardando') RETURNING id",
                                      (now, max(1, int(fim - aberta["inicio"])), len(epcs))).fetchone()["id"]
                with con.cursor() as cur:
                    cur.executemany("INSERT INTO lote_itens(lote_id,epc) VALUES(%s,%s)", [(lote_id, e) for e in epcs])
            con.execute("UPDATE sessoes SET status='encerrada', fim=%s, tags=%s, lote_id=%s WHERE id=%s",
                        (fim, len(epcs), lote_id, aberta["id"]))
            con.commit()
        return
    ult_fim = con.execute("SELECT MAX(fim) m FROM sessoes").fetchone()["m"] or desde
    inicio = con.execute("SELECT MIN(received_at) m FROM events WHERE epc IS NOT NULL AND received_at>%s", (ult_fim,)).fetchone()["m"]
    if inicio is not None and ultimo is not None and now - ultimo < ocioso:
        con.execute("INSERT INTO sessoes(inicio,status) VALUES(%s,'aberta')", (inicio,))
        con.commit()


async def loop_sessao():
    desde = time.time()
    while True:
        await asyncio.sleep(5)
        def ciclo():
            with closing(db()) as con:
                ciclo_sessao(con, time.time(), SESSAO_OCIOSO, desde)
        try:
            await asyncio.to_thread(ciclo)
        except Exception as e:  # noqa: BLE001
            print("ciclo de sessão falhou:", e)


async def background():
    n = 0
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(push_kuma)
        def resumo():
            with closing(db()) as con:
                agregar_horas(con)
        try:
            await asyncio.to_thread(resumo)
        except Exception as e:  # noqa: BLE001
            print("resumo por hora falhou:", e)
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
    ts = asyncio.create_task(loop_sessao()) if SESSAO_AUTO else None
    yield
    t.cancel()
    if ts:
        ts.cancel()

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
    state["ultimo_sinal"] = now  # só em memória: prova que o rádio está enviando, sem gastar banco
    if not await asyncio.to_thread(gravando):
        return {"ok": True, "received": len(events), "stored": 0}
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
    return {"ok": True, "received": len(rows), "stored": len(rows)}


@app.post("/api/leitura/armar", dependencies=[Depends(op_auth)])
def armar_leitura(segundos: int = 15):
    segundos = max(1, min(segundos, 600))
    state["armado_ate"] = time.time() + segundos
    return {"ok": True, "segundos": segundos, "armado_ate": state["armado_ate"]}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(ui_auth)])
def summary():
    now = time.time()
    with closing(db()) as con:
        q = lambda sql, *a: list(con.execute(sql, a).fetchone().values())[0]
        last = q("SELECT MAX(received_at) FROM events")
        sinal = max(x for x in (last, state["ultimo_sinal"], 0) if x is not None) or None
        return {
            "now": now, "last_event": sinal, "gravando": now < state["armado_ate"] or state["conf_ativa"],
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


@app.post("/api/lotes/registrar", dependencies=[Depends(op_auth)])
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


@app.post("/api/lotes/{lote_id}/transferir")
def transferir_lote(lote_id: int, payload: TransferenciaIn, user: dict = Depends(op_auth)):
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
            con.execute("INSERT INTO transferencias(lote_id,destino_id,observacao,criado_em,usuario) VALUES(%s,%s,%s,%s,%s)",
                        (lote_id, payload.destino_id, (payload.observacao or "").strip() or None, now, user["usuario"]))
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


@app.post("/api/destinos", dependencies=[Depends(admin_auth)])
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


@app.patch("/api/destinos/{destino_id}", dependencies=[Depends(admin_auth)])
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
                    t.observacao, t.criado_em, t.usuario, l.quantidade
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


@app.post("/api/itens", dependencies=[Depends(op_auth)])
def cadastrar_item(payload: ItemIn):
    epc, tipo = epc_manual(payload.epc) or "", payload.tipo.strip()
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


@app.post("/api/estados", dependencies=[Depends(admin_auth)])
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


@app.patch("/api/itens/{epc}/estado", dependencies=[Depends(op_auth)])
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
    payload.epc = epc_manual(payload.epc) or ""
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


@app.post("/api/antenas", dependencies=[Depends(admin_auth)])
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


@app.post("/api/acomodacoes", dependencies=[Depends(admin_auth)])
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


FILTRO_CAMPOS = ("localizacao", "estado", "tipo", "fabricante", "tamanho", "composicao", "cor")


def filtro_sql(f: dict):
    cond, args = [], []
    for c in FILTRO_CAMPOS:  # nomes de coluna vêm só desta lista fixa
        if f.get(c):
            cond.append(f"v.{c}=%s"); args.append(f[c])
    return ((" WHERE " + " AND ".join(cond)) if cond else ""), args


class ConferenciaIn(BaseModel):
    filtros: dict[str, str] = {}


def _stats(itens, iniciada, finalizada, agora):
    conf = sum(1 for i in itens if i["conferido_em"])
    total = len(itens)
    return {"esperados": total, "conferidos": conf, "nao_conferidos": total - conf,
            "percentual": round(100 * conf / total) if total else 0,
            "duracao_min": round(((finalizada or agora) - iniciada) / 60)}


@app.post("/api/conferencias")
def iniciar_conferencia(payload: ConferenciaIn, user: dict = Depends(op_auth)):
    filtros = {k: v for k, v in payload.filtros.items() if k in FILTRO_CAMPOS and v}
    where, args = filtro_sql(filtros)
    now = time.time()
    with closing(db()) as con:
        if con.execute("SELECT 1 FROM conferencias WHERE status='andamento'").fetchone():
            raise HTTPException(409, "já existe uma conferência em andamento; finalize-a antes")
        itens = con.execute(f"SELECT v.epc, v.tipo FROM ({ITENS_VIEW_SQL}) v{where}", args).fetchall()
        if not itens:
            raise HTTPException(400, "nenhum item cadastrado com esses filtros")
        desc = ", ".join(f'{k} "{v}"' for k, v in filtros.items()) or "sem filtros"
        cid = con.execute("""INSERT INTO conferencias(filtros_desc,iniciada_em,status,esperados,usuario)
                             VALUES(%s,%s,'andamento',%s,%s) RETURNING id""", (desc, now, len(itens), user["usuario"])).fetchone()["id"]
        with con.cursor() as cur:
            cur.executemany("INSERT INTO conferencia_itens(conferencia_id,epc,tipo) VALUES(%s,%s,%s)",
                            [(cid, i["epc"], i["tipo"]) for i in itens])
        con.commit()
    return {"id": cid, "esperados": len(itens), "filtros_desc": desc}


@app.get("/api/conferencias", dependencies=[Depends(ui_auth)])
def listar_conferencias():
    with closing(db()) as con:
        return con.execute("SELECT * FROM conferencias ORDER BY id DESC LIMIT 100").fetchall()


@app.get("/api/conferencias/{cid}", dependencies=[Depends(ui_auth)])
def detalhe_conferencia(cid: int, somente: str | None = None, limit: int = 5000):
    now = time.time()
    with closing(db()) as con:
        c = con.execute("SELECT * FROM conferencias WHERE id=%s", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "conferência não encontrada")
        if c["status"] == "andamento":  # conferido = visto pelo leitor desde o início
            itens = con.execute("""SELECT ci.epc, ci.tipo, e.m AS conferido_em FROM conferencia_itens ci
                LEFT JOIN (SELECT epc, MIN(received_at) m FROM events WHERE received_at>=%s GROUP BY epc) e ON e.epc=ci.epc
                WHERE ci.conferencia_id=%s ORDER BY ci.tipo, ci.epc""", (c["iniciada_em"], cid)).fetchall()
        else:
            itens = con.execute("""SELECT epc, tipo, conferido_em FROM conferencia_itens
                                   WHERE conferencia_id=%s ORDER BY tipo, epc""", (cid,)).fetchall()
    stats = _stats(itens, c["iniciada_em"], c["finalizada_em"], now)
    if somente == "nao_conferidos":
        itens = [i for i in itens if not i["conferido_em"]]
    elif somente == "conferidos":
        itens = [i for i in itens if i["conferido_em"]]
    return {**c, **stats, "itens": itens[:min(limit, 10000)]}


@app.post("/api/conferencias/{cid}/finalizar", dependencies=[Depends(op_auth)])
def finalizar_conferencia(cid: int):
    now = time.time()
    with closing(db()) as con:
        c = con.execute("SELECT * FROM conferencias WHERE id=%s", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "conferência não encontrada")
        if c["status"] == "finalizada":
            raise HTTPException(409, "conferência já finalizada")
        con.execute("""UPDATE conferencia_itens ci SET conferido_em=e.m
                       FROM (SELECT epc, MIN(received_at) m FROM events WHERE received_at>=%s GROUP BY epc) e
                       WHERE ci.conferencia_id=%s AND e.epc=ci.epc""", (c["iniciada_em"], cid))
        conf = con.execute("SELECT COUNT(*) n FROM conferencia_itens WHERE conferencia_id=%s AND conferido_em IS NOT NULL",
                           (cid,)).fetchone()["n"]
        con.execute("UPDATE conferencias SET status='finalizada', finalizada_em=%s, conferidos=%s WHERE id=%s",
                    (now, conf, cid))
        con.commit()
    return {"ok": True, "id": cid, "esperados": c["esperados"], "conferidos": conf}


class LoginIn(BaseModel):
    usuario: str
    senha: str


@app.post("/api/login")
def login(payload: LoginIn, request: Request):
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    agora = time.time()
    _falhas[ip] = [t for t in _falhas.get(ip, []) if agora - t < 300]
    if len(_falhas[ip]) >= 8:
        raise HTTPException(429, "muitas tentativas; aguarde alguns minutos")
    u = autentica(payload.usuario.strip(), payload.senha)
    if not u:
        _falhas[ip].append(agora)
        raise HTTPException(401, "usuário ou senha inválidos")
    resp = JSONResponse({"usuario": u["usuario"], "nome": u["nome"], "papel": u["papel"]})
    resp.set_cookie(COOKIE, emite_token(u), max_age=SESSION_HORAS * 3600, httponly=True, samesite="lax",
                    secure=request.headers.get("x-forwarded-proto") == "https")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(u: dict = Depends(ui_auth)):
    return {"usuario": u["usuario"], "nome": u["nome"], "papel": u["papel"]}



def csv_resp(nome, cabecalho, linhas):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cabecalho); w.writerows(linhas)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


def hora_local(epoch):
    return time.strftime("%d/%m/%Y %H:%M", time.gmtime(epoch + TZ_OFFSET)) if epoch else ""


@app.get("/api/leituras-hora", dependencies=[Depends(ui_auth)])
def leituras_hora(horas: int = 168):
    desde = int(time.time() // 3600 * 3600) - max(1, min(horas, 24 * 90)) * 3600
    with closing(db()) as con:
        return con.execute("""SELECT hora, SUM(leituras)::int leituras, COUNT(*)::int tags FROM leituras_hora
                              WHERE hora>=%s GROUP BY hora ORDER BY hora DESC""", (desde,)).fetchall()


@app.get("/api/export/leituras.csv", dependencies=[Depends(ui_auth)])
def export_leituras(horas: int = 168):
    desde = int(time.time() // 3600 * 3600) - max(1, min(horas, 24 * 365)) * 3600
    with closing(db()) as con:
        rows = con.execute("""SELECT l.hora, l.epc, i.tipo, l.leituras, l.melhor_rssi FROM leituras_hora l
                              LEFT JOIN itens i ON i.epc=l.epc WHERE l.hora>=%s ORDER BY l.hora DESC, l.epc""", (desde,)).fetchall()
    return csv_resp("leituras-por-hora.csv", ["hora_local", "epc", "item", "leituras", "melhor_rssi"],
                    [(hora_local(r["hora"]), r["epc"], r["tipo"] or "", r["leituras"], r["melhor_rssi"] if r["melhor_rssi"] is not None else "") for r in rows])


@app.get("/api/export/inventario.csv", dependencies=[Depends(ui_auth)])
def export_inventario():
    with closing(db()) as con:
        rows = con.execute(f"SELECT * FROM ({ITENS_VIEW_SQL}) v ORDER BY v.tipo, v.epc").fetchall()
    return csv_resp("inventario.csv", ["epc", "item", "fabricante", "tamanho", "composicao", "cor", "localizacao", "estado"],
                    [(r["epc"], r["tipo"], r["fabricante"] or "", r["tamanho"] or "", r["composicao"] or "", r["cor"] or "",
                      r["localizacao"] or "", r["estado"]) for r in rows])


@app.get("/api/export/transferencias.csv", dependencies=[Depends(ui_auth)])
def export_transferencias():
    with closing(db()) as con:
        rows = con.execute("""SELECT t.lote_id, t.criado_em, d.nome destino, d.tipo, t.usuario, l.quantidade, t.observacao
                              FROM transferencias t JOIN destinos d ON d.id=t.destino_id JOIN lotes l ON l.id=t.lote_id
                              ORDER BY t.id DESC""").fetchall()
    return csv_resp("transferencias.csv", ["lote", "data", "destino", "tipo_destino", "usuario", "quantidade", "observacao"],
                    [(r["lote_id"], hora_local(r["criado_em"]), r["destino"], r["tipo"], r["usuario"] or "", r["quantidade"], r["observacao"] or "") for r in rows])


@app.get("/api/sessoes", dependencies=[Depends(ui_auth)])
def listar_sessoes(limit: int = 50):
    with closing(db()) as con:
        return {"auto": SESSAO_AUTO, "ocioso_segundos": SESSAO_OCIOSO,
                "sessoes": con.execute("SELECT * FROM sessoes ORDER BY id DESC LIMIT %s", (min(limit, 500),)).fetchall()}

@app.get("/login")
def pagina_login():
    return FileResponse(STATIC / "login.html")


class UsuarioIn(BaseModel):
    usuario: str
    nome: str | None = None
    papel: str = "operador"
    senha: str | None = None


class UsuarioPatch(BaseModel):
    nome: str | None = None
    papel: str | None = None
    ativo: bool | None = None
    senha: str | None = None


def _valida_usuario(papel, senha):
    if papel is not None and papel not in PAPEIS:
        raise HTTPException(400, "papel deve ser leitura, operador ou admin")
    if senha is not None and len(senha) < 8:
        raise HTTPException(400, "a senha precisa ter ao menos 8 caracteres")


@app.get("/api/usuarios", dependencies=[Depends(admin_auth)])
def listar_usuarios():
    with closing(db()) as con:
        return con.execute("""SELECT id,usuario,nome,papel,ativo,(senha_hash IS NOT NULL) AS tem_senha
                              FROM usuarios ORDER BY usuario""").fetchall()


@app.post("/api/usuarios", dependencies=[Depends(admin_auth)])
def criar_usuario(payload: UsuarioIn):
    nome_login = payload.usuario.strip().lower()
    if not nome_login or " " in nome_login:
        raise HTTPException(400, "usuário obrigatório, sem espaços")
    _valida_usuario(payload.papel, payload.senha)
    with closing(db()) as con:
        try:
            con.execute("INSERT INTO usuarios(usuario,nome,senha_hash,papel,ativo,criado_em) VALUES(%s,%s,%s,%s,1,%s)",
                        (nome_login, payload.nome, hash_senha(payload.senha) if payload.senha else None, payload.papel, time.time()))
            con.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "já existe um usuário com esse login")
    return {"ok": True}


@app.patch("/api/usuarios/{uid}", dependencies=[Depends(admin_auth)])
def atualizar_usuario(uid: int, payload: UsuarioPatch):
    _valida_usuario(payload.papel, payload.senha)
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM usuarios WHERE id=%s", (uid,)).fetchone():
            raise HTTPException(404, "usuário não encontrado")
        if payload.nome is not None:
            con.execute("UPDATE usuarios SET nome=%s WHERE id=%s", (payload.nome, uid))
        if payload.papel is not None:
            con.execute("UPDATE usuarios SET papel=%s WHERE id=%s", (payload.papel, uid))
        if payload.ativo is not None:
            con.execute("UPDATE usuarios SET ativo=%s WHERE id=%s", (1 if payload.ativo else 0, uid))
        if payload.senha is not None:
            con.execute("UPDATE usuarios SET senha_hash=%s WHERE id=%s", (hash_senha(payload.senha), uid))
        con.commit()
    return {"ok": True}

@app.get("/")
def index(u=Depends(usuario_opt)):
    if not u:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC / "index.html")
