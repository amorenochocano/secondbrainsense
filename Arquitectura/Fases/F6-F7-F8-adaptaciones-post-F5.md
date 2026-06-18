# Adaptaciones F6, F7 y F8 tras la nueva F5 Unificada

Este documento describe los cambios necesarios en F6, F7 y F8 como consecuencia de la F5 unificada (pipeline Brain como motor de ingesta, Qdrant como único motor vectorial, eliminación de pgvector).

---

## F6 — UI Integrada: cambios necesarios

### F6.5 — Brain Chat
**Cambio:** El Brain Chat y el chat SurfSense ahora comparten el mismo backend de vectores (Qdrant). La distinción ya no es "pgvector vs Qdrant" sino "cascada multinivel F4 vs búsqueda directa".

- Brain Chat → `POST /api/v1/brain/query` → cascada L1→L2→BM25→Web→L0 (con pasaportes)
- SurfSense Chat → `search_knowledge_base` → hybrid_search (Qdrant vectors + PG BM25)

**Ambos consultan Qdrant knowledge.** La diferencia es que Brain Chat también consulta la colección `brain` (pasaportes) como L1.

**Impacto en UI:** ninguno — los endpoints no cambian. El LevelBadge sigue mostrando el nivel de la cascada.

### F6.8 — Brain Ingest
**Cambio importante:** La vista de ingesta ahora muestra el pipeline unificado, no uno separado.

**Antes (F5 original):** "Ingesta Brain" era una vista separada para subir ficheros al pipeline Second Brain.

**Ahora (F5 unificada):** TODOS los documentos (conectores + uploads) pasan por el pipeline Brain. La vista "Brain Ingest" se convierte en una vista de **monitorización** del pipeline:

- Muestra el estado de las 3 categorías (A/B/C) por documento
- Indica cuántos chunks se generaron y en qué colección Qdrant están
- Log de fases SSE sigue igual: extracción → limpieza → embedding → Qdrant
- Añadir columna "Categoría" (A/B/C) en la tabla de documentos recientes
- El routing preview ahora también muestra la categoría del "saco" (C)

### F6.9 — Métricas Brain
**Cambio:** Las métricas de colecciones Qdrant ahora reflejan TODO el contenido de SurfSense, no solo lo que pasa por Brain.

- `count(knowledge)` = TODOS los chunks de TODOS los conectores
- `count(brain)` = solo documentos con pasaporte sintetizado
- Añadir métrica: "Documentos por categoría" (A/B/C) como gráfico de barras

### F6.10 — Admin Brain
**Cambio:** El tab de configuración LLM ahora controla el pipeline de ingesta completo.

- `BRAIN_INGESTION_ENABLED` → toggle visible (activar/desactivar pipeline Brain)
- `BRAIN_SYNTHESIS_ENABLED` → toggle visible (pasaportes on/off sin afectar ingesta)
- `BRAIN_EMBEDDING_MODEL` → selector (nomic-embed-text por defecto)
- `BRAIN_QUALITY_THRESHOLD` → slider 0.0-1.0
- **NUEVO:** Sección "Fallback SurfSense" — muestra si está activo el fallback original

### F6.12 — LevelBadge en chat SurfSense
**Sin cambios** — el chat SurfSense sigue mostrando resultados normales. El LevelBadge solo aplica al Brain Chat.

---

## F7 — Hardening: cambios necesarios

### DT-01, DT-02, DT-03, DT-07 — Sin cambios
Estas deudas técnicas son sobre los preprocesadores (docx.py, html.py, etc.) y passport_builder.py. No dependen de pgvector ni del pipeline de ingesta — se mantienen exactamente como están en el plan F7 original.

### DT-05 — quality_score ya se aplica en ingesta
**Antes (F7 original):** DT-05 proponía filtrar en `brain_hook.py` DESPUÉS de la ingesta SurfSense.

**Ahora:** El filtrado por quality_score se hace DENTRO de `brain_ingestion_adapter.py` DURANTE la ingesta, en la función `_filter_low_quality()`. DT-05 ya está resuelta por F5.

**Acción en F7:** Verificar que `_filter_low_quality()` en el adaptador funciona correctamente. No crear filtro adicional en un hook separado.

### DT-09 — brain_watcher
**Sin cambios** — el watcher opera sobre pasaportes .md, no sobre la ingesta. Su corrección (gestión de excepciones en `run_in_executor`) no depende de pgvector.

### DT-10 — Deduplicación cross-space
**Antes:** Se proponía en `brain_hook.py`.

**Ahora:** Se implementa en `brain_ingestion_adapter.py` dentro de `_upsert_to_qdrant()`. La detección de duplicados se hace antes de insertar en Qdrant, no después.

### Circuit breaker Ollama
**Antes:** Se proponía para `brain_hook.py`.

**Ahora:** Se implementa en `unified_embedder.py` Y en el adaptador. Si Ollama no responde:
1. Embed fallido → circuit breaker abre → fallback a SurfSense original (`embed_texts` con MiniLM + pgvector)
2. Synthesis fallido → solo la síntesis se omite, los chunks SÍ se indexan en Qdrant

