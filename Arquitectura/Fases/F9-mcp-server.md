# F9 — BrainSense MCP Server
**Duración:** 2 semanas  
**Equipo:** Backend Senior (1) + Frontend (0.5)  
**Dependencias:** F4 completada (mínimo), F5 recomendada  
**Entregable:** Servidor MCP desplegado como contenedor independiente que expone las capacidades de BrainSense a cualquier cliente MCP (Claude Desktop, Claude Code, Cursor, Gemini CLI). Sistema de Service Tokens para autenticación de larga duración. Tres agentes de negocio como Prompts MCP.

---

## Contexto

El MCP (Model Context Protocol) es el estándar abierto para conectar LLMs con herramientas externas. BrainSense como MCP server permite que cualquier miembro del equipo consulte la knowledge base corporativa directamente desde Claude Desktop, Claude Code o Cursor — sin abrir la UI de BrainSense.

El MCP server es un **contenedor ligero y separado** que actúa como proxy entre el protocolo MCP (JSON-RPC) y la API REST de BrainSense. No contiene lógica de negocio — solo traduce llamadas MCP a requests HTTP al backend FastAPI.

**Requisitos completos:** ver `BRAINSENSE-MCP-REQUISITOS.md`

---

## F9.0 — Estado real del codebase (Día 0)

### Endpoints que YA EXISTEN (tras F4)

| Endpoint | Fase | Usado por tool MCP |
|----------|------|-------------------|
| `POST /api/v1/brain/query` | F4 | `search_knowledge` |
| `GET /api/v1/brain/passport/{source}` | F4 | `get_passport` |
| `DELETE /api/v1/brain/document/{source}` | F4 | `delete_document` |
| `GET /health` | SurfSense | `get_system_health` |

### Endpoints que hay que CREAR

| Endpoint | Para qué | Usado por |
|----------|---------|-----------|
| `GET /api/v1/brain/list` | Lista documentos con filtros | `list_documents` tool |
| `GET /api/v1/brain/graph` | Grafo de relaciones JSON | `get_graph_relations` tool + resource |
| `GET /api/v1/brain/stats` | Estado colecciones Qdrant | `get_system_health` tool |
| `POST /api/v1/brain/ingest/url` | Ingestar URL desde chat | `ingest_url` tool |
| `POST /api/v1/brain/{source}/resynthesize` | Re-generar pasaporte | `resynthesize_document` tool |
| `GET /api/v1/admin/config` | Config activa del sistema | `get_config` tool |
| `GET /api/v1/admin/ollama-models` | Modelos Ollama disponibles | `list_ollama_models` tool |
| `POST /api/v1/mcp/tokens` | Crear service token | Admin UI |
| `GET /api/v1/mcp/tokens` | Listar tokens activos | Admin UI |
| `DELETE /api/v1/mcp/tokens/{id}` | Revocar token | Admin UI |
| `GET /api/v1/mcp/tokens/{id}/audit` | Historial de uso | Admin UI |

### Lo que F9 debe CREAR (ficheros nuevos)

| Componente | Fichero | Descripción |
|-----------|---------|-------------|
| MCP Server | `brainsense-mcp/mcp_server.py` | Servidor MCP completo (FastMCP) |
| Dockerfile MCP | `brainsense-mcp/Dockerfile` | Imagen Python 3.11-slim ultraligera |
| Docker Compose entry | `docker/docker-compose.dev.yml` | Servicio `mcp-server` |
| Modelo ServiceToken | `app/db.py` (ampliar) | Tabla `service_tokens` |
| Migración Alembic | `alembic/versions/XXX_service_tokens.py` | Crear tabla |
| Rutas MCP tokens | `app/routes/mcp_token_routes.py` | CRUD de service tokens |
| Middleware auth dual | `app/middleware/dual_auth.py` | JWT + ServiceToken |
| Rutas Brain extras | `app/routes/brain_routes.py` (ampliar) | list, graph, stats, ingest_url |
| Tests | `tests/brain/test_mcp_f9.py` | Tests del MCP + tokens |

---

## F9.1 — Modelo `ServiceToken` y migración (Día 1)

