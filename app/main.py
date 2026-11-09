"""Receptor de webhook do leitor Impinj (R700 Event Reporting) + painel ao vivo."""
import asyncio, datetime, re, threading, base64, csv, io, binascii, hashlib, hmac, json, os, secrets, time, urllib.parse, urllib.request
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
# Alerta por WhatsApp (Evolution API). Sem WHATSAPP_APIKEY/INSTANCE/TO o alerta fica desligado.
WA_URL = os.environ.get("WHATSAPP_API_URL", "http://evolution-api:8080").rstrip("/")
WA_KEY = os.environ.get("WHATSAPP_APIKEY", "")
WA_INSTANCE = os.environ.get("WHATSAPP_INSTANCE", "")
ALERTA_SEM_SINAL_SEG = int(os.environ.get("ALERTA_SEM_SINAL_SEGUNDOS", "60"))  # rádio lendo estoque envia ~5 eventos/s
ALERTA_FALHAS = int(os.environ.get("ALERTA_FALHAS_INGEST", "5"))  # falhas (4xx/5xx) do /ingest em 10 min
ALERTA_REPETIR_MIN = int(os.environ.get("ALERTA_REPETIR_MINUTOS", "120"))
SAIDA_JANELA_MINUTOS = int(os.environ.get("SAIDA_JANELA_MINUTOS", "60"))
STATIC = Path(__file__).parent / "static"
security = HTTPBasic()
_sess = os.environ.get("SESSION_SECRET", "")
if len(_sess) < 32:  # chave do cookie não pode derivar de senha: exigir segredo próprio (>=32 caracteres)
    raise RuntimeError("defina SESSION_SECRET com pelo menos 32 caracteres (ex.: openssl rand -hex 32)")
SESSION_SECRET = _sess.encode()
SESSION_HORAS = int(os.environ.get("SESSION_HORAS", "12"))
COOKIE = "cabine_sessao"
# Permissões por módulo. Cada usuário tem um perfil (tabela `perfis`) que é um conjunto delas; o perfil `admin` é fixo e tem todas.
PERMISSOES = {
    "dashboard": "Ver o Dashboard",
    "consultar": "Consultar telas (inventário, lotes, transferências, evasão, relatórios, tags, eventos, conferências)",
    "operar_cabine": "Operar a cabine (registrar lotes, transferir, divergência, estado do lote)",
    "excluir_lotes": "Excluir lotes aguardando transferência",
    "catalogo_editar": "Cadastrar e editar itens (catálogo, lote, leitor manual, estado do item)",
    "conferencia": "Iniciar e finalizar conferências de estoque",
    "cadastros": "Manter destinos, estados, antenas e acomodações",
    "exportar": "Exportar CSV",
    "alertas_whatsapp": "Configurar alertas por WhatsApp e destinatários",
    "usuarios": "Gerenciar usuários e perfis",
    "auditoria": "Ver a auditoria",
    "sistema": "Agentes do hotel, comandos ao rádio e download do agente",
}
PERFIS_PADRAO = {  # criados só na primeira vez; depois podem ser editados ou excluídos (menos o admin)
    "operador": ("Opera a cabine e edita o catálogo", ["dashboard", "consultar", "exportar", "operar_cabine", "excluir_lotes", "catalogo_editar", "conferencia"]),
    "leitura": ("Só consulta e exporta", ["dashboard", "consultar", "exportar"]),
    "painel": ("Só o Dashboard", ["dashboard"]),
}

ITENS_VIEW_SQL = """
SELECT i.epc, i.tipo, i.fabricante, i.tamanho, i.composicao, i.cor, i.criado_em, i.atualizado_em,
       i.data_compra, d.nome AS localizacao, e.nome AS estado
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
        con.execute("ALTER TABLE itens ADD COLUMN IF NOT EXISTS data_compra TEXT")  # AAAA-MM-DD
        con.execute("CREATE INDEX IF NOT EXISTS ix_itens_tipo ON itens(tipo)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_lote_itens_epc ON lote_itens(epc)")

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
          papel TEXT NOT NULL DEFAULT 'operador',
          ativo INTEGER NOT NULL DEFAULT 1,
          criado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("ALTER TABLE transferencias ADD COLUMN IF NOT EXISTS usuario TEXT")
        con.execute("""
        CREATE TABLE IF NOT EXISTS leituras_hora(
          hora BIGINT NOT NULL, epc TEXT NOT NULL, leituras INTEGER NOT NULL, melhor_rssi DOUBLE PRECISION,
          PRIMARY KEY(hora, epc))""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS alerta_destinatarios(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          nome TEXT NOT NULL,
          whatsapp TEXT NOT NULL UNIQUE,
          ativo BOOLEAN NOT NULL DEFAULT TRUE,
          recebe_sem_sinal BOOLEAN NOT NULL DEFAULT TRUE,
          recebe_falhas BOOLEAN NOT NULL DEFAULT TRUE,
          criado_em DOUBLE PRECISION NOT NULL)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS alerta_envios(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          quando DOUBLE PRECISION NOT NULL,
          destinatario_id INTEGER REFERENCES alerta_destinatarios(id) ON DELETE SET NULL,
          nome TEXT, whatsapp TEXT, tipo TEXT, status TEXT NOT NULL, detalhe TEXT)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS config(chave TEXT PRIMARY KEY, valor TEXT NOT NULL)""")
        con.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_papel_check")  # perfis agora são dinâmicos (tabela perfis)
        con.execute("""
        CREATE TABLE IF NOT EXISTS perfis(
          nome TEXT PRIMARY KEY, descricao TEXT, permissoes TEXT NOT NULL DEFAULT '[]', criado_em DOUBLE PRECISION NOT NULL)""")
        if not con.execute("SELECT 1 FROM perfis LIMIT 1").fetchone():  # só semeia na primeira vez (senão um perfil excluído voltaria)
            for nome, (desc, perms) in PERFIS_PADRAO.items():
                con.execute("INSERT INTO perfis(nome,descricao,permissoes,criado_em) VALUES(%s,%s,%s,%s)", (nome, desc, json.dumps(perms), time.time()))
        con.execute("""INSERT INTO perfis(nome,descricao,permissoes,criado_em) VALUES('admin','Acesso total (fixo)',%s,%s)
                       ON CONFLICT(nome) DO UPDATE SET permissoes=excluded.permissoes""", (json.dumps(list(PERMISSOES)), time.time()))  # admin sempre com todas, inclusive as novas
        con.execute("""
        CREATE TABLE IF NOT EXISTS sessoes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          inicio DOUBLE PRECISION NOT NULL, fim DOUBLE PRECISION,
          status TEXT NOT NULL DEFAULT 'aberta' CHECK(status IN ('aberta','encerrada')),
          tags INTEGER NOT NULL DEFAULT 0, lote_id INTEGER REFERENCES lotes(id))""")

        con.execute("""
        CREATE TABLE IF NOT EXISTS auditoria_login(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          criado_em DOUBLE PRECISION NOT NULL, usuario TEXT, resultado TEXT NOT NULL,
          ip TEXT, navegador TEXT)""")  # nunca guarda senha
        con.execute("CREATE INDEX IF NOT EXISTS ix_auditoria_login_em ON auditoria_login(criado_em)")
        con.execute("DELETE FROM auditoria_login WHERE criado_em < %s", (time.time() - 365 * 86400,))
        con.execute("""
        CREATE TABLE IF NOT EXISTS auditoria_acoes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          criado_em DOUBLE PRECISION NOT NULL, usuario TEXT, acao TEXT NOT NULL, metodo TEXT NOT NULL,
          caminho TEXT NOT NULL, status INTEGER NOT NULL, ip TEXT)""")  # só quem/quê/onde/resultado: nunca o corpo (senhas)
        con.execute("CREATE INDEX IF NOT EXISTS ix_auditoria_acoes_em ON auditoria_acoes(criado_em)")
        con.execute("DELETE FROM auditoria_acoes WHERE criado_em < %s", (time.time() - 365 * 86400,))
        con.execute("""
        CREATE TABLE IF NOT EXISTS alerta_estado(
          chave TEXT PRIMARY KEY, desde DOUBLE PRECISION NOT NULL)""")  # alertas de antena/agente já enviados (sobrevive a restart/deploy)
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
        for col in ("descricao TEXT", "modo TEXT NOT NULL DEFAULT 'automatico'", "alerta BOOLEAN NOT NULL DEFAULT TRUE",
                    "ip TEXT", "porta INTEGER", "evasao BOOLEAN NOT NULL DEFAULT FALSE"):
            con.execute(f"ALTER TABLE antenas ADD COLUMN IF NOT EXISTS {col}")

        con.execute("""
        CREATE TABLE IF NOT EXISTS agentes(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          nome TEXT NOT NULL UNIQUE,
          token_hash TEXT NOT NULL UNIQUE,
          criado_em DOUBLE PRECISION NOT NULL,
          visto_em DOUBLE PRECISION, versao TEXT, host TEXT)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS antena_status(
          antena_id INTEGER PRIMARY KEY REFERENCES antenas(id) ON DELETE CASCADE,
          online BOOLEAN NOT NULL, latencia_ms INTEGER, detalhe TEXT, extra TEXT,
          verificado_em DOUBLE PRECISION NOT NULL, agente_id INTEGER REFERENCES agentes(id) ON DELETE SET NULL)""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS comandos(
          id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          antena_id INTEGER NOT NULL REFERENCES antenas(id) ON DELETE CASCADE,
          metodo TEXT NOT NULL, caminho TEXT NOT NULL, corpo TEXT,
          status TEXT NOT NULL DEFAULT 'pendente',  -- pendente | enviado | ok | erro | expirado
          http_status INTEGER, resposta TEXT,
          usuario TEXT, criado_em DOUBLE PRECISION NOT NULL, enviado_em DOUBLE PRECISION, concluido_em DOUBLE PRECISION)""")

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
    def dep(request: Request, c: HTTPBasicCredentials = Depends(security)):
        ip = ip_cliente(request)
        if bloqueado(ip):
            raise HTTPException(429, "muitas tentativas; aguarde alguns minutos")
        ok = secrets.compare_digest(c.username, user) & secrets.compare_digest(c.password, pw)
        if not ok:
            registra_falha(ip)
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return dep


webhook_auth = auth(WEBHOOK_USER, WEBHOOK_PASS)
saida_auth = auth(SAIDA_USER, SAIDA_PASS)

# ---- Login com usuários e papéis (leitura < operador < admin) ------------------------------------
# Aceita cookie de sessão (tela /login) ou HTTP Basic (scripts/curl). O par UI_USER/UI_PASS do ambiente
# continua valendo como admin (não depende do banco). Usuários do banco sem senha cadastrada não logam.
_basic_opt = HTTPBasic(auto_error=False)
_falhas: dict[str, list[float]] = {}
LIMITE_FALHAS, JANELA_FALHAS = 8, 300


def ip_cliente(request: Request) -> str:
    """IP real: o Traefik ACRESCENTA o IP de quem conectou ao fim do X-Forwarded-For; os valores anteriores
    vêm do cliente e podem ser forjados, então vale o último."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def bloqueado(ip: str) -> bool:
    agora = time.time()
    for k in [k for k, v in _falhas.items() if not v or agora - v[-1] > JANELA_FALHAS]:  # expurgo: não cresce sem limite
        del _falhas[k]
    _falhas[ip] = [t for t in _falhas.get(ip, []) if agora - t < JANELA_FALHAS]
    return len(_falhas[ip]) >= LIMITE_FALHAS


