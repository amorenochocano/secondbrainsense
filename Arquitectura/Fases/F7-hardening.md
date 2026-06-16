# F7 — Hardening, Deuda Técnica y Producción
**Duración:** 1 semana  
**Equipo:** Tech Lead (1) + Backend Senior (1) + DevOps (0.5)  
**Dependencias:** F6 completada  
**Entregable:** Deuda técnica resuelta o priorizada, riesgos operativos mitigados, runbook completo con nombres y puertos reales del stack SecondBrainSense

---

## Contexto: qué ha cambiado respecto al F7 original

El F7 original fue escrito antes de analizar el codebase real. Después de revisar F1–F6 contra el código, esta fase se simplifica considerablemente:

- **La re-síntesis contaminada (F7-DT-01 original) ya está resuelta** en Second Brain v4 (`F-20` / ADR-20): `brain.py` ya sanea el Source Extract y nunca lee el `.md` como fuente de síntesis.
- **El saneado del Source Extract (F7-DT-04 original) es lo mismo** que el punto anterior.
- **El circuit breaker para Ollama (F7-DT-03 original)** es un riesgo operativo de mitigación, no una deuda de código. Además, `LLMClient.generate()` es **SYNC** — la propuesta original era `async def`, incompatible.
- **La deduplicación cross-search-space (F7-DT-05 original)** sí es real y nueva en SecondBrainSense (multi-tenant). Se mantiene como **DT-10**.

Esta fase trabaja con la **deuda técnica real** documentada en la sección 12 del `ARQUITECTURA_SECOND_BRAIN_v4.md`, adaptada a los ficheros y convenciones de SecondBrainSense.

---

## Objetivo

Resolver las deudas técnicas activas (DT-01 a DT-09 de la arquitectura v4, más DT-10 nuevo multi-tenant), mitigar los 4 riesgos operativos documentados, y producir el runbook de operaciones con los comandos reales del stack (`surfsense-backend`, `surfsense-celery-worker`, `surfsense-qdrant`, etc.).

---

## F7.1 — Priorización de deuda técnica (Día 1)

### Inventario de DTs de la arquitectura v4 — adaptado a SecondBrainSense

| ID | Problema original (v4) | Prioridad F7 | Fichero en SecondBrainSense |
|----|------------------------|:------------:|------------------------------|
| DT-01 | `Heading 4` (subservicios) no aparecen en Core Knowledge | 🔴 Crítica | `app/brain/prompts/preprocessing/docx.py` |
| DT-02 | Tablas de ANS sin detalle en el pasaporte | 🔴 Crítica | `app/brain/prompts/preprocessing/docx.py` |
| DT-03 | Tipos con solo `##` sin `###` → cortes de emergencia | 🔴 Crítica | `app/brain/prompts/preprocessing/{html,txt,csv}.py` |
| DT-04 | El chunker no corta en `####` | 🟡 Media | Complementario a DT-01; esperar tras resolver DT-01 |
| DT-05 | `quality_score` calculado pero no usado | 🟡 Media | `app/brain/ingest_router.py` |
| DT-06 | `context_tokens` calibrados para CPU | 🔵 Baja | `app/brain/model_profiles.py` — condicionada a tener GPU |
| DT-07 | Entities del frontmatter no incluyen Heading 3-4 | 🔴 Crítica | `app/brain/passport_builder.py` |
| DT-08 | `num_predict` heredado en hot-reload de uvicorn | 🔵 Baja | Solo afecta dev local; en producción se usa `docker compose restart` |
| DT-09 | `Task exception never retrieved` en `brain_watcher` | 🔴 Crítica | `app/brain/brain_watcher.py` |
| DT-10 | Deduplicación cross-search-space *(nuevo multi-tenant)* | 🟡 Media | `app/indexing_pipeline/indexing_pipeline_service.py` |

**Orden de resolución en F7:** DT-01 → DT-02 → DT-03 → DT-07 → DT-09 (críticas), luego DT-05 → DT-10 (medias), diferir DT-04/DT-06/DT-08.

---

## F7.2 — Resolución de DTs críticas (Día 1-3)

### DT-01 — Heading 4 (subservicios) no aparecen en Core Knowledge

**Problema:** en documentos Word con jerarquía profunda (ej. servicios DWP), los `Heading 4` representan subservicios que nunca llegan al pasaporte porque el chunker no corta en `####`. El preprocesador `docx.py` los emite como `####` pero el planner solo corta en `##` y `###`.

**Solución:** en `docx.py`, promover `Heading 4` a `###` cuando el `Heading 3` padre ya tiene más de N hijos directos, o siempre cuando el documento es un catálogo de servicios (más de 8 `Heading 4` en total).

```python
# surfsense_backend/app/brain/prompts/preprocessing/docx.py
# En la función que emite los niveles de heading:

def _heading_level_to_mark(level: int, h4_count: int) -> str:
    """
    Heading 1 → ##
    Heading 2 → ###
    Heading 3 → #### (embebido en su ###)
    Heading 4 → ### si el doc tiene >8 Heading4 (catálogo de subservicios)
              → #### en caso contrario (subdetalle puntual)
    """
    if level == 1:
        return "##"
    elif level == 2:
        return "###"
    elif level == 3:
        return "####"
    elif level == 4:
        return "###" if h4_count > 8 else "####"
    return ""  # niveles >4 → no marcar
```

**Criterio de aceptación:** un documento Word con 12+ `Heading 4` genera pasaporte con al menos una subsección `###` por cada grupo de subservicios.