**Fichero:** `surfsense_backend/app/db.py` ← **AMPLIAR**

```python
class ServiceToken(BaseModel, TimestampMixin):
    """
    Token de servicio para autenticación MCP de larga duración.

    A diferencia de los JWT de usuario (caducan en horas), los service tokens
    están diseñados para clientes MCP de uso continuado: Claude Desktop,
    Claude Code, pipelines CI/CD.

    Seguridad:
      - Solo Admin puede crear/revocar tokens
      - Se almacena SOLO el hash SHA-256 del token (no el token en claro)
      - El token se muestra UNA SOLA VEZ al crearlo
      - Cada token accede solo a los Search Spaces asignados
      - Revocación inmediata — el token deja de funcionar al instante

    Formato del token: bs_{nombre_corto}_{random_urlsafe_32}
    Ejemplo: bs_carlos_xK9mPqR7vL2nW8sT6yU1oP3aB5cD0eF
    """
    __tablename__ = "service_tokens"

    name           = Column(String(200), nullable=False)      # "Claude Desktop - Carlos"
    token_hash     = Column(String(64), nullable=False, unique=True, index=True)  # SHA-256
    token_prefix   = Column(String(20), nullable=False)       # "bs_carlos_" — para identificar sin exponer
    search_spaces  = Column(ARRAY(Integer), nullable=False)   # IDs de SearchSpace accesibles
    created_by_id  = Column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False)
    expires_at     = Column(DateTime(timezone=True), nullable=False)
    last_used_at   = Column(DateTime(timezone=True), nullable=True)
    revoked        = Column(Boolean, default=False, nullable=False)
    revoked_at     = Column(DateTime(timezone=True), nullable=True)

    # Relación con User
    created_by     = relationship("User")
```

**Migración Alembic:** `alembic/versions/XXX_create_service_tokens.py`

```python
"""Create service_tokens table for MCP authentication.

Service tokens provide long-lived authentication for MCP clients
(Claude Desktop, Claude Code, CI/CD pipelines). Only the SHA-256
hash is stored — the raw token is shown once at creation time.
"""
```

---

## F9.2 — Modelo `TokenAuditLog` (Día 1)

```python
class TokenAuditLog(BaseModel, TimestampMixin):
    """
    Registro de auditoría de cada uso de un service token.

    Cada llamada al MCP server queda registrada para:
    - Detectar uso anómalo (frecuencia inusual, espacios no habituales)
    - Compliance: saber quién consultó qué y cuándo
    - Debugging: correlacionar errores con llamadas específicas
    """
    __tablename__ = "token_audit_logs"

    token_id       = Column(Integer, ForeignKey("service_tokens.id"), nullable=False, index=True)
    tool_name      = Column(String(100), nullable=False)        # "search_knowledge", "get_passport"
    search_space_id = Column(Integer, nullable=True)            # Space consultado
    parameters     = Column(JSONB, nullable=True)               # Parámetros de la llamada (sin datos sensibles)
    response_status = Column(String(20), nullable=False)        # "ok", "error", "unauthorized"
    response_time_ms = Column(Integer, nullable=True)           # Latencia en ms
    ip_address     = Column(String(45), nullable=True)          # IP del cliente (VPN)

    token          = relationship("ServiceToken")
```

---

## F9.3 — Middleware de autenticación dual (Día 2)

**Fichero:** `surfsense_backend/app/middleware/dual_auth.py` ← **CREAR**

El backend debe aceptar tanto JWT de usuario (flujo SurfSense existente) como service tokens (nuevo para MCP).

```python
"""
dual_auth.py
-----------
Middleware de autenticación que acepta dos tipos de credencial:

1. JWT de usuario (ya existe en SurfSense)
   - Viene de login email/password
   - Caduca en horas
   - Para uso humano en UI

2. Service Token (nuevo — F9)
   - Formato: bs_{nombre}_{random_32}
   - Se valida por hash SHA-256 contra la tabla service_tokens
   - Caduca en días/meses (configurable al crear)
   - Para clientes MCP y CI/CD

Flujo de validación:
  Authorization: Bearer <token>
      │
      ├─ Empieza con "bs_" → validar como service token
      │     → SHA-256(token) → buscar en service_tokens
      │     → verificar: no revocado, no expirado
      │     → verificar: search_space_id en token.search_spaces
      │     → registrar en token_audit_logs
      │     → devolver TokenPayload(is_service_token=True, ...)
      │
      └─ No empieza con "bs_" → validar como JWT (flujo existente)
            → verify_jwt(token) → devolver TokenPayload(is_service_token=False, ...)
"""
```