def registra_falha(ip: str):
    _falhas.setdefault(ip, []).append(time.time())


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


def _perms(texto):
    try:
        return frozenset(p for p in json.loads(texto or "[]") if p in PERMISSOES)
    except ValueError:
        return frozenset()


def autentica(usuario: str, senha: str):
    if secrets.compare_digest(usuario, UI_USER) & secrets.compare_digest(senha, UI_PASS):
        return {"usuario": UI_USER, "nome": UI_USER, "papel": "admin", "env": True, "perms": frozenset(PERMISSOES)}
    with closing(db()) as con:
        u = con.execute("""SELECT u.usuario,u.nome,u.papel,u.senha_hash,p.permissoes FROM usuarios u
                           LEFT JOIN perfis p ON p.nome=u.papel WHERE u.usuario=%s AND u.ativo=1""", (usuario,)).fetchone()
    if u and u["senha_hash"] and confere_senha(senha, u["senha_hash"]):
        return {"usuario": u["usuario"], "nome": u["nome"] or u["usuario"], "papel": u["papel"], "env": False, "perms": _perms(u["permissoes"])}


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
            return {"usuario": UI_USER, "nome": UI_USER, "papel": "admin", "env": True, "perms": frozenset(PERMISSOES)}
        with closing(db()) as con:  # perfil, permissões e "ativo" valem na hora, não só no login
            u = con.execute("""SELECT u.usuario,u.nome,u.papel,p.permissoes FROM usuarios u
                               LEFT JOIN perfis p ON p.nome=u.papel WHERE u.usuario=%s AND u.ativo=1""", (d["u"],)).fetchone()
        return {"usuario": u["usuario"], "nome": u["nome"] or u["usuario"], "papel": u["papel"], "env": False, "perms": _perms(u["permissoes"])} if u else None
    except Exception:  # noqa: BLE001
        return None


def usuario_opt(request: Request, c: HTTPBasicCredentials | None = Depends(_basic_opt)):
    tok = request.cookies.get(COOKIE)
    u = valida_token(tok) if tok else None
    if not u and c:
        ip = ip_cliente(request)
        if bloqueado(ip):
            raise HTTPException(429, "muitas tentativas; aguarde alguns minutos")
        u = autentica(c.username, c.password)
        if not u:
            registra_falha(ip)
    return u


def painel_auth(u=Depends(usuario_opt)):
    """Qualquer usuário autenticado. Use só em /api/summary e /api/me; o resto exige permissão (`exige`)."""
    if not u:
        raise HTTPException(401, "não autenticado")
    return u


def exige(perm: str):
    assert perm in PERMISSOES, perm

    def dep(u=Depends(painel_auth)):
        if perm not in u["perms"]:
            raise HTTPException(403, f"seu perfil não tem a permissão '{perm}'")
        return u
    return dep


P_DASH, P_CONSULTAR, P_OPERAR, P_EXCLUIR = exige("dashboard"), exige("consultar"), exige("operar_cabine"), exige("excluir_lotes")
P_CATALOGO, P_CONF, P_CADASTROS, P_EXPORTAR = exige("catalogo_editar"), exige("conferencia"), exige("cadastros"), exige("exportar")
P_WHATSAPP, P_USUARIOS, P_AUDITORIA, P_SISTEMA = exige("alertas_whatsapp"), exige("usuarios"), exige("auditoria"), exige("sistema")


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


def epc_valido(epc):
    """EPC válido para cadastro manual: hexadecimal, tamanho par, 4 a 64 caracteres."""
    return bool(epc) and re.fullmatch(r"[0-9A-F]{4,64}", epc) is not None and len(epc) % 2 == 0


def exige_epc_valido(epc):
    if not epc_valido(epc):
        raise HTTPException(400, f"EPC inválido ({epc[:40]}): use hexadecimal, tamanho par, de 4 a 64 caracteres")


def data_iso(v):
    """Aceita AAAA-MM-DD ou DD/MM/AAAA; devolve AAAA-MM-DD, None se vazio, 400 se inválida."""
    v = (v or "").strip()
    if not v:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v) or re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", v)
    try:
        if not m:
            raise ValueError
        a, b, c = m.groups()
        d = datetime.date(int(a), int(b), int(c)) if "-" in v else datetime.date(int(c), int(b), int(a))
        return d.isoformat()
    except ValueError:
        raise HTTPException(400, f"data de compra inválida: {v[:20]}")


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


INICIO = time.time()
state = {"alertas": {}, "falhas_ingest": [], "last_push": None, "ultimo_sinal": None, "armado_ate": 0.0, "conf_ate": 0.0, "conf_ativa": False}


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


CFG_PADRAO = {"wa_url": WA_URL, "wa_instance": WA_INSTANCE, "wa_apikey": WA_KEY,
              "ativo": "1", "sem_sinal_seg": str(ALERTA_SEM_SINAL_SEG), "falhas": str(ALERTA_FALHAS),
              "repetir_min": str(ALERTA_REPETIR_MIN)}


def cfg() -> dict:
    """Configuração do alerta: o que foi salvo na tela (tabela config) vale mais que o ambiente."""
    c = dict(CFG_PADRAO)
    try:
        with closing(db()) as con:
            c.update({r["chave"]: r["valor"] for r in con.execute("SELECT chave,valor FROM config").fetchall()})
    except Exception as e:  # noqa: BLE001
        print("config indisponível:", e)
    return c


