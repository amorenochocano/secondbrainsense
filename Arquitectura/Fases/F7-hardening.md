# F7 — Hardening, Deuda Técnica y Producción
**Duración:** 1 semana  
**Equipo:** Backend Senior (1)  
**Dependencias:** F5 completada y tests pasando  
**Entregable:** Pipeline Brain estabilizado. DT críticas resueltas. Circuit breaker Ollama. Tests E2E con Qdrant. Runbook actualizado.

---

## Contexto: qué ha cambiado respecto al F7 original

| Punto original F7 | Estado en SecondBrainSense |
|------------------|---------------------------|
| DT-05 quality_score | ✅ RESUELTA por F5 — `_filter_low_quality()` en `brain_ingestion_adapter.py` |
| DT-09 brain_watcher | Sin cambios — `app/brain/brain_watcher.py` línea 181 |
| DT-10 deduplicación | Cambia: de `brain_hook.py` → `brain_ingestion_adapter.py` |
| Circuit breaker Ollama | Cambia: de `brain_hook.py` → `unified_embedder.py` |
| Tests E2E | Verifican Qdrant en vez de pgvector |
| Runbook | Actualizado para arquitectura sin pgvector |

DTs **DT-01, DT-02, DT-03, DT-07** — sin cambios respecto al F7 original.

---

## F7.1 — Priorización de deuda técnica

| ID | Problema | Fichero real | Prioridad | Estado |
|----|---------|-------------|:---------:|--------|
| DT-01 | Heading 4 no aparece en Core Knowledge | `app/brain/extractors/docx.py` | 🔴 Crítica | Pendiente |
| DT-02 | Tablas ANS sin detalle en pasaporte | `app/brain/extractors/docx.py` | 🔴 Crítica | Pendiente |
| DT-03 | html/txt/csv sin `###` → cortes de emergencia | `extractors/web.py`, `txt.py`, `csv.py` | 🔴 Crítica | Pendiente |
| DT-07 | Entities no incluyen Heading 3-4 | `app/brain/passport_builder.py` | 🟡 Media | Pendiente |
| DT-09 | `Task exception never retrieved` en brain_watcher | `app/brain/brain_watcher.py` L181 | 🔴 Crítica | Pendiente |
| DT-05 | quality_score calculado pero no usado | `brain_ingestion_adapter.py` | ✅ Resuelta | F5 |
| DT-10 | Deduplicación cross-search-space | `brain_ingestion_adapter.py` | 🟡 Media | Pendiente |
| DT-04 | Chunker no corta en `####` | `app/brain/chunking.py` | 🟢 Baja | Diferida |
| DT-06 | context_tokens calibrados para CPU | `app/config/*.yaml` | 🟢 Baja | Diferida |
| DT-08 | num_predict heredado en hot-reload | Docs operacionales | 🟢 Baja | Mitigada |

---

## F7.2 — DTs críticas (Día 1-3)

### DT-01 — Heading 4 no aparece en Core Knowledge

**Fichero:** `app/brain/extractors/docx.py` — `preprocess()`

```python
# Convertir #### en ## para forzar corte del chunker
processed_text = re.sub(r'^####\s+', '## ', processed_text, flags=re.MULTILINE)
```

**Verificar:** .docx con Heading 4 → subservicio aparece en `entities[]` del pasaporte.

---

### DT-02 — Tablas ANS sin detalle en pasaporte

**Fichero:** `app/brain/extractors/docx.py` — `preprocess()`

```python
# Adjuntar bloque table al bloque de texto precedente
merged_blocks = []
for block in blocks:
    if block.get("type") == "table" and merged_blocks:
        prev = merged_blocks[-1]
        prev["content"] = prev.get("content", "") + "\n\n" + block.get("content", "")
        prev["has_table"] = True
    else:
        merged_blocks.append(block)
```

**Verificar:** .docx con tablas ANS → tabla aparece en `core_knowledge` del pasaporte.

---

### DT-03 — html/txt/csv sin `###` internos

**Ficheros:** `extractors/web.py`, `txt.py`, `csv.py`

Cada extractor debe producir `###` como puntos de corte semántico internos:
- `web.py` — `###` en cada `<h3>/<h4>` del DOM
- `txt.py` — `###` en cada entrada de log detectada
- `csv.py` — `###` por cada N filas

**Verificar:** URL crawled → bloques con `###`. .csv → chunks sin truncar.

---

### DT-09 — `Task exception never retrieved` en brain_watcher