**Integración con el sistema de auth existente:**
El middleware se inyecta como alternativa a `current_active_user` en las rutas que deben aceptar ambos tipos de token. Las rutas de Brain (`/api/v1/brain/*`) aceptan ambos. Las rutas de admin de tokens (`/api/v1/mcp/tokens`) solo aceptan JWT de Admin.

---

## F9.4 — Rutas CRUD de Service Tokens (Día 2-3)

**Fichero:** `surfsense_backend/app/routes/mcp_token_routes.py` ← **CREAR**

```python
"""
mcp_token_routes.py
-------------------
CRUD de Service Tokens para autenticación MCP.

Endpoints:
  POST   /api/v1/mcp/tokens          — crear token (solo Admin)
  GET    /api/v1/mcp/tokens          — listar tokens activos (solo Admin)
  DELETE /api/v1/mcp/tokens/{id}     — revocar token (solo Admin)
  GET    /api/v1/mcp/tokens/{id}/audit — historial de uso

Seguridad:
  - TODOS los endpoints requieren rol Admin (no service token)
  - El token generado se muestra UNA SOLA VEZ en la respuesta de POST
  - Solo se almacena el hash SHA-256
  - La revocación es inmediata y permanente
"""

# POST /api/v1/mcp/tokens
# Request: {"name": "Claude Desktop - Carlos", "search_spaces": [1, 3], "expires_days": 365}
# Response: {"id": 1, "token": "bs_carlos_xK9m...", "expires_at": "...", "WARNING": "token shown once"}

# GET /api/v1/mcp/tokens
# Response: [{"id": 1, "name": "...", "prefix": "bs_carlos_", "expires_at": "...", "last_used": "..."}]
# NOTA: nunca devuelve el token ni el hash

# DELETE /api/v1/mcp/tokens/{id}
# Response: {"status": "revoked"}

# GET /api/v1/mcp/tokens/{id}/audit
# Response: [{"timestamp": "...", "tool": "search_knowledge", "space": 1, "status": "ok", "ms": 230}]
```

---

## F9.5 — Endpoints Brain adicionales (Día 3-5)

**Fichero:** `surfsense_backend/app/routes/brain_routes.py` ← **AMPLIAR**

Endpoints que el MCP server necesita y aún no existen:

### `GET /api/v1/brain/list`

```python
@router.get("/list")
async def list_brain_documents(
    search_space_id: int,
    domain: str | None = None,
    doc_type: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    current_user = Depends(dual_auth),
):
    """
    Lista documentos del Brain con filtros opcionales.

    Devuelve metadatos del pasaporte: source, título, dominio, tags,
    fecha de síntesis, quality_score promedio. NO devuelve el contenido.

    Usado por: MCP tool `list_documents`, UI Brain Wiki, UI Brain Home.
    """
```

### `GET /api/v1/brain/graph`