def enviar_um(c: dict, numero: str, texto: str) -> str | None:
    """Envia a um número pela Evolution. None = enviou; senão a mensagem de erro."""
    if not (c["wa_apikey"] and c["wa_instance"]):
        return "WhatsApp não configurado (instância e API key)"
    req = urllib.request.Request(
        f"{c['wa_url'].rstrip('/')}/message/sendText/{c['wa_instance']}", method="POST",
        data=json.dumps({"number": numero if numero.endswith("@g.us") else "".join(ch for ch in numero if ch.isdigit()), "text": texto}).encode(),
        headers={"apikey": c["wa_apikey"], "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return None
    except urllib.error.HTTPError as e:
        return f"Evolution respondeu HTTP {e.code} (confira instância, chave e número)"
    except Exception as e:  # noqa: BLE001
        return f"falha ao falar com a Evolution: {e}"


def notificar(tipo: str, texto: str, c: dict | None = None, destinatario_id: int | None = None) -> list[dict]:
    """Envia a cada destinatário ativo que recebe esse tipo ('sem_sinal'|'falhas'|'teste') e registra em alerta_envios."""
    c = c or cfg()
    resultados = []
    with closing(db()) as con:
        if destinatario_id:
            dests = con.execute("SELECT * FROM alerta_destinatarios WHERE id=%s", (destinatario_id,)).fetchall()
        else:
            filtro = {"sem_sinal": " AND recebe_sem_sinal", "falhas": " AND recebe_falhas"}.get(tipo, "")
            dests = con.execute("SELECT * FROM alerta_destinatarios WHERE ativo" + filtro + " ORDER BY id").fetchall()
        for d in dests:
            erro = enviar_um(c, d["whatsapp"], texto)
            con.execute("INSERT INTO alerta_envios(quando,destinatario_id,nome,whatsapp,tipo,status,detalhe) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (time.time(), d["id"], d["nome"], d["whatsapp"], tipo, "erro" if erro else "enviado", erro))
            resultados.append({"id": d["id"], "nome": d["nome"], "erro": erro})
        con.commit()
        con.execute("DELETE FROM alerta_envios WHERE quando<%s", (time.time() - 30 * 86400,))
        con.commit()
    return resultados


def avaliar_alertas(agora: float, ultimo_sinal, falhas: int, c: dict | None = None, inicio: float | None = None) -> list[tuple[str, str]]:
    """Máquina de estados: devolve as mensagens a enviar (alerta na transição, lembrete a cada ALERTA_REPETIR_MIN, retomada)."""
    c = c or cfg()
    sem_seg, max_falhas, repetir = int(c["sem_sinal_seg"]), int(c["falhas"]), int(c["repetir_min"])
    msgs = []
    # Depois de um restart o último evento no banco é antigo (só gravamos sob demanda): conta a partir da subida do app.
    base = max(ultimo_sinal or 0, INICIO if inicio is None else inicio)
    sem_sinal = agora - base
    condicoes = {
        "sem_sinal": (sem_sinal >= sem_seg,
                      f"⚠️ Cabine RFID: o rádio está sem enviar sinal ao webhook há {sem_sinal / 60:.1f} min ({sem_sinal:.0f}s)."),
        "falhas": (falhas >= max_falhas,
                   f"⚠️ Cabine RFID: {falhas} requisições do rádio ao /ingest falharam nos últimos 10 min (credencial ou JSON inválido?)."),
    }
    for nome, (ativo, texto) in condicoes.items():
        est = state["alertas"].get(nome)
        if ativo and (est is None or agora - est >= repetir * 60):
            state["alertas"][nome] = agora
            msgs.append((nome, texto))
        elif not ativo and est is not None:
            del state["alertas"][nome]
            msgs.append((nome, "✅ Cabine RFID: " + ("o rádio voltou a enviar sinal." if nome == "sem_sinal" else "as falhas no /ingest cessaram.")))
    return msgs


def _webhook_status(extra) -> dict | None:
    try:
        w = (json.loads(extra) if isinstance(extra, str) else extra or {}).get("eventWebhookStatus")
        return w if isinstance(w, dict) else None
    except ValueError:
        return None


def avaliar_agentes(agora: float, antenas: list[dict], agentes: list[dict], c: dict) -> list[tuple[str, str]]:
    """Alertas do agente do hotel: antena offline (com alerta ligado) e agente sem contato. Um aviso por transição."""
    msgs = []
    repetir = int(c["repetir_min"])
    condicoes = {}
    for a in antenas:  # cobertura: nenhum agente a vê (ex.: PC do hotel desligado) -> quando algum voltar a vê-la online, avisa
        if not (a["alerta"] or a.get("evasao")) or a["verificado_em"] is None:
            continue
        chave = f"cobertura:{a['id']}"
        if agora - a["verificado_em"] >= AGENTE_SEM_CONTATO_SEG:
            state["alertas"].setdefault(chave, agora)  # em silêncio: o aviso de "agente sem contato" já cobre a perda
        elif chave in state["alertas"] and a["online"]:
            del state["alertas"][chave]
            if f"antena:{a['id']}" not in state["alertas"]:  # se havia alerta de offline, a retomada dele já avisa
                pri = "🚨 EVASÃO — " if a.get("evasao") else ""
                msgs.append(("antena_online", f"✅ Cabine RFID: {pri}antena {a['nome']} ({a.get('ip') or 'sem IP'}) está ONLINE"
                                              + (f" (agente {a['agente']})" if a.get("agente") else "") + " — voltou a ser monitorada."))
    for a in antenas:
        pri = "🚨 EVASÃO — " if a.get("evasao") else ""  # antena de saída: sem ela, a evasão passa sem ser detectada
        # sem dado fresco (nenhum agente reportando) não se avalia: manter o estado, nem alertar nem dar "voltou" falso
        if (a["alerta"] or a.get("evasao")) and a["verificado_em"] is not None and agora - a["verificado_em"] < AGENTE_SEM_CONTATO_SEG:
            off = not a["online"]
            condicoes[f"antena:{a['id']}"] = (off, f"⚠️ Cabine RFID: {pri}antena {a['nome']} ({a.get('ip') or 'sem IP'}) está OFFLINE — {a.get('detalhe') or 'sem resposta'}.",
                                              f"✅ Cabine RFID: {pri}antena {a['nome']} voltou a responder.")
    for a in antenas:  # webhook de eventos do R700 (informado pelo próprio leitor em /api/v1/status)
        w = _webhook_status(a.get("extra"))
        if a["alerta"] and w is not None and a["verificado_em"] is not None and agora - a["verificado_em"] < AGENTE_SEM_CONTATO_SEG:
            ruim = w.get("status") != "active" or int(w.get("httpStatusCode") or 0) >= 400
            condicoes[f"webhook:{a['id']}"] = (ruim, f"⚠️ Cabine RFID: o rádio {a['nome']} está com FALHA ao enviar eventos pelo webhook (status {w.get('status')}, HTTP {w.get('httpStatusCode')}).",
                                               f"✅ Cabine RFID: o webhook do rádio {a['nome']} voltou a entregar eventos.")
    for ag in agentes:
        if ag["visto_em"] is not None:
            sem = agora - ag["visto_em"] >= AGENTE_SEM_CONTATO_SEG
            condicoes[f"agente:{ag['id']}"] = (sem, f"⚠️ Cabine RFID: o agente '{ag['nome']}' do hotel está sem contato há {int(agora - ag['visto_em'])}s (PC desligado ou sem internet?).",
                                               f"✅ Cabine RFID: o agente '{ag['nome']}' voltou a se comunicar.")
    for chave, (ativo, texto, ok_texto) in condicoes.items():
        est = state["alertas"].get(chave)
        if ativo and (est is None or agora - est >= repetir * 60):
            state["alertas"][chave] = agora
            msgs.append((chave.split(":")[0] + "_offline" if not chave.startswith("webhook") else "webhook_falha", texto))
        elif not ativo and est is not None:
            del state["alertas"][chave]
            msgs.append((chave.split(":")[0] + "_offline" if not chave.startswith("webhook") else "webhook_falha", ok_texto))
    return msgs


PERSISTE_ALERTA = ("antena:", "webhook:", "agente:", "cobertura:")  # derivados do banco; sem_sinal/falhas dependem de memória e ficam fora


def carregar_estado_alertas():
    """Restaura os alertas de antena/agente já enviados, para um restart/deploy não reenviar o mesmo aviso."""
    with closing(db()) as con:
        rows = con.execute("SELECT chave, desde FROM alerta_estado").fetchall()
    for r in rows:
        state["alertas"][r["chave"]] = r["desde"]
    state["_salvo"] = {r["chave"]: r["desde"] for r in rows}


def salvar_estado_alertas(antenas: list[dict], agentes: list[dict]):
    """Grava no banco o estado dos alertas de antena/agente (só se mudou) e descarta os de itens que não existem mais."""
    validas = {f"{p}{a['id']}" for a in antenas for p in ("antena:", "webhook:", "cobertura:")} | {f"agente:{g['id']}" for g in agentes}
    for chave in [k for k in state["alertas"] if k.startswith(PERSISTE_ALERTA) and k not in validas]:
        del state["alertas"][chave]
    atual = {k: v for k, v in state["alertas"].items() if k.startswith(PERSISTE_ALERTA)}
    if atual == state.get("_salvo"):
        return
    with closing(db()) as con:
        con.execute("DELETE FROM alerta_estado")
        for k, v in atual.items():
            con.execute("INSERT INTO alerta_estado(chave, desde) VALUES(%s,%s)", (k, v))
        con.commit()
    state["_salvo"] = dict(atual)


def checar_alertas():
    agora = time.time()
    state["falhas_ingest"] = [t for t in state["falhas_ingest"] if agora - t < 600]
    with closing(db()) as con:
        last = con.execute("SELECT MAX(received_at) m FROM events").fetchone()["m"]
    ultimo = max(x for x in (last, state["ultimo_sinal"], 0) if x is not None) or None
    c = cfg()
    if c["ativo"] != "1":
        return
    for tipo, texto in avaliar_alertas(agora, ultimo, len(state["falhas_ingest"]), c):
        notificar(tipo, texto, c)
    with closing(db()) as con:
        antenas = con.execute("""SELECT a.id,a.nome,a.ip,a.alerta,a.evasao,s.online,s.detalhe,s.verificado_em,s.extra,g.nome agente FROM antenas a
                                 JOIN antena_status s ON s.antena_id=a.id LEFT JOIN agentes g ON g.id=s.agente_id""").fetchall()
        agentes = con.execute("SELECT id,nome,visto_em FROM agentes").fetchall()
    for tipo, texto in avaliar_agentes(agora, antenas, agentes, c):
        notificar(tipo, texto, c)
    salvar_estado_alertas(antenas, agentes)


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


async def loop_alertas():
    """Checa o sinal do rádio a cada 10s (o laço de 60s do resumo/Kuma era lento demais para alertar)."""
    while True:
        await asyncio.sleep(10)
        try:
            await asyncio.to_thread(checar_alertas)
        except Exception as e:  # noqa: BLE001
            print("checagem de alertas falhou:", e)


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
    await asyncio.to_thread(carregar_estado_alertas)
    t = asyncio.create_task(background())
    ts = asyncio.create_task(loop_sessao()) if SESSAO_AUTO else None
    ta = asyncio.create_task(loop_alertas())
    yield
    t.cancel()
    ta.cancel()
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


@app.post("/api/leitura/armar", dependencies=[Depends(P_OPERAR)])
def armar_leitura(segundos: int = 15):
    segundos = max(1, min(segundos, 600))
    state["armado_ate"] = time.time() + segundos
    return {"ok": True, "segundos": segundos, "armado_ate": state["armado_ate"]}


@app.middleware("http")
async def conta_falhas_ingest(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/ingest" and resp.status_code >= 400:
        state["falhas_ingest"].append(time.time())
    return resp


class AlertaCfg(BaseModel):
    wa_url: str = ""
    wa_instance: str = ""
    wa_apikey: str = ""  # vazio = manter a atual
    ativo: bool = True
    sem_sinal_seg: int = 60
    falhas: int = 5
    repetir_min: int = 120


@app.get("/api/config/alertas", dependencies=[Depends(P_WHATSAPP)])
def ler_config_alertas():
    c = cfg()
    return {"wa_url": c["wa_url"], "wa_instance": c["wa_instance"], "wa_apikey_definida": bool(c["wa_apikey"]),
            "ativo": c["ativo"] == "1", "sem_sinal_seg": int(c["sem_sinal_seg"]),
            "falhas": int(c["falhas"]), "repetir_min": int(c["repetir_min"]),
            "alertas_abertos": sorted(state["alertas"])}


@app.put("/api/config/alertas", dependencies=[Depends(P_WHATSAPP)])
def salvar_config_alertas(b: AlertaCfg):
    if not (10 <= b.sem_sinal_seg <= 86400 and 1 <= b.falhas <= 1000 and 5 <= b.repetir_min <= 10080):
        raise HTTPException(400, "limites inválidos (sem sinal 10–86400 s, falhas 1–1000, repetir 5–10080 min)")
    valores = {"wa_url": b.wa_url.strip() or CFG_PADRAO["wa_url"], "wa_instance": b.wa_instance.strip(),
               "ativo": "1" if b.ativo else "0",
               "sem_sinal_seg": str(b.sem_sinal_seg), "falhas": str(b.falhas), "repetir_min": str(b.repetir_min)}
    if b.wa_apikey.strip():
        valores["wa_apikey"] = b.wa_apikey.strip()
    with closing(db()) as con:
        for k, v in valores.items():
            con.execute("INSERT INTO config(chave,valor) VALUES(%s,%s) ON CONFLICT(chave) DO UPDATE SET valor=EXCLUDED.valor", (k, v))
        con.commit()
    return ler_config_alertas()


class DestinatarioIn(BaseModel):
    nome: str
    whatsapp: str
    ativo: bool = True
    recebe_sem_sinal: bool = True
    recebe_falhas: bool = True


def _valida_dest(b: DestinatarioIn) -> dict:
    nome, bruto = b.nome.strip(), b.whatsapp.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    if bruto.endswith("@g.us"):  # grupo: ID completo (ex.: 120363123456789012@g.us)
        num = bruto
        if not re.fullmatch(r"[0-9]{10,25}(-[0-9]+)?@g\.us", num):
            raise HTTPException(400, "ID de grupo inválido (ex.: 120363123456789012@g.us)")
    else:
        num = "".join(ch for ch in bruto if ch.isdigit())
        if len(num) in (10, 11):  # DDD + número sem DDI: assume Brasil
            num = "55" + num
        if not 12 <= len(num) <= 15:
            raise HTTPException(400, "número inválido: use DDI+DDD+número (ex.: 5511999999999) ou o ID do grupo (...@g.us)")
    return {"nome": nome, "whatsapp": num, "ativo": b.ativo, "recebe_sem_sinal": b.recebe_sem_sinal, "recebe_falhas": b.recebe_falhas}


@app.get("/api/alertas/destinatarios", dependencies=[Depends(P_WHATSAPP)])
def listar_destinatarios():
    with closing(db()) as con:
        return con.execute("SELECT * FROM alerta_destinatarios ORDER BY nome").fetchall()


@app.post("/api/alertas/destinatarios", dependencies=[Depends(P_WHATSAPP)])
def criar_destinatario(b: DestinatarioIn):
    v = _valida_dest(b)
    with closing(db()) as con:
        if con.execute("SELECT 1 FROM alerta_destinatarios WHERE whatsapp=%s", (v["whatsapp"],)).fetchone():
            raise HTTPException(409, "esse número já está cadastrado")
        con.execute("""INSERT INTO alerta_destinatarios(nome,whatsapp,ativo,recebe_sem_sinal,recebe_falhas,criado_em)
                       VALUES(%(nome)s,%(whatsapp)s,%(ativo)s,%(recebe_sem_sinal)s,%(recebe_falhas)s,%(t)s)""", {**v, "t": time.time()})
        con.commit()
    return {"ok": True}


@app.put("/api/alertas/destinatarios/{dest_id}", dependencies=[Depends(P_WHATSAPP)])
def editar_destinatario(dest_id: int, b: DestinatarioIn):
    v = _valida_dest(b)
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM alerta_destinatarios WHERE id=%s", (dest_id,)).fetchone():
            raise HTTPException(404, "destinatário não encontrado")
        if con.execute("SELECT 1 FROM alerta_destinatarios WHERE whatsapp=%s AND id<>%s", (v["whatsapp"], dest_id)).fetchone():
            raise HTTPException(409, "esse número já está cadastrado")
        con.execute("""UPDATE alerta_destinatarios SET nome=%(nome)s, whatsapp=%(whatsapp)s, ativo=%(ativo)s,
                       recebe_sem_sinal=%(recebe_sem_sinal)s, recebe_falhas=%(recebe_falhas)s WHERE id=%(id)s""", {**v, "id": dest_id})
        con.commit()
    return {"ok": True}


@app.delete("/api/alertas/destinatarios/{dest_id}", dependencies=[Depends(P_WHATSAPP)])
def excluir_destinatario(dest_id: int):
    with closing(db()) as con:
        con.execute("DELETE FROM alerta_destinatarios WHERE id=%s", (dest_id,))
        con.commit()
    return {"ok": True}


@app.post("/api/alertas/destinatarios/{dest_id}/teste", dependencies=[Depends(P_WHATSAPP)])
def testar_destinatario(dest_id: int):
    r = notificar("teste", "✅ Cabine RFID: mensagem de teste do alerta por WhatsApp.", destinatario_id=dest_id)
    if not r:
        raise HTTPException(404, "destinatário não encontrado")
    if r[0]["erro"]:
        raise HTTPException(400, r[0]["erro"])
    return {"ok": True}


GRUPOS = {"status": "vazio", "grupos": [], "atualizado_em": None, "erro": None}


def _buscar_grupos():
    c = cfg()
    req = urllib.request.Request(f"{c['wa_url'].rstrip('/')}/group/fetchAllGroups/{c['wa_instance']}?getParticipants=false",
                                 headers={"apikey": c["wa_apikey"]})
    try:
        dados = json.loads(urllib.request.urlopen(req, timeout=300).read())  # instâncias com centenas de grupos levam minutos
        GRUPOS["grupos"] = sorted(({"id": g.get("id"), "nome": g.get("subject") or g.get("id")} for g in dados if g.get("id")),
                                  key=lambda g: g["nome"].lower())
        GRUPOS.update(status="pronto", atualizado_em=time.time(), erro=None)
    except urllib.error.HTTPError as e:
        GRUPOS.update(status="erro", erro=f"Evolution respondeu HTTP {e.code} (confira instância e chave)")
    except Exception as e:  # noqa: BLE001
        GRUPOS.update(status="erro", erro=f"falha ao falar com a Evolution: {e}")


@app.post("/api/alertas/grupos/atualizar", dependencies=[Depends(P_WHATSAPP)])
def atualizar_grupos():
    """Busca os grupos da instância Evolution em segundo plano (pode levar minutos); consulte GET /api/alertas/grupos."""
    c = cfg()
    if not (c["wa_apikey"] and c["wa_instance"]):
        raise HTTPException(400, "configure instância e API key e salve antes de buscar grupos")
    if GRUPOS["status"] != "buscando":
        GRUPOS.update(status="buscando", erro=None, iniciado_em=time.time())
        threading.Thread(target=_buscar_grupos, daemon=True).start()
    return {"status": GRUPOS["status"]}


@app.get("/api/alertas/grupos", dependencies=[Depends(P_WHATSAPP)])
def listar_grupos_whatsapp():
    return {k: GRUPOS.get(k) for k in ("status", "grupos", "atualizado_em", "erro", "iniciado_em")}


@app.get("/api/alertas/envios", dependencies=[Depends(P_WHATSAPP)])
def historico_envios(limit: int = 50):
    with closing(db()) as con:
        return con.execute("SELECT * FROM alerta_envios ORDER BY id DESC LIMIT %s", (max(1, min(limit, 500)),)).fetchall()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(painel_auth)])
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
            "stale_minutes": STALE_MINUTES, "sem_sinal_seg": int(cfg()["sem_sinal_seg"]),
        }


def _extra_publico(extra) -> dict:
    """Só campos seguros/úteis do que o agente leu do rádio (firmware, uptime, preset, webhook)."""
    try:
        e = json.loads(extra) if isinstance(extra, str) else (extra or {})
    except ValueError:
        return {}
    out = {k: e[k] for k in ("firmware", "uptime", "serial") if isinstance(e.get(k), (str, int, float))}
    ap = e.get("activePreset")
    if isinstance(ap, dict) and isinstance(ap.get("id"), str):
        out["preset"] = ap["id"]
    w = _webhook_status(e)
    if w:
        out["webhook"] = {"status": w.get("status"), "http": w.get("httpStatusCode")}
    return out


@app.get("/api/dashboard", dependencies=[Depends(P_DASH)])
def dashboard():
    """Visão geral para o Painel: monitoramento das antenas/agentes, alertas e números da operação."""
    now = time.time()
    dia = now - 86400
    hoje = now - (now + TZ_OFFSET) % 86400  # 00:00 local de hoje
    with closing(db()) as con:
        q1 = lambda sql, *a: list(con.execute(sql, a).fetchone().values())[0]
        antenas = con.execute("""
            SELECT a.id, a.nome, a.descricao, a.modelo, a.ip, a.evasao, a.alerta, d.nome ambiente,
                   s.online, s.latencia_ms, s.detalhe, s.extra, s.verificado_em, g.nome agente
            FROM antenas a LEFT JOIN destinos d ON d.id=a.destino_id
            LEFT JOIN antena_status s ON s.antena_id=a.id LEFT JOIN agentes g ON g.id=s.agente_id ORDER BY a.evasao, a.nome""").fetchall()
        agentes = con.execute("SELECT nome, visto_em, versao, host FROM agentes ORDER BY nome").fetchall()
        envios = con.execute("SELECT quando, tipo, status FROM alerta_envios ORDER BY quando DESC LIMIT 400").fetchall()
        estados = con.execute("""SELECT e.nome estado, e.comportamento, COUNT(i.epc) qtd FROM estados_inventario e
                                 LEFT JOIN itens i ON i.estado_id=e.id GROUP BY e.id ORDER BY qtd DESC, e.nome""").fetchall()
        aguard = con.execute("SELECT COUNT(*) n, COALESCE(SUM(quantidade),0) pecas FROM lotes WHERE status='aguardando'").fetchone()
        transf = con.execute("""SELECT COUNT(*) n, COALESCE(SUM(l.quantidade),0) pecas FROM transferencias t
                                JOIN lotes l ON l.id=t.lote_id WHERE t.criado_em>=%s""", (hoje,)).fetchone()
        ultimas = con.execute("""SELECT t.criado_em, d.nome destino, l.quantidade FROM transferencias t
                                 JOIN destinos d ON d.id=t.destino_id JOIN lotes l ON l.id=t.lote_id
                                 ORDER BY t.criado_em DESC LIMIT 5""").fetchall()
        saida = con.execute("""SELECT COUNT(*) total, COUNT(*) FILTER (WHERE documentado=0) nao FROM eventos_saida
                               WHERE detectado_em>%s""", (dia,)).fetchone()
        conf = con.execute("""SELECT id, status, esperados, conferidos, iniciada_em, finalizada_em FROM conferencias
                              ORDER BY iniciada_em DESC LIMIT 1""").fetchone()
        total_itens = q1("SELECT COUNT(*) FROM itens")
    lista = []
    for a in antenas:
        visto = a["verificado_em"]
        fresco = visto is not None and now - visto < AGENTE_SEM_CONTATO_SEG
        lista.append({**{k: a[k] for k in ("id", "nome", "descricao", "modelo", "ip", "evasao", "alerta", "ambiente", "latencia_ms", "detalhe", "verificado_em", "agente")},
                      "online": bool(a["online"]) if fresco else None,  # None = sem informação recente do agente
                      "extra": _extra_publico(a["extra"])})
    ag = [{**x, "online": x["visto_em"] is not None and now - x["visto_em"] < AGENTE_SEM_CONTATO_SEG} for x in agentes]
    env24 = [e for e in envios if e["quando"] > dia]
    problemas = [a["nome"] for a in lista if a["online"] is False] + [x["nome"] for x in ag if not x["online"]]
    return {
        "now": now,
        "antenas": lista, "agentes": ag,
        "resumo": {"antenas_total": len(lista), "antenas_online": sum(1 for a in lista if a["online"]),
                   "antenas_offline": sum(1 for a in lista if a["online"] is False),
                   "antenas_sem_info": sum(1 for a in lista if a["online"] is None), "problemas": problemas},
        "alertas": {"ativos": len(state["alertas"]), "enviados_24h": sum(1 for e in env24 if e["status"] == "enviado"),
                    "falhas_24h": sum(1 for e in env24 if e["status"] != "enviado"),
                    "ultimos": [{"quando": e["quando"], "tipo": e["tipo"], "status": e["status"]} for e in envios[:6]]},
        "cabine": {"lotes_aguardando": aguard["n"], "pecas_aguardando": aguard["pecas"],
                   "transferencias_hoje": transf["n"], "pecas_hoje": transf["pecas"], "ultimas": ultimas},
        "evasao": {"saidas_24h": saida["total"], "nao_documentadas_24h": saida["nao"]},
        "inventario": {"total": total_itens, "por_estado": estados},
        "conferencia": conf,
    }


@app.get("/api/tags", dependencies=[Depends(P_CONSULTAR)])
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


@app.get("/api/events", dependencies=[Depends(P_CONSULTAR)])
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
        f"""SELECT i.epc, i.tipo, i.fabricante, i.tamanho, i.composicao, i.cor, e.nome estado,
                   (SELECT d.nome FROM lote_itens li JOIN transferencias t ON t.lote_id=li.lote_id
                      JOIN destinos d ON d.id=t.destino_id WHERE li.epc=i.epc ORDER BY t.criado_em DESC LIMIT 1) localizacao
            FROM itens i JOIN estados_inventario e ON e.id=i.estado_id WHERE i.epc IN ({placeholders})""", epcs).fetchall()}
    identificados = [cadastrados[e] for e in epcs if e in cadastrados]
    novos = [e for e in epcs if e not in cadastrados]
    return identificados, novos


