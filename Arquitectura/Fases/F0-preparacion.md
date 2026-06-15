# F0 — Preparación y Setup
**Duración:** 3 días  
**Equipo:** Tech Lead + DevOps  
**Dependencias:** ninguna  
**Entregable:** monorepo configurado, entorno dev funcional, CI básico

---

## Objetivo

Crear la base técnica sobre la que todas las fases posteriores construyen. Sin una F0 sólida, las fases siguientes acumulan deuda desde el primer día.

---

## F0.1 — Fork y estructura del monorepo (Día 1)

### Acción
Fork de `MODSetter/SurfSense` como repositorio base. Second Brain **no** es el repo base — SurfSense aporta más infraestructura que requiere más cambios.

### Estructura de directorios resultante

```
secondbrainsense/                          ← nombre del fork
├── surfsense_backend/
│   └── app/
│       ├── routes/                  ← SurfSense (intacto inicialmente)
│       ├── connectors/              ← SurfSense (intacto)
│       ├── tasks/                   ← SurfSense (modificado en F1)
│       ├── utils/
│       │   └── document_converters.py   ← PUNTO DE INTEGRACIÓN PRINCIPAL
│       │
│       └── brain/                   ← NUEVO — Second Brain migrado aquí
│           ├── extractors/          ← tus 16 extractores
│           │   ├── __init__.py      ← _CleaningExtractorWrapper
│           │   ├── factory.py
│           │   ├── base.py
│           │   ├── python_file.py
│           │   ├── sql_file.py
│           │   └── ...
│           ├── rag_lib/
│           │   └── layer1_universal/
│           │       └── universal_cleaner.py
│           ├── preprocessing/       ← tus 14 preprocesadores
│           │   ├── py.py
│           │   ├── sql.py
│           │   └── ...
│           ├── prompts/             ← síntesis multi-call
│           │   ├── __init__.py      ← _TYPE_INSTRUCTIONS_FOCUSED
│           │   ├── planner.py
│           │   ├── builder.py
│           │   ├── type_specs.py
│           │   └── preprocessing/   ← prompts por tipo
│           ├── passport_builder.py
│           ├── synthesizer.py
│           ├── writer.py
│           ├── ingest_router.py
│           ├── router.py            ← BrainRouter (cascade L1→L2→L0)
│           ├── graph.py
│           ├── brain_watcher.py
│           ├── llm_client.py
│           ├── collections.py
│           └── sharepoint_client.py
│
├── surfsense_web/                   ← SurfSense frontend (Next.js)
├── docker-compose.yml               ← MODIFICADO en F0.3
└── tests/
    ├── brain/                       ← tests del pipeline Second Brain
    └── integration/                 ← tests de integración
```

### Comandos

```bash
# 1. Ir a https://github.com/MODSetter/SurfSense → Fork → nombre: secondbrainsense → cuenta: amorenochocano
# 2. Una vez creado el fork, clonarlo:
git clone https://github.com/amorenochocano/secondbrainsense
cd secondbrainsense

# Añadir upstream para mantener sincronía con SurfSense
git remote add upstream https://github.com/MODSetter/SurfSense
git remote add amch https://github.com/amorenochocano/AMCH  # Second Brain es una carpeta dentro de AMCH

# Crear rama de integración
git checkout -b integration/f0-setup
```

### Migración del código de Second Brain

```powershell
# AMCH ya está clonado en local en:
# C:\Users\EN31380\OneDrive - Enagás, S.A\Documentos\Mis_proyectos\AMCH\second brain

$AMCH = "C:\Users\EN31380\OneDrive - Enagás, S.A\Documentos\Mis_proyectos\AMCH\second brain"
$DEST = "surfsense_backend\app\brain"

# Copiar directorio brain completo al monorepo
Copy-Item -Recurse -Force "$AMCH\app\brain" $DEST
Copy-Item -Recurse -Force "$AMCH\app\api\extractors" "$DEST\extractors"
Copy-Item -Recurse -Force "$AMCH\app\rag_lib" "$DEST\rag_lib"

# Verificar que los imports relativos sean correctos
# Los imports del código migrado usan: from app.brain.extractors import ...
```

---

## F0.2 — Dependencias y requirements (Día 1-2)

### Añadir al `requirements.txt` de SurfSense

```txt
# ── Second Brain: extracción ──────────────────────────
PyMuPDF>=1.23.0          # PDF con headings por tamaño de fuente
python-docx>=1.1.0       # DOCX con jerarquía de headings
python-pptx>=0.6.23      # PPTX con SmartArt y notas
openpyxl>=3.1.0          # XLSX multi-hoja
nbformat>=5.9.0          # Jupyter notebooks
beautifulsoup4>=4.12.0   # HTML/HTM

# ── Second Brain: limpieza universal ──────────────────
ftfy>=6.1.3              # Corrección mojibake y encoding
lingua-language-detector>=2.0.0   # Detección de idioma
presidio-analyzer>=2.2.0          # Detección de PII
presidio-anonymizer>=2.2.0

# ── Second Brain: síntesis ────────────────────────────
# (sin dependencias adicionales — usa el LLM client de SurfSense)

# ── Qdrant (sustituye pgvector para vectores) ─────────
qdrant-client>=1.9.0

# ── Reranking local ───────────────────────────────────
flashrank>=0.2.0         # Cross-encoder local sin API key

# ── Sentence transformers (precargar en build) ────────
sentence-transformers>=2.7.0
```

