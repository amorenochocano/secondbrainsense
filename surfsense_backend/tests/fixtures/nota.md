# nota.md — fixture para tests del pipeline Brain F1.
# Fragmento real de ARQUITECTURA_SECOND_BRAIN.md del proyecto Second Brain.
# Contiene headings H1/H2/H3, listas y texto narrativo.

# Second Brain v4 — Arquitectura

## Visión general

Second Brain es un sistema RAG empresarial diseñado para indexar, clasificar
y recuperar conocimiento técnico corporativo. Combina extracción semántica,
limpieza universal y síntesis multi-call para producir pasaportes semánticos
de alta calidad sobre cualquier tipo de documento.

## Principios de diseño

### P1 — Pipeline de tres fases

Todo documento pasa por tres fases antes de indexarse:

1. **Fase 1**: Extracción — extractor específico por formato (PDF, DOCX, PY, SQL...)
2. **Fase 2**: Limpieza universal — UniversalCleaner (encoding, unicode, PII, quality score)
3. **Fase 3**: Preprocesado semántico — módulo específico por tipo que inyecta marcas ##/###

### P2 — Pasaporte semántico

Cada documento produce un pasaporte `.md` con 7 secciones:

- **Resumen ejecutivo**: qué es el documento en 3-5 frases
- **Core Knowledge**: conocimiento estructurado por secciones ###
- **Entidades**: tecnologías, sistemas y conceptos detectados
- **Tags normalizados**: vocabulario controlado (pyspark, delta-lake...)
- **Insights**: patrones, decisiones implícitas, riesgos
- **Uso práctico**: cuándo y cómo usar el documento
- **Pitfalls**: errores comunes y limitaciones

### P3 — Router multinivel

El router BrainRouter implementa una cascada L1 → L2 → L0:

- **L1** (colección `brain`): pasaportes semánticos, alta precisión
- **L2** (colección `knowledge`): chunks de documentos, alta cobertura
- **L0** (fallback web): búsqueda externa si L1+L2 no son suficientes

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Vector store | Qdrant (3 colecciones: brain, knowledge, code) |
| Embeddings | nomic-embed-text (768d), qwen3-embedding:4b (2560d) |
| LLM síntesis | qwen2.5-coder:7b (Ollama local) |
| LLM chat | qwen2.5-coder:3b (Ollama local) |
| Backend | FastAPI + Celery + Redis |
| Base de datos | PostgreSQL + pgvector |

## Tipos de documento soportados

El pipeline soporta 14 tipos documentales con preprocesadores específicos:

- **Código**: `.py`, `.sql`, `.ipynb`
- **Documentos ricos**: `.pdf`, `.docx`, `.pptx`
- **Datos**: `.xlsx`, `.csv`
- **Config/orquestación**: `.json`, `.xml`, `.drawio`
- **Markup**: `.md`, `.html`, `.txt`
- **Conectores API**: `.confluence`, `.jira_ticket`, `.github_file`

## Variables de entorno clave

```bash
SYNTHESIS_ENABLED=true
QUALITY_TRIGGER_ENABLED=true
CHUNKING_STRATEGY=semantic
BRAIN_DATA_PATH=/data/brain
PASSPORT_WATCHER_ENABLED=true
```