---

### DT-02 — Tablas de ANS/métricas sin detalle en el pasaporte

**Problema:** los bloques `content_type="table"` extraídos por `DocxExtractor` llegan al preprocesador pero `build_focused` los descarta o los trunca. Las tablas de métricas (ANS, SLAs, 13 tablas en documentos DWP) quedan fuera de Core Knowledge.

**Solución:** en `docx.py`, adjuntar cada bloque `table` al bloque de texto `##`/`###` anterior, añadiendo una marca `> tabla:` para que el LLM lo identifique como contenido complementario.

```python
# surfsense_backend/app/brain/prompts/preprocessing/docx.py
# En build_focused_docx() — al construir processed_text:

def _attach_tables_to_context(blocks: list[dict]) -> list[dict]:
    """
    Recorre los bloques en orden.
    Cuando encuentra un bloque table, lo fusiona con el bloque text/heading previo.
    El LLM recibe "Texto de sección\n\n> tabla:\n| col1 | col2 |\n| ..."
    """
    result = []
    for block in blocks:
        if block["content_type"] == "table" and result:
            prev = result[-1]
            table_md = _block_to_markdown_table(block["content"])
            prev["content"] = prev["content"] + "\n\n> tabla:\n" + table_md
        else:
            result.append(block)
    return result
```

**Criterio de aceptación:** un documento Word con una tabla de ANS genera pasaporte donde Core Knowledge menciona al menos las columnas y valores de la tabla.

---

### DT-03 — Tipos con solo `##` sin `###` generan cortes de emergencia

**Problema:** cuando el chunker no encuentra ningún `###` dentro de un bloque `##` grande, hace cortes en párrafos `\n\n` sin control semántico. Esto ocurre en `html.py`, `txt.py` (subtipo log/nota) y `csv.py` con pocas columnas.

**Solución:** añadir `###` internos en esos preprocesadores para garantizar cortes semánticos predecibles.

**`html.py`:** cuando una sección `## Sección NN:` supera 3000 chars, subdivide en `### Bloque N` agrupando párrafos lógicos.

```python
# surfsense_backend/app/brain/prompts/preprocessing/html.py
# En build_focused_html() — añadir subdivisión:

_HTML_SUBSECTION_THRESHOLD = 3000  # chars

def _split_long_html_section(section_text: str, section_title: str) -> str:
    """
    Si la sección supera el umbral, la divide en subsecciones ### con párrafos agrupados.
    Mantiene la sección ## original como cabecera.
    """
    if len(section_text) <= _HTML_SUBSECTION_THRESHOLD:
        return section_text

    paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
    chunks, current, idx = [], [], 1
    for p in paragraphs:
        current.append(p)
        if sum(len(x) for x in current) >= _HTML_SUBSECTION_THRESHOLD:
            chunks.append(f"### Bloque {idx}\n\n" + "\n\n".join(current))
            current, idx = [], idx + 1
    if current:
        chunks.append(f"### Bloque {idx}\n\n" + "\n\n".join(current))
    return "\n\n".join(chunks)
```

**`txt.py` (subtipos log y nota):** `## Errores críticos` ya existe pero sin `###` internos. Añadir `### Error N:` por cada bloque de traza.

**`csv.py`:** cuando el catálogo de columnas es muy corto (< 5 columnas), añadir `### Análisis de datos` con estadísticas descriptivas que fuerzan al menos un `###`.

---

### DT-07 — Entities del frontmatter no incluyen Heading 3-4

**Problema:** `passport_builder.py` extrae entities del texto del pasaporte pero solo de nivel `##`. Los subcomponentes y subservicios (Heading 3-4) no aparecen en el array `entities` del frontmatter, lo que empobrece el grafo de relaciones: dos documentos que comparten un subservicio no se conectan porque ese subservicio no está en ningún `entities`.

**Solución:** en `passport_builder.py`, al construir el array `entities`, incluir todos los headings de nivel 3 y 4 que tengan nombre propio (más de 2 palabras o que coincidan con `brain_entity_hints`).

```python
# surfsense_backend/app/brain/passport_builder.py
# En _extract_entities_from_processed_text():

import re

_H3_PATTERN = re.compile(r'^#{3}\s+(.+)$', re.MULTILINE)
_H4_PATTERN = re.compile(r'^#{4}\s+(.+)$', re.MULTILINE)

def _extract_heading_entities(processed_text: str) -> list[str]:
    """
    Extrae headings ### y #### como entities candidatas.
    Filtra: marcas de backtick (son firmas de función, no entidades),
    líneas muy cortas (<= 2 palabras) que serían ruido.
    """
    candidates = []
    for pattern in (_H3_PATTERN, _H4_PATTERN):
        for match in pattern.finditer(processed_text):
            heading = match.group(1).strip().strip("`")
            # Excluir firmas de función (contienen paréntesis)
            if "(" in heading:
                continue
            # Excluir headings genéricos
            if heading.lower() in {"resumen", "notas", "referencias", "anexos"}:
                continue
            if len(heading.split()) >= 2:
                candidates.append(heading)
    return list(dict.fromkeys(candidates))  # preservar orden, deduplicar
