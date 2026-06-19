# F6.B — Backend API SecondBrainSense (v3 — versión definitiva)

**Tipo:** Backend Python / FastAPI  
**Dependencia:** F5 completada, F6 UI compilada y funcionando  
**Entregable:** 24 endpoints nuevos + Swagger completo

---

## ANTES DE EMPEZAR — lectura obligatoria para el implementador

Este documento usa código copiable directamente. Antes de tocar nada:

```bash
# 1. Verifica que el backend recarga automáticamente (hot-reload)
docker compose -f docker/docker-compose.dev.yml logs --tail=5 backend
# Debes ver "Application startup complete." — eso significa que los cambios
# en app/ son visibles sin rebuild

# 2. Obtén un token para probar los endpoints mientras implementas
export TOKEN=$(curl -s -X POST http://localhost:8929/auth/jwt/login \
  -F "username=tu_email@test.com" -F "password=tu_password" \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3. Verifica que el token funciona
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8929/users/me | python -m json.tool
```

---

## 1. Diagnóstico completo: frontend vs backend

### 1.1 Endpoints que existen (✅ NO tocar)
```
POST   /api/v1/brain/query             — Cascada L1→L2→BM25→Web→L0
GET    /api/v1/brain/passport/{src}    — Lee pasaporte (AMPLIAR en F6.B.05)
DELETE /api/v1/brain/document/{src}    — Elimina .md + vectores
GET    /health                         — Liveness probe docker-compose (NO TOCAR)
```

### 1.2 Bug de URL: el frontend llama /api/v1/health, no /health
```python
# En surfsense_web/lib/brain/constants.ts:
HEALTH: "/api/v1/health",   ← esta ruta no existe en el backend
```
El `/health` del backend es el liveness probe del docker-compose. La UI necesita
una ruta enriquecida en `/api/v1/health`. Solución: añadir ruta nueva, NO tocar `/health`.

### 1.3 Endpoints faltantes (24 en total)
```
GET  /api/v1/health                            ← bug de URL (ver 1.2)
GET  /api/v1/brain/stats
GET  /api/v1/brain/level-usage
GET  /api/v1/brain/list
GET  /api/v1/brain/passport/{src}              ← ampliar GET existente
PUT  /api/v1/brain/passport/{src}
GET  /api/v1/brain/passport/{src}/history
POST /api/v1/brain/document/{src}/resynthesize
GET  /api/v1/brain/graph
POST /api/v1/brain/ingest/url
GET  /api/v1/brain/ingest/stream               (SSE)
GET  /api/v1/admin/config
POST /api/v1/admin/config
GET  /api/v1/admin/ollama-models
GET/POST     /api/v1/brain/admin/domains
PUT/DELETE   /api/v1/brain/admin/domains/{id}
PATCH        /api/v1/brain/admin/domains/{id}/toggle-active   ← NUEVO (faltaba en v2)
GET/POST     /api/v1/brain/admin/doc-types
PUT/DELETE   /api/v1/brain/admin/doc-types/{id}
GET/POST     /api/v1/brain/admin/entity-hints
PUT/DELETE   /api/v1/brain/admin/entity-hints/{id}
GET/POST     /api/v1/brain/admin/vocabulary
PUT/DELETE   /api/v1/brain/admin/vocabulary/{id}
GET          /api/v1/brain/admin/vocabulary/lookup             ← NUEVO (faltaba en v2)
```

---

## 2. Reglas de integración con SurfSense (NO inventar patrones nuevos)

```python
# REGLA 1: Auth en TODOS los endpoints
from app.users import current_active_user
current_user: User = Depends(current_active_user)

# REGLA 2: DB async solo cuando se usa PostgreSQL
from app.db import get_async_session
db: AsyncSession = Depends(get_async_session)

# REGLA 3: Qdrant — singleton del lifespan, NUNCA crear cliente nuevo
from app.brain.qdrant_manager import QdrantManager
mgr = QdrantManager.get_instance()

# REGLA 4: Operaciones síncronas pesadas → thread pool (NO bloquear event loop)
result = await asyncio.to_thread(funcion_sincrona, arg1, arg2)

# REGLA 5: Verificar acceso al search space (usar SearchSpaceMembership, NO SearchSpaceMember)
from app.db import SearchSpaceMembership   # ← nombre correcto verificado en db.py

# REGLA 6: Cero hardcode — todo desde env vars
BRAIN_DIR   = os.getenv("BRAIN_DIR",   "/data/brain")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
REDIS_URL   = os.getenv("REDIS_APP_URL", "redis://redis:6379/0")
```

---

## 3. Ficheros a crear/modificar

```
surfsense_backend/app/
├── app.py                        MODIFICAR (sección al final, ver F6.B.01 y F6.B.22)
├── routes/
│   ├── brain_routes.py           MODIFICAR (añadir F6.B.02 a F6.B.10)
│   └── brain_admin_routes.py     CREAR (F6.B.11 a F6.B.21)
└── brain/
    └── masters.py                NO TOCAR — solo para el motor de clasificación
                                  Las tablas ya están en PostgreSQL (migración 160)
```

**Las tablas brain_domains, brain_doc_types, brain_entity_hints, brain_vocabulary ya existen
en PostgreSQL** desde la migración `160_brain_metadata_tables.py`. Los SQLAlchemy models
se usan directamente con `AsyncSession`. NO hay que modificar `masters.py`.

---

## 4. Prerequisito: SQLAlchemy models para las tablas Brain (migración 160)

Las tablas ya existen. Solo necesitas saber cómo acceder a ellas con SQLAlchemy async.
Añade estos modelos en `app/db.py` si no existen ya — o confirma que están presentes:

```python
# Verificar si ya existen en db.py:
grep -n "brain_domains\|BrainDomain\|brain_vocabulary" surfsense_backend/app/db.py
```

Si no existen, añadir en `app/db.py`:

```python
from sqlalchemy.dialects.postgresql import JSONB as _JSONB

class BrainDomain(BaseModel, TimestampMixin):
    __tablename__ = "brain_domains"
    domain_key    = Column(String(80),  nullable=False)
    label         = Column(String(200), nullable=False)
    description   = Column(Text,        server_default="")
    signal_tags   = Column(_JSONB,      server_default="[]")
    signal_kw     = Column(_JSONB,      server_default="[]")
    search_space_id = Column(Integer, ForeignKey("searchspaces.id", ondelete="CASCADE"), nullable=True)
    is_active     = Column(Boolean,     server_default="true")

class BrainDocType(BaseModel, TimestampMixin):
    __tablename__ = "brain_doc_types"
    type_key      = Column(String(80),  nullable=False)
    label         = Column(String(200), nullable=False)
    signal_tags   = Column(_JSONB,      server_default="[]")
    signal_kw     = Column(_JSONB,      server_default="[]")
    signal_formats = Column(_JSONB,     server_default="[]")
    search_space_id = Column(Integer, ForeignKey("searchspaces.id", ondelete="CASCADE"), nullable=True)
    is_active     = Column(Boolean,     server_default="true")

class BrainEntityHint(BaseModel, TimestampMixin):
    __tablename__ = "brain_entity_hints"
    hint_key      = Column(String(150), nullable=False)
    domain_key    = Column(String(80),  nullable=True)
    doc_type_key  = Column(String(80),  nullable=True)
    label         = Column(String(300), nullable=False)
    patterns      = Column(_JSONB,      server_default="[]")
    examples      = Column(_JSONB,      server_default="[]")
    search_space_id = Column(Integer, ForeignKey("searchspaces.id", ondelete="CASCADE"), nullable=True)
    is_active     = Column(Boolean,     server_default="true")

class BrainVocabulary(BaseModel, TimestampMixin):
    __tablename__ = "brain_vocabulary"
    canonical_tag = Column(String(150), nullable=False)
    aliases       = Column(_JSONB,      server_default="[]")
    search_space_id = Column(Integer, ForeignKey("searchspaces.id", ondelete="CASCADE"), nullable=True)
    is_active     = Column(Boolean,     server_default="true")
```

**Patrón de consulta en los endpoints** (async, sin thread pool para vocab):

```python
# Listar dominios globales + del space
from sqlalchemy import select, or_
from app.db import BrainDomain, AsyncSession

async def _list_domains(db: AsyncSession, search_space_id: int) -> list:
    result = await db.execute(
        select(BrainDomain)
        .where(or_(
            BrainDomain.search_space_id == None,        # globales
            BrainDomain.search_space_id == search_space_id  # del space
        ))
        .where(BrainDomain.is_active == True)
        .order_by(BrainDomain.id)
    )
    rows = result.scalars().all()
    return [
        {
            "id":             r.id,
            "domain_key":     r.domain_key,
            "label":          r.label,
            "description":    r.description or "",
            "signal_tags":    r.signal_tags or [],
            "signal_kw":      r.signal_kw or [],
            "is_active":      r.is_active,
            "scope":          "global" if r.search_space_id is None else "space",
            "search_space_id": r.search_space_id,
        }
        for r in rows
    ]
```

**Fichero:** `app/brain/masters.py` — añadir ANTES de `_init_db()`:

```python
# masters.py — ampliar _SCHEMA con nuevas columnas y tabla vocabulary
_SCHEMA_V2_MIGRATIONS = """
-- Añadir columnas faltantes a tablas existentes (seguro si ya existen)
ALTER TABLE domains        ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE domains        ADD COLUMN scope TEXT DEFAULT 'global';
ALTER TABLE domains        ADD COLUMN search_space_id INTEGER;
ALTER TABLE subdomains     ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE doc_types      ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE doc_types      ADD COLUMN scope TEXT DEFAULT 'global';
ALTER TABLE doc_types      ADD COLUMN search_space_id INTEGER;
ALTER TABLE entity_hints   ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE entity_hints   ADD COLUMN scope TEXT DEFAULT 'global';
ALTER TABLE entity_hints   ADD COLUMN search_space_id INTEGER;
"""

_SCHEMA_VOCABULARY = """
CREATE TABLE IF NOT EXISTS vocabulary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_tag TEXT NOT NULL UNIQUE,
    aliases TEXT DEFAULT '[]',
    scope TEXT DEFAULT 'global',
    is_active INTEGER DEFAULT 1,
    search_space_id INTEGER
);
"""
```