@app.post("/api/lotes/registrar", dependencies=[Depends(P_OPERAR)])
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


@app.get("/api/lotes", dependencies=[Depends(P_CONSULTAR)])
def listar_lotes(status: str | None = None, limit: int = 100):
    sql = "SELECT * FROM lotes"
    args: list = []
    if status:
        sql += " WHERE status=%s"; args.append(status)
    sql += " ORDER BY id DESC LIMIT %s"; args.append(min(limit, 500))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


@app.get("/api/lotes/{lote_id}", dependencies=[Depends(P_CONSULTAR)])
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


class EstadoLoteIn(BaseModel):
    estado_id: int


class DivergenciaIn(BaseModel):
    esperado: int | None = None
    observacao: str | None = None


@app.delete("/api/lotes/{lote_id}")
def reiniciar_leitura(lote_id: int, _: dict = Depends(P_EXCLUIR)):
    """'Reiniciar leitura': descarta um lote que ainda não foi transferido."""
    with closing(db()) as con:
        lote = con.execute("SELECT status FROM lotes WHERE id=%s", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
        if lote["status"] != "aguardando":
            raise HTTPException(409, "lote já transferido não pode ser descartado")
        con.execute("DELETE FROM lote_itens WHERE lote_id=%s", (lote_id,))
        con.execute("DELETE FROM lotes WHERE id=%s", (lote_id,))
        con.commit()
    return {"ok": True}


@app.post("/api/lotes/{lote_id}/estado")
def definir_estado_lote(lote_id: int, payload: EstadoLoteIn, _: dict = Depends(P_OPERAR)):
    """'Definir estado' em lote: aplica o estado a todos os itens cadastrados do lote."""
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM lotes WHERE id=%s", (lote_id,)).fetchone():
            raise HTTPException(404, "lote não encontrado")
        est = con.execute("SELECT nome FROM estados_inventario WHERE id=%s", (payload.estado_id,)).fetchone()
        if not est:
            raise HTTPException(400, "estado inválido")
        n = con.execute("""UPDATE itens SET estado_id=%s, atualizado_em=%s
                           WHERE epc IN (SELECT epc FROM lote_itens WHERE lote_id=%s)""",
                        (payload.estado_id, time.time(), lote_id)).rowcount
        con.commit()
    return {"ok": True, "itens": n, "estado": est["nome"]}


@app.post("/api/lotes/{lote_id}/divergencia")
def avisar_divergencia(lote_id: int, payload: DivergenciaIn, user: dict = Depends(P_OPERAR)):
    """'Clique aqui para nos avisar': manda a divergência de quantidade pelo WhatsApp aos destinatários ativos."""
    with closing(db()) as con:
        lote = con.execute("SELECT quantidade FROM lotes WHERE id=%s", (lote_id,)).fetchone()
        if not lote:
            raise HTTPException(404, "lote não encontrado")
    esp = f", esperado {payload.esperado}" if payload.esperado is not None else ""
    obs = f" Obs.: {payload.observacao.strip()}" if (payload.observacao or "").strip() else ""
    r = notificar("divergencia", f"⚠️ Cabine RFID: divergência na leitura do lote #{lote_id} — detectado {lote['quantidade']}{esp}. Informado por {user['usuario']}.{obs}")
    if not r:
        raise HTTPException(400, "nenhum destinatário ativo cadastrado em Alertas WhatsApp")
    return {"ok": True, "enviados": sum(1 for x in r if not x["erro"]), "falhas": sum(1 for x in r if x["erro"])}


@app.get("/api/transferencias/{transf_id}/comprovante", dependencies=[Depends(P_CONSULTAR)])
def comprovante_transferencia(transf_id: int):
    with closing(db()) as con:
        t = con.execute("""SELECT t.id, t.lote_id, t.criado_em, t.usuario, t.observacao, d.nome destino
                           FROM transferencias t JOIN destinos d ON d.id=t.destino_id WHERE t.id=%s""", (transf_id,)).fetchone()
        if not t:
            raise HTTPException(404, "transferência não encontrada")
        itens = con.execute("""SELECT COALESCE(i.tipo,'Sem cadastro') tipo, COUNT(*) qtd
                               FROM lote_itens li LEFT JOIN itens i ON i.epc=li.epc
                               WHERE li.lote_id=%s GROUP BY 1 ORDER BY 1""", (t["lote_id"],)).fetchall()
    return {**t, "itens": itens, "total": sum(x["qtd"] for x in itens)}


@app.post("/api/lotes/{lote_id}/transferir")
def transferir_lote(lote_id: int, payload: TransferenciaIn, user: dict = Depends(P_OPERAR)):
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
            transf_id = con.execute("INSERT INTO transferencias(lote_id,destino_id,observacao,criado_em,usuario) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                        (lote_id, payload.destino_id, (payload.observacao or "").strip() or None, now, user["usuario"])).fetchone()["id"]
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "lote já foi transferido")
        con.execute("UPDATE lotes SET status='transferido' WHERE id=%s", (lote_id,))
        con.commit()
    return {"ok": True, "lote_id": lote_id, "transferencia_id": transf_id, "destino": destino["nome"], "criado_em": now}


@app.get("/api/destinos", dependencies=[Depends(P_CONSULTAR)])
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


@app.post("/api/destinos", dependencies=[Depends(P_CADASTROS)])
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


@app.patch("/api/destinos/{destino_id}", dependencies=[Depends(P_CADASTROS)])
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


@app.get("/api/transferencias", dependencies=[Depends(P_CONSULTAR)])
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
    data_compra: str | None = None


@app.post("/api/itens", dependencies=[Depends(P_CATALOGO)])
def cadastrar_item(payload: ItemIn):
    epc, tipo = epc_manual(payload.epc) or "", payload.tipo.strip()
    if not epc or not tipo:
        raise HTTPException(400, "epc e tipo são obrigatórios")
    exige_epc_valido(epc)
    data_compra = data_iso(payload.data_compra)
    now = time.time()
    with closing(db()) as con:
        estado_padrao = con.execute("SELECT id FROM estados_inventario WHERE padrao=1 LIMIT 1").fetchone()["id"]
        con.execute("""INSERT INTO itens(epc,tipo,fabricante,tamanho,composicao,cor,data_compra,estado_id,criado_em,atualizado_em)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(epc) DO UPDATE SET tipo=excluded.tipo, fabricante=excluded.fabricante,
                          tamanho=excluded.tamanho, composicao=excluded.composicao, cor=excluded.cor,
                          data_compra=excluded.data_compra, atualizado_em=excluded.atualizado_em""",
                    (epc, tipo, payload.fabricante, payload.tamanho, payload.composicao, payload.cor, data_compra,
                     estado_padrao, now, now))
        con.commit()
        row = con.execute(f"SELECT * FROM ({ITENS_VIEW_SQL}) v WHERE v.epc=%s", (epc,)).fetchone()
    return row


class LoteEdicaoIn(BaseModel):
    epcs: list[str]
    tipo: str | None = None
    fabricante: str | None = None
    tamanho: str | None = None
    composicao: str | None = None
    cor: str | None = None
    data_compra: str | None = None
    estado_id: int | None = None


@app.post("/api/itens/editar-lote", dependencies=[Depends(P_CATALOGO)])
def editar_itens_lote(payload: LoteEdicaoIn):
    """Edita só os campos enviados (os ausentes não mudam; texto vazio limpa o campo, exceto `tipo`)."""
    epcs = list(dict.fromkeys(epc_manual(e) for e in payload.epcs if e and e.strip()))
    if not epcs or len(epcs) > 2000:
        raise HTTPException(400, "informe de 1 a 2000 EPCs")
    sets, args = [], []
    for col in ("tipo", "fabricante", "tamanho", "composicao", "cor"):
        if col in payload.model_fields_set:
            v = (getattr(payload, col) or "").strip()
            if col == "tipo" and not v:
                raise HTTPException(400, "tipo não pode ficar vazio")
            sets.append(f"{col}=%s"); args.append(v or None)
    if "data_compra" in payload.model_fields_set:
        sets.append("data_compra=%s"); args.append(data_iso(payload.data_compra))
    if payload.estado_id is not None:
        sets.append("estado_id=%s"); args.append(payload.estado_id)
    if not sets:
        raise HTTPException(400, "nenhum campo para alterar")
    with closing(db()) as con:
        if payload.estado_id is not None and not con.execute("SELECT 1 FROM estados_inventario WHERE id=%s", (payload.estado_id,)).fetchone():
            raise HTTPException(400, "estado inválido")
        sets.append("atualizado_em=%s"); args.append(time.time())
        cur = con.execute(f"UPDATE itens SET {', '.join(sets)} WHERE epc = ANY(%s)", (*args, epcs))
        alterados = cur.rowcount
        con.commit()
    return {"alterados": alterados, "nao_encontrados": len(epcs) - alterados}


class ConsultaIn(BaseModel):
    epcs: list[str]


def _normaliza_lista(epcs):
    lista = list(dict.fromkeys(epc_manual(e) for e in epcs if e and e.strip()))
    if not lista or len(lista) > 2000:
        raise HTTPException(400, "informe de 1 a 2000 EPCs")
    return lista


@app.post("/api/itens/consulta", dependencies=[Depends(P_CONSULTAR)])
def consultar_itens(payload: ConsultaIn):
    """Leitor manual: diz quais EPCs colados/digitados já estão cadastrados e quais são novos ou inválidos."""
    lista = _normaliza_lista(payload.epcs)
    invalidos = [e for e in lista if not epc_valido(e)]
    validos = [e for e in lista if epc_valido(e)]
    with closing(db()) as con:
        identificados, novos = classificar_epcs(con, validos)
    return {"identificados": identificados, "novos": novos, "invalidos": invalidos}


class CadastroLoteIn(BaseModel):
    epcs: list[str]
    tipo: str
    fabricante: str | None = None
    tamanho: str | None = None
    composicao: str | None = None
    cor: str | None = None
    data_compra: str | None = None


@app.post("/api/itens/cadastrar-lote", dependencies=[Depends(P_CATALOGO)])
def cadastrar_itens_lote(payload: CadastroLoteIn):
    """Cadastra vários EPCs com os mesmos atributos. EPC já cadastrado não é alterado; inválido é devolvido."""
    tipo = payload.tipo.strip()
    if not tipo:
        raise HTTPException(400, "tipo é obrigatório")
    lista = _normaliza_lista(payload.epcs)
    invalidos = [e for e in lista if not epc_valido(e)]
    validos = [e for e in lista if epc_valido(e)]
    data_compra = data_iso(payload.data_compra)
    now = time.time()
    with closing(db()) as con:
        padrao = con.execute("SELECT id FROM estados_inventario WHERE padrao=1 LIMIT 1").fetchone()["id"]
        antes = con.execute("SELECT COUNT(*) n FROM itens WHERE epc = ANY(%s)", (validos,)).fetchone()["n"]
        with con.cursor() as cur:
            cur.executemany(
                """INSERT INTO itens(epc,tipo,fabricante,tamanho,composicao,cor,data_compra,estado_id,criado_em,atualizado_em)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(epc) DO NOTHING""",
                [(e, tipo, payload.fabricante, payload.tamanho, payload.composicao, payload.cor, data_compra, padrao, now, now)
                 for e in validos])
        con.commit()
    return {"cadastrados": len(validos) - antes, "ja_existiam": antes, "invalidos": invalidos}


@app.get("/api/inventario/revisao", dependencies=[Depends(P_CONSULTAR)])
def revisao_inventario(dias: int = 30, limit: int = 500):
    """Qualidade do cadastro: EPCs fora do padrão e tags lidas pelas antenas que não estão no inventário."""
    dias = max(1, min(dias, 365))
    limit = max(1, min(limit, 5000))
    desde = time.time() - dias * 86400
    with closing(db()) as con:
        invalidos = con.execute(
            """SELECT epc, tipo, length(epc) tamanho FROM itens
               WHERE epc !~ '^[0-9A-F]+$' OR length(epc) <> 24 ORDER BY epc LIMIT %s""", (limit,)).fetchall()
        total_invalidos = con.execute(
            "SELECT COUNT(*) n FROM itens WHERE epc !~ '^[0-9A-F]+$' OR length(epc) <> 24").fetchone()["n"]
        sem_cadastro = con.execute(
            """SELECT ev.epc, COUNT(*) leituras, MAX(ev.received_at) ultima
               FROM events ev LEFT JOIN itens i ON i.epc=ev.epc
               WHERE i.epc IS NULL AND ev.epc IS NOT NULL AND ev.received_at>=%s
               GROUP BY ev.epc ORDER BY leituras DESC LIMIT %s""", (desde, limit)).fetchall()
    return {"dias": dias, "epcs_fora_do_padrao": invalidos, "total_fora_do_padrao": total_invalidos,
            "lidas_sem_cadastro": sem_cadastro}


@app.get("/api/itens", dependencies=[Depends(P_CONSULTAR)])
def listar_itens(q: str | None = None, limit: int = 200):
    sql = f"SELECT * FROM ({ITENS_VIEW_SQL}) v"
    args: list = []
    if q:
        sql += " WHERE v.epc ILIKE %s OR v.tipo ILIKE %s"
        like = f"%{q}%"; args += [like, like]
    sql += " ORDER BY v.atualizado_em DESC LIMIT %s"; args.append(min(limit, 1000))
    with closing(db()) as con:
        return con.execute(sql, args).fetchall()


@app.get("/api/itens/opcoes", dependencies=[Depends(P_CONSULTAR)])
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


@app.get("/api/estados", dependencies=[Depends(P_CONSULTAR)])
def listar_estados():
    with closing(db()) as con:
        return con.execute("SELECT * FROM estados_inventario ORDER BY padrao DESC, nome").fetchall()


@app.post("/api/estados", dependencies=[Depends(P_CADASTROS)])
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


@app.patch("/api/itens/{epc}/estado", dependencies=[Depends(P_CATALOGO)])
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


@app.get("/api/inventario", dependencies=[Depends(P_CONSULTAR)])
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
    exige_epc_valido(payload.epc)
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


@app.get("/api/saida", dependencies=[Depends(P_CONSULTAR)])
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
    descricao: str | None = None
    modelo: str | None = None
    destino_id: int | None = None  # "Localização" no eTrace
    modo: str = "automatico"  # automatico | manual
    alerta: bool = True
    ip: str | None = None
    porta: int | None = None
    evasao: bool = False  # antena de saída (detecção de evasão): monitoramento e alertas prioritários


def _valida_antena(con, p: AntenaIn) -> dict:
    nome = p.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    if p.modo not in ("automatico", "manual"):
        raise HTTPException(400, "modo deve ser automatico ou manual")
    if p.porta is not None and not 1 <= p.porta <= 65535:
        raise HTTPException(400, "porta inválida (1–65535)")
    if p.destino_id and not con.execute("SELECT 1 FROM destinos WHERE id=%s", (p.destino_id,)).fetchone():
        raise HTTPException(400, "localização inválida")
    return {"nome": nome, "descricao": (p.descricao or "").strip() or None, "modelo": (p.modelo or "").strip() or None,
            "destino_id": p.destino_id, "modo": p.modo, "alerta": p.alerta, "ip": (p.ip or "").strip() or None,
            "porta": p.porta, "evasao": p.evasao}


@app.get("/api/antenas", dependencies=[Depends(P_CONSULTAR)])
def listar_antenas():
    with closing(db()) as con:
        return con.execute("""SELECT a.id, a.nome, a.descricao, a.modelo, a.destino_id, d.nome destino_nome,
                                     a.modo, a.alerta, a.ip, a.porta, a.evasao,
                                     s.online, s.latencia_ms, s.detalhe, s.verificado_em, s.extra
                              FROM antenas a LEFT JOIN destinos d ON d.id=a.destino_id
                              LEFT JOIN antena_status s ON s.antena_id=a.id ORDER BY a.nome""").fetchall()


@app.post("/api/antenas", dependencies=[Depends(P_CADASTROS)])
def salvar_antena(payload: AntenaIn):
    with closing(db()) as con:
        v = _valida_antena(con, payload)
        if con.execute("SELECT 1 FROM antenas WHERE nome=%s", (v["nome"],)).fetchone():
            raise HTTPException(409, "já existe antena com esse nome (use Editar)")
        con.execute("""INSERT INTO antenas(nome,descricao,modelo,destino_id,modo,alerta,ip,porta,evasao,criado_em)
                       VALUES(%(nome)s,%(descricao)s,%(modelo)s,%(destino_id)s,%(modo)s,%(alerta)s,%(ip)s,%(porta)s,%(evasao)s,%(t)s)""",
                    {**v, "t": time.time()})
        con.commit()
    return {"ok": True}


@app.put("/api/antenas/{antena_id}", dependencies=[Depends(P_CADASTROS)])
def editar_antena(antena_id: int, payload: AntenaIn):
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM antenas WHERE id=%s", (antena_id,)).fetchone():
            raise HTTPException(404, "antena não encontrada")
        v = _valida_antena(con, payload)
        if con.execute("SELECT 1 FROM antenas WHERE nome=%s AND id<>%s", (v["nome"], antena_id)).fetchone():
            raise HTTPException(409, "já existe outra antena com esse nome")
        con.execute("""UPDATE antenas SET nome=%(nome)s, descricao=%(descricao)s, modelo=%(modelo)s, destino_id=%(destino_id)s,
                       modo=%(modo)s, alerta=%(alerta)s, ip=%(ip)s, porta=%(porta)s, evasao=%(evasao)s WHERE id=%(id)s""", {**v, "id": antena_id})
        con.commit()
    return {"ok": True}


AGENTE_INTERVALO_SEG = int(os.environ.get("AGENTE_INTERVALO_SEGUNDOS", "15"))
AGENTE_SEM_CONTATO_SEG = int(os.environ.get("AGENTE_SEM_CONTATO_SEGUNDOS", "120"))
CMD_CAMINHO = re.compile(r"^/api/v1/[A-Za-z0-9_\-/.?=&]*$")  # só a API REST do leitor; nada fora de /api/v1/


def _hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def agente_auth(request: Request) -> dict:
    token = request.headers.get("x-agent-token", "")
    if not token:
        raise HTTPException(401, "token do agente ausente")
    with closing(db()) as con:
        ag = con.execute("SELECT * FROM agentes WHERE token_hash=%s", (_hash_token(token),)).fetchone()
        if not ag:
            raise HTTPException(401, "token do agente inválido")
        con.execute("UPDATE agentes SET visto_em=%s WHERE id=%s", (time.time(), ag["id"]))
        con.commit()
    return ag


AGENTE_DIST = Path(os.environ.get("AGENTE_DIST", "/srv/agente"))
AGENTE_ARQUIVOS = {"cabine-agent.exe": "application/octet-stream", "instalar.cmd": "text/plain", "cabine-agent.exe.sha256": "text/plain"}


@app.get("/api/agente/download/{nome}", dependencies=[Depends(P_SISTEMA)])
def baixar_agente(nome: str):
    """Download do instalador do agente (só admin logado): .exe, instalar.cmd e o hash SHA-256."""
    if nome not in AGENTE_ARQUIVOS:
        raise HTTPException(404, "arquivo não encontrado")
    f = AGENTE_DIST / nome
    if not f.is_file():
        raise HTTPException(404, "o instalador ainda não foi publicado neste servidor (rode agent/build.sh e faça o deploy)")
    return FileResponse(f, media_type=AGENTE_ARQUIVOS[nome], filename=nome)


class AgenteIn(BaseModel):
    nome: str


@app.post("/api/agentes")
def criar_agente(b: AgenteIn, _: dict = Depends(P_SISTEMA)):
    nome = b.nome.strip()
    if not nome:
        raise HTTPException(400, "nome é obrigatório")
    token = secrets.token_urlsafe(32)
    with closing(db()) as con:
        try:
            ag = con.execute("INSERT INTO agentes(nome,token_hash,criado_em) VALUES(%s,%s,%s) RETURNING id",
                             (nome, _hash_token(token), time.time())).fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "já existe agente com esse nome")
        con.commit()
    return {"id": ag["id"], "nome": nome, "token": token, "aviso": "guarde o token agora: ele não é mostrado de novo"}