**Problema en código real (`app/brain/brain_watcher.py` L181):**

```python
# BUG ACTUAL:
loop.run_in_executor(None, _reingest_md, md_path)  # Future ignorado
```

**Solución:**

```python
# F7 FIX:
fut = loop.run_in_executor(None, _reingest_md, md_path)

def _on_done(future: asyncio.Future) -> None:
    exc = future.exception()
    if exc is not None:
        log.error("[watcher] Re-ingestión FAILED para '%s': %s",
                  md_path, exc, exc_info=exc)
    else:
        log.info("[watcher] Re-ingestión OK para '%s'", md_path)

fut.add_done_callback(_on_done)
```

**Verificar:** Error en `_reingest_md()` → log muestra `FAILED` con stack trace.

---

## F7.3 — DTs medias (Día 3-4)

### DT-07 — Entities no incluyen Heading 3-4

**Fichero:** `app/brain/passport_builder.py`

```python
def _extract_heading_entities(processed_text: str) -> list[str]:
    pattern = re.compile(r'^#{3,4}\s+(.+)$', re.MULTILINE)
    return [m.group(1).strip() for m in pattern.finditer(processed_text)]

# Al construir entities:
heading_entities = _extract_heading_entities(processed_text)
all_entities = list(dict.fromkeys(existing_entities + heading_entities))
```

**Verificar:** Heading 3 → aparece en `entities[]` del frontmatter YAML.

---

### DT-10 — Deduplicación cross-search-space

**Ubicación:** `brain_ingestion_adapter.py` — `_embed_and_upsert()`

```python
# F7 DT-10: verificar unique_id antes de re-embedir
existing = _find_existing_vectors(mgr.client, connector_doc.unique_id,
                                  str(connector_doc.search_space_id))
if existing:
    logger.info("[brain_adapter] DT-10: unique_id=%s existe → reusar vectores",
                connector_doc.unique_id)
    await _copy_vectors_to_space(mgr.client, existing,
                                  str(connector_doc.search_space_id), source_slug)
    return
# Si no existe → pipeline normal
```

**Verificar:** Mismo fichero en dos Search Spaces → Qdrant sin vectores duplicados.

---

## F7.4 — Circuit breaker para Ollama (Día 4)

**Fichero:** `app/indexing_pipeline/unified_embedder.py`

### Variables de entorno

```bash
OLLAMA_CB_FAILURE_THRESHOLD=3   # Fallos para abrir el breaker
OLLAMA_CB_RECOVERY_TIMEOUT=60   # Segundos hasta half-open
```

### Implementación

```python
import threading, time

_cb_lock        = threading.Lock()
_cb_failures    = 0
_cb_opened_at: float | None = None
_CB_FAILURE_THRESHOLD = int(os.getenv("OLLAMA_CB_FAILURE_THRESHOLD", "3"))
_CB_RECOVERY_TIMEOUT  = int(os.getenv("OLLAMA_CB_RECOVERY_TIMEOUT",  "60"))


class OllamaCircuitOpen(RuntimeError):
    """Ollama no disponible — circuit breaker abierto."""


def _cb_record_failure() -> None:
    global _cb_failures, _cb_opened_at
    with _cb_lock:
        _cb_failures += 1
        if _cb_failures >= _CB_FAILURE_THRESHOLD and _cb_opened_at is None:
            _cb_opened_at = time.monotonic()
            logger.error("[unified_embedder] circuit breaker ABIERTO tras %d fallos.",
                         _cb_failures)


def _cb_record_success() -> None:
    global _cb_failures, _cb_opened_at
    with _cb_lock:
        if _cb_opened_at is not None:
            logger.info("[unified_embedder] circuit breaker CERRADO.")
        _cb_failures = 0
        _cb_opened_at = None


def _cb_is_open() -> bool:
    with _cb_lock:
        if _cb_opened_at is None:
            return False
        if time.monotonic() - _cb_opened_at >= _CB_RECOVERY_TIMEOUT:
            logger.info("[unified_embedder] circuit breaker HALF-OPEN.")
            return False
        return True


# En _embed_ollama():
def _embed_ollama(text: str, model: str) -> list[float]:
    if _cb_is_open():
        raise OllamaCircuitOpen(f"Ollama no disponible. Reintento en {_CB_RECOVERY_TIMEOUT}s.")
    try:
        import ollama
        client = ollama.Client(host=OLLAMA_HOST, timeout=OLLAMA_EMBED_TIMEOUT)
        response = client.embeddings(model=model, prompt=text)
        _cb_record_success()
        return response["embedding"]
    except OllamaCircuitOpen:
        raise
    except Exception as exc:
        _cb_record_failure()
        logger.error("[unified_embedder] ollama fallo #%d: %s", _cb_failures, exc, exc_info=True)
        raise
```