**Reemplazar la función `_init_db()` existente:**

```python
def _init_db():
    conn = _get_conn()
    conn.executescript(_SCHEMA)      # tablas originales
    conn.executescript(_SCHEMA_VOCABULARY)  # nueva tabla vocabulary
    # Migraciones seguras (ignorar errores si columna ya existe)
    for stmt in _SCHEMA_V2_MIGRATIONS.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass  # columna ya existe — OK
    conn.commit()
    conn.close()
```

**Añadir funciones de lectura con rowid** (AÑADIR al final del módulo, tras las existentes):

```python
# ── Lectura con rowid como id entero ─────────────────────────────────────────

def get_domains_list() -> list[dict]:
    """Devuelve dominios con rowid como id entero (compatibilidad frontend)."""
    _ensure_loaded()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT rowid AS id, id AS domain_key, label, description, "
        "signal_tags, signal_kw, is_active, scope, search_space_id FROM domains"
    ).fetchall()
    conn.close()
    return [
        {
            "id":             r["id"],
            "domain_key":     r["domain_key"],
            "label":          r["label"],
            "description":    r["description"] or "",
            "signal_tags":    json.loads(r["signal_tags"] or "[]"),
            "signal_kw":      json.loads(r["signal_kw"] or "[]"),
            "is_active":      bool(r["is_active"]),
            "scope":          r["scope"] or "global",
            "search_space_id": r["search_space_id"],
        }
        for r in rows
    ]

def get_doc_types_list() -> list[dict]:
    _ensure_loaded()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT rowid AS id, id AS type_key, label, "
        "signal_tags, signal_kw, signal_formats, is_active, scope, search_space_id "
        "FROM doc_types"
    ).fetchall()
    conn.close()
    return [
        {
            "id":             r["id"],
            "type_key":       r["type_key"],
            "label":          r["label"],
            "signal_tags":    json.loads(r["signal_tags"] or "[]"),
            "signal_kw":      json.loads(r["signal_kw"] or "[]"),
            "signal_formats": json.loads(r["signal_formats"] or "[]"),
            "is_active":      bool(r["is_active"]),
            "scope":          r["scope"] or "global",
            "search_space_id": r["search_space_id"],
        }
        for r in rows
    ]

def get_entity_hints_list(domain_key: str | None = None) -> list[dict]:
    _ensure_loaded()
    conn = _get_conn()
    sql = (
        "SELECT rowid AS id, id AS hint_key, label, domain_id AS domain_key, "
        "doc_type_id AS doc_type_key, patterns, examples, is_active, search_space_id "
        "FROM entity_hints"
    )
    params = []
    if domain_key:
        sql += " WHERE domain_id = ?"
        params.append(domain_key)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "id":             r["id"],
            "hint_key":       r["hint_key"],
            "label":          r["label"],
            "domain_key":     r["domain_key"],
            "doc_type_key":   r["doc_type_key"],
            "patterns":       json.loads(r["patterns"] or "[]"),
            "examples":       json.loads(r["examples"] or "[]"),
            "is_active":      bool(r["is_active"]),
            "search_space_id": r["search_space_id"],
        }
        for r in rows
    ]

# ── Write API (create / update / delete por rowid) ────────────────────────────

def create_domain_record(domain_key: str, label: str, description: str = "",
                          signal_tags: list | None = None,
                          signal_kw: list | None = None) -> dict:
    _ensure_loaded()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO domains (id, label, description, signal_tags, signal_kw) VALUES (?,?,?,?,?)",
        (domain_key, label, description,
         json.dumps(signal_tags or []), json.dumps(signal_kw or []))
    )
    rowid = cur.lastrowid
    conn.commit(); conn.close(); reload()
    return next((d for d in get_domains_list() if d["id"] == rowid), {})

def update_domain_record(rowid: int, **kwargs) -> dict | None:
    _ensure_loaded()
    conn = _get_conn()
    fields, values = [], []
    mapping = {
        "label": "label", "description": "description",
        "signal_tags": "signal_tags", "signal_kw": "signal_kw",
        "is_active": "is_active",
    }
    for k, col in mapping.items():
        if k in kwargs and kwargs[k] is not None:
            fields.append(f"{col}=?")
            v = json.dumps(kwargs[k]) if isinstance(kwargs[k], list) else kwargs[k]
            values.append(int(v) if k == "is_active" else v)
    if not fields:
        conn.close(); return next((d for d in get_domains_list() if d["id"] == rowid), None)
    values.append(rowid)
    conn.execute(f"UPDATE domains SET {', '.join(fields)} WHERE rowid=?", values)
    conn.commit(); conn.close(); reload()
    return next((d for d in get_domains_list() if d["id"] == rowid), None)

def delete_domain_record(rowid: int) -> bool:
    _ensure_loaded()
    conn = _get_conn()
    cur = conn.execute("DELETE FROM domains WHERE rowid=?", (rowid,))
    deleted = cur.rowcount > 0
    conn.commit(); conn.close()
    if deleted: reload()
    return deleted

def toggle_domain_active(rowid: int) -> dict | None:
    _ensure_loaded()
    conn = _get_conn()
    conn.execute("UPDATE domains SET is_active = 1 - is_active WHERE rowid=?", (rowid,))
    conn.commit(); conn.close(); reload()
    return next((d for d in get_domains_list() if d["id"] == rowid), None)

# Mismo patrón para doc_types (create_doc_type_record, update_doc_type_record,
# delete_doc_type_record) y entity_hints (create_entity_hint_record, etc.)
# → Ver sección F6.B.16-19 para el código completo

# ── Vocabulary table ──────────────────────────────────────────────────────────

def get_vocabulary_list(q: str | None = None, search_space_id: int | None = None) -> list[dict]:
    _ensure_loaded()
    conn = _get_conn()
    sql = "SELECT id, canonical_tag, aliases, scope, is_active, search_space_id FROM vocabulary"
    params = []
    if q:
        sql += " WHERE canonical_tag LIKE ?"
        params.append(f"%{q}%")
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "id":             r["id"],
            "canonical_tag":  r["canonical_tag"],
            "aliases":        json.loads(r["aliases"] or "[]"),
            "scope":          r["scope"] or "global",
            "is_active":      bool(r["is_active"]),
            "search_space_id": r["search_space_id"],
        }
        for r in rows
    ]

def lookup_vocabulary(tag: str) -> dict:
    """Busca si un tag es canónico o alias. Devuelve {canonical_tag, aliases, found}."""
    _ensure_loaded()
    conn = _get_conn()
    # Buscar como canónico
    row = conn.execute(
        "SELECT canonical_tag, aliases FROM vocabulary WHERE canonical_tag=?", (tag,)
    ).fetchone()
    if row:
        conn.close()
        return {"canonical_tag": row["canonical_tag"],
                "aliases": json.loads(row["aliases"] or "[]"), "found": True}
    # Buscar como alias (en el JSON)
    rows = conn.execute("SELECT canonical_tag, aliases FROM vocabulary").fetchall()
    conn.close()
    for r in rows:
        aliases = json.loads(r["aliases"] or "[]")
        if tag in aliases:
            return {"canonical_tag": r["canonical_tag"], "aliases": aliases, "found": True}
    return {"canonical_tag": None, "aliases": [], "found": False}

def create_vocabulary_record(canonical_tag: str, aliases: list | None = None,
                              scope: str = "global",
                              search_space_id: int | None = None) -> dict:
    _ensure_loaded()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO vocabulary (canonical_tag, aliases, scope, search_space_id) VALUES (?,?,?,?)",
        (canonical_tag, json.dumps(aliases or []), scope, search_space_id)
    )
    vid = cur.lastrowid
    conn.commit(); conn.close()
    return next((v for v in get_vocabulary_list() if v["id"] == vid), {})

def update_vocabulary_record(vid: int, aliases: list | None = None,
                              is_active: bool | None = None) -> dict | None:
    _ensure_loaded()
    conn = _get_conn()
    fields, values = [], []
    if aliases is not None:  fields.append("aliases=?"); values.append(json.dumps(aliases))
    if is_active is not None:fields.append("is_active=?"); values.append(int(is_active))
    if fields:
        values.append(vid)
        conn.execute(f"UPDATE vocabulary SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
    conn.close()
    return next((v for v in get_vocabulary_list() if v["id"] == vid), None)

def delete_vocabulary_record(vid: int) -> bool:
    _ensure_loaded()
    conn = _get_conn()
    cur = conn.execute("DELETE FROM vocabulary WHERE id=?", (vid,))
    deleted = cur.rowcount > 0
    conn.commit(); conn.close()
    return deleted
```

---

## 5. Helpers comunes: añadir al inicio de brain_routes.py

**Fichero:** `app/routes/brain_routes.py` — añadir al bloque de imports y tras ellos:

```python
# ── Imports adicionales (añadir a los existentes) ────────────────────────────
import asyncio
import datetime
import uuid
import time

import redis as redis_lib
from qdrant_client import models as qdrant_models
from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import select

# ── Helper: verificar acceso al search space ──────────────────────────────────
async def _require_space_access(
    search_space_id: int,
    db: AsyncSession,
    current_user: User,
) -> None:
    """
    Lanza HTTP 403 si el usuario no es miembro del search space.
    Los superusuarios tienen acceso a todos los spaces.

    IMPORTANTE: el modelo es SearchSpaceMembership (NO SearchSpaceMember).
    Importar desde app.db.
    """
    from app.db import SearchSpaceMembership

    if current_user.is_superuser:
        return
    row = await db.execute(
        select(SearchSpaceMembership).where(
            SearchSpaceMembership.search_space_id == search_space_id,
            SearchSpaceMembership.user_id == current_user.id,
        )
    )
    if row.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Sin acceso al search space.")


# ── Helper: incrementar contador de nivel en Redis ────────────────────────────
def _track_level_usage(level: int, search_space_id: int) -> None:
    """
    Fire-and-forget: incrementa contador Redis con TTL 24h.
    Nunca lanza excepción — si Redis falla, la respuesta no se ve afectada.
    Llamar al FINAL de brain_query() con el level_used de la respuesta.
    """
    try:
        r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
        key = f"brain:level:{level}:space:{search_space_id}"
        r.incr(key)
        r.expire(key, 86400)
    except Exception:
        pass


# ── Servicio compartido: lista de pasaportes del space ────────────────────────
async def _list_passports_for_space(search_space_id: int) -> list[dict]:
    """
    Servicio reutilizable — lo usan /list y /graph.
    NO llamar a otro endpoint FastAPI desde aquí (anti-patrón).
    """
    from app.brain.writer import BrainWriter

    all_docs = await asyncio.to_thread(BrainWriter().list_all)

    mgr = QdrantManager.get_instance()
    space_sources: set[str] = set()
    try:
        result, _ = mgr.client.scroll(
            collection_name="brain",
            scroll_filter=qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(
                    key="search_space_id",
                    match=qdrant_models.MatchValue(value=str(search_space_id))
                )
            ]),
            with_payload=["source"],
            limit=10000,
        )
        space_sources = {p.payload.get("source") for p in result if p.payload}
    except Exception:
        pass

    passports = []
    for doc in all_docs:
        slug = doc.get("filename", "")
        if space_sources and slug not in space_sources:
            continue
        passports.append({
            "source":     slug,
            "title":      doc.get("title", slug),
            "domain":     doc.get("domain"),
            "subdomain":  doc.get("subdomain"),
            "tags":       doc.get("tags", []),
            "importance": int(doc.get("importance", 3)),
            "doc_type":   doc.get("type"),
            "updated_at": doc.get("updated_at", ""),
            "scopes":     doc.get("embedding_scope", ["brain", "knowledge"]),
            "confidence": doc.get("confidence"),
        })
    return passports


# ── Helper: guardar versión del pasaporte antes de sobrescribir ───────────────
async def _save_passport_version(source: str, content: str) -> None:
    """Guarda .md actual en .history/{slug}/vNNN_TS.md (thread pool)."""
    import pathlib
    from app.brain.writer import _slugify

    def _write():
        brain_dir = pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain"))
        slug = _slugify(source)
        hdir = brain_dir / ".history" / slug
        hdir.mkdir(parents=True, exist_ok=True)
        n = len(list(hdir.glob("v*.md"))) + 1
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (hdir / f"v{n:03d}_{ts}.md").write_text(content, encoding="utf-8")

    await asyncio.to_thread(_write)


# ── Job registry para ingesta SSE ─────────────────────────────────────────────
_ingest_jobs: dict[str, dict] = {}  # {job_id: {"queue": asyncio.Queue, "created_at": float}}
_JOB_TTL = 3600.0  # 1 hora

def _cleanup_stale_jobs() -> None:
    """Elimina jobs con más de 1 hora de antigüedad."""
    now = time.monotonic()
    stale = [jid for jid, m in _ingest_jobs.items() if now - m["created_at"] > _JOB_TTL]
    for jid in stale:
        _ingest_jobs.pop(jid, None)
```

---

## 6. Endpoints — implementación paso a paso

### F6.B.01 — GET /api/v1/health

**Fichero:** `app/app.py` (NO en brain_routes.py — el prefix es distinto)  
**Añadir ANTES de la línea** `@app.get("/health", ...)`:

```python
# app.py — NUEVO endpoint enriquecido para la UI Brain
# Añadir ANTES del @app.get("/health") existente (el de docker healthcheck)

import asyncio as _asyncio  # import local para no colisionar
import httpx as _httpx

@app.get("/api/v1/health", tags=["health"])
async def brain_health_v1(db: AsyncSession = Depends(get_async_session)):
    """
    Health check enriquecido: Qdrant + Ollama + PostgreSQL + Redis.
    La UI Brain llama a /api/v1/health — DISTINTO del /health del docker healthcheck.
    """
    from app.brain.qdrant_manager import QdrantManager
    import redis as _redis
    from sqlalchemy import text as _text

    status: dict[str, str] = {}

    # Qdrant — síncrono → thread pool
    def _check_qdrant():
        try:
            QdrantManager.get_instance().client.get_collections()
            return "ok"
        except Exception:
            return "error"
    status["qdrant"] = await _asyncio.to_thread(_check_qdrant)

    # Ollama — HTTP async
    try:
        async with _httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{os.getenv('OLLAMA_HOST','http://localhost:11434')}/api/tags")
            status["ollama"] = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        status["ollama"] = "error"

    # PostgreSQL — query async mínima
    try:
        await db.execute(_text("SELECT 1"))
        status["postgresql"] = "ok"
    except Exception:
        status["postgresql"] = "error"

    # Redis — síncrono → thread pool
    def _check_redis():
        try:
            _redis.from_url(
                os.getenv("REDIS_APP_URL", "redis://redis:6379/0"), socket_timeout=2
            ).ping()
            return "ok"
        except Exception:
            return "degraded"
    status["redis"] = await _asyncio.to_thread(_check_redis)

    return status
```

**Test:**
```bash
curl -s http://localhost:8929/api/v1/health
# Esperado: {"qdrant":"ok","ollama":"ok","postgresql":"ok","redis":"ok"}
```

---

### F6.B.02 — GET /api/v1/brain/stats

**Fichero:** `app/routes/brain_routes.py` — añadir DESPUÉS del endpoint `brain_query`

```python
@router.get("/stats")
async def brain_stats(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Estadísticas de colecciones Qdrant + última ingesta + uso de niveles."""
    await _require_space_access(search_space_id, db, current_user)

    def _qdrant_stats():
        mgr = QdrantManager.get_instance()
        cols: dict[str, dict] = {}
        for col_name in ["brain", "knowledge", "code"]:
            try:
                info = mgr.client.get_collection(col_name)
                vectors = info.points_count or 0
                result, _ = mgr.client.scroll(
                    collection_name=col_name,
                    scroll_filter=qdrant_models.Filter(must=[
                        qdrant_models.FieldCondition(
                            key="search_space_id",
                            match=qdrant_models.MatchValue(value=str(search_space_id))
                        )
                    ]),
                    with_payload=["source"], limit=10000,
                )
                sources = len({p.payload.get("source") for p in result if p.payload})
                # Dimensión del vector desde la config de colección
                vec_cfg = getattr(info.config.params, "vectors", None)
                dim = getattr(vec_cfg, "size", None) or 768
                cols[col_name] = {"vectors": vectors, "sources": sources, "dimension": dim}
            except Exception:
                cols[col_name] = {"vectors": 0, "sources": 0, "dimension": 768}
        return cols

    def _redis_stats():
        usage: dict[str, int] = {}
        last_ingest = last_ingest_doc = None
        try:
            r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
            for lvl in range(5):
                val = r.get(f"brain:level:{lvl}:space:{search_space_id}")
                if val:
                    usage[str(lvl)] = int(val)
            li  = r.get(f"brain:last_ingest:space:{search_space_id}")
            lid = r.get(f"brain:last_ingest_doc:space:{search_space_id}")
            last_ingest     = li.decode()  if li  else None
            last_ingest_doc = lid.decode() if lid else None
        except Exception:
            pass
        return usage, last_ingest, last_ingest_doc

    # Lanzar las dos consultas en paralelo (asyncio.gather)
    collections, (level_usage, last_ingest, last_ingest_doc) = await asyncio.gather(
        asyncio.to_thread(_qdrant_stats),
        asyncio.to_thread(_redis_stats),
    )

    bc = collections.get("brain", {})
    kc = collections.get("knowledge", {})
    cc = collections.get("code", {})

    return {
        "collections":     collections,
        "last_ingest":     last_ingest,
        "last_ingest_doc": last_ingest_doc,
        "level_usage":     level_usage,
        "brain_count":     bc.get("vectors", 0),   # alias para compatibilidad UI
        "knowledge_count": kc.get("vectors", 0),
        "code_count":      cc.get("vectors", 0),
    }
```

**Añadir en `brain_query` handler existente** — al FINAL, justo antes del `return`:
```python
    # Añadir en brain_query() tras calcular level_used
    _track_level_usage(level_used, req.search_space_id)
```

**Test:**
```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8929/api/v1/brain/stats?search_space_id=1" | python -m json.tool
```

---

### F6.B.03 — GET /api/v1/brain/level-usage

```python
@router.get("/level-usage")
async def brain_level_usage(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)

    def _read():
        usage: dict[str, int] = {}
        try:
            r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
            for lvl in range(5):
                val = r.get(f"brain:level:{lvl}:space:{search_space_id}")
                if val:
                    usage[str(lvl)] = int(val)
        except Exception:
            pass
        return usage

    return {"usage": await asyncio.to_thread(_read), "period_hours": 24}
```

---

### F6.B.04 — GET /api/v1/brain/list

```python
@router.get("/list")
async def brain_list(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    await _require_space_access(search_space_id, db, current_user)
    passports = await _list_passports_for_space(search_space_id)
    return {"count": len(passports), "passports": passports}
```

---

### F6.B.05 — GET + PUT /api/v1/brain/passport/{source}

**El GET existente devuelve `{source, passport_md}`. La UI espera `{source, content, metadata}`.
REEMPLAZAR el GET existente con esta versión ampliada y AÑADIR el PUT.**