@app.get("/api/agentes", dependencies=[Depends(P_SISTEMA)])
def listar_agentes():
    with closing(db()) as con:
        return con.execute("SELECT id,nome,criado_em,visto_em,versao,host FROM agentes ORDER BY nome").fetchall()


@app.delete("/api/agentes/{agente_id}", dependencies=[Depends(P_SISTEMA)])
def excluir_agente(agente_id: int):
    with closing(db()) as con:
        con.execute("DELETE FROM agentes WHERE id=%s", (agente_id,))
        con.commit()
    return {"ok": True}


@app.get("/api/agente/config")
def agente_config(ag: dict = Depends(agente_auth)):
    """Lista de antenas a monitorar (as que têm IP) + intervalo."""
    with closing(db()) as con:
        antenas = con.execute("""SELECT id,nome,modelo,ip,porta,modo,alerta,evasao FROM antenas
                                 WHERE ip IS NOT NULL AND ip<>'' ORDER BY nome""").fetchall()
    return {"agente": ag["nome"], "intervalo_seg": AGENTE_INTERVALO_SEG, "antenas": antenas}


class StatusAntenaIn(BaseModel):
    id: int
    online: bool
    latencia_ms: int | None = None
    detalhe: str | None = None
    extra: dict | None = None


class StatusIn(BaseModel):
    versao: str | None = None
    host: str | None = None
    antenas: list[StatusAntenaIn] = []