### Fallback cuando el breaker está abierto

1. `embed_single()` lanza `OllamaCircuitOpen`
2. `process_document()` lo captura → cae a `_basic_chunk_text()`
3. Texto en PostgreSQL (BM25), sin vector en Qdrant
4. Documento re-indexable cuando Ollama se recupere

---

## F7.5 — Riesgos operativos

### R-01 — Token az CLI expirado (OneDrive/SharePoint)
Sin cambios. Válido para conectores OneDrive de SurfSense.

### R-02 — OOM en Ollama
Mitigado por `OLLAMA_EMBED_TIMEOUT` + circuit breaker (F7.4).

### R-03 — Contención GPU
Separar `BRAIN_EMBEDDING_MODEL` (embedding) de `LLM_PROVIDER` (síntesis) en `.env`.

### R-04 — Chunks obsoletos al cambiar modelo de embedding

**Tras F5:** Colección `code` → autodetecta dimensión ✅. Colecciones `brain` y `knowledge` → sin autodetección ⚠️.

```bash
# Procedimiento si cambia la dimensión (ej: 768d → 384d):
docker compose exec backend python -c "
from app.brain.qdrant_manager import QdrantManager
mgr = QdrantManager.get_instance()
mgr.recreate_collection('knowledge', vector_size=384)
mgr.recreate_collection('brain', vector_size=384)
"
docker compose exec backend python -m scripts.migrate_pgvector_to_qdrant
docker compose restart backend celery_worker
```

---

## F7.6 — DTs diferidas

- **DT-04** — Evaluar tras DT-01 (pueden resolverse juntas).
- **DT-06** — Recalibrar solo si se migra a GPU.
- **DT-08** — Mitigado: usar `docker compose restart` en producción.

---

## F7.7 — Tests (Día 3-4)

**Fichero:** `tests/brain/test_hardening_f7.py`

| Clase | Tests | Qué cubre |
|-------|-------|-----------|
| `TestDT01HeadingFour` | 2 | docx.py convierte `####`→`##`, aparece en entities |
| `TestDT02TablasANS` | 2 | tabla adjunta al texto, aparece en core_knowledge |
| `TestDT03PreprocesadoresGenéricos` | 3 | web/txt/csv producen `###` |
| `TestDT07Entities` | 2 | passport_builder incluye Heading 3-4 |
| `TestDT09BrainWatcher` | 3 | Future guardado, callback de error, log FAILED |
| `TestDT10Deduplicacion` | 2 | cross-space detectado, vectores reutilizados |
| `TestCircuitBreaker` | 5 | abre/cierra, half-open, OllamaCircuitOpen, env vars |
| `TestE2EPipelineQdrant` | 4 | chunks sin embedding PG, knowledge existe, env, Qdrant activo |
| **Total** | **23** | |

```python
class TestDT09BrainWatcher:
    def test_future_guardado(self):
        src = open("app/brain/brain_watcher.py").read()
        assert "fut = loop.run_in_executor(" in src

    def test_callback_error_añadido(self):
        src = open("app/brain/brain_watcher.py").read()
        assert "fut.add_done_callback(" in src
        assert "future.exception()" in src

    def test_loguea_fallo(self):
        src = open("app/brain/brain_watcher.py").read()
        assert "Re-ingestión FAILED" in src


class TestCircuitBreaker:
    def test_abre_tras_n_fallos(self):
        from app.indexing_pipeline.unified_embedder import (
            _cb_record_failure, _cb_is_open, _cb_record_success, _CB_FAILURE_THRESHOLD,
        )
        _cb_record_success()
        for _ in range(_CB_FAILURE_THRESHOLD):
            _cb_record_failure()
        assert _cb_is_open()
        _cb_record_success()

    def test_cierra_tras_exito(self):
        from app.indexing_pipeline.unified_embedder import (
            _cb_record_failure, _cb_is_open, _cb_record_success, _CB_FAILURE_THRESHOLD,
        )
        for _ in range(_CB_FAILURE_THRESHOLD):
            _cb_record_failure()
        _cb_record_success()
        assert not _cb_is_open()

    def test_embed_lanza_circuit_open(self):
        import pytest
        from unittest.mock import patch
        from app.indexing_pipeline.unified_embedder import (
            _cb_record_failure, _cb_record_success,
            embed_single, OllamaCircuitOpen, _CB_FAILURE_THRESHOLD,
        )
        _cb_record_success()
        for _ in range(_CB_FAILURE_THRESHOLD):
            _cb_record_failure()
        with patch("app.indexing_pipeline.unified_embedder.BRAIN_EMBEDDING_PROVIDER", "ollama"):
            with pytest.raises(OllamaCircuitOpen):
                embed_single("test", "nomic-embed-text")
        _cb_record_success()

    def test_variables_desde_entorno(self):
        src = open("app/indexing_pipeline/unified_embedder.py").read()
        assert 'os.getenv("OLLAMA_CB_FAILURE_THRESHOLD"' in src
        assert 'os.getenv("OLLAMA_CB_RECOVERY_TIMEOUT"' in src

    def test_exception_definida(self):
        from app.indexing_pipeline.unified_embedder import OllamaCircuitOpen
        assert issubclass(OllamaCircuitOpen, RuntimeError)
```

