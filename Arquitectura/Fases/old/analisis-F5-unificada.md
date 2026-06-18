# Análisis: Integración SurfSense ↔ Second Brain — Propuesta F5 Unificada

## 1. Estado actual — Dos pipelines paralelos

### Pipeline SurfSense (hoy)
```
Conector/Upload → source_markdown
    → chunk_text_hybrid()              ← solo corta en tablas + tokens, sin limpieza
    → embed_texts(all-MiniLM-L6-v2)   ← 384 dimensiones
    → PostgreSQL: Chunk(content + embedding)  ← pgvector
    → PostgreSQL: Document(embedding)         ← summary vector
    → BM25 index (tsvector en chunks.content)
```
**Sin limpieza, sin quality_score, sin detección de idioma, sin PII.**

### Pipeline Second Brain (F1-F3)
```
Fichero → Extractor especializado (docx, pdf, py, csv...)
    → UniversalCleaner (quality_score, idioma, PII)
    → Preprocessing semántico por tipo
    → embed(nomic-embed-text)          ← 768 dimensiones
    → Qdrant: brain (pasaportes)
    → Qdrant: knowledge (chunks limpios)
    → Qdrant: code (chunks de código)
    → Síntesis LLM → pasaporte .md
```
**Potente, pero desconectado de los conectores de SurfSense.**

---

## 2. Quién consume pgvector en SurfSense

Rastreado en el código real. Hay **un único punto de consumo**:

```
Chat Agent (search_knowledge_base tool)
    → knowledge_search.py:search_knowledge_base()
        → embed_texts([query])  ← all-MiniLM-L6-v2, 384d
        → ChucksHybridSearchRetriever.hybrid_search()
            → pgvector: Chunk.embedding <=> query_embedding
            → BM25: to_tsvector(Chunk.content) @@ tsquery
            → Combina scores con RRF (Reciprocal Rank Fusion)
```

**Ficheros involucrados:**
| Fichero | Rol |
|---------|-----|
| `knowledge_search.py` | Orquesta embed + hybrid_search |
| `chunks_hybrid_search.py` | vector_search + full_text_search + hybrid_search |
| `documents_hybrid_search.py` | Document-level vector search (menos usado) |
| `search_knowledge_base.py` | Tool del chat agent que llama a knowledge_search |

**Conclusión: si reemplazamos `hybrid_search()` para que consulte Qdrant en vez de pgvector, TODO el chat de SurfSense usa el nuevo backend automáticamente.**

---

## 3. Mapa de tipos — Second Brain vs SurfSense

### Conectores SurfSense (20+)
| Conector | DocumentType | Qué produce | ¿Second Brain tiene extractor? |
|----------|-------------|-------------|-------------------------------|
| Confluence | CONFLUENCE_CONNECTOR | markdown | ✅ `confluence.py` |
| Jira | JIRA_CONNECTOR | markdown | ✅ `jira_ticket.py` |
| GitHub | GITHUB_CONNECTOR | markdown/código | ✅ `github_file.py` + `python_file.py` |
| Notion | NOTION_CONNECTOR | markdown | ⚠️ Genérico (markdown.py) |
| Slack | SLACK_CONNECTOR | texto plano | ⚠️ Solo txt.py — efímero |
| Discord | DISCORD_CONNECTOR | texto plano | ⚠️ Solo txt.py — efímero |
| Gmail | GOOGLE_GMAIL_CONNECTOR | texto/html | ⚠️ Genérico — efímero |
| Google Calendar | GOOGLE_CALENDAR_CONNECTOR | texto | ⚠️ Genérico — efímero |
| Google Drive | GOOGLE_DRIVE_FILE | PDF/DOCX/etc | ✅ Según extensión del fichero |
| OneDrive | ONEDRIVE_FILE | PDF/DOCX/etc | ✅ Según extensión del fichero |
| Dropbox | DROPBOX_FILE | PDF/DOCX/etc | ✅ Según extensión del fichero |
| Linear | LINEAR_CONNECTOR | markdown | ⚠️ Genérico (markdown.py) |
| ClickUp | CLICKUP_CONNECTOR | markdown | ⚠️ Genérico |
| Airtable | AIRTABLE_CONNECTOR | JSON/tabla | ⚠️ Genérico |
| Webcrawler | WEBCRAWLER_CONNECTOR | HTML | ✅ `web.py` |
| Bookstack | BOOKSTACK_CONNECTOR | markdown | ⚠️ Genérico (markdown.py) |
| YouTube | YOUTUBE_VIDEO | transcripción | ⚠️ Genérico (txt.py) |
| Teams | TEAMS_CONNECTOR | texto | ⚠️ Genérico — efímero |
| Elasticsearch | ELASTICSEARCH_CONNECTOR | JSON | ⚠️ Genérico |
| Luma | LUMA_CONNECTOR | texto | ⚠️ Genérico |
| Local Folder | LOCAL_FOLDER_FILE | cualquier fichero | ✅ Según extensión |
| File Upload | FILE | cualquier fichero | ✅ Según extensión |
| Obsidian | OBSIDIAN_CONNECTOR | markdown | ✅ `markdown.py` |

