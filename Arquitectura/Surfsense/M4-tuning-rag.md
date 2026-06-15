# M4 — Tuning del RAG con Ollama
> Nivel: Avanzado · Tiempo estimado: 3h  
> Objetivo: optimizar la calidad de las respuestas para tu base de conocimiento técnica

---

## 4.1 Cómo funciona el RAG internamente

SurfSense implementa un pipeline RAG de dos fases:

```
CAPA 1 — Índice de Documentos (document-level)
  • 1 embedding por documento completo
  • Tabla: document (PostgreSQL + pgvector)
  • Permite encontrar "qué documentos son relevantes"

CAPA 2 — Índice de Chunks (chunk-level)
  • N embeddings por documento (fragmentos semánticos)
  • Tabla: document_segment (PostgreSQL + pgvector)
  • Permite encontrar "qué fragmento exacto responde la pregunta"

BÚSQUEDA HÍBRIDA (en cada query):
  Vector search  →  similitud semántica (coseno)
       +
  Full-text BM25 →  coincidencia de palabras clave
       ↓
  RRF (Reciprocal Rank Fusion) — fusiona ambos rankings
       ↓
  Reranking (opcional) — reordena los top-K resultados
       ↓
  Top-N chunks → contexto para el LLM (Ollama)
```

La búsqueda híbrida es la clave: una query sobre "función refreshToken" se beneficia tanto de la similitud semántica (el concepto de "renovar tokens") como del match exacto del nombre de la función.

---

## 4.2 Variables de configuración RAG en .env

```bash
# ── Chunking ────────────────────────────────────────────────
# Tamaño de chunk en tokens
# Para código:      256-512  (función completa)
# Para docs prosa:  512-1024 (párrafo/sección completa)
# Para runbooks:    512      (paso a paso)
CHUNK_SIZE=512
CHUNK_OVERLAP=64   # solapamiento entre chunks consecutivos

# ── Retrieval ───────────────────────────────────────────────
# Cuántos chunks recuperar ANTES del reranking
TOP_K_RETRIEVAL=15   # más es más preciso pero más lento

# Cuántos chunks pasar AL LLM después del reranking
# Ajusta según el context window de tu modelo Ollama:
#   llama3.2  (8k tokens)  → 5
#   mistral   (32k tokens) → 10
#   qwen2.5   (128k tokens)→ 20
TOP_K_FINAL=8

# ── Embeddings con Ollama ───────────────────────────────────
EMBEDDING_MODEL=local://nomic-ai/nomic-embed-text-v1.5

# Alternativa con más dimensiones (mejor calidad, más RAM):
# EMBEDDING_MODEL=local://mxbai-embed-large

# ── Reranker local (sin API key) ───────────────────────────
# Flashrank funciona 100% local, sin coste adicional
RERANKER=flashrank

# ── Procesado de documentos ─────────────────────────────────
# DOCLING: sin API key, excelente para código y Markdown
# UNSTRUCTURED: mejor OCR y tablas complejas (requiere API key)
ETL_SERVICE=DOCLING
```

---

## 4.3 Ajustar según el modelo Ollama

Cada modelo tiene un context window diferente. Configurar mal esto degrada la calidad.

```bash
# Ver el context window de un modelo
curl http://localhost:11434/api/show \
  -d '{"name": "llama3.2:latest"}' | python3 -m json.tool | grep "context"
```

| Modelo | Context window | TOP_K_FINAL recomendado | CHUNK_SIZE recomendado |
|--------|---------------|------------------------|----------------------|
| llama3.2:latest | 8k | 4-5 | 256-512 |
| mistral:latest | 32k | 8-12 | 512 |
| qwen2.5:7b | 128k | 15-20 | 512-1024 |
| qwen2.5:14b | 128k | 20-25 | 512-1024 |

> Si las respuestas parecen cortadas o el modelo dice "no tengo suficiente contexto", sube `TOP_K_FINAL`. Si las respuestas son lentas o el modelo alucina mezclando información, bájalo.

---

## 4.4 Diagnóstico de calidad del RAG

### Test 1 — Verificar chunking