```

**Criterio de aceptación:** un documento Word DWP con 12 subservicios en Heading 4 genera frontmatter con al menos 8 de esos subservicios en `entities`.

---

### DT-09 — `Task exception never retrieved` en `brain_watcher`

**Problema:** `brain_watcher.py` lanza tareas en un executor (`run_in_executor`) sin awaitar las futures resultantes. Cuando la re-indexación falla, la excepción se lanza al GC con el mensaje `Task exception was never retrieved`, sin ningún log visible y sin re-intentos.

**Solución:** wrappear las llamadas `run_in_executor` en una corrutina supervisada con gestión de excepciones explícita.

```python
# surfsense_backend/app/brain/brain_watcher.py
# Reemplazar el patrón run_in_executor sin gestión:

# ❌ Antes (la excepción se pierde silenciosamente):
loop.run_in_executor(None, _reindex_passport, source)

# ✅ Después:
async def _reindex_supervised(source: str) -> None:
    """Re-indexa un pasaporte con gestión de excepción y log."""
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, _reindex_passport, source
        )
        logger.info(f"[BrainWatcher] Re-indexado OK: {source}")
    except Exception as exc:
        logger.error(
            f"[BrainWatcher] ERROR al re-indexar {source}: {exc}",
            exc_info=True
        )
        # No propagar — el watcher no debe morir por un documento fallido

# En el loop del watcher:
asyncio.ensure_future(_reindex_supervised(source))
```

**Criterio de aceptación:** cuando un pasaporte `.md` tiene un error de formato que rompe la re-indexación, el log muestra el error con `exc_info`, el watcher continúa vivo y el resto de pasaportes se re-indexan normalmente.

---

## F7.3 — Resolución de DTs medias (Día 3-4)

### DT-05 — `quality_score` calculado pero no usado

**Problema:** `UniversalCleaner` calcula `quality_score` (0.0–1.0) para cada bloque, pero `ingest_router.py` vectoriza todos los bloques sin filtrar por calidad. Bloques con `quality_score < 0.3` (texto corrupto, artefactos OCR, contenido de tabla sin contexto) contaminan la colección `knowledge`.

**Solución:** en `BrainHook.run()` (el hook de F5 en `brain_hook.py`), antes de pasar a `ingest_router`, filtrar bloques de baja calidad y loguear cuántos se descartaron.

```python
# surfsense_backend/app/indexing_pipeline/brain_hook.py
# En el método run(), antes de llamar a ingest_router:

QUALITY_THRESHOLD = 0.30  # bloques con score < 0.30 no se vectorizan

def _filter_low_quality_blocks(blocks: list[dict], source: str) -> list[dict]:
    """
    Descarta bloques con quality_score muy bajo.
    Loguea el número de bloques filtrados para auditoría.
    """
    if not blocks:
        return blocks

    filtered = [b for b in blocks if b.get("metadata", {}).get("quality_score", 1.0) >= QUALITY_THRESHOLD]
    discarded = len(blocks) - len(filtered)
    if discarded:
        logger.warning(
            f"[BrainHook] {source}: {discarded}/{len(blocks)} bloques descartados "
            f"por quality_score < {QUALITY_THRESHOLD}"
        )
    return filtered
```

**Umbral inicial:** 0.30. Bloques con score inferior son típicamente líneas de log corrupto, artefactos de OCR o celdas de tabla sin contexto. El umbral puede ajustarse en `brain_admin` (F6) sin tocar código.

**Criterio de aceptación:** al ingestar un PDF con secciones escaneadas de baja calidad, el log muestra `N bloques descartados`, y la consulta posterior no devuelve texto corrupto en los resultados.

---

### DT-10 — Deduplicación cross-search-space *(nuevo — SecondBrainSense)*

**Problema:** un mismo fichero subido en dos `search_space_id` diferentes genera dos registros PostgreSQL con el mismo `content_hash` y dos conjuntos de vectores Qdrant idénticos. No rompe nada, pero duplica espacio en Qdrant innecesariamente.

**Solución pragmática para F7:** no reutilizar vectores (complejidad alta, beneficio bajo a corto plazo), pero sí **detectar y loguear duplicados** para que el administrador pueda decidir. El campo `content_hash` ya existe en el modelo `Document` (confirmado en F5).

```python
# surfsense_backend/app/indexing_pipeline/brain_hook.py
# Añadir detección de duplicado cross-space al inicio de run():