### Extractores Second Brain (ficheros binarios)
| Extractor | Extensiones | Qué aporta sobre "raw markdown" |
|-----------|------------|--------------------------------|
| `docx.py` | .docx | Headings jerárquicos, tablas ANS, metadata Word |
| `pdf.py` | .pdf | OCR, secciones, tablas, quality_score |
| `pptx.py` | .pptx | Slides como secciones, notas del presenter |
| `xlsx.py` | .xlsx | Hojas como secciones, estadísticas columnas |
| `csv.py` | .csv | Análisis de columnas, detección tipo datos |
| `python_file.py` | .py | AST parse, funciones/clases como bloques |
| `sql_file.py` | .sql | Statements parsed, tablas/vistas extraídas |
| `ipynb.py` | .ipynb | Celdas markdown/code separadas |
| `markdown.py` | .md | Headings como secciones, code blocks |
| `txt.py` | .txt | Detección subtipo (log, nota, doc) |
| `web.py` | .html | DOM → secciones semánticas, tablas |
| `xml_ext.py` | .xml | Nodos como secciones |
| `json_ext.py` | .json | Claves como secciones, arrays tabulados |
| `drawio.py` | .drawio | Nodos y conexiones del diagrama |

---

## 4. El problema del "saco" — Tu pregunta clave

Second Brain tiene extractores para ~15 formatos de fichero. Pero SurfSense tiene **30+ DocumentTypes** y los conectores ya entregan `source_markdown` — texto ya extraído.

**Hay tres categorías de contenido:**

### Categoría A — Fichero binario con extractor especializado
Uploads directos y ficheros de Drive/OneDrive/Dropbox donde Second Brain tiene extractor.
```
.docx → DocxExtractor → headings, tablas, metadata → UniversalCleaner → Qdrant
.pdf  → PdfExtractor  → OCR, secciones            → UniversalCleaner → Qdrant
.py   → PythonExtractor → AST, funciones           → UniversalCleaner → Qdrant
```
**Pipeline completo de Second Brain. Máxima calidad.**

### Categoría B — Conector con extractor virtual (texto→bloques)
Confluence, Jira, GitHub, Webcrawler — Second Brain ya tiene extractores virtuales que toman el `source_markdown` del conector y lo procesan.
```
Confluence markdown → ConfluenceExtractor → secciones → UniversalCleaner → Qdrant
Jira markdown       → JiraTicketExtractor → campos    → UniversalCleaner → Qdrant
```
**Pipeline semi-especializado. Buena calidad.**

### Categoría C — El "saco" (tu propuesta)
Slack, Discord, Gmail, Calendar, Airtable, Luma, Teams, Linear, ClickUp, YouTube, Elasticsearch, Obsidian, Bookstack, Notion (cuando no hay extractor específico).

Estos conectores entregan `source_markdown` pero Second Brain NO tiene un extractor específico. Hoy SurfSense los procesa con chunking básico.

**La propuesta "saco" es:**
```
source_markdown (de cualquier conector)
    → UniversalCleaner(text)           ← calidad, idioma, PII
    → Chunking semántico genérico      ← corta por headings ##/### si los hay,
                                          por párrafos si no, respetando tablas
    → embed(nomic-embed-text, 768d)    ← mismo modelo que el resto del Brain
    → Qdrant: colección knowledge      ← unificado con el resto
    → (Opcional) Síntesis → pasaporte  ← si el contenido es sustancial
```

**El "saco" no es una chapuza — es el fallback inteligente que aplica lo mejor de Second Brain (limpieza + embedding potente) a contenido que no necesita extracción especializada.**

---

## 5. Visión de la F5 Unificada