```python
@router.get("/graph")
async def get_brain_graph(
    search_space_id: int,
    current_user = Depends(dual_auth),
):
    """
    Grafo de relaciones entre documentos del Brain.

    Devuelve nodos (documentos) y aristas (relaciones semánticas)
    en formato JSON para vis.js / D3.js.

    Usado por: MCP tool `get_graph_relations`, MCP resource `brainsense://graph`,
    UI Brain Graph.
    """
```

### `GET /api/v1/brain/stats`

```python
@router.get("/stats")
async def get_brain_stats(
    current_user = Depends(dual_auth),
):
    """
    Estado de las colecciones Qdrant y estadísticas del Brain.

    Devuelve: nº vectores por colección (brain/knowledge/code),
    nº pasaportes .md, modelos activos, estado de servicios.

    Usado por: MCP tool `get_system_health`, UI Brain Metrics.
    """
```

### `POST /api/v1/brain/ingest/url`

```python
@router.post("/ingest/url")
async def ingest_url(
    url: str,
    search_space_id: int,
    synthesis_model: str | None = None,
    current_user = Depends(dual_auth),
):
    """
    Ingesta una URL externa: descarga HTML → extracción → síntesis → Qdrant.

    El procesamiento es asíncrono (tarea Celery). Devuelve el task_id
    para polling del estado.

    Usado por: MCP tool `ingest_url`, UI Brain Ingest.
    """
```

### `POST /api/v1/brain/{source}/resynthesize`

```python
@router.post("/{source}/resynthesize")
async def resynthesize_document(
    source: str,
    model: str | None = None,
    current_user = Depends(dual_auth),
):
    """
    Re-genera el pasaporte .md de un documento existente.

    Útil cuando se cambia de modelo LLM o se mejora el prompt de síntesis.
    El procesamiento es asíncrono (tarea Celery).

    Usado por: MCP tool `resynthesize_document`, UI Brain Admin.
    """
```

---

## F9.6 — MCP Server (contenedor independiente) (Día 5-8)

**Directorio:** `brainsense-mcp/` ← **CREAR** en la raíz del proyecto

### Estructura de ficheros

```
brainsense-mcp/
├── mcp_server.py               ← servidor MCP completo (FastMCP)
├── requirements.txt            ← fastmcp>=3.4.0, httpx>=0.27.0
├── Dockerfile                  ← Python 3.11-slim
├── README.md                   ← guía de instalación y uso
└── claude_desktop_config.json  ← plantilla de configuración
```

### `mcp_server.py` — diseño

```python
"""
BrainSense MCP Server
---------------------
Servidor MCP que expone las capacidades de BrainSense como herramientas
invocables desde cualquier cliente MCP compatible.

ARQUITECTURA:
  El MCP server es un PROXY ultraligero:
  - Recibe llamadas MCP (JSON-RPC) de clientes (Claude Desktop, etc.)
  - Las traduce a HTTP requests al backend FastAPI de BrainSense
  - Devuelve las respuestas al cliente MCP

  NO contiene lógica de negocio — toda la lógica está en el backend.
  Si el backend evoluciona, el MCP server no necesita cambios.

TOOLS (10):
  Búsqueda:   search_knowledge, get_passport, list_documents, get_graph_relations
  Gestión:    ingest_url, resynthesize_document, delete_document
  Admin:      get_system_health, get_config, list_ollama_models

RESOURCES (2):
  brainsense://passport/{source}  — pasaporte .md
  brainsense://graph              — grafo completo JSON

PROMPTS (3):
  onboarding_agent       — guía técnica para nuevos miembros
  code_review_agent      — revisión de coherencia arquitectónica
  incident_analysis_agent — análisis de incidencias de producción

TRANSPORTE:
  stdio — para Claude Desktop / Claude Code (local)
  HTTP  — para acceso en red VPN (flag --http, puerto 9000)