@app.post("/api/agente/status")
def agente_status(b: StatusIn, ag: dict = Depends(agente_auth)):
    """Recebe o status das antenas e devolve os comandos pendentes (marcados como enviados)."""
    now = time.time()
    with closing(db()) as con:
        con.execute("UPDATE agentes SET versao=%s, host=%s WHERE id=%s", (b.versao, b.host, ag["id"]))
        for a in b.antenas:
            con.execute("""INSERT INTO antena_status(antena_id,online,latencia_ms,detalhe,extra,verificado_em,agente_id)
                           SELECT id,%s,%s,%s,%s,%s,%s FROM antenas WHERE id=%s
                           ON CONFLICT(antena_id) DO UPDATE SET online=EXCLUDED.online, latencia_ms=EXCLUDED.latencia_ms,
                             detalhe=EXCLUDED.detalhe, extra=EXCLUDED.extra, verificado_em=EXCLUDED.verificado_em, agente_id=EXCLUDED.agente_id
                           -- vários agentes na mesma antena: "online" de um agente em dia vale mais que "offline" de outro
                           WHERE EXCLUDED.online OR NOT antena_status.online OR antena_status.agente_id IS NULL
                              OR antena_status.agente_id = EXCLUDED.agente_id OR antena_status.verificado_em < %s""",
                        (a.online, a.latencia_ms, (a.detalhe or "")[:300], json.dumps(a.extra) if a.extra else None, now, ag["id"], a.id,
                         now - AGENTE_SEM_CONTATO_SEG))
        con.execute("UPDATE comandos SET status='expirado', concluido_em=%s WHERE status IN ('pendente','enviado') AND criado_em<%s",
                    (now, now - 120))
        cmds = con.execute("""UPDATE comandos SET status='enviado', enviado_em=%s WHERE status='pendente'
                              RETURNING id, antena_id, metodo, caminho, corpo""", (now,)).fetchall()
        con.commit()
    return {"ok": True, "comandos": cmds}


class ResultadoIn(BaseModel):
    ok: bool
    http_status: int | None = None
    resposta: str | None = None


@app.post("/api/agente/comandos/{cmd_id}/resultado")
def agente_resultado(cmd_id: int, b: ResultadoIn, _: dict = Depends(agente_auth)):
    with closing(db()) as con:
        con.execute("UPDATE comandos SET status=%s, http_status=%s, resposta=%s, concluido_em=%s WHERE id=%s",
                    ("ok" if b.ok else "erro", b.http_status, (b.resposta or "")[:20000], time.time(), cmd_id))
        con.commit()
    return {"ok": True}


class ComandoIn(BaseModel):
    metodo: str = "GET"
    caminho: str
    corpo: str | None = None