```bash
# Ver cómo se chunkearon los documentos
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT d.title,
             count(ds.id) as num_chunks,
             avg(length(ds.content))::int as avg_chars,
             min(length(ds.content)) as min_chars,
             max(length(ds.content)) as max_chars
      FROM document d
      JOIN document_segment ds ON d.id = ds.document_id
      GROUP BY d.title
      ORDER BY num_chunks DESC
      LIMIT 15;"
```

Si `avg_chars` es muy bajo (< 200) → chunks demasiado pequeños, pierde contexto.  
Si `avg_chars` es muy alto (> 2000) → chunks demasiado grandes, demasiado ruido.

### Test 2 — Verificar embeddings generados

```bash
# Debe devolver 0 (ningún chunk sin embedding)
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as pendientes
      FROM document_segment
      WHERE embedding IS NULL;"

# Si hay pendientes, hay un problema con Ollama o los embeddings
# Revisa los logs del worker:
docker exec surfsense supervisorctl tail -f celery-worker
```

### Test 3 — Preguntas de control (calidad subjetiva)

Haz estas preguntas en el chat y verifica que:

1. Cita el fichero/doc correcto como fuente
2. La respuesta es factualmente correcta según tu conocimiento
3. No mezcla información de diferentes proyectos

```
❓ "¿Qué hace la función [nombre_función_real]?"
   → Debe citar el fichero de código correcto

❓ "¿Cuál es la decisión de arquitectura sobre [tema]?"
   → Debe citar el ADR o doc de arquitectura correcto

❓ "¿Cómo se configura [herramienta_interna]?"
   → Debe citar el runbook o README correcto

❓ "[Pregunta sobre algo que NO está indexado]"
   → Debe responder que no tiene esa información
      (si alucina respuestas, hay un problema de configuración)
```

### Test 4 — Medir latencia de queries

```bash
# Query simple para medir tiempo de respuesta
time curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "search_space_id": "{SPACE_ID}",
    "messages": [{"role": "user", "content": "hola, qué puedes hacer?"}]
  }'
```

Tiempos de referencia con Ollama local:
- < 5s: excelente
- 5-15s: aceptable para llama3.2
- > 15s: considera un modelo más pequeño o bajar TOP_K_FINAL

---

## 4.5 Optimizaciones específicas para código fuente

El código fuente tiene características especiales que el chunking por tokens no respeta bien (parte funciones por la mitad). Configuraciones recomendadas:

```bash
# Chunking más pequeño para código (cada función encaja en un chunk)
CHUNK_SIZE=256
CHUNK_OVERLAP=32

# ETL_SERVICE=DOCLING es el mejor para código y Markdown
ETL_SERVICE=DOCLING
```

Para los ficheros de código, SurfSense usa el lenguaje de programación como señal de chunking cuando es posible. Verifica que los ficheros `.py`, `.ts`, `.go`, etc. se están indexando correctamente:

```bash
# Ver qué tipos de fichero están indexados
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT
        split_part(title, '.', -1) as extension,
        count(*) as ficheros
      FROM document
      WHERE connector_id IS NOT NULL
      GROUP BY extension
      ORDER BY ficheros DESC;"
```

---

## 4.6 Re-indexar con nueva configuración

Si cambias `CHUNK_SIZE`, `EMBEDDING_MODEL` o `ETL_SERVICE`, necesitas re-indexar para que los cambios tengan efecto en documentos existentes.

```bash
# Parar el contenedor
docker stop surfsense

# Editar .env con los nuevos valores
nano .env

# Reiniciar
docker start surfsense

# Esperar que arranque (~60 segundos)
sleep 60

# Forzar re-indexación de todos los conectores
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors/reindex-all" \
  -H "Authorization: Bearer $TOKEN"
```

> ⚠️ La re-indexación completa puede tardar minutos u horas según el volumen de documentos. Hazla fuera del horario de uso del equipo.

---

## Checklist del módulo

- [ ] `TOP_K_FINAL` ajustado según el context window de tu modelo Ollama
- [ ] `CHUNK_SIZE` configurado (256 para código, 512 para docs)
- [ ] Test de calidad ejecutado con preguntas reales
- [ ] Latencia de queries medida y aceptable
- [ ] Embeddings verificados (0 pendientes)

---

**Anterior:** [M3 — Conectores](./M3-conectores.md)  
**Siguiente:** [M5 — Backups y Mantenimiento](./M5-backups-mantenimiento.md)