### Variables de entorno nuevas (añadir a `.env`)

```bash
# ── Modelos de embedding diferenciados ────────────────
EMBED_MODEL=nomic-embed-text           # brain + knowledge (768d)
EMBED_MODEL_CODE=qwen3-embedding:4b   # code (2560d)

# ── Modelos LLM diferenciados por tarea ───────────────
OLLAMA_SYNTHESIS_MODEL=qwen2.5-coder:7b    # síntesis L1 (pasaporte)
OLLAMA_CHAT_MODEL=qwen2.5-coder:3b         # chat L2 (conversacional)

# ── Qdrant ────────────────────────────────────────────
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# ── Pipeline Brain ────────────────────────────────────
SYNTHESIS_ENABLED=true
CHUNKING_STRATEGY=semantic
QUALITY_TRIGGER_ENABLED=true
VISION_MODE_DEFAULT=off
BRAIN_DATA_PATH=/data/brain

# ── Pasaporte ─────────────────────────────────────────
PASSPORT_PERSIST=true
PASSPORT_WATCHER_ENABLED=true
```

---

## F0.3 — Docker Compose actualizado (Día 2)

```yaml
# docker-compose.yml — secondbrainSense Platform
version: '3.9'

services:
  backend:
    build: ./surfsense_backend
    container_name: brainsense-backend
    ports:
      - "8000:8000"
    volumes:
      - brain-data:/data/brain        # pasaportes .md
      - ./surfsense_backend:/app      # hot-reload dev
    env_file: .env
    depends_on:
      - postgres
      - redis
      - qdrant
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Ollama en el host

  frontend:
    build: ./surfsense_web
    container_name: brainsense-frontend
    ports:
      - "3000:3000"
    env_file: .env

  celery-worker:
    build: ./surfsense_backend
    container_name: brainsense-celery
    command: celery -A app.celery_app worker --loglevel=info -Q default,brain_ingestion
    volumes:
      - brain-data:/data/brain
    env_file: .env
    depends_on:
      - postgres
      - redis
      - qdrant
    extra_hosts:
      - "host.docker.internal:host-gateway"

  celery-beat:
    build: ./surfsense_backend
    container_name: brainsense-beat
    command: celery -A app.celery_app beat --loglevel=info
    env_file: .env
    depends_on:
      - redis

  postgres:
    image: postgres:16
    container_name: brainsense-postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres-data:/var/lib/postgresql/data

  qdrant:
    image: qdrant/qdrant:latest
    container_name: brainsense-qdrant
    ports:
      - "6333:6333"
    volumes:
      - qdrant-data:/qdrant/storage

  redis:
    image: redis:7-alpine
    container_name: brainsense-redis
    volumes:
      - redis-data:/data

volumes:
  postgres-data:
  qdrant-data:
  brain-data:
  redis-data:
```

---

## F0.4 — Tests de smoke (Día 3)

Antes de empezar F1, verificar que el entorno funciona:

```bash
# Arrancar el stack completo
docker compose up -d

# Smoke test 1: API SurfSense responde
curl http://localhost:8000/health

# Smoke test 2: Qdrant responde
curl http://localhost:6333/health

# Smoke test 3: Ollama accesible desde el contenedor
docker exec brainsense-backend \
  curl http://host.docker.internal:11434/api/tags

# Smoke test 4: Importar módulos de Second Brain sin errores
docker exec brainsense-backend python3 -c "
from app.brain.extractors import get_extractor_for_extension
from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner
from app.brain.preprocessing import py as py_preprocessor
print('✅ Todos los módulos de Second Brain importan correctamente')
"

# Smoke test 5: UniversalCleaner funciona
docker exec brainsense-backend python3 -c "
from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner
uc = UniversalCleaner()
block = {'content': 'Texto de prueba con  espacios  dobles', 'content_type': 'text'}
cleaned = uc.clean_block(block)
print(f'✅ UniversalCleaner: quality_score={cleaned[\"metadata\"][\"quality_score\"]}')
"
```

---

## Checklist F0

- [ ] Fork creado con remote `upstream` apuntando a SurfSense
- [ ] Código de Second Brain migrado a `surfsense_backend/app/brain/`
- [ ] `requirements.txt` actualizado y `docker compose build` exitoso
- [ ] `docker-compose.yml` con Qdrant añadido
- [ ] Variables de entorno documentadas en `.env.example`
- [ ] 5 smoke tests pasando
- [ ] Rama `integration/f0-setup` mergeada a `main`

---

**Siguiente:** [F1 — Pipeline de Tres Fases en SurfSense](./F1-pipeline-tres-fases.md)