```python
class PassportUpdateRequest(BaseModel):
    content: str

# REEMPLAZA el @router.get("/passport/{source}") existente
@router.get("/passport/{source}")
async def get_passport(
    source: str,
    search_space_id: int = 1,
    current_user: User = Depends(current_active_user),
):
    """Lee pasaporte con contenido y metadata del frontmatter YAML."""
    from app.brain.writer import BrainWriter
    import yaml, re as _re

    def _read():
        content = BrainWriter().read(source)
        if content is None:
            return None
        fm = _re.match(r'^---\n(.*?)\n---\n', content, _re.DOTALL)
        meta = {}
        if fm:
            try:
                meta = yaml.safe_load(fm.group(1)) or {}
            except Exception:
                pass
        return content, meta

    result = await asyncio.to_thread(_read)
    if result is None:
        raise HTTPException(404, f"Pasaporte no encontrado: '{source}'")

    content, meta = result
    return {
        "source":  source,
        "content": content,
        "metadata": {
            "domain":    meta.get("domain"),
            "subdomain": meta.get("subdomain"),
            "tags":      meta.get("tags", []),
            "importance":int(meta.get("importance", 3)),
            "doc_type":  meta.get("type"),
            "scopes":    meta.get("embedding_scope", ["brain", "knowledge"]),
            "confidence":meta.get("confidence"),
        },
    }


@router.put("/passport/{source}")
async def update_passport(
    source: str,
    body: PassportUpdateRequest,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Actualiza el .md y re-vectoriza en brain + knowledge."""
    from app.brain.writer import BrainWriter
    from app.brain.ingest_router import IngestRouter

    await _require_space_access(search_space_id, db, current_user)

    # Leer contenido actual (para guardarlo en historial)
    current_content = await asyncio.to_thread(BrainWriter().read, source)
    if current_content is None:
        raise HTTPException(404, f"Pasaporte no encontrado: '{source}'")

    await _save_passport_version(source, current_content)

    def _write_and_revectorize():
        BrainWriter().write(source, body.content)
        IngestRouter(QdrantManager.get_instance().client).route(
            md_content=body.content, blocks=[], source=source,
            search_space_id=str(search_space_id),
        )
        try:
            r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
            now = datetime.datetime.utcnow().isoformat()
            r.set(f"brain:last_ingest:space:{search_space_id}", now)
            r.set(f"brain:last_ingest_doc:space:{search_space_id}", source)
        except Exception:
            pass

    await asyncio.to_thread(_write_and_revectorize)
    logger.info("[brain] passport updated source='%s' space=%d", source, search_space_id)
    return {"source": source, "content": body.content, "updated": True}
```

---

### F6.B.06 — GET /api/v1/brain/passport/{source}/history

```python
@router.get("/passport/{source}/history")
async def passport_history(
    source: str,
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    import pathlib
    from app.brain.writer import _slugify

    await _require_space_access(search_space_id, db, current_user)

    def _read():
        hdir = pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain")) / ".history" / _slugify(source)
        if not hdir.exists():
            return []
        return [
            {
                "version_id": vf.stem,
                "created_at": datetime.datetime.utcfromtimestamp(vf.stat().st_mtime).isoformat(),
                "content":    vf.read_text(encoding="utf-8"),
            }
            for vf in sorted(hdir.glob("v*.md"), reverse=True)[:20]  # máximo 20
        ]

    return {"source": source, "versions": await asyncio.to_thread(_read)}
```

---

### F6.B.07 — POST /api/v1/brain/document/{source}/resynthesize

```python
class ResynthesizeRequest(BaseModel):
    search_space_id: int
    model: str | None = None

@router.post("/document/{source}/resynthesize")
async def resynthesize_passport(
    source: str,
    body: ResynthesizeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Re-sintetiza el pasaporte desde chunks en Qdrant. Ejecución en background."""
    await _require_space_access(body.search_space_id, db, current_user)

    def _resynthesize():
        from app.brain.synthesizer import DocumentSynthesizer
        from app.brain.writer import BrainWriter, _slugify
        from app.brain.ingest_router import IngestRouter
        import pathlib

        mgr = QdrantManager.get_instance()
        result, _ = mgr.client.scroll(
            collection_name="knowledge",
            scroll_filter=qdrant_models.Filter(must=[
                qdrant_models.FieldCondition(key="source", match=qdrant_models.MatchValue(value=source)),
                qdrant_models.FieldCondition(
                    key="search_space_id",
                    match=qdrant_models.MatchValue(value=str(body.search_space_id))
                ),
            ]),
            with_payload=True, limit=500,
        )
        raw_text = "\n\n".join(p.payload.get("text", "") for p in result if p.payload)
        if not raw_text.strip():
            logger.warning("[brain] resynthesize: sin chunks para source='%s'", source)
            return

        model = body.model or os.getenv("SYNTHESIS_MODEL", "qwen2.5-coder:3b")
        new_md = DocumentSynthesizer(model=model).synthesize(source=source, text=raw_text)

        writer = BrainWriter()
        old = writer.read(source) or ""

        # Guardar versión anterior (síncrono — estamos en thread pool)
        hdir = pathlib.Path(os.getenv("BRAIN_DIR", "/data/brain")) / ".history" / _slugify(source)
        hdir.mkdir(parents=True, exist_ok=True)
        n = len(list(hdir.glob("v*.md"))) + 1
        ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        (hdir / f"v{n:03d}_{ts}.md").write_text(old, encoding="utf-8")

        writer.write(source, new_md)
        IngestRouter(mgr.client).route(
            md_content=new_md, blocks=[], source=source,
            search_space_id=str(body.search_space_id),
        )
        logger.info("[brain] resynthesize DONE source='%s'", source)

    # asyncio.to_thread dentro de background_tasks
    background_tasks.add_task(asyncio.to_thread, _resynthesize)
    return {"status": "queued", "source": source}
```

---

### F6.B.08 — GET /api/v1/brain/graph

**Nota:** `BrainGraph.build()` devuelve `{"nodes": [...], "edges": [...], "meta": {...}}`.
Clave confirmada como `"edges"` en graph.py.

```python
@router.get("/graph")
async def brain_graph(
    search_space_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.brain.graph import BrainGraph

    await _require_space_access(search_space_id, db, current_user)
    # Usar el SERVICIO compartido — NO llamar al handler brain_list()
    passports = await _list_passports_for_space(search_space_id)

    graph_data = await asyncio.to_thread(BrainGraph().build, passports)

    return {
        "nodes":      graph_data.get("nodes", []),
        "edges":      graph_data.get("edges", []),   # clave verificada en graph.py
        "node_count": len(graph_data.get("nodes", [])),
        "edge_count": len(graph_data.get("edges", [])),
    }
```

---

### F6.B.09 — POST /api/v1/brain/ingest/url + F6.B.10 — GET /api/v1/brain/ingest/stream

```python
class IngestUrlRequest(BaseModel):
    url: str
    search_space_id: int
    model: str | None = None


@router.post("/ingest/url")
async def ingest_url(
    body: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Inicia ingesta de URL. Devuelve job_id para suscribirse al SSE."""
    await _require_space_access(body.search_space_id, db, current_user)

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _ingest_jobs[job_id] = {"queue": queue, "created_at": time.monotonic()}
    _cleanup_stale_jobs()

    background_tasks.add_task(
        _run_ingest_pipeline,
        url=body.url,
        search_space_id=body.search_space_id,
        model=body.model,
        queue=queue,
    )

    return {"job_id": job_id, "status": "queued", "background": True}


async def _run_ingest_pipeline(
    url: str, search_space_id: int, model: str | None, queue: asyncio.Queue
) -> None:
    """
    Pipeline de 4 fases. Emite eventos SSE a la cola.
    None en la cola = señal de fin de stream.
    """
    from app.brain.extractors.web import WebExtractor
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.ingest_router import IngestRouter
    from app.brain.writer import BrainWriter

    async def emit(phase: str, status: str, chunks: int = 0, detail: str = "") -> None:
        await queue.put({"phase": phase, "status": status, "chunks": chunks, "detail": detail})

    try:
        # FASE 1 — Extracción HTML
        await emit("extraction", "running")
        blocks = await asyncio.to_thread(WebExtractor().extract, url)
        await emit("extraction", "ok", chunks=len(blocks))

        # FASE 2 — Limpieza (UniversalCleaner ya aplicado en WebExtractor)
        await emit("cleaning", "running")
        text = "\n\n".join(b.get("content", "") for b in blocks if b.get("content"))
        await emit("cleaning", "ok", chunks=len(blocks))

        # FASE 3 — Síntesis pasaporte
        await emit("embedding", "running")
        synth_model = model or os.getenv("SYNTHESIS_MODEL", "qwen2.5-coder:3b")
        new_md = await asyncio.to_thread(
            DocumentSynthesizer(model=synth_model).synthesize,
            source=url, text=text,
        )
        writer = BrainWriter()
        await asyncio.to_thread(writer.write, url, new_md)
        await emit("embedding", "ok")

        # FASE 4 — Vectorización Qdrant
        await emit("qdrant", "running")
        results = await asyncio.to_thread(
            IngestRouter(QdrantManager.get_instance().client).route,
            new_md, blocks, url, str(search_space_id),
        )
        total = sum(v.get("chunks_created", 0) for v in results.values())

        def _register():
            try:
                r = redis_lib.from_url(os.getenv("REDIS_APP_URL", "redis://redis:6379/0"))
                r.set(f"brain:last_ingest:space:{search_space_id}",
                      datetime.datetime.utcnow().isoformat())
                r.set(f"brain:last_ingest_doc:space:{search_space_id}", url)
            except Exception:
                pass
        await asyncio.to_thread(_register)

        await emit("qdrant", "ok", chunks=total)

    except Exception as exc:
        logger.error("[brain] ingest error url='%s': %s", url, exc, exc_info=True)
        await emit("qdrant", "error", detail=str(exc))
    finally:
        await queue.put(None)  # fin de stream


@router.get("/ingest/stream")
async def ingest_stream(
    job_id: str,
    current_user: User = Depends(current_active_user),
):
    """
    SSE del progreso de ingesta. El cliente usa fetch() + Authorization: Bearer.
    (EventSource nativo no soporta cabeceras personalizadas.)
    """
    import json as _json

    job_meta = _ingest_jobs.get(job_id)
    if job_meta is None:
        raise HTTPException(404, f"Job no encontrado: {job_id}")

    queue: asyncio.Queue = job_meta["queue"]

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield "data: [DONE]\n\n"
                    return
                if event is None:
                    yield "data: [DONE]\n\n"
                    return
                yield f"data: {_json.dumps(event)}\n\n"
        finally:
            _ingest_jobs.pop(job_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

---

## 7. Crear brain_admin_routes.py

**Fichero:** `app/routes/brain_admin_routes.py` (fichero nuevo completo)

```python
"""
brain_admin_routes.py
---------------------
Dos routers exportados:
  router_admin  → /api/v1/admin        (config + ollama)
  router_vocab  → /api/v1/brain/admin  (vocabulario CRUD)

Ambos se registran en app.py con include_router().

Las tablas brain_domains, brain_doc_types, brain_entity_hints, brain_vocabulary
existen en PostgreSQL desde la migración 160. Se usan directamente con AsyncSession.
NO hay SQLite ni masters.py en esta capa.
"""
import asyncio
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import User, get_async_session
from app.users import current_active_user

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# ROUTER 1: /api/v1/admin — Config en caliente + Ollama
# ══════════════════════════════════════════════════════════════════════════════
router_admin = APIRouter(prefix="/api/v1/admin", tags=["brain-admin"])