### El flujo propuesto
```
Documento llega (conector o upload)
          │
          ▼
¿Tiene fichero binario con extensión conocida?
    │                    │
   SÍ                   NO (ya es source_markdown)
    │                    │
    ▼                    ▼
Categoría A:         ¿Hay extractor virtual?
Second Brain             │          │
Extractor               SÍ         NO
completo                 │          │
    │                    ▼          ▼
    │               Categoría B: Categoría C:
    │               Extractor    "SACO" genérico
    │               virtual      UniversalCleaner
    │                    │       + chunking semántico
    │                    │          │
    ▼                    ▼          ▼
    └──────── TODOS ────────────────┘
                    │
                    ▼
            UniversalCleaner
            (quality_score, idioma, PII)
                    │
                    ▼
            embed(nomic-embed-text, 768d)
                    │
                    ▼
        ┌───────────┴──────────────┐
        ▼                          ▼
  PostgreSQL                    Qdrant
  chunks.content (BM25)      knowledge (vectores)
  Document metadata           brain (pasaportes)
  ← SIN embedding             code (código)
  ← Solo texto + FK
        │                          │
        └──────────┬───────────────┘
                   ▼
        ChucksHybridSearchRetriever
        (BM25 de PostgreSQL + vectores de Qdrant)
                   │
                   ▼
        Chat agent search_knowledge_base
        Router F4 brain_query
```

### Lo que cambia respecto al plan F5 original
| Aspecto | F5 original (hook) | F5 unificada |
|---------|-------------------|-------------|
| pgvector | Se mantiene (redundante) | Se elimina Chunk.embedding |
| Embedding model | Dos (MiniLM + nomic) | Uno (nomic-embed-text) |
| Chunks duplicados | Sí (PG + Qdrant) | No (PG texto + Qdrant vector) |
| Chat de SurfSense | Usa pgvector | Usa Qdrant |
| Calidad de chunks | SurfSense: básico, Brain: limpio | Todo limpio |
| "Saco" genérico | No existe | Cleaner + chunking semántico |
| Latencia ingesta | Doble embedding | Un solo embedding |
| BM25 | PostgreSQL | PostgreSQL (sin cambios) |

### Ficheros a modificar
| Fichero | Cambio |
|---------|--------|
| `indexing_pipeline_service.py` | Reemplazar `chunk_text_hybrid + embed_texts` por pipeline Brain |
| `chunks_hybrid_search.py` | `vector_search()` → consulta Qdrant en vez de pgvector |
| `knowledge_search.py` | `embed_texts` → `nomic-embed-text` vía Ollama |
| `documents_hybrid_search.py` | Adaptar a Qdrant o eliminar si no se usa |
| `db.py` | Eliminar `Chunk.embedding` (migración Alembic) |
| `brain_hook.py` (nuevo) | Orquesta: categoría A/B/C → Cleaner → Qdrant |
| `config/__init__.py` | `EMBEDDING_MODEL=nomic-embed-text` |

### Ficheros que NO cambian
- **Conectores** (20 indexers) — siguen produciendo `ConnectorDocument`
- **Brain F1-F4** — ya usa Qdrant, sin cambios
- **BM25** — sigue en PostgreSQL sobre `chunks.content`
- **UI, auth, Celery, multi-tenant** — intacto

---

## 6. ¿Es técnicamente viable?

**Sí.** Las razones:

1. **Un solo punto de consumo de pgvector** — `hybrid_search()` es la única puerta. Cambiarlo ahí cambia todo SurfSense de golpe.

2. **BM25 no se toca** — sigue en PostgreSQL, independiente de los vectores.

3. **Los conectores no cambian** — siguen produciendo `ConnectorDocument(source_markdown=...)`, el cambio es DESPUÉS de ellos.

4. **Qdrant ya está desplegado** — F2 lo puso en marcha, las colecciones existen.

5. **El "saco" es simple** — `UniversalCleaner(text) → chunking por headings/párrafos → embed → Qdrant`. No necesita extractor nuevo, usa lo que ya existe.

---

## 7. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Latencia de embedding con Ollama (nomic local) | Es ~50ms/chunk en CPU — comparable a MiniLM. No hay degradación |
| Migración de vectores existentes en pgvector | Migración batch: re-embed con nomic y upsert a Qdrant |
| Mensajes efímeros (Slack, Discord) saturan Qdrant | `_SKIP_SYNTHESIS_TYPES` los excluye del pasaporte, pero SÍ van a knowledge con quality_score |
| SurfSense actualiza y rompe hybrid_search | El cambio es una capa de abstracción; el contrato de `hybrid_search()` no cambia |