VARIABLES:
  BRAINSENSE_URL      — URL del backend (default: http://localhost:8000)
  BRAINSENSE_TOKEN    — Service token o JWT para autenticación
  BRAINSENSE_SPACE    — Search Space ID por defecto
  BRAINSENSE_TIMEOUT  — Timeout HTTP en segundos (default: 30)
  MCP_PORT            — Puerto en modo HTTP (default: 9000)
"""
from fastmcp import FastMCP
import httpx
import os

mcp = FastMCP("BrainSense")

BRAINSENSE_URL     = os.getenv("BRAINSENSE_URL", "http://localhost:8000")
BRAINSENSE_TOKEN   = os.getenv("BRAINSENSE_TOKEN", "")
BRAINSENSE_SPACE   = os.getenv("BRAINSENSE_SPACE", "")
BRAINSENSE_TIMEOUT = int(os.getenv("BRAINSENSE_TIMEOUT", "30"))

def _headers():
    return {"Authorization": f"Bearer {BRAINSENSE_TOKEN}"}

def _space():
    return int(BRAINSENSE_SPACE) if BRAINSENSE_SPACE else None

# ── TOOLS ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def search_knowledge(question: str, top_k: int = 5, force_level: int | None = None) -> str:
    """Search BrainSense knowledge base using multinivel router (L1→L2→BM25→Web→L0).
    Returns answer with sources and confidence level."""
    async with httpx.AsyncClient(timeout=BRAINSENSE_TIMEOUT) as c:
        r = await c.post(f"{BRAINSENSE_URL}/api/v1/brain/query",
            headers=_headers(),
            json={"question": question, "search_space_id": _space(),
                  "top_k": top_k, "force_level": force_level})
        r.raise_for_status()
        data = r.json()
        return (f"{data['level_label']} | Sources: {', '.join(data.get('sources', []))}\n\n"
                f"{data['answer']}")

@mcp.tool()
async def get_passport(source: str) -> str:
    """Get the semantic passport (.md) of a document. Contains structured
    summary, core knowledge, entities, and quality metadata."""
    async with httpx.AsyncClient(timeout=BRAINSENSE_TIMEOUT) as c:
        r = await c.get(f"{BRAINSENSE_URL}/api/v1/brain/passport/{source}",
            headers=_headers())
        r.raise_for_status()
        return r.json()["passport_md"]

# ... (list_documents, get_graph_relations, ingest_url, resynthesize_document,
#      delete_document, get_system_health, get_config, list_ollama_models)

# ── RESOURCES ─────────────────────────────────────────────────────────────

@mcp.resource("brainsense://passport/{source}")
async def passport_resource(source: str) -> str:
    """Semantic passport for document {source}."""
    return await get_passport(source)

@mcp.resource("brainsense://graph")
async def graph_resource() -> str:
    """Complete document relationship graph as JSON."""
    async with httpx.AsyncClient(timeout=BRAINSENSE_TIMEOUT) as c:
        r = await c.get(f"{BRAINSENSE_URL}/api/v1/brain/graph",
            headers=_headers(), params={"search_space_id": _space()})
        r.raise_for_status()
        return r.text

# ── PROMPTS (Agentes de negocio) ──────────────────────────────────────────

@mcp.prompt()
def onboarding_agent(developer_name: str, project: str, role: str = "backend") -> str:
    """Technical onboarding guide for new team members. Searches the knowledge
    base to answer questions about architecture, code and processes."""
    return (f"Eres un mentor técnico experto. Estás haciendo onboarding a {developer_name}, "
            f"nuevo {role} en el proyecto {project}.\n\n"
            f"Usa search_knowledge para buscar información relevante en la knowledge base.\n"
            f"Usa get_passport para mostrar pasaportes de módulos específicos.\n\n"
            f"Guía al desarrollador paso a paso. Cita fuentes de la knowledge base.")

@mcp.prompt()
def code_review_agent(pr_description: str, changed_files: str) -> str:
    """Semantic code review: checks if a PR is consistent with documented
    architecture decisions (ADRs). Does NOT review syntax — reviews semantic coherence."""
    return (f"Eres un revisor de código semántico. Tu trabajo NO es revisar sintaxis "
            f"sino verificar que los cambios son coherentes con las decisiones de "
            f"arquitectura documentadas.\n\n"
            f"PR: {pr_description}\nFicheros cambiados: {changed_files}\n\n"
            f"1. Usa search_knowledge para buscar ADRs y decisiones relevantes\n"
            f"2. Compara los cambios con las decisiones documentadas\n"
            f"3. Señala incoherencias o validaciones")

@mcp.prompt()
def incident_analysis_agent(incident_description: str, affected_system: str) -> str:
    """Production incident analysis: searches for similar past incidents,
    related runbooks, and implicated code. Generates initial analysis."""
    return (f"Eres un ingeniero de guardia analizando una incidencia de producción.\n\n"
            f"Incidencia: {incident_description}\nSistema afectado: {affected_system}\n\n"
            f"1. Usa search_knowledge para buscar incidencias similares previas\n"
            f"2. Busca runbooks relacionados con el sistema afectado\n"
            f"3. Busca el código del módulo implicado con get_passport\n"
            f"4. Genera un análisis con posibles causas y pasos de resolución")

# ── Arranque ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        port = int(os.getenv("MCP_PORT", "9000"))
        mcp.run(transport="streamable-http", port=port)
    else:
        mcp.run()  # modo stdio para Claude Desktop
```

### `Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_server.py .
EXPOSE 9000
CMD ["python", "mcp_server.py", "--http"]
```

### `requirements.txt`

```
fastmcp>=3.4.0
httpx>=0.27.0
```

---

## F9.7 — Docker Compose entry (Día 5)

**Fichero:** `docker/docker-compose.dev.yml` ← **AMPLIAR**

```yaml
mcp-server:
  build:
    context: ../brainsense-mcp
    dockerfile: Dockerfile
  container_name: sbs-dev-mcp
  restart: unless-stopped
  ports:
    - "9000:9000"
  environment:
    BRAINSENSE_URL: "http://sbs-dev-backend:8929"
    BRAINSENSE_TOKEN: "${MCP_SERVICE_TOKEN}"
    BRAINSENSE_SPACE: "${DEFAULT_SPACE_ID}"
    MCP_PORT: "9000"
    BRAINSENSE_TIMEOUT: "30"
  depends_on:
    backend:
      condition: service_healthy
  networks:
    - default
```

**Variables en `.env`:**

```bash
# ── MCP Server F9 ──────────────────────────────────────────────────────────
MCP_SERVICE_TOKEN=              # Se genera con POST /api/v1/mcp/tokens tras primer arranque
DEFAULT_SPACE_ID=1              # Search Space por defecto para el MCP
```

---

## F9.8 — Registrar rutas en `app.py` (Día 3)

```python
# En app.py, junto al resto de include_router:
from app.routes.mcp_token_routes import router as mcp_token_router  # noqa: E402
app.include_router(mcp_token_router)
# F9 — CRUD de Service Tokens MCP. Monta en /api/v1/mcp/*
```

---

## F9.9 — Tests (Día 8-10)

**Fichero:** `tests/brain/test_mcp_f9.py`

| Clase | Tests | Qué cubre |
|-------|-------|-----------|
| `TestServiceTokenModel` | 5 | Creación, hash SHA-256, prefijo, search_spaces array, unicidad |
| `TestServiceTokenCRUD` | 8 | POST crear, GET listar, DELETE revocar, token shown once, solo Admin |
| `TestDualAuth` | 7 | JWT válido → ok, service token válido → ok, token revocado → 401, expirado → 401, space no autorizado → 403 |
| `TestTokenAuditLog` | 4 | Registro de cada uso, tool_name correcto, response_time_ms |
| `TestBrainListEndpoint` | 5 | Filtros domain/type/tag, paginación, search_space_id |
| `TestBrainGraphEndpoint` | 3 | JSON válido con nodes/edges, filtrado por space |
| `TestBrainStatsEndpoint` | 3 | Conteo colecciones Qdrant, modelos activos |
| `TestBrainIngestURLEndpoint` | 4 | URL válida → tarea Celery, URL inválida → 400, auth requerida |
| `TestMCPServerTools` | 6 | search_knowledge, get_passport, list_documents, ingest_url (mock httpx) |
| `TestMCPServerResources` | 3 | passport resource, graph resource |
| `TestMCPServerPrompts` | 3 | onboarding, code_review, incident_analysis |
| **Total** | **51** | |

---

## F9.10 — Orden de implementación

| Día | Tarea | Riesgo |
|-----|-------|--------|
| 1 | F9.1 Modelo ServiceToken + migración Alembic | Bajo |
| 1 | F9.2 Modelo TokenAuditLog | Bajo |
| 2 | F9.3 Middleware dual_auth.py | Medio |
| 2-3 | F9.4 Rutas CRUD tokens (mcp_token_routes.py) | Bajo |
| 3-5 | F9.5 Endpoints Brain adicionales (list, graph, stats, ingest_url, resynthesize) | Medio |
| 5-8 | F9.6 MCP Server completo (mcp_server.py + Dockerfile) | Medio |
| 5 | F9.7 Docker Compose entry | Bajo |
| 3 | F9.8 Registrar rutas en app.py | Bajo |
| 8-10 | F9.9 Tests | Bajo |

---

## Checklist F9

### F9.1-F9.2 — Modelos de datos
- [ ] `ServiceToken` en db.py con hash SHA-256, search_spaces, expires_at, revoked
- [ ] `TokenAuditLog` en db.py con tool_name, search_space_id, response_time_ms
- [ ] Migración Alembic creada y ejecutada

### F9.3 — Autenticación dual
- [ ] JWT de usuario → validación existente (sin cambios)
- [ ] Service token (`bs_*`) → hash → lookup en service_tokens → validar
- [ ] Token revocado → 401 inmediato
- [ ] Token expirado → 401
- [ ] Space no autorizado → 403
- [ ] Cada uso registrado en token_audit_logs

### F9.4 — CRUD de tokens
- [ ] POST crear — solo Admin, token mostrado una sola vez
- [ ] GET listar — solo Admin, sin hash ni token en respuesta
- [ ] DELETE revocar — inmediato, solo Admin
- [ ] GET audit — historial de uso con timestamps

### F9.5 — Endpoints Brain
- [ ] GET /brain/list — filtros domain/type/tag, paginación
- [ ] GET /brain/graph — JSON con nodes/edges para vis.js
- [ ] GET /brain/stats — conteo colecciones Qdrant
- [ ] POST /brain/ingest/url — tarea Celery asíncrona
- [ ] POST /brain/{source}/resynthesize — tarea Celery asíncrona

### F9.6 — MCP Server
- [ ] 10 tools implementadas y funcionales
- [ ] 2 resources implementados
- [ ] 3 prompts (agentes de negocio) implementados
- [ ] Modo stdio funcional (Claude Desktop)
- [ ] Modo HTTP funcional (red VPN, puerto 9000)
- [ ] Dockerfile construye correctamente
- [ ] README con guía de instalación

### F9.7 — Docker Compose
- [ ] Servicio `mcp-server` arranca y se conecta al backend
- [ ] Puerto 9000 expuesto
- [ ] Depende de backend healthy

### F9.9 — Tests
- [ ] 51+ tests pasando
- [ ] Tests de auth dual cubren JWT + service token + revocado + expirado

### Criterio de aceptación global F9
- [ ] Claude Desktop conectado al MCP server puede hacer `search_knowledge("¿cómo funciona el pipeline?")` y recibe respuesta con sources
- [ ] Un service token revocado deja de funcionar inmediatamente
- [ ] El audit trail registra cada llamada con timestamp, tool y space
- [ ] MCP Inspector (`npx @modelcontextprotocol/inspector`) muestra las 10 tools + 2 resources + 3 prompts
- [ ] El contenedor `mcp-server` arranca con `docker compose up` sin errores
- [ ] Un service token con acceso al Space 1 NO puede consultar el Space 2

---

## Verificación end-to-end

```bash
# 1. Crear un service token desde la API
curl -X POST http://localhost:8929/api/v1/mcp/tokens \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test MCP", "search_spaces": [1], "expires_days": 30}'
# → {"id": 1, "token": "bs_test_xK9m...", "expires_at": "..."}

# 2. Configurar el MCP server con ese token
export MCP_SERVICE_TOKEN="bs_test_xK9m..."
export DEFAULT_SPACE_ID=1

# 3. Levantar el MCP server
docker compose -f docker/docker-compose.dev.yml up mcp-server -d

# 4. Verificar con MCP Inspector
npx @modelcontextprotocol/inspector -- \
  docker exec sbs-dev-mcp python mcp_server.py

# 5. Verificar desde Claude Desktop
# Configurar claude.json apuntando al MCP server
# Preguntar: "¿cómo funciona el pipeline de ingesta?"
# Claude debe invocar search_knowledge y devolver respuesta con sources
```

---

**Anterior:** [F8 — Agentes Especializados](./F8-agentes-especializados.md)  
**Volver al plan maestro:** [00 — Plan Maestro](./00-plan-maestro.md)