_config_overrides: dict[str, Any] = {}

def _get_config() -> dict[str, Any]:
    """Mezcla env vars (base) con overrides en memoria. Overrides tienen prioridad."""
    base: dict[str, Any] = {
        "BRAIN_CHUNK_STRATEGY":     os.getenv("BRAIN_CHUNK_STRATEGY", "paragraph"),
        "BRAIN_CHUNK_SIZE":         int(os.getenv("BRAIN_CHUNK_SIZE", "512")),
        "BRAIN_CHUNK_OVERLAP":      int(os.getenv("BRAIN_CHUNK_OVERLAP", "64")),
        "ROUTER_L1_HIGH_SCORE":     float(os.getenv("ROUTER_L1_HIGH_SCORE", "0.75")),
        "ROUTER_L1_MIN_SCORE":      float(os.getenv("ROUTER_L1_MIN_SCORE", "0.60")),
        "BRAIN_TOP_K":              int(os.getenv("BRAIN_TOP_K", "5")),
        "BRAIN_RERANKING_ENABLED":  os.getenv("BRAIN_RERANKING_ENABLED", "true").lower() == "true",
        "BRAIN_INGESTION_ENABLED":  os.getenv("BRAIN_INGESTION_ENABLED", "true").lower() == "true",
        "BRAIN_SYNTHESIS_ENABLED":  os.getenv("BRAIN_SYNTHESIS_ENABLED", "true").lower() == "true",
        "BRAIN_QUALITY_THRESHOLD":  float(os.getenv("BRAIN_QUALITY_THRESHOLD", "0.3")),
        "BRAIN_EMBEDDING_MODEL":    os.getenv("BRAIN_EMBEDDING_MODEL", "nomic-embed-text"),
        "BRAIN_LLM_PROVIDER":       os.getenv("BRAIN_LLM_PROVIDER", "ollama"),
        "BRAIN_LLM_MODEL":          os.getenv("SYNTHESIS_MODEL", "qwen2.5-coder:3b"),
        "BRAIN_LLM_TEMPERATURE":    float(os.getenv("BRAIN_LLM_TEMPERATURE", "0.1")),
        "BRAIN_LLM_MAX_TOKENS":     int(os.getenv("BRAIN_LLM_MAX_TOKENS", "4096")),
        "CRAG_EVALUATOR_ENABLED":   os.getenv("CRAG_EVALUATOR_ENABLED", "false").lower() == "true",
        "CRAG_EVALUATOR_PROVIDER":  os.getenv("CRAG_EVALUATOR_PROVIDER", ""),
        "CRAG_REWRITER_MODEL":      os.getenv("CRAG_REWRITER_MODEL", "qwen2.5-coder:3b"),
        "CRAG_TIMEOUT":             int(os.getenv("CRAG_TIMEOUT", "15")),
    }
    return {**base, **_config_overrides}


class AdminConfigPatch(BaseModel):
    """PATCH semántico: solo se aplican los campos no-None."""
    BRAIN_CHUNK_STRATEGY:    str   | None = None
    BRAIN_CHUNK_SIZE:        int   | None = None
    BRAIN_CHUNK_OVERLAP:     int   | None = None
    ROUTER_L1_HIGH_SCORE:    float | None = None
    ROUTER_L1_MIN_SCORE:     float | None = None
    BRAIN_TOP_K:             int   | None = None
    BRAIN_RERANKING_ENABLED: bool  | None = None
    BRAIN_INGESTION_ENABLED: bool  | None = None
    BRAIN_SYNTHESIS_ENABLED: bool  | None = None
    BRAIN_QUALITY_THRESHOLD: float | None = None
    BRAIN_EMBEDDING_MODEL:   str   | None = None
    BRAIN_LLM_PROVIDER:      str   | None = None
    BRAIN_LLM_MODEL:         str   | None = None
    BRAIN_LLM_TEMPERATURE:   float | None = None
    BRAIN_LLM_MAX_TOKENS:    int   | None = None
    CRAG_EVALUATOR_ENABLED:  bool  | None = None
    CRAG_EVALUATOR_PROVIDER: str   | None = None
    CRAG_REWRITER_MODEL:     str   | None = None
    CRAG_TIMEOUT:            int   | None = None


@router_admin.get("/config")
async def get_admin_config(current_user: User = Depends(current_active_user)):
    return _get_config()

@router_admin.post("/config")
async def update_admin_config(
    patch: AdminConfigPatch,
    current_user: User = Depends(current_active_user),
):
    changes = {k: v for k, v in patch.model_dump().items() if v is not None}
    _config_overrides.update(changes)
    logger.info("[admin] config actualizada: %s", list(changes.keys()))
    return _get_config()

@router_admin.get("/ollama-models")
async def get_ollama_models(current_user: User = Depends(current_active_user)):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{os.getenv('OLLAMA_HOST','http://localhost:11434')}/api/tags")
            r.raise_for_status()
            return {"models": [m["name"] for m in r.json().get("models", [])]}
    except Exception as exc:
        logger.warning("[admin] Ollama no disponible: %s", exc)
        return {"models": []}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER 2: /api/v1/brain/admin — Vocabulary CRUD (PostgreSQL, migración 160)
# ══════════════════════════════════════════════════════════════════════════════
router_vocab = APIRouter(prefix="/api/v1/brain/admin", tags=["brain-vocab"])


def _domain_to_dict(r) -> dict:
    return {
        "id": r.id, "domain_key": r.domain_key, "label": r.label,
        "description": r.description or "",
        "signal_tags": r.signal_tags or [], "signal_kw": r.signal_kw or [],
        "is_active": r.is_active,
        "scope": "global" if r.search_space_id is None else "space",
        "search_space_id": r.search_space_id,
    }

def _doc_type_to_dict(r) -> dict:
    return {
        "id": r.id, "type_key": r.type_key, "label": r.label,
        "signal_tags": r.signal_tags or [], "signal_kw": r.signal_kw or [],
        "signal_formats": r.signal_formats or [],
        "is_active": r.is_active,
        "scope": "global" if r.search_space_id is None else "space",
        "search_space_id": r.search_space_id,
    }

def _hint_to_dict(r) -> dict:
    return {
        "id": r.id, "hint_key": r.hint_key, "label": r.label,
        "domain_key": r.domain_key, "doc_type_key": r.doc_type_key,
        "patterns": r.patterns or [], "examples": r.examples or [],
        "is_active": r.is_active, "search_space_id": r.search_space_id,
    }

def _vocab_to_dict(r) -> dict:
    return {
        "id": r.id, "canonical_tag": r.canonical_tag,
        "aliases": r.aliases or [],
        "is_active": r.is_active,
        "scope": "global" if r.search_space_id is None else "space",
        "search_space_id": r.search_space_id,
    }


# ── Modelos Pydantic ──────────────────────────────────────────────────────────

class DomainCreate(BaseModel):
    domain_key: str
    label: str
    description: str = ""
    signal_tags: list[str] = []
    signal_kw: list[str] = []

class DomainUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    signal_tags: list[str] | None = None
    signal_kw: list[str] | None = None
    is_active: bool | None = None

class DocTypeCreate(BaseModel):
    type_key: str
    label: str
    signal_tags: list[str] = []
    signal_kw: list[str] = []
    signal_formats: list[str] = []

class DocTypeUpdate(BaseModel):
    label: str | None = None
    signal_tags: list[str] | None = None
    signal_kw: list[str] | None = None
    signal_formats: list[str] | None = None
    is_active: bool | None = None

class EntityHintCreate(BaseModel):
    hint_key: str
    label: str
    domain_key: str | None = None
    doc_type_key: str | None = None
    patterns: list[str] = []
    examples: list[str] = []

class EntityHintUpdate(BaseModel):
    label: str | None = None
    domain_key: str | None = None
    doc_type_key: str | None = None
    patterns: list[str] | None = None
    examples: list[str] | None = None
    is_active: bool | None = None

class VocabCreate(BaseModel):
    canonical_tag: str
    aliases: list[str] = []