**Cambio clave:** El fallback del circuit breaker es "caer al pipeline SurfSense original", no "dejar el documento sin procesar". Esto es más robusto que el F7 original.

### Tests E2E
**Cambios en los tests:**

```python
# ANTES (F7 original):
# test verifica que el documento está en pgvector Y en Qdrant

# AHORA:
# test verifica que el documento está SOLO en Qdrant (vectores) + PostgreSQL (texto BM25)
# test verifica que el chat SurfSense busca en Qdrant

def test_python_ingestion_completes_pipeline(auth_headers, test_space):
    # 1. Subir fichero .py
    # 2. Esperar Celery
    # 3. Verificar: chunks en PostgreSQL (texto, SIN embedding)
    # 4. Verificar: vectores en Qdrant knowledge con search_space_id
    # 5. Verificar: pasaporte en Qdrant brain
    # 6. Consulta desde chat SurfSense → busca en Qdrant
    # 7. Consulta desde Brain Chat F4 → busca en Qdrant (mismos resultados)
```

### Runbook
**Cambios en el runbook:**
- pgvector ya no se usa para búsqueda vectorial — actualizar secciones relevantes
- Qdrant es el único motor de vectores — documentar backup con snapshots
- El procedimiento de migración de embedding model es más simple (solo Qdrant, no pgvector + Qdrant)
- Healthcheck: verificar Qdrant disponible, no pgvector HNSW index

---

## F8 — Agentes Especializados: cambios necesarios

### F8.1 — `brain_router_tool.py`
**Sin cambios funcionales** — BrainRouter ya busca en Qdrant (F4). Los agentes siguen usando el mismo tool.

### F8.1 — `bm25_search_tool.py`
**Sin cambios** — BM25 sigue en PostgreSQL sobre `chunks.content`. La herramienta funciona exactamente igual.

### F8.2 — Knowledge Synthesizer
**Mejora automática:** Los chunks recuperados por `brain_router_tool` ahora son de mayor calidad (procesados por UniversalCleaner, filtrados por quality_score). El agente no necesita cambios de código pero produce mejores síntesis.

### F8.3 — Project Intelligence
**Sin cambios funcionales** — el agente busca en las mismas colecciones Qdrant.

### F8.4 — Meeting Prep
**Sin cambios funcionales** — si busca en knowledge, los resultados son de Qdrant.

### F8.5 — Code Explainer
**Sin cambios funcionales** — busca en la colección `code` de Qdrant (ya existente en F2).

### Cambio transversal en F8
El middleware `knowledge_search.py` que usan los agentes de chat ya fue adaptado en F5.5 para usar nomic-embed-text + Qdrant. Por lo tanto:

**TODOS los agentes que llaman a `search_knowledge_base()` automáticamente usan Qdrant** sin necesidad de cambios en su código. La mejora es transparente.

### Nueva consideración para F8
Con el pipeline unificado, los agentes pueden ahora distinguir entre:
- Chunks procesados con extractor especializado (categoría A/B) → más ricos en metadata
- Chunks procesados con el "saco" (categoría C) → metadata básica

Los agentes podrían usar la metadata `category` para ponderar la fiabilidad de cada chunk. Esto no es obligatorio en F8 pero es una mejora futura documentable.

---

## Resumen de impacto

| Fase | Nivel de cambio | Qué cambia |
|------|:---------------:|-----------|
| **F6** | 🟡 Medio | UI de ingesta muestra categorías A/B/C, métricas unificadas, admin controla pipeline completo |
| **F7** | 🟢 Bajo | DT-05 ya resuelta por F5, circuit breaker en adaptador en vez de hook, tests E2E verifican Qdrant |
| **F8** | 🟢 Mínimo | Sin cambios de código — los agentes mejoran automáticamente por chunks de mayor calidad |

---

## Tabla resumen: qué sección de F6/F7/F8 cambia

| Sección original | Estado | Detalle |
|-----------------|--------|---------|
| F6.5 Brain Chat | ✅ Sin cambios | Ambos chats consultan Qdrant, endpoints no cambian |
| F6.8 Brain Ingest | 🟡 Adaptar | Vista de monitorización + columna categoría A/B/C |
| F6.9 Métricas | 🟡 Adaptar | Métricas reflejan todo SurfSense, no solo Brain |
| F6.10 Admin | 🟡 Adaptar | Controla pipeline unificado completo |
| F7 DT-01/02/03/07/09 | ✅ Sin cambios | Preprocesadores y watcher no dependen de pgvector |
| F7 DT-05 | ✅ Resuelta por F5 | quality_score se filtra en brain_ingestion_adapter |
| F7 DT-10 | 🟡 Mover | De brain_hook.py a brain_ingestion_adapter.py |
| F7 Circuit breaker | 🟡 Mover | De brain_hook.py a unified_embedder.py + adaptador |
| F7 Tests E2E | 🟡 Adaptar | Verificar Qdrant en vez de pgvector |
| F7 Runbook | 🟡 Adaptar | Reflejar arquitectura sin pgvector |
| F8.1-F8.5 Agentes | ✅ Sin cambios | search_knowledge_base ya fue adaptado en F5.5 |