@app.post("/api/antenas/{antena_id}/comandos")
def criar_comando(antena_id: int, b: ComandoIn, user: dict = Depends(P_SISTEMA)):
    metodo = b.metodo.upper()
    if metodo not in ("GET", "PUT", "POST"):
        raise HTTPException(400, "método deve ser GET, PUT ou POST")
    if not CMD_CAMINHO.match(b.caminho) or ".." in b.caminho:
        raise HTTPException(400, "caminho inválido: use a API do leitor, começando por /api/v1/")
    if b.corpo:
        try:
            json.loads(b.corpo)
        except ValueError:
            raise HTTPException(400, "o corpo precisa ser JSON válido")
    with closing(db()) as con:
        if not con.execute("SELECT 1 FROM antenas WHERE id=%s", (antena_id,)).fetchone():
            raise HTTPException(404, "antena não encontrada")
        cid = con.execute("INSERT INTO comandos(antena_id,metodo,caminho,corpo,usuario,criado_em) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                          (antena_id, metodo, b.caminho, b.corpo or None, user["usuario"], time.time())).fetchone()["id"]
        con.commit()
    return {"id": cid}


@app.get("/api/comandos/{cmd_id}", dependencies=[Depends(P_SISTEMA)])
def ler_comando(cmd_id: int):
    with closing(db()) as con:
        c = con.execute("SELECT * FROM comandos WHERE id=%s", (cmd_id,)).fetchone()
    if not c:
        raise HTTPException(404, "comando não encontrado")
    return c


class AcomodacaoItemIn(BaseModel):
    tipo: str
    qtd: float
    fluxo: float = 1


class AcomodacaoIn(BaseModel):
    nome: str
    uhs: int = 0
    itens: list[AcomodacaoItemIn] = []


@app.get("/api/acomodacoes", dependencies=[Depends(P_CONSULTAR)])
def listar_acomodacoes():
    with closing(db()) as con:
        acs = con.execute("SELECT id,nome,uhs FROM acomodacoes ORDER BY nome").fetchall()
        itens = con.execute("SELECT acomodacao_id,tipo,qtd,fluxo FROM acomodacao_itens ORDER BY tipo").fetchall()
    for a in acs:
        a["itens"] = [{"tipo": i["tipo"], "qtd": i["qtd"], "fluxo": i["fluxo"]} for i in itens if i["acomodacao_id"] == a["id"]]
    return acs


@app.post("/api/acomodacoes", dependencies=[Depends(P_CADASTROS)])
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


@app.get("/api/enxoval/esperado", dependencies=[Depends(P_CONSULTAR)])
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
def iniciar_conferencia(payload: ConferenciaIn, user: dict = Depends(P_CONF)):
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


@app.get("/api/conferencias", dependencies=[Depends(P_CONSULTAR)])
def listar_conferencias():
    with closing(db()) as con:
        return con.execute("SELECT * FROM conferencias ORDER BY id DESC LIMIT 100").fetchall()


@app.get("/api/conferencias/{cid}", dependencies=[Depends(P_CONSULTAR)])
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


@app.get("/api/conferencias/{cid}/export.csv", dependencies=[Depends(P_EXPORTAR)])
def export_conferencia(cid: int):
    d = detalhe_conferencia(cid, None, 10000)
    return csv_resp(f"conferencia-{cid}.csv", ["epc", "item", "situacao", "conferido_em"],
                    [(i["epc"], i["tipo"], "conferido" if i["conferido_em"] else "nao_conferido",
                      hora_local(i["conferido_em"]) if i["conferido_em"] else "") for i in d["itens"]])


@app.post("/api/conferencias/{cid}/finalizar", dependencies=[Depends(P_CONF)])
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


def audita_login(request: Request, usuario: str, resultado: str):
    """Registra tentativa de login (ok/falha/bloqueado). Nunca derruba o login se o registro falhar."""
    try:
        with closing(db()) as con:
            con.execute("INSERT INTO auditoria_login(criado_em,usuario,resultado,ip,navegador) VALUES(%s,%s,%s,%s,%s)",
                        (time.time(), usuario[:80], resultado, ip_cliente(request),
                         request.headers.get("user-agent", "")[:200]))
            con.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[auditoria] falha ao registrar login: {e}", flush=True)


@app.post("/api/login")
def login(payload: LoginIn, request: Request):
    ip = ip_cliente(request)
    if bloqueado(ip):
        audita_login(request, payload.usuario.strip(), "bloqueado")
        raise HTTPException(429, "muitas tentativas; aguarde alguns minutos")
    u = autentica(payload.usuario.strip(), payload.senha)
    if not u:
        registra_falha(ip)
        audita_login(request, payload.usuario.strip(), "falha")
        raise HTTPException(401, "usuário ou senha inválidos")
    audita_login(request, u["usuario"], "ok")
    resp = JSONResponse({"usuario": u["usuario"], "nome": u["nome"], "papel": u["papel"], "permissoes": sorted(u["perms"])})
    resp.set_cookie(COOKIE, emite_token(u), max_age=SESSION_HORAS * 3600, httponly=True, samesite="lax",
                    secure=request.headers.get("x-forwarded-proto") == "https")
    return resp


ROTULOS_ACAO = [  # (método, regex do caminho, descrição) — o que não casar aparece como "MÉTODO caminho"
    ("DELETE", r"/api/lotes/\d+", "Excluiu lote"), ("POST", r"/api/lotes/registrar", "Registrou lote"),
    ("POST", r"/api/lotes/\d+/transferir", "Transferiu lote"), ("POST", r"/api/lotes/\d+/estado", "Mudou estado do lote"),
    ("POST", r"/api/lotes/\d+/divergencia", "Avisou divergência"), ("POST", r"/api/leitura/armar", "Armou a leitura"),
    ("POST", r"/api/itens", "Cadastrou/atualizou item"), ("POST", r"/api/itens/editar-lote", "Editou itens em lote"),
    ("POST", r"/api/itens/cadastrar-lote", "Cadastrou itens em lote"), ("PATCH", r"/api/itens/[^/]+/estado", "Mudou estado do item"),
    ("POST", r"/api/conferencias", "Iniciou conferência"), ("POST", r"/api/conferencias/\d+/finalizar", "Finalizou conferência"),
    ("POST", r"/api/destinos", "Criou destino"), ("PATCH", r"/api/destinos/\d+", "Alterou destino"),
    ("POST", r"/api/estados", "Criou estado"), ("POST", r"/api/antenas", "Criou antena"), ("PUT", r"/api/antenas/\d+", "Alterou antena"),
    ("POST", r"/api/antenas/\d+/comandos", "Enviou comando ao rádio"), ("POST", r"/api/acomodacoes", "Salvou acomodação"),
    ("PUT", r"/api/config/alertas", "Alterou config de alertas"), ("POST", r"/api/alertas/destinatarios", "Criou destinatário de alerta"),
    ("PUT", r"/api/alertas/destinatarios/\d+", "Alterou destinatário de alerta"), ("DELETE", r"/api/alertas/destinatarios/\d+", "Excluiu destinatário de alerta"),
    ("POST", r"/api/alertas/destinatarios/\d+/teste", "Testou destinatário de alerta"),
    ("POST", r"/api/agentes", "Criou agente"), ("DELETE", r"/api/agentes/\d+", "Excluiu agente"),
    ("POST", r"/api/usuarios", "Criou usuário"), ("PATCH", r"/api/usuarios/\d+", "Alterou usuário (perfil/senha/status)"),
    ("POST", r"/api/perfis", "Criou perfil"), ("PUT", r"/api/perfis/[^/]+", "Alterou perfil"), ("DELETE", r"/api/perfis/[^/]+", "Excluiu perfil"),
]
_SEM_AUDITORIA = ("/api/login", "/api/logout", "/api/agente/", "/api/saida")  # login tem tabela própria; o resto é tráfego de máquina


def _rotulo_acao(metodo, caminho):
    for m, rx, texto in ROTULOS_ACAO:
        if m == metodo and re.fullmatch(rx, caminho):
            return texto
    return f"{metodo} {caminho}"


def _quem(request: Request):
    tok = request.cookies.get(COOKIE)
    u = valida_token(tok) if tok else None
    if u:
        return u["usuario"]
    cred = request.headers.get("authorization", "")
    if cred.lower().startswith("basic "):
        try:
            return base64.b64decode(cred[6:]).decode().split(":", 1)[0][:80]
        except Exception:  # noqa: BLE001
            pass
    return None


def _grava_acao(usuario, metodo, caminho, status, ip):
    try:
        with closing(db()) as con:
            con.execute("INSERT INTO auditoria_acoes(criado_em,usuario,acao,metodo,caminho,status,ip) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (time.time(), usuario, _rotulo_acao(metodo, caminho), metodo, caminho[:200], status, ip))
            con.commit()
    except Exception as e:  # noqa: BLE001
        print(f"[auditoria] falha ao registrar ação: {e}", flush=True)


@app.middleware("http")
async def audita_acoes(request: Request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and p.startswith("/api/") and not p.startswith(_SEM_AUDITORIA) \
            and resp.status_code not in (401, 404, 405, 422, 429):  # 403 (negado) conta: tentativa sem permissão
        usuario = _quem(request)
        if usuario:
            await asyncio.to_thread(_grava_acao, usuario, request.method, p, resp.status_code, ip_cliente(request))
    return resp


@app.get("/api/auditoria/acoes", dependencies=[Depends(P_AUDITORIA)])
def auditoria_acoes(usuario: str | None = None, resultado: str | None = None, dias: int = 30, limit: int = 300):
    where, args = ["criado_em >= %s"], [time.time() - max(1, min(dias, 365)) * 86400]
    if usuario:
        where.append("usuario ILIKE %s"); args.append(f"%{usuario.strip()}%")
    if resultado == "ok":
        where.append("status < 400")
    elif resultado == "negado":
        where.append("status = 403")
    elif resultado == "erro":
        where.append("status >= 400 AND status <> 403")
    w = " AND ".join(where)
    with closing(db()) as con:
        rows = con.execute(f"SELECT id,criado_em,usuario,acao,caminho,status,ip FROM auditoria_acoes WHERE {w} ORDER BY criado_em DESC LIMIT %s",
                           (*args, max(1, min(limit, 2000)))).fetchall()
        n = con.execute(f"SELECT COUNT(*) n, COUNT(*) FILTER (WHERE status=403) neg FROM auditoria_acoes WHERE {w}", args).fetchone()
    return {"total": n["n"], "negadas": n["neg"], "acoes": rows}


@app.get("/api/auditoria/acoes.csv", dependencies=[Depends(P_AUDITORIA)])
def auditoria_acoes_csv(dias: int = 30):
    with closing(db()) as con:
        rows = con.execute("SELECT criado_em,usuario,acao,caminho,status,ip FROM auditoria_acoes WHERE criado_em >= %s ORDER BY criado_em DESC LIMIT 20000",
                           (time.time() - max(1, min(dias, 365)) * 86400,)).fetchall()
    return csv_resp("auditoria-acoes.csv", ["data", "usuario", "acao", "caminho", "status", "ip"],
                    [(hora_local(r["criado_em"]), r["usuario"] or "", r["acao"], r["caminho"], r["status"], r["ip"] or "") for r in rows])


def _filtro_logins(usuario, resultado, dias):
    dias = max(1, min(dias, 365))
    where, args = ["criado_em >= %s"], [time.time() - dias * 86400]
    if usuario:
        where.append("usuario ILIKE %s"); args.append(f"%{usuario.strip()}%")
    if resultado in ("ok", "falha", "bloqueado"):
        where.append("resultado=%s"); args.append(resultado)
    return " AND ".join(where), args


@app.get("/api/auditoria/logins", dependencies=[Depends(P_AUDITORIA)])
def auditoria_logins(usuario: str | None = None, resultado: str | None = None, dias: int = 30, limit: int = 300):
    where, args = _filtro_logins(usuario, resultado, dias)
    with closing(db()) as con:
        rows = con.execute(f"SELECT id,criado_em,usuario,resultado,ip,navegador FROM auditoria_login WHERE {where} ORDER BY criado_em DESC LIMIT %s",
                           (*args, max(1, min(limit, 2000)))).fetchall()
        res = {r["resultado"]: r["n"] for r in con.execute(
            f"SELECT resultado, COUNT(*) n FROM auditoria_login WHERE {where} GROUP BY resultado", args).fetchall()}
    return {"resumo": {"ok": res.get("ok", 0), "falha": res.get("falha", 0), "bloqueado": res.get("bloqueado", 0)}, "logins": rows}


@app.get("/api/auditoria/logins.csv", dependencies=[Depends(P_AUDITORIA)])
def auditoria_logins_csv(usuario: str | None = None, resultado: str | None = None, dias: int = 30):
    where, args = _filtro_logins(usuario, resultado, dias)
    with closing(db()) as con:
        rows = con.execute(f"SELECT criado_em,usuario,resultado,ip,navegador FROM auditoria_login WHERE {where} ORDER BY criado_em DESC LIMIT 20000", args).fetchall()
    return csv_resp("auditoria-logins.csv", ["data", "usuario", "resultado", "ip", "navegador"],
                    [(hora_local(r["criado_em"]), r["usuario"] or "", r["resultado"], r["ip"] or "", r["navegador"] or "") for r in rows])


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(u: dict = Depends(painel_auth)):
    return {"usuario": u["usuario"], "nome": u["nome"], "papel": u["papel"], "permissoes": sorted(u["perms"])}



def csv_resp(nome, cabecalho, linhas):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(cabecalho); w.writerows(linhas)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


def hora_local(epoch):
    return time.strftime("%d/%m/%Y %H:%M", time.gmtime(epoch + TZ_OFFSET)) if epoch else ""


@app.get("/api/leituras-hora", dependencies=[Depends(P_CONSULTAR)])
def leituras_hora(horas: int = 168):
    desde = int(time.time() // 3600 * 3600) - max(1, min(horas, 24 * 90)) * 3600
    with closing(db()) as con:
        return con.execute("""SELECT hora, SUM(leituras)::int leituras, COUNT(*)::int tags FROM leituras_hora
                              WHERE hora>=%s GROUP BY hora ORDER BY hora DESC""", (desde,)).fetchall()


@app.get("/api/export/leituras.csv", dependencies=[Depends(P_EXPORTAR)])
def export_leituras(horas: int = 168):
    desde = int(time.time() // 3600 * 3600) - max(1, min(horas, 24 * 365)) * 3600
    with closing(db()) as con:
        rows = con.execute("""SELECT l.hora, l.epc, i.tipo, l.leituras, l.melhor_rssi FROM leituras_hora l
                              LEFT JOIN itens i ON i.epc=l.epc WHERE l.hora>=%s ORDER BY l.hora DESC, l.epc""", (desde,)).fetchall()
    return csv_resp("leituras-por-hora.csv", ["hora_local", "epc", "item", "leituras", "melhor_rssi"],
                    [(hora_local(r["hora"]), r["epc"], r["tipo"] or "", r["leituras"], r["melhor_rssi"] if r["melhor_rssi"] is not None else "") for r in rows])


@app.get("/api/export/inventario.csv", dependencies=[Depends(P_EXPORTAR)])
def export_inventario():
    with closing(db()) as con:
        rows = con.execute(f"SELECT * FROM ({ITENS_VIEW_SQL}) v ORDER BY v.tipo, v.epc").fetchall()
    return csv_resp("inventario.csv", ["epc", "item", "fabricante", "tamanho", "composicao", "cor", "data_compra", "localizacao", "estado"],
                    [(r["epc"], r["tipo"], r["fabricante"] or "", r["tamanho"] or "", r["composicao"] or "", r["cor"] or "",
                      r["data_compra"] or "", r["localizacao"] or "", r["estado"]) for r in rows])


@app.get("/api/export/transferencias.csv", dependencies=[Depends(P_EXPORTAR)])
def export_transferencias():
    with closing(db()) as con:
        rows = con.execute("""SELECT t.lote_id, t.criado_em, d.nome destino, d.tipo, t.usuario, l.quantidade, t.observacao
                              FROM transferencias t JOIN destinos d ON d.id=t.destino_id JOIN lotes l ON l.id=t.lote_id
                              ORDER BY t.id DESC""").fetchall()
    return csv_resp("transferencias.csv", ["lote", "data", "destino", "tipo_destino", "usuario", "quantidade", "observacao"],
                    [(r["lote_id"], hora_local(r["criado_em"]), r["destino"], r["tipo"], r["usuario"] or "", r["quantidade"], r["observacao"] or "") for r in rows])


@app.get("/api/sessoes", dependencies=[Depends(P_CONSULTAR)])
def listar_sessoes(limit: int = 50):
    with closing(db()) as con:
        return {"auto": SESSAO_AUTO, "ocioso_segundos": SESSAO_OCIOSO,
                "sessoes": con.execute("SELECT * FROM sessoes ORDER BY id DESC LIMIT %s", (min(limit, 500),)).fetchall()}

# ---- PWA: arquivos públicos (o navegador os busca sem cookie; não têm dado sensível) ---------------
ICONES = {p.name for p in (STATIC / "icons").glob("*.png")} if (STATIC / "icons").is_dir() else set()


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def pwa_service_worker():
    return FileResponse(STATIC / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/offline.html")
def pwa_offline():
    return FileResponse(STATIC / "offline.html", media_type="text/html")


@app.get("/icons/{nome}")
def pwa_icone(nome: str):
    if nome not in ICONES:  # lista fechada: sem path traversal
        raise HTTPException(404)
    return FileResponse(STATIC / "icons" / nome, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


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


def _perms_do_perfil(con, nome):
    r = con.execute("SELECT permissoes FROM perfis WHERE nome=%s", (nome,)).fetchone()
    return _perms(r["permissoes"]) if r else None


def _pode_conceder(u, perms):
    """Só o admin concede qualquer coisa; os demais só concedem permissões que eles próprios têm (sem escalar privilégio)."""
    return u["papel"] == "admin" or set(perms) <= set(u["perms"])


def _valida_usuario(con, u, papel, senha):
    if papel is not None:
        perms = _perms_do_perfil(con, papel)
        if perms is None:
            raise HTTPException(400, f"perfil '{papel}' não existe")
        if not _pode_conceder(u, perms):
            raise HTTPException(403, "você não pode atribuir um perfil com permissões que você não tem")
    if senha is not None and len(senha) < 8:
        raise HTTPException(400, "a senha precisa ter ao menos 8 caracteres")


@app.get("/api/usuarios", dependencies=[Depends(P_USUARIOS)])
def listar_usuarios():
    with closing(db()) as con:
        return con.execute("""SELECT id,usuario,nome,papel,ativo,(senha_hash IS NOT NULL) AS tem_senha
                              FROM usuarios ORDER BY usuario""").fetchall()


@app.post("/api/usuarios")
def criar_usuario(payload: UsuarioIn, u: dict = Depends(P_USUARIOS)):
    nome_login = payload.usuario.strip().lower()
    if not nome_login or " " in nome_login:
        raise HTTPException(400, "usuário obrigatório, sem espaços")
    with closing(db()) as con:
        _valida_usuario(con, u, payload.papel, payload.senha)
        try:
            con.execute("INSERT INTO usuarios(usuario,nome,senha_hash,papel,ativo,criado_em) VALUES(%s,%s,%s,%s,1,%s)",
                        (nome_login, payload.nome, hash_senha(payload.senha) if payload.senha else None, payload.papel, time.time()))
            con.commit()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "já existe um usuário com esse login")
    return {"ok": True}


@app.patch("/api/usuarios/{uid}")
def atualizar_usuario(uid: int, payload: UsuarioPatch, u: dict = Depends(P_USUARIOS)):
    with closing(db()) as con:
        alvo = con.execute("SELECT papel FROM usuarios WHERE id=%s", (uid,)).fetchone()
        if not alvo:
            raise HTTPException(404, "usuário não encontrado")
        _valida_usuario(con, u, payload.papel, payload.senha)
        if not _pode_conceder(u, _perms_do_perfil(con, alvo["papel"]) or set()):  # não mexe em quem tem mais poder que você
            raise HTTPException(403, "esse usuário tem permissões que você não tem")
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


class PerfilIn(BaseModel):
    nome: str | None = None
    descricao: str | None = None
    permissoes: list[str] = []


def _valida_perfil(u, perms):
    desconhecidas = [p for p in perms if p not in PERMISSOES]
    if desconhecidas:
        raise HTTPException(400, f"permissão desconhecida: {desconhecidas[0]}")
    if not _pode_conceder(u, perms):
        raise HTTPException(403, "você não pode conceder permissões que você não tem")


@app.get("/api/perfis", dependencies=[Depends(P_USUARIOS)])
def listar_perfis():
    with closing(db()) as con:
        rows = con.execute("""SELECT p.nome, p.descricao, p.permissoes, (SELECT COUNT(*) FROM usuarios u WHERE u.papel=p.nome) AS usuarios
                              FROM perfis p ORDER BY (p.nome='admin') DESC, p.nome""").fetchall()
    return {"permissoes": [{"id": k, "rotulo": v} for k, v in PERMISSOES.items()],
            "perfis": [{"nome": r["nome"], "descricao": r["descricao"], "permissoes": sorted(_perms(r["permissoes"])),
                        "usuarios": r["usuarios"], "fixo": r["nome"] == "admin"} for r in rows]}


@app.post("/api/perfis")
def criar_perfil(payload: PerfilIn, u: dict = Depends(P_USUARIOS)):
    nome = (payload.nome or "").strip()
    if not re.fullmatch(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 _-]{1,39}", nome):
        raise HTTPException(400, "nome do perfil: 2 a 40 caracteres (letras, números, espaço, _ ou -)")
    if nome.lower() == "admin":
        raise HTTPException(409, "o perfil admin é reservado")
    _valida_perfil(u, payload.permissoes)
    with closing(db()) as con:
        if con.execute("SELECT 1 FROM perfis WHERE lower(nome)=lower(%s)", (nome,)).fetchone():
            raise HTTPException(409, "já existe um perfil com esse nome")
        con.execute("INSERT INTO perfis(nome,descricao,permissoes,criado_em) VALUES(%s,%s,%s,%s)",
                    (nome, (payload.descricao or "").strip() or None, json.dumps(sorted(set(payload.permissoes))), time.time()))
        con.commit()
    return {"ok": True}


@app.put("/api/perfis/{nome}")
def editar_perfil(nome: str, payload: PerfilIn, u: dict = Depends(P_USUARIOS)):
    if nome == "admin":
        raise HTTPException(403, "o perfil admin não pode ser alterado")
    _valida_perfil(u, payload.permissoes)
    with closing(db()) as con:
        atual = _perms_do_perfil(con, nome)
        if atual is None:
            raise HTTPException(404, "perfil não encontrado")
        if not _pode_conceder(u, atual):
            raise HTTPException(403, "esse perfil tem permissões que você não tem")
        con.execute("UPDATE perfis SET descricao=%s, permissoes=%s WHERE nome=%s",
                    ((payload.descricao or "").strip() or None, json.dumps(sorted(set(payload.permissoes))), nome))
        con.commit()
    return {"ok": True}


@app.delete("/api/perfis/{nome}")
def excluir_perfil(nome: str, u: dict = Depends(P_USUARIOS)):
    if nome == "admin":
        raise HTTPException(403, "o perfil admin não pode ser excluído")
    with closing(db()) as con:
        atual = _perms_do_perfil(con, nome)
        if atual is None:
            raise HTTPException(404, "perfil não encontrado")
        if not _pode_conceder(u, atual):
            raise HTTPException(403, "esse perfil tem permissões que você não tem")
        n = con.execute("SELECT COUNT(*) n FROM usuarios WHERE papel=%s", (nome,)).fetchone()["n"]
        if n:
            raise HTTPException(409, f"{n} usuário(s) usam este perfil; troque o perfil deles antes de excluir")
        con.execute("DELETE FROM perfis WHERE nome=%s", (nome,))
        con.commit()
    return {"ok": True}


@app.get("/")
def index(u=Depends(usuario_opt)):
    if not u:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC / "index.html")