class VocabUpdate(BaseModel):
    aliases: list[str] | None = None
    is_active: bool | None = None


# ── Domains ───────────────────────────────────────────────────────────────────

@router_vocab.get("/domains")
async def list_domains(
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDomain
    result = await db.execute(
        select(BrainDomain)
        .where(or_(BrainDomain.search_space_id.is_(None),
                   BrainDomain.search_space_id == search_space_id))
        .order_by(BrainDomain.id)
    )
    return [_domain_to_dict(r) for r in result.scalars().all()]

@router_vocab.post("/domains", status_code=201)
async def create_domain(
    body: DomainCreate,
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDomain
    obj = BrainDomain(
        domain_key=body.domain_key, label=body.label,
        description=body.description, signal_tags=body.signal_tags,
        signal_kw=body.signal_kw,
        search_space_id=search_space_id if search_space_id else None,
    )
    db.add(obj); await db.commit(); await db.refresh(obj)
    return _domain_to_dict(obj)

@router_vocab.put("/domains/{domain_id}")
async def update_domain(
    domain_id: int,
    body: DomainUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDomain
    obj = await db.get(BrainDomain, domain_id)
    if obj is None:
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")
    if body.label       is not None: obj.label       = body.label
    if body.description is not None: obj.description = body.description
    if body.signal_tags is not None: obj.signal_tags = body.signal_tags
    if body.signal_kw   is not None: obj.signal_kw   = body.signal_kw
    if body.is_active   is not None: obj.is_active   = body.is_active
    await db.commit(); await db.refresh(obj)
    return _domain_to_dict(obj)

@router_vocab.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDomain
    obj = await db.get(BrainDomain, domain_id)
    if obj is None:
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")
    await db.delete(obj); await db.commit()

@router_vocab.patch("/domains/{domain_id}/toggle-active")
async def toggle_domain_active(
    domain_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDomain
    obj = await db.get(BrainDomain, domain_id)
    if obj is None:
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")
    obj.is_active = not obj.is_active
    await db.commit(); await db.refresh(obj)
    return _domain_to_dict(obj)


# ── Doc Types ─────────────────────────────────────────────────────────────────

@router_vocab.get("/doc-types")
async def list_doc_types(
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDocType
    result = await db.execute(
        select(BrainDocType)
        .where(or_(BrainDocType.search_space_id.is_(None),
                   BrainDocType.search_space_id == search_space_id))
        .order_by(BrainDocType.id)
    )
    return [_doc_type_to_dict(r) for r in result.scalars().all()]

@router_vocab.post("/doc-types", status_code=201)
async def create_doc_type(
    body: DocTypeCreate,
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDocType
    obj = BrainDocType(
        type_key=body.type_key, label=body.label,
        signal_tags=body.signal_tags, signal_kw=body.signal_kw,
        signal_formats=body.signal_formats,
        search_space_id=search_space_id if search_space_id else None,
    )
    db.add(obj); await db.commit(); await db.refresh(obj)
    return _doc_type_to_dict(obj)

@router_vocab.put("/doc-types/{doc_type_id}")
async def update_doc_type(
    doc_type_id: int,
    body: DocTypeUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDocType
    obj = await db.get(BrainDocType, doc_type_id)
    if obj is None:
        raise HTTPException(404, f"Tipo de documento no encontrado: {doc_type_id}")
    if body.label          is not None: obj.label          = body.label
    if body.signal_tags    is not None: obj.signal_tags    = body.signal_tags
    if body.signal_kw      is not None: obj.signal_kw      = body.signal_kw
    if body.signal_formats is not None: obj.signal_formats = body.signal_formats
    if body.is_active      is not None: obj.is_active      = body.is_active
    await db.commit(); await db.refresh(obj)
    return _doc_type_to_dict(obj)

@router_vocab.delete("/doc-types/{doc_type_id}", status_code=204)
async def delete_doc_type(
    doc_type_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainDocType
    obj = await db.get(BrainDocType, doc_type_id)
    if obj is None:
        raise HTTPException(404, f"Tipo de documento no encontrado: {doc_type_id}")
    await db.delete(obj); await db.commit()


# ── Entity Hints ──────────────────────────────────────────────────────────────

@router_vocab.get("/entity-hints")
async def list_entity_hints(
    domain_key: str | None = None,
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainEntityHint
    q = select(BrainEntityHint).where(
        or_(BrainEntityHint.search_space_id.is_(None),
            BrainEntityHint.search_space_id == search_space_id)
    )
    if domain_key:
        q = q.where(BrainEntityHint.domain_key == domain_key)
    result = await db.execute(q.order_by(BrainEntityHint.id))
    return [_hint_to_dict(r) for r in result.scalars().all()]

@router_vocab.post("/entity-hints", status_code=201)
async def create_entity_hint(
    body: EntityHintCreate,
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainEntityHint
    obj = BrainEntityHint(
        hint_key=body.hint_key, label=body.label,
        domain_key=body.domain_key, doc_type_key=body.doc_type_key,
        patterns=body.patterns, examples=body.examples,
        search_space_id=search_space_id if search_space_id else None,
    )
    db.add(obj); await db.commit(); await db.refresh(obj)
    return _hint_to_dict(obj)

@router_vocab.put("/entity-hints/{hint_id}")
async def update_entity_hint(
    hint_id: int,
    body: EntityHintUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainEntityHint
    obj = await db.get(BrainEntityHint, hint_id)
    if obj is None:
        raise HTTPException(404, f"Entity hint no encontrado: {hint_id}")
    if body.label        is not None: obj.label        = body.label
    if body.domain_key   is not None: obj.domain_key   = body.domain_key
    if body.doc_type_key is not None: obj.doc_type_key = body.doc_type_key
    if body.patterns     is not None: obj.patterns     = body.patterns
    if body.examples     is not None: obj.examples     = body.examples
    if body.is_active    is not None: obj.is_active    = body.is_active
    await db.commit(); await db.refresh(obj)
    return _hint_to_dict(obj)

@router_vocab.delete("/entity-hints/{hint_id}", status_code=204)
async def delete_entity_hint(
    hint_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainEntityHint
    obj = await db.get(BrainEntityHint, hint_id)
    if obj is None:
        raise HTTPException(404, f"Entity hint no encontrado: {hint_id}")
    await db.delete(obj); await db.commit()


# ── Vocabulario canónico ──────────────────────────────────────────────────────

@router_vocab.get("/vocabulary/lookup")   # DEBE ir ANTES de /vocabulary/{id}
async def lookup_vocabulary(
    tag: str,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    """Busca si un tag es canónico o alias. SIEMPRE antes de /vocabulary/{id}."""
    from app.db import BrainVocabulary
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB

    # Buscar como canónico
    result = await db.execute(
        select(BrainVocabulary).where(BrainVocabulary.canonical_tag == tag)
    )
    row = result.scalar_one_or_none()
    if row:
        return {"canonical_tag": row.canonical_tag, "aliases": row.aliases or [], "found": True}

    # Buscar como alias — PostgreSQL JSONB contains operator
    result = await db.execute(
        select(BrainVocabulary).where(
            BrainVocabulary.aliases.contains(cast([tag], JSONB))
        )
    )
    row = result.scalar_one_or_none()
    if row:
        return {"canonical_tag": row.canonical_tag, "aliases": row.aliases or [], "found": True}

    return {"canonical_tag": None, "aliases": [], "found": False}

@router_vocab.get("/vocabulary")
async def list_vocabulary(
    search_space_id: int = 0,
    q: str | None = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainVocabulary
    stmt = select(BrainVocabulary).where(
        or_(BrainVocabulary.search_space_id.is_(None),
            BrainVocabulary.search_space_id == search_space_id)
    )
    if q:
        stmt = stmt.where(BrainVocabulary.canonical_tag.ilike(f"%{q}%"))
    result = await db.execute(stmt.order_by(BrainVocabulary.id))
    return [_vocab_to_dict(r) for r in result.scalars().all()]

@router_vocab.post("/vocabulary", status_code=201)
async def create_vocabulary_entry(
    body: VocabCreate,
    search_space_id: int = 0,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainVocabulary
    obj = BrainVocabulary(
        canonical_tag=body.canonical_tag, aliases=body.aliases,
        search_space_id=search_space_id if search_space_id else None,
    )
    db.add(obj); await db.commit(); await db.refresh(obj)
    return _vocab_to_dict(obj)

@router_vocab.put("/vocabulary/{vocab_id}")
async def update_vocabulary_entry(
    vocab_id: int,
    body: VocabUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainVocabulary
    obj = await db.get(BrainVocabulary, vocab_id)
    if obj is None:
        raise HTTPException(404, f"Entrada de vocabulario no encontrada: {vocab_id}")
    if body.aliases   is not None: obj.aliases   = body.aliases
    if body.is_active is not None: obj.is_active = body.is_active
    await db.commit(); await db.refresh(obj)
    return _vocab_to_dict(obj)

@router_vocab.delete("/vocabulary/{vocab_id}", status_code=204)
async def delete_vocabulary_entry(
    vocab_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
):
    from app.db import BrainVocabulary
    obj = await db.get(BrainVocabulary, vocab_id)
    if obj is None:
        raise HTTPException(404, f"Entrada de vocabulario no encontrada: {vocab_id}")
    await db.delete(obj); await db.commit()
```
import asyncio
import json
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

from app.db import User
from app.users import current_active_user

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# ROUTER 1: /api/v1/admin — Config en caliente + Ollama
# ══════════════════════════════════════════════════════════════════════════════
router_admin = APIRouter(prefix="/api/v1/admin", tags=["brain-admin"])

_config_overrides: dict[str, Any] = {}

def _get_config() -> dict[str, Any]:
    """Mezcla env vars (base) con overrides en memoria. Overrides tienen prioridad."""
    base: dict[str, Any] = {
        "BRAIN_CHUNK_STRATEGY":     os.getenv("BRAIN_CHUNK_STRATEGY", "paragraph"),
        "BRAIN_CHUNK_SIZE":         int(os.getenv("BRAIN_CHUNK_SIZE", "512")),
        "BRAIN_CHUNK_OVERLAP":      int(os.getenv("BRAIN_CHUNK_OVERLAP", "64")),
        "ROUTER_L1_HIGH_SCORE":     float(os.getenv("ROUTER_L1_HIGH_SCORE", "0.75")),
        "ROUTER_L1_MIN_SCORE":      float(os.getenv("ROUTER_L1_MIN_SCORE", "0.60")),
        "BRAIN_TOP_K":              int(os.getenv("BRAIN_TOP_K", "5")),
        "BRAIN_RERANKING_ENABLED":  os.getenv("BRAIN_RERANKING_ENABLED", "true").lower() == "true",
        "BRAIN_INGESTION_ENABLED":  os.getenv("BRAIN_INGESTION_ENABLED", "true").lower() == "true",
        "BRAIN_SYNTHESIS_ENABLED":  os.getenv("BRAIN_SYNTHESIS_ENABLED", "true").lower() == "true",
        "BRAIN_QUALITY_THRESHOLD":  float(os.getenv("BRAIN_QUALITY_THRESHOLD", "0.3")),
        "BRAIN_EMBEDDING_MODEL":    os.getenv("BRAIN_EMBEDDING_MODEL", "nomic-embed-text"),
        "BRAIN_LLM_PROVIDER":       os.getenv("BRAIN_LLM_PROVIDER", "ollama"),
        "BRAIN_LLM_MODEL":          os.getenv("SYNTHESIS_MODEL", "qwen2.5-coder:3b"),
        "BRAIN_LLM_TEMPERATURE":    float(os.getenv("BRAIN_LLM_TEMPERATURE", "0.1")),
        "BRAIN_LLM_MAX_TOKENS":     int(os.getenv("BRAIN_LLM_MAX_TOKENS", "4096")),
        "CRAG_EVALUATOR_ENABLED":   os.getenv("CRAG_EVALUATOR_ENABLED", "false").lower() == "true",
        "CRAG_EVALUATOR_PROVIDER":  os.getenv("CRAG_EVALUATOR_PROVIDER", ""),
        "CRAG_REWRITER_MODEL":      os.getenv("CRAG_REWRITER_MODEL", "qwen2.5-coder:3b"),
        "CRAG_TIMEOUT":             int(os.getenv("CRAG_TIMEOUT", "15")),
    }
    return {**base, **_config_overrides}


class AdminConfigPatch(BaseModel):
    """PATCH semántico: solo se aplican los campos no-None."""
    BRAIN_CHUNK_STRATEGY:    str   | None = None
    BRAIN_CHUNK_SIZE:        int   | None = None
    BRAIN_CHUNK_OVERLAP:     int   | None = None
    ROUTER_L1_HIGH_SCORE:    float | None = None
    ROUTER_L1_MIN_SCORE:     float | None = None
    BRAIN_TOP_K:             int   | None = None
    BRAIN_RERANKING_ENABLED: bool  | None = None
    BRAIN_INGESTION_ENABLED: bool  | None = None
    BRAIN_SYNTHESIS_ENABLED: bool  | None = None
    BRAIN_QUALITY_THRESHOLD: float | None = None
    BRAIN_EMBEDDING_MODEL:   str   | None = None
    BRAIN_LLM_PROVIDER:      str   | None = None
    BRAIN_LLM_MODEL:         str   | None = None
    BRAIN_LLM_TEMPERATURE:   float | None = None
    BRAIN_LLM_MAX_TOKENS:    int   | None = None
    CRAG_EVALUATOR_ENABLED:  bool  | None = None
    CRAG_EVALUATOR_PROVIDER: str   | None = None
    CRAG_REWRITER_MODEL:     str   | None = None
    CRAG_TIMEOUT:            int   | None = None


@router_admin.get("/config")
async def get_admin_config(current_user: User = Depends(current_active_user)):
    return _get_config()

@router_admin.post("/config")
async def update_admin_config(
    patch: AdminConfigPatch,
    current_user: User = Depends(current_active_user),
):
    changes = {k: v for k, v in patch.model_dump().items() if v is not None}
    _config_overrides.update(changes)
    logger.info("[admin] config actualizada: %s", list(changes.keys()))
    return _get_config()

@router_admin.get("/ollama-models")
async def get_ollama_models(current_user: User = Depends(current_active_user)):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{os.getenv('OLLAMA_HOST','http://localhost:11434')}/api/tags")
            r.raise_for_status()
            return {"models": [m["name"] for m in r.json().get("models", [])]}
    except Exception as exc:
        logger.warning("[admin] Ollama no disponible: %s", exc)
        return {"models": []}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER 2: /api/v1/brain/admin — Vocabulary CRUD
# ══════════════════════════════════════════════════════════════════════════════
router_vocab = APIRouter(prefix="/api/v1/brain/admin", tags=["brain-vocab"])

# ── Modelos Pydantic ──────────────────────────────────────────────────────────

class DomainCreate(BaseModel):
    domain_key: str   # ← nombre correcto según frontend (no "id")
    label: str
    description: str = ""
    signal_tags: list[str] = []
    signal_kw: list[str] = []

class DomainUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    signal_tags: list[str] | None = None
    signal_kw: list[str] | None = None
    is_active: bool | None = None

class DocTypeCreate(BaseModel):
    type_key: str
    label: str
    description: str = ""
    signal_tags: list[str] = []
    signal_kw: list[str] = []
    signal_formats: list[str] = []

class DocTypeUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    signal_tags: list[str] | None = None
    signal_kw: list[str] | None = None
    signal_formats: list[str] | None = None
    is_active: bool | None = None

class EntityHintCreate(BaseModel):
    hint_key: str
    label: str
    domain_key: str | None = None
    doc_type_key: str | None = None
    patterns: list[str] = []
    examples: list[str] = []

class EntityHintUpdate(BaseModel):
    label: str | None = None
    domain_key: str | None = None
    doc_type_key: str | None = None
    patterns: list[str] | None = None
    examples: list[str] | None = None
    is_active: bool | None = None

class VocabCreate(BaseModel):
    canonical_tag: str
    aliases: list[str] = []
    scope: str = "global"

class VocabUpdate(BaseModel):
    aliases: list[str] | None = None
    is_active: bool | None = None

# ── Domains ───────────────────────────────────────────────────────────────────

@router_vocab.get("/domains")
async def list_domains(
    search_space_id: int = 0,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import get_domains_list, _ensure_loaded
    await asyncio.to_thread(_ensure_loaded)
    return await asyncio.to_thread(get_domains_list)

@router_vocab.post("/domains", status_code=201)
async def create_domain(
    body: DomainCreate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import create_domain_record
    return await asyncio.to_thread(
        create_domain_record,
        body.domain_key, body.label, body.description,
        body.signal_tags, body.signal_kw,
    )

@router_vocab.put("/domains/{domain_id}")
async def update_domain(
    domain_id: int,
    body: DomainUpdate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import update_domain_record
    result = await asyncio.to_thread(
        update_domain_record, domain_id,
        label=body.label, description=body.description,
        signal_tags=body.signal_tags, signal_kw=body.signal_kw,
        is_active=body.is_active,
    )
    if result is None:
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")
    return result

@router_vocab.delete("/domains/{domain_id}", status_code=204)
async def delete_domain(
    domain_id: int,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import delete_domain_record
    if not await asyncio.to_thread(delete_domain_record, domain_id):
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")

@router_vocab.patch("/domains/{domain_id}/toggle-active")  # ← NUEVO endpoint que faltaba
async def toggle_domain_active(
    domain_id: int,
    current_user: User = Depends(current_active_user),
):
    """Activa/desactiva un dominio. Alterna is_active entre 0 y 1."""
    from app.brain.masters import toggle_domain_active as _toggle
    result = await asyncio.to_thread(_toggle, domain_id)
    if result is None:
        raise HTTPException(404, f"Dominio no encontrado: {domain_id}")
    return result

# ── Doc Types (mismo patrón que Domains) ─────────────────────────────────────

@router_vocab.get("/doc-types")
async def list_doc_types(
    search_space_id: int = 0,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import get_doc_types_list, _ensure_loaded
    await asyncio.to_thread(_ensure_loaded)
    return await asyncio.to_thread(get_doc_types_list)

@router_vocab.post("/doc-types", status_code=201)
async def create_doc_type(
    body: DocTypeCreate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn
    import json as _json

    def _create():
        _ensure_loaded()
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO doc_types (id, label, signal_tags, signal_kw, signal_formats) VALUES (?,?,?,?,?)",
            (body.type_key, body.label,
             _json.dumps(body.signal_tags), _json.dumps(body.signal_kw),
             _json.dumps(body.signal_formats))
        )
        rowid = cur.lastrowid
        conn.commit(); conn.close()
        from app.brain.masters import get_doc_types_list
        return next((d for d in get_doc_types_list() if d["id"] == rowid), {})

    return await asyncio.to_thread(_create)

@router_vocab.put("/doc-types/{doc_type_id}")
async def update_doc_type(
    doc_type_id: int,
    body: DocTypeUpdate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn, get_doc_types_list
    import json as _json

    def _update():
        _ensure_loaded()
        conn = _get_conn()
        fields, values = [], []
        if body.label is not None:          fields.append("label=?"); values.append(body.label)
        if body.description is not None:    fields.append("description=?"); values.append(body.description)
        if body.signal_tags is not None:    fields.append("signal_tags=?"); values.append(_json.dumps(body.signal_tags))
        if body.signal_kw is not None:      fields.append("signal_kw=?"); values.append(_json.dumps(body.signal_kw))
        if body.signal_formats is not None: fields.append("signal_formats=?"); values.append(_json.dumps(body.signal_formats))
        if body.is_active is not None:      fields.append("is_active=?"); values.append(int(body.is_active))
        if fields:
            values.append(doc_type_id)
            conn.execute(f"UPDATE doc_types SET {', '.join(fields)} WHERE rowid=?", values)
            conn.commit()
        conn.close()
        from app.brain.masters import reload
        reload()
        return next((d for d in get_doc_types_list() if d["id"] == doc_type_id), None)

    result = await asyncio.to_thread(_update)
    if result is None:
        raise HTTPException(404, f"Tipo de documento no encontrado: {doc_type_id}")
    return result

@router_vocab.delete("/doc-types/{doc_type_id}", status_code=204)
async def delete_doc_type(
    doc_type_id: int,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn, reload

    def _delete():
        _ensure_loaded()
        conn = _get_conn()
        cur = conn.execute("DELETE FROM doc_types WHERE rowid=?", (doc_type_id,))
        deleted = cur.rowcount > 0
        conn.commit(); conn.close()
        if deleted: reload()
        return deleted

    if not await asyncio.to_thread(_delete):
        raise HTTPException(404, f"Tipo de documento no encontrado: {doc_type_id}")

# ── Entity Hints ──────────────────────────────────────────────────────────────

@router_vocab.get("/entity-hints")
async def list_entity_hints(
    domain_key: str | None = None,
    search_space_id: int = 0,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import get_entity_hints_list, _ensure_loaded
    await asyncio.to_thread(_ensure_loaded)
    return await asyncio.to_thread(get_entity_hints_list, domain_key)

@router_vocab.post("/entity-hints", status_code=201)
async def create_entity_hint(
    body: EntityHintCreate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn, get_entity_hints_list
    import json as _json

    def _create():
        _ensure_loaded()
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO entity_hints (id, domain_id, doc_type_id, label, patterns, examples) VALUES (?,?,?,?,?,?)",
            (body.hint_key, body.domain_key, body.doc_type_key, body.label,
             _json.dumps(body.patterns), _json.dumps(body.examples))
        )
        rowid = cur.lastrowid
        conn.commit(); conn.close()
        return next((h for h in get_entity_hints_list() if h["id"] == rowid), {})

    return await asyncio.to_thread(_create)

@router_vocab.put("/entity-hints/{hint_id}")
async def update_entity_hint(
    hint_id: int,
    body: EntityHintUpdate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn, get_entity_hints_list, reload
    import json as _json

    def _update():
        _ensure_loaded()
        conn = _get_conn()
        fields, values = [], []
        if body.label      is not None: fields.append("label=?");      values.append(body.label)
        if body.domain_key is not None: fields.append("domain_id=?");  values.append(body.domain_key)
        if body.doc_type_key is not None: fields.append("doc_type_id=?"); values.append(body.doc_type_key)
        if body.patterns   is not None: fields.append("patterns=?");   values.append(_json.dumps(body.patterns))
        if body.examples   is not None: fields.append("examples=?");   values.append(_json.dumps(body.examples))
        if body.is_active  is not None: fields.append("is_active=?");  values.append(int(body.is_active))
        if fields:
            values.append(hint_id)
            conn.execute(f"UPDATE entity_hints SET {', '.join(fields)} WHERE rowid=?", values)
            conn.commit()
        conn.close(); reload()
        return next((h for h in get_entity_hints_list() if h["id"] == hint_id), None)

    result = await asyncio.to_thread(_update)
    if result is None:
        raise HTTPException(404, f"Entity hint no encontrado: {hint_id}")
    return result

@router_vocab.delete("/entity-hints/{hint_id}", status_code=204)
async def delete_entity_hint(
    hint_id: int,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import _ensure_loaded, _get_conn, reload

    def _delete():
        _ensure_loaded()
        conn = _get_conn()
        cur = conn.execute("DELETE FROM entity_hints WHERE rowid=?", (hint_id,))
        deleted = cur.rowcount > 0
        conn.commit(); conn.close()
        if deleted: reload()
        return deleted

    if not await asyncio.to_thread(_delete):
        raise HTTPException(404, f"Entity hint no encontrado: {hint_id}")

# ── Vocabulario canónico ──────────────────────────────────────────────────────

@router_vocab.get("/vocabulary/lookup")   # ← NUEVO endpoint que faltaba en v2
async def lookup_vocabulary(
    tag: str,
    current_user: User = Depends(current_active_user),
):
    """
    Busca si un tag es canónico o alias.
    Respuesta: {canonical_tag, aliases, found}.
    IMPORTANTE: esta ruta debe estar ANTES de /vocabulary/{id} para que
    FastAPI no intente parsear 'lookup' como un integer id.
    """
    from app.brain.masters import lookup_vocabulary as _lookup, _ensure_loaded
    await asyncio.to_thread(_ensure_loaded)
    return await asyncio.to_thread(_lookup, tag)

@router_vocab.get("/vocabulary")
async def list_vocabulary(
    search_space_id: int = 0,
    q: str | None = None,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import get_vocabulary_list, _ensure_loaded
    await asyncio.to_thread(_ensure_loaded)
    return await asyncio.to_thread(get_vocabulary_list, q, search_space_id)

@router_vocab.post("/vocabulary", status_code=201)
async def create_vocabulary_entry(
    body: VocabCreate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import create_vocabulary_record
    return await asyncio.to_thread(
        create_vocabulary_record,
        body.canonical_tag, body.aliases, body.scope,
    )

@router_vocab.put("/vocabulary/{vocab_id}")
async def update_vocabulary_entry(
    vocab_id: int,
    body: VocabUpdate,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import update_vocabulary_record
    result = await asyncio.to_thread(
        update_vocabulary_record, vocab_id, body.aliases, body.is_active
    )
    if result is None:
        raise HTTPException(404, f"Entrada de vocabulario no encontrada: {vocab_id}")
    return result

@router_vocab.delete("/vocabulary/{vocab_id}", status_code=204)
async def delete_vocabulary_entry(
    vocab_id: int,
    current_user: User = Depends(current_active_user),
):
    from app.brain.masters import delete_vocabulary_record
    if not await asyncio.to_thread(delete_vocabulary_record, vocab_id):
        raise HTTPException(404, f"Entrada de vocabulario no encontrada: {vocab_id}")
```

---

## 8. Registrar routers en app.py (F6.B.22)

**Fichero:** `app/app.py` — añadir al final del fichero, DESPUÉS de la línea `app.include_router(brain_router)`:

```python
# Añadir DESPUÉS de: app.include_router(brain_router)
from app.routes.brain_admin_routes import router_admin as brain_admin_cfg_router  # noqa: E402
from app.routes.brain_admin_routes import router_vocab as brain_vocab_router       # noqa: E402
app.include_router(brain_admin_cfg_router)
app.include_router(brain_vocab_router)

# Añadir metadatos Swagger — busca la línea "app = FastAPI(" y añade openapi_tags:
# app.openapi_tags no existe como atributo — en su lugar, añadir en la definición FastAPI
# (está en la parte superior de app.py):
#
#   app = FastAPI(
#       ...,
#       openapi_tags=[
#           {"name": "brain",       "description": "Brain Chat, pasaportes, grafo, ingesta"},
#           {"name": "brain-admin", "description": "Config hot-reload y Ollama models"},
#           {"name": "brain-vocab", "description": "CRUD vocabulario: dominios, tipos, hints, tags"},
#           {"name": "health",      "description": "Health checks"},
#       ],
#   )
```

---

## 9. Verificación final

```bash
# 1. Verificar que hay ≥24 rutas Brain/Admin
curl -s http://localhost:8929/openapi.json | python3 -c "
import sys, json
data = json.load(sys.stdin)
brain = sorted([p for p in data['paths'] if any(x in p for x in ['/brain', '/admin/config', '/admin/ollama'])])
print(f'Brain/Admin endpoints: {len(brain)}')
for p in brain: print(f'  {p}')
"

# 2. Test health enriquecida
curl -s http://localhost:8929/api/v1/health
# → {"qdrant":"ok","ollama":"ok","postgresql":"ok","redis":"ok"}

# 3. Test stats (con token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8929/api/v1/brain/stats?search_space_id=1"

# 4. Test dominios
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8929/api/v1/brain/admin/domains?search_space_id=1"

# 5. Test vocabulary lookup
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8929/api/v1/brain/admin/vocabulary/lookup?tag=rag"

# 6. Swagger visual
open http://localhost:8929/docs
```

---

## 10. Errores comunes y soluciones

| Error | Causa | Solución |
|-------|-------|---------|
| `422 Unprocessable Entity` | Falta un campo requerido en el body | Revisar el modelo Pydantic del endpoint y el body que envía el frontend |
| `ImportError: cannot import name 'SearchSpaceMember'` | Nombre incorrecto del modelo | Usar `SearchSpaceMembership` (verificado en `app/db.py`) |
| `ImportError: cannot import name 'BrainDomain'` | Modelos no añadidos a db.py | Añadir los modelos de la sección 4 a `app/db.py` |
| `RuntimeError: no running event loop` | Llamada síncrona a `asyncio` fuera de contexto | Envolver la llamada en `asyncio.to_thread()` |
| SSE stream no llega al frontend | CORS o buffering nginx | Verificar header `X-Accel-Buffering: no` en el response |
| `404` en `/vocabulary/lookup` | FastAPI parsea "lookup" como id numérico | La ruta `/vocabulary/lookup` debe declararse ANTES de `/vocabulary/{id}` en el router |
| `sqlalchemy.exc.IntegrityError` en vocab CREATE | Tag canónico duplicado | El frontend debería verificar antes de crear; el backend devuelve 409 si se añade `try/except` |