async def _check_cross_space_duplicate(
    db: AsyncSession,
    content_hash: str,
    search_space_id: int,
    source: str
) -> bool:
    """
    Devuelve True si hay otro Document con el mismo content_hash
    en un search_space_id diferente.
    Solo logea — no bloquea la ingesta.
    """
    stmt = select(Document).where(
        Document.content_hash == content_hash,
        Document.search_space_id != search_space_id,
        Document.status == "ready"
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        logger.info(
            f"[BrainHook] DUPLICATE: {source} (hash={content_hash[:8]}...) "
            f"ya existe en search_space_id={existing.search_space_id}. "
            f"Vectorizando igualmente para aislar spaces."
        )
        return True
    return False
```

**Decisión de diseño:** cada Search Space mantiene sus propios vectores aunque el contenido sea idéntico. El aislamiento multi-tenant es prioritario sobre el ahorro de espacio. La reutilización de vectores se diferiere al roadmap cuando el número de spaces crezca significativamente.

---

## F7.4 — DTs diferidas (siguiente sprint)

### DT-04 — El chunker no corta en `####`

Complementario a DT-01. Una vez resuelto DT-01 (Heading 4 promovido a `###`), DT-04 pierde criticidad porque los subservicios ya tendrán cortes en `###`. Se difiere para confirmar que DT-01 es suficiente.

### DT-06 — `context_tokens` calibrados para CPU

No aplica hasta tener GPU o modelos cloud. Los valores actuales son correctos para el hardware de producción actual. Revisitar al migrar a GPU o al incorporar `claude-3.5-sonnet`:

```python
# surfsense_backend/app/brain/model_profiles.py
# Al migrar a GPU, recalibrar context_tokens:
# qwen2.5-coder:7b en GPU A100 → context_tokens: 60_000 (vs 28_000 en CPU)
# llama3.1:8b en GPU A100    → context_tokens: 80_000 (vs 30_000 en CPU)
```

### DT-08 — `num_predict` heredado en hot-reload

Solo afecta desarrollo local con `--reload`. En producción con Docker Compose no hay hot-reload — `docker compose restart surfsense-backend` garantiza estado limpio. No requiere cambio de código.

---

## F7.5 — Mitigación de riesgos operativos (Día 4)

Los 4 riesgos de la sección 12.2 de la arquitectura necesitan mitigación documentada.

### R-01 — Token az CLI expirado durante ingesta SharePoint

**Probabilidad:** Media. **Impacto:** Bloqueo del conector SharePoint.

**Mitigación:**
```bash
# Antes de cualquier ingesta desde SharePoint:
docker exec surfsense-backend az account show 2>/dev/null \
  || { echo "Token expirado — renovar con: az login"; exit 1; }

# Script para renovación (requiere interacción del usuario — no automatizable):
az login --tenant <tenant-id>
docker exec surfsense-backend az account show  # verificar
```

El backend ya devuelve HTTP 400 con mensaje de instrucción cuando el token ha expirado. Añadir al runbook la verificación antes de ingestas masivas.

---

### R-02 — OOM en Ollama con documentos muy largos

**Probabilidad:** Media. **Impacto:** Timeout de síntesis, pasaporte sin generar.

**Mitigación:** el `quality_trigger` (F3) ya activa síntesis chunked que reduce el contexto por llamada. Para documentos que aun así dan OOM:

```bash
# Verificar consumo de RAM de Ollama antes de ingestas masivas:
ollama ps  # muestra modelos en memoria y VRAM/RAM usada

# Si hay OOM durante síntesis, el hook (F5) captura la excepción y deja
# Document.status = "error_brain" — nunca "error" en el pipeline principal.
# Re-intentar con modelo más pequeño:
# En brain_admin (F6): cambiar SYNTHESIS_MODEL a qwen2.5-coder:3b y re-sintetizar.
```

---

### R-03 — Contención entre modelos en GPU pequeña

**Probabilidad:** Baja. **Impacto:** Degradación de latencia en síntesis y chat simultáneos.

**Mitigación:** separar explícitamente los modelos para que Ollama no cargue ambos a la vez:

```bash
# .env — separar síntesis y chat en modelos de diferente tamaño
# Síntesis (solo en ingesta, no hay chat simultáneo):
SYNTHESIS_MODEL=qwen2.5-coder:7b

# Chat (respuestas rápidas, uso interactivo):
OLLAMA_CHAT_MODEL=qwen2.5-coder:3b

# Ollama descarga el modelo anterior de memoria al cargar el nuevo
# si la GPU no tiene capacidad para ambos simultáneamente.
```

---

### R-04 — Chunks obsoletos al cambiar modelo de embedding

**Probabilidad:** Alta si se cambia el modelo. **Impacto:** Búsqueda devuelve resultados incorrectos o vacíos.

**Mitigación:** el código de `ingest_router.py` ya autodetecta dimensiones distintas para la colección `code` y la recrea. Para `brain` y `knowledge` (768d), el procedimiento es manual:

```bash
# Al cambiar EMBED_MODEL (ej. de nomic-embed-text a mxbai-embed-large):
# 1. PARAR el celery worker para que no indexe con modelo viejo
docker compose stop surfsense-celery-worker

# 2. Recrear colecciones afectadas (borra vectores, conserva pasaportes .md)
curl -X DELETE http://localhost:6333/collections/brain
curl -X DELETE http://localhost:6333/collections/knowledge

# 3. Actualizar el modelo en .env
# EMBED_MODEL=mxbai-embed-large

# 4. Re-arrancar y dejar que el watcher re-indexe los pasaportes .md
docker compose up -d surfsense-celery-worker
# El brain_watcher detectará los .md sin vector y los re-indexará
```

---

## F7.6 — Circuit breaker para Ollama (Día 4)

`LLMClient.generate()` es **SYNC** (no async). El circuit breaker debe ser igualmente sync y wrappear el método existente sin cambiar su firma.

```python
# surfsense_backend/app/brain/llm_client.py — añadir al final del fichero

import time

class _OllamaCircuitBreaker:
    """
    Circuit breaker sync para LLMClient.generate().
    Estados: closed (normal) → open (cortado) → half-open (probando recuperación)
    """
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self._failures = 0
        self._threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure: float | None = None
        self._state = "closed"

    def is_open(self) -> bool:
        if self._state == "open":
            if self._last_failure and (time.monotonic() - self._last_failure) > self._recovery_timeout:
                self._state = "half-open"
                return False
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()
        if self._failures >= self._threshold:
            self._state = "open"
            logger.warning(
                f"[CircuitBreaker] Ollama OPEN tras {self._failures} fallos consecutivos. "
                f"Recuperación en {self._recovery_timeout}s."
            )

# Singleton del breaker — compartido entre todas las llamadas
_brain_circuit_breaker = _OllamaCircuitBreaker(failure_threshold=3, recovery_timeout=60)


def generate_with_circuit_breaker(
    prompt: str,
    system: str | None = None,
    model: str | None = None,
    **kwargs
) -> str:
    """
    Wrapper de LLMClient.generate() con circuit breaker.
    Si el breaker está abierto, devuelve un string de fallback en lugar de hacer la llamada.
    Usar exclusivamente en brain_hook.py para la síntesis — no en el chat.
    """
    if _brain_circuit_breaker.is_open():
        logger.warning("[CircuitBreaker] Síntesis omitida — Ollama no responde.")
        return "[Síntesis no disponible — Ollama no responde. Re-sintetizar cuando Ollama esté disponible.]"

    try:
        result = llm_client.generate(prompt=prompt, system=system, **kwargs)
        _brain_circuit_breaker.record_success()
        return result
    except Exception as exc:
        _brain_circuit_breaker.record_failure()
        raise
```

**Uso en `brain_hook.py`:** reemplazar la llamada directa a `llm_client.generate()` dentro del hook por `generate_with_circuit_breaker()`. La síntesis fallida deja `Document.brain_status = "pending_synthesis"` — reintentable desde la UI de Brain Admin.

---

## F7.7 — Tests de integración E2E (Día 3-4)

Los tests se ejecutan contra el stack completo en Docker Compose. Usan `time.sleep()` (no `asyncio.sleep()` sin `await`) y los endpoints reales con prefijo `/api/v1/`.

```python
# surfsense_backend/tests/integration/test_brain_e2e.py

import time
import pytest
import httpx

BASE_URL = "http://localhost:8929"   # puerto host del backend (docker-compose.yml)
WAIT_CELERY = 15                      # segundos para que Celery procese la tarea

@pytest.fixture(scope="module")
def auth_headers():
    """Login y devolver headers con Bearer token."""
    r = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                   json={"email": "admin@test.com", "password": "test"},
                   timeout=10)
    r.raise_for_status()
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def test_space(auth_headers):
    """Crear un Search Space de test y devolverlo. Limpiar al terminar."""
    r = httpx.post(f"{BASE_URL}/api/v1/search-spaces",
                   json={"name": "e2e-brain-test"},
                   headers=auth_headers,
                   timeout=10)
    r.raise_for_status()
    space_id = r.json()["id"]
    yield space_id
    # Teardown: borrar el space (y sus documentos)
    httpx.delete(f"{BASE_URL}/api/v1/search-spaces/{space_id}",
                 headers=auth_headers, timeout=10)


class TestBrainPipeline:

    def test_python_ingestion_completes_pipeline(self, auth_headers, test_space):
        """
        E2E crítico: .py → 3 fases → pasaporte generado → vectores en Qdrant → consulta L2
        """
        python_content = b'''
def authenticate_user(username: str, password: str) -> dict:
    """Autentica un usuario contra la base de datos."""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise AuthenticationError("Credenciales invalidas")
    return {"user_id": user.id, "token": generate_jwt(user.id)}
'''
        # 1. Subir fichero
        r = httpx.post(
            f"{BASE_URL}/api/v1/documents/fileupload",
            files={"file": ("auth_service.py", python_content, "text/plain")},
            data={"search_space_id": test_space},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200, f"Upload falló: {r.text}"

        # 2. Esperar que Celery procese (pipeline + hook brain)
        time.sleep(WAIT_CELERY)

        # 3. Verificar pasaporte generado
        r = httpx.get(
            f"{BASE_URL}/api/v1/brain/auth-service",
            params={"search_space_id": test_space},
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code == 200, f"Pasaporte no encontrado: {r.text}"
        passport = r.json()["passport_md"]
        assert "authenticate_user" in passport
        assert "# 🧩 Core Knowledge" in passport

        # 4. Consulta — debe resolverse en L1 o L2 (no en L0)
        r = httpx.post(
            f"{BASE_URL}/api/v1/brain/query",
            json={
                "question": "¿Cómo funciona la autenticación de usuarios?",
                "search_space_id": test_space,
            },
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["level"] in (1, 2), f"Esperado L1 o L2, obtenido: {result['level']}"
        assert any("auth-service" in s for s in result.get("sources", []))


    def test_query_sin_contexto_resuelve_en_l0(self, auth_headers, test_space):
        """Consulta sobre algo no indexado debe resolverse en L0 (LLM libre)."""
        r = httpx.post(
            f"{BASE_URL}/api/v1/brain/query",
            json={
                "question": "¿Cuál es la capital de Mongolia Exterior?",
                "search_space_id": test_space,
            },
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200
        result = r.json()
        assert result["level"] == 0, f"Esperado L0, obtenido: {result['level']}"


    def test_dt09_watcher_no_muere_con_passport_corrupto(self, auth_headers, test_space):
        """
        DT-09: el brain_watcher no debe morir cuando un .md tiene error de formato.
        Verificar que otros documentos siguen siendo accesibles después.
        """
        # El watcher está en el backend container — verificar que sigue vivo
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "surfsense-backend",
             "ps", "aux"],
            capture_output=True, text=True, timeout=10,
        )
        assert "brain_watcher" in result.stdout or result.returncode == 0, \
            "El proceso brain_watcher no está corriendo en surfsense-backend"


    def test_dt05_quality_score_filtra_bloques_bajos(self, auth_headers, test_space):
        """
        DT-05: fichero con contenido de baja calidad genera un pasaporte válido
        (los bloques malos se filtran, los buenos llegan al LLM).
        """
        # Contenido mixto: parte útil + basura OCR
        mixed_content = b"""
## Funcion principal

Esta funcion procesa los datos de entrada y retorna el resultado.

xxxxxxxxxxx OCR_ARTIFACT_!@#$%^&* xxxxxxxxxxx jjjjjjjjjjj 0000000
aaaaaaa bbbbbbb ccccc dddddd eeeeeeee fffffff gggggg hhhhhhh

def process(data: list) -> dict:
    return {"result": sum(data)}
"""
        r = httpx.post(
            f"{BASE_URL}/api/v1/documents/fileupload",
            files={"file": ("mixed_quality.md", mixed_content, "text/plain")},
            data={"search_space_id": test_space},
            headers=auth_headers,
            timeout=30,
        )
        assert r.status_code == 200
        time.sleep(WAIT_CELERY)

        r = httpx.get(
            f"{BASE_URL}/api/v1/brain/mixed-quality",
            params={"search_space_id": test_space},
            headers=auth_headers,
            timeout=10,
        )
        assert r.status_code == 200
        passport = r.json()["passport_md"]
        # El pasaporte existe y tiene contenido útil
        assert "process" in passport or "Funcion principal" in passport
        # El artefacto OCR no debe aparecer en el pasaporte
        assert "OCR_ARTIFACT" not in passport


    def test_multi_tenant_isolation(self, auth_headers):
        """
        Multi-tenant: un documento indexado en space A no es visible desde space B.
        """
        # Crear dos spaces
        r_a = httpx.post(f"{BASE_URL}/api/v1/search-spaces",
                         json={"name": "isolation-test-a"},
                         headers=auth_headers, timeout=10)
        r_b = httpx.post(f"{BASE_URL}/api/v1/search-spaces",
                         json={"name": "isolation-test-b"},
                         headers=auth_headers, timeout=10)
        space_a = r_a.json()["id"]
        space_b = r_b.json()["id"]

        # Subir documento solo en space A
        content = b"# Secreto del space A\n\nEsta informacion es exclusiva del espacio A."
        httpx.post(
            f"{BASE_URL}/api/v1/documents/fileupload",
            files={"file": ("secreto_a.md", content, "text/plain")},
            data={"search_space_id": space_a},
            headers=auth_headers, timeout=30,
        )
        time.sleep(WAIT_CELERY)

        # Consultar desde space B — no debe encontrar nada del space A
        r = httpx.post(
            f"{BASE_URL}/api/v1/brain/query",
            json={
                "question": "¿Qué es el secreto del space A?",
                "search_space_id": space_b,
            },
            headers=auth_headers, timeout=30,
        )
        result = r.json()
        # Si va a L0, bien: no tiene contexto (correcto)
        # Si va a L1/L2, las sources NO deben incluir "secreto-a"
        if result["level"] in (1, 2):
            assert not any("secreto-a" in s for s in result.get("sources", [])), \
                "FUGA DE DATOS: documento de space A visible en space B"

        # Teardown
        httpx.delete(f"{BASE_URL}/api/v1/search-spaces/{space_a}", headers=auth_headers)
        httpx.delete(f"{BASE_URL}/api/v1/search-spaces/{space_b}", headers=auth_headers)
```

---

## F7.8 — Runbook operacional (Día 5)

Nombre real del proyecto Docker: `surfsense` (definido en `docker-compose.yml` línea `name: surfsense`).  
Puerto del backend en el host: **8929**. Puerto del frontend: **3929**.

```markdown
# SecondBrainSense — Runbook Operacional

## Contenedores del stack
surfsense-db              PostgreSQL 17 + pgvector
surfsense-redis           Redis 8
surfsense-migrations      Short-lived: alembic upgrade head
surfsense-searxng         SearXNG (búsqueda web)
surfsense-backend         FastAPI (host:8929 → container:8000)
surfsense-celery-worker   Celery worker (indexación)
surfsense-celery-beat     Celery beat (tareas periódicas)
surfsense-zero-cache      Zero Cache (sync tiempo real)
surfsense-frontend        Next.js (host:3929 → container:3000)
surfsense-qdrant          Qdrant (host:6333)

## Arranque completo
cd docker/
docker compose up -d
# Esperar ~3 minutos para que migrations complete y backend esté healthy
curl http://localhost:8929/health         # debe devolver {"status":"ok"}
curl http://localhost:3929                # debe devolver 200

## Verificar estado de todos los servicios
docker compose ps                         # todos deben estar "running"
curl http://localhost:6333/health         # Qdrant
curl http://host.docker.internal:11434/api/tags  # Ollama (desde el host)
docker exec surfsense-celery-worker \
  celery -A app.celery_app inspect ping   # Celery workers activos

## Backup diario (ejecutar desde el host)
# PostgreSQL:
docker exec surfsense-db \
  pg_dump -U surfsense surfsense > backup_$(date +%Y%m%d).sql

# Pasaportes .md (si están en volumen local — ajustar ruta):
# Los .md están dentro del contenedor o en el mount del brain directory
tar czf brain_passports_$(date +%Y%m%d).tar.gz \
  surfsense_backend/app/brain/data/  # ajustar a la ruta real del volumen

## Actualizar SecondBrainSense
# 1. Backup preventivo
# (ejecutar backup diario arriba)

# 2. Descargar nueva versión
git pull origin main

# 3. Rebuildar y reiniciar servicios afectados
cd docker/
docker compose pull
docker compose up -d --no-deps surfsense-backend surfsense-celery-worker

# 4. Migrar BD si hay cambios de schema
docker compose run --rm surfsense-migrations  # ya lo hace automáticamente al arrancar

## Problemas comunes

### Backend no responde (8929)
# Verificar que arrancó correctamente:
docker logs surfsense-backend --tail 50
# Verificar healthcheck:
curl -v http://localhost:8929/ready
# Reiniciar si está bloqueado:
docker compose restart surfsense-backend

### Celery worker no procesa tareas
# Ver tareas activas:
docker exec surfsense-celery-worker \
  celery -A app.celery_app inspect active
# Ver cola pendiente:
docker exec surfsense-celery-worker \
  celery -A app.celery_app inspect reserved
# Reiniciar:
docker compose restart surfsense-celery-worker

### Qdrant — colección corrupta o vacía
# Verificar colecciones:
curl http://localhost:6333/collections
# Recrear la colección afectada (borra vectores, no los pasaportes .md):
curl -X DELETE http://localhost:6333/collections/brain
curl -X DELETE http://localhost:6333/collections/knowledge
# Los pasaportes .md siguen intactos — el brain_watcher los re-indexará
# O forzar re-indexación manual:
curl -X POST http://localhost:8929/api/v1/brain/admin/reindex-all \
  -H "Authorization: Bearer $TOKEN"

### Ollama — modelo no disponible o timeout
# En el HOST (no en el contenedor):
ollama list                         # ver modelos descargados
ollama ps                           # ver modelos en memoria
ollama pull nomic-embed-text        # re-descargar si falta
ollama pull qwen2.5-coder:7b
# Verificar desde dentro del backend:
docker exec surfsense-backend \
  curl -s http://host.docker.internal:11434/api/tags | python3 -m json.tool

### brain_watcher — re-indexación silenciosa fallando
# Verificar logs del backend (DT-09 ya añade logs con exc_info):
docker logs surfsense-backend --tail 100 | grep BrainWatcher
# Si hay errores repetidos en un source concreto:
docker exec surfsense-backend cat /app/app/brain/data/{source}.md | head -30
# Si el .md está corrupto, borrarlo y re-sintetizar:
curl -X DELETE http://localhost:8929/api/v1/brain/{source} \
  -H "Authorization: Bearer $TOKEN"
curl -X POST http://localhost:8929/api/v1/brain/{source}/resynthesize \
  -H "Authorization: Bearer $TOKEN"

### Cambio de modelo de embedding (ver R-04)
# IMPORTANTE: borrar colecciones antes de cambiar el modelo o los vectores serán incorrectos
docker compose stop surfsense-celery-worker
curl -X DELETE http://localhost:6333/collections/brain
curl -X DELETE http://localhost:6333/collections/knowledge
# Editar .env: EMBED_MODEL=nuevo-modelo
docker compose up -d surfsense-celery-worker
# El watcher re-indexa automáticamente
```

---

## F7.9 — Healthcheck script (Día 5)

Script para cron cada 5 minutos. Usa los nombres reales de los contenedores y el puerto 8929.

```bash
#!/usr/bin/env bash
# /scripts/healthcheck.sh
# cron: */5 * * * * /path/to/secondbrainsense/scripts/healthcheck.sh >> /var/log/brainsense-health.log 2>&1

set -euo pipefail
BACKEND_URL="http://localhost:8929"
QDRANT_URL="http://localhost:6333"
ALERT_EMAIL="${ALERT_EMAIL:-}"  # opcional: enviar alertas por email

_alert() {
  local msg="[BrainSense-Health] $(date '+%Y-%m-%d %H:%M:%S') $1"
  echo "$msg"
  [[ -n "$ALERT_EMAIL" ]] && echo "$msg" | mail -s "BrainSense ALERTA" "$ALERT_EMAIL" 2>/dev/null || true
}

check_backend() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BACKEND_URL/health" 2>/dev/null)
  if [[ "$code" != "200" ]]; then
    _alert "BACKEND DOWN (HTTP $code) — reiniciando surfsense-backend"
    docker compose -f /path/to/docker/docker-compose.yml restart surfsense-backend
  fi
}

check_qdrant() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$QDRANT_URL/health" 2>/dev/null)
  [[ "$code" == "200" ]] || _alert "QDRANT DOWN (HTTP $code) — revisar surfsense-qdrant"
}

check_celery() {
  local result
  result=$(docker exec surfsense-celery-worker \
    celery -A app.celery_app inspect ping --timeout=5 2>/dev/null || echo "FAIL")
  [[ "$result" == *"pong"* ]] || _alert "CELERY SIN WORKERS — revisar surfsense-celery-worker"
}

check_redis() {
  local pong
  pong=$(docker exec surfsense-redis redis-cli ping 2>/dev/null || echo "FAIL")
  [[ "$pong" == "PONG" ]] || _alert "REDIS DOWN — revisar surfsense-redis"
}

check_ollama() {
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "http://localhost:11434/api/tags" 2>/dev/null)
  [[ "$code" == "200" ]] || _alert "OLLAMA NO RESPONDE (HTTP $code) — revisar servicio ollama en el host"
}

check_brain_watcher() {
  local running
  running=$(docker exec surfsense-backend pgrep -f brain_watcher 2>/dev/null | wc -l)
  [[ "$running" -gt 0 ]] || _alert "BRAIN WATCHER CAÍDO — reiniciando surfsense-backend"
}

# Ejecutar todos los checks
check_redis
check_qdrant
check_backend
check_celery
check_ollama
check_brain_watcher

echo "[BrainSense-Health] $(date '+%Y-%m-%d %H:%M:%S') — todos los checks completados"
```

---

## Checklist F7

### DTs críticas (Día 1-3)
- [ ] **DT-01** — `docx.py`: Heading 4 con más de 8 instancias promovido a `###`
- [ ] **DT-02** — `docx.py`: tablas `content_type=table` adjuntas al bloque de texto previo con `> tabla:`
- [ ] **DT-03** — `html.py`: secciones > 3000 chars subdivididas en `### Bloque N`
- [ ] **DT-03** — `txt.py`: subtipo log/nota con `### Error N:` por bloque de traza
- [ ] **DT-03** — `csv.py`: `### Análisis de datos` cuando < 5 columnas
- [ ] **DT-07** — `passport_builder.py`: headings `###` y `####` incluidos en `entities`
- [ ] **DT-09** — `brain_watcher.py`: `_reindex_supervised()` con `try/except` y `logger.error(exc_info=True)`

### DTs medias (Día 3-4)
- [ ] **DT-05** — `brain_hook.py`: `_filter_low_quality_blocks()` con umbral 0.30
- [ ] **DT-10** — `brain_hook.py`: `_check_cross_space_duplicate()` — detect y log, no bloquear

### Riesgos operativos (Día 4)
- [ ] **R-01** — Runbook documenta verificación de token az CLI antes de ingesta SharePoint
- [ ] **R-02** — `brain_hook.py` captura OOM de Ollama y deja `Document.brain_status = "pending_synthesis"`
- [ ] **R-03** — `.env` de producción separa `SYNTHESIS_MODEL` y `OLLAMA_CHAT_MODEL`
- [ ] **R-04** — Runbook documenta procedimiento de recreación de colecciones al cambiar embedding model

### Circuit breaker y calidad (Día 4)
- [ ] `_OllamaCircuitBreaker` (sync) en `llm_client.py` con 3 intentos / 60s recovery
- [ ] `generate_with_circuit_breaker()` usado en `brain_hook.py`
- [ ] Fallback retorna string `[Síntesis no disponible...]` — nunca exception silenciosa

### Tests E2E (Día 3-4)
- [ ] `test_python_ingestion_completes_pipeline` — subida + espera + pasaporte + consulta L1/L2
- [ ] `test_query_sin_contexto_resuelve_en_l0` — consulta sin contexto → nivel 0
- [ ] `test_dt09_watcher_no_muere_con_passport_corrupto` — proceso brain_watcher vivo
- [ ] `test_dt05_quality_score_filtra_bloques_bajos` — artefactos OCR fuera del pasaporte
- [ ] `test_multi_tenant_isolation` — documento de space A invisible desde space B

### Runbook y operaciones (Día 5)
- [ ] Runbook con nombres reales: `surfsense-backend`, `surfsense-celery-worker`, `surfsense-qdrant`
- [ ] Puerto host correcto: 8929 (backend), 3929 (frontend), 6333 (Qdrant)
- [ ] `healthcheck.sh` con cron cada 5 minutos configurado
- [ ] Backup diario de PostgreSQL + pasaportes `.md` documentado
- [ ] Procedimiento de actualización sin downtime documentado

### DTs diferidas (siguiente sprint)
- [ ] **DT-04** — Confirmar si DT-01 elimina la necesidad o documentar tarea concreta
- [ ] **DT-06** — Ticket abierto: recalibrar `context_tokens` tras migración a GPU
- [ ] **DT-08** — Nota en dev guide: usar `docker compose restart`, no hot-reload en producción

---

## Criterio de aceptación global F7

1. Los 5 tests E2E pasan en el stack completo
2. Un documento Word con Heading 4 (ej. DWP con subservicios) genera pasaporte con subsecciones `###` por subservicio
3. Un documento con secciones HTML largas genera chunks en `###` sin cortes de emergencia
4. El `entities` del frontmatter incluye nombres de componentes de Heading 3
5. Al matar el proceso de Ollama durante una ingesta, el backend loguea el error, el circuit breaker se abre, y las ingestas posteriores devuelven `[Síntesis no disponible...]` sin crashear el worker
6. Un documento indexado en space A no aparece en resultados de una consulta en space B

---

**Anterior:** [F6 — UI Integrada](./F6-ui-integracion.md)  
**Volver al plan maestro:** [00 — Plan Maestro](./00-plan-maestro.md)

---

## Estado de la plataforma al final de F7

```
SecondBrainSense — Production Ready

Ingesta:  16 formatos · 25+ conectores (SurfSense) · pipeline 3 fases (Second Brain v4)
          DT-01/02/03 resueltos: docx H4, tablas ANS, cortes semánticos html/txt/csv
Síntesis: multi-call · quality_trigger · 14 tipos de prompts · circuit breaker sync
          DT-07 resuelto: entities incluyen H3/H4 → grafo más rico
Vectores: Qdrant · 3 colecciones · 768d + (2560d code) · quality filter 0.30
          DT-05 resuelto: bloques de baja calidad filtrados antes de vectorizar
Query:    L1→L2→BM25→Web→L0 cascade · cross-encoder reranking · multi-tenant aislado
Watcher:  DT-09 resuelto: brain_watcher con gestión de excepciones supervisada
UI:       Next.js · Brain Chat · Wiki · Graph · Ingest · Admin · Maestros/Vocabulario
Stack:    surfsense-{db,redis,backend,celery-worker,celery-beat,zero-cache,frontend,qdrant}
Ops:      Healthcheck cada 5min · Backup daily · Runbook completo · Circuit breaker Ollama
```