---

## F7.8 — Runbook operacional

### Healthcheck

```bash
#!/bin/bash
echo "=== Backend ===" && curl -s http://localhost:8929/health | jq .status
echo "=== Qdrant ===" && for col in brain knowledge code; do
  echo "  $col: $(curl -s "http://localhost:6333/collections/$col" | jq '.result.vectors_count // 0') vectores"
done
echo "=== Circuit breaker ===" && docker logs sbs-dev-backend 2>&1 | grep "circuit breaker" | tail -3
echo "=== Motor de búsqueda ===" && docker logs sbs-dev-backend 2>&1 | grep "hybrid_search_qdrant\|hybrid_search_pgvector" | tail -3
```

### Problemas comunes

**Circuit breaker abierto:**
```bash
docker compose restart ollama
# Se cierra automáticamente tras OLLAMA_CB_RECOVERY_TIMEOUT segundos
```

**Qdrant vacío:**
```bash
curl -X POST "http://localhost:6333/collections/knowledge/snapshots"  # backup
docker compose exec backend python -m scripts.migrate_pgvector_to_qdrant
```

**brain_watcher silencioso (antes de fix DT-09):**
```bash
docker logs sbs-dev-backend 2>&1 | grep "watcher.*FAILED" | tail -5
```

**Búsqueda en pgvector en vez de Qdrant:**
```bash
docker compose exec backend printenv BRAIN_INGESTION_ENABLED  # debe ser "true"
```

---

## Checklist F7

### DTs críticas
- [ ] DT-01: `docx.py` convierte `####` en `##`
- [ ] DT-02: `docx.py` adjunta bloques `table` al texto precedente
- [ ] DT-03: `web.py`, `txt.py`, `csv.py` producen `###` internos
- [ ] DT-07: `passport_builder.py` incluye Heading 3-4 en `entities[]`
- [ ] DT-09: `brain_watcher.py` L181 → `fut` + `fut.add_done_callback(_on_done)`

### DTs medias
- [ ] DT-10: `_embed_and_upsert()` verifica `unique_id` cross-space

### Circuit breaker
- [ ] `OllamaCircuitOpen` definida en `unified_embedder.py`
- [ ] `_cb_record_failure/success/is_open()` thread-safe
- [ ] `_embed_ollama()` usa el breaker
- [ ] `OLLAMA_CB_FAILURE_THRESHOLD` + `OLLAMA_CB_RECOVERY_TIMEOUT` en `.env`

### Tests
- [ ] 23 tests en `test_hardening_f7.py` pasando

### Criterio de aceptación global
- [ ] .docx con Heading 4 → subservicio en `entities[]`
- [ ] .docx con tablas ANS → tabla en `core_knowledge`
- [ ] Error en `_reingest_md()` → log `[watcher] Re-ingestión FAILED` con stack trace
- [ ] 3 fallos Ollama → log "circuit breaker ABIERTO"
- [ ] Tras 60s → breaker se cierra automáticamente
- [ ] `docker logs` muestra `hybrid_search_qdrant`
- [ ] `chunks.embedding` NULL en PostgreSQL (migración 162 ejecutada)

---

**Anterior:** [F6 — UI Integrada](./F6-ui-integracion.md)  
**Siguiente:** [F8 — Agentes Especializados](./F8-agentes-especializados.md)
