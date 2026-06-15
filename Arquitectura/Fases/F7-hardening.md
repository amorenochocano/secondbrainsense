# F7 — Hardening, Deuda Técnica y Producción
**Duración:** 1 semana  
**Equipo:** Tech Lead (1) + Backend Senior (1) + DevOps (0.5)  
**Dependencias:** F6 completada  
**Entregable:** Sistema en producción, deuda técnica documentada y priorizada, runbook operacional

---

## Objetivo

Consolidar las 8 fases anteriores en un sistema estable y operable. Resolver las deudas técnicas críticas (DT-01 a DT-09 del documento de arquitectura), asegurar que los fallos son recuperables, y documentar el sistema para el equipo.

---

## F7.1 — Resolución de deuda técnica crítica (Día 1-3)

### DT-01 — Contaminación circular en re-síntesis

**Problema:** cuando se re-sintetiza un documento, el sistema puede leer el pasaporte `.md` existente como si fuera la fuente original, creando un loop donde el LLM resume su propio resumen previo.

**Solución:**

```python
# surfsense_backend/app/brain/synthesizer.py — añadir

def _get_source_content_for_resynthesis(source: str, db_session) -> tuple[bytes, str]:
    """
    Para re-síntesis, siempre usar la fuente original del conector
    NUNCA el pasaporte .md existente.
    """
    doc = db_session.query(Document).filter(Document.source == source).first()
    if not doc:
        raise ValueError(f"Documento no encontrado: {source}")

    if doc.connector_id:
        # Documento de conector → re-fetch desde el origen
        connector = _get_connector(doc.connector_id)
        return connector.fetch_raw_content(doc.source_id), doc.filename
    elif doc.file_path:
        # Documento subido manualmente → leer fichero original
        # (debe estar en /data/uploads/, no en /data/brain/)
        if "/data/brain/" in doc.file_path:
            raise ValueError(
                f"DT-01: Intento de re-síntesis desde pasaporte .md, no fuente original. "
                f"source={source}"
            )
        content = Path(doc.file_path).read_bytes()
        return content, doc.filename
    else:
        raise ValueError(f"Sin fuente original para: {source}")
```

### DT-02 — Chunking code vs text en mismo documento

**Problema:** un fichero `.md` puede tener bloques de código y bloques de prosa. Actualmente todos los bloques van a `knowledge` con `nomic-embed-text` (768d). Los bloques de código deberían ir a `code` con `qwen3-embedding` (2560d).

**Solución:** en `IngestRouter._ingest_knowledge()`, filtrar los bloques de código y re-rutarlos:

```python
# ingest_router.py — actualización

async def _ingest_knowledge(self, source, search_space_id, processed_text, blocks):
    """Bloques de texto → knowledge. Bloques de código → code (re-ruta)."""
    text_blocks = [b for b in blocks if b.get("content_type") != "code"]
    code_blocks_in_md = [b for b in blocks if b.get("content_type") == "code"]

    # Procesar texto normal
    if text_blocks:
        await self._ingest_text_blocks(source, search_space_id, text_blocks)

    # Re-rutar código a la colección code aunque venga de un .md
    if code_blocks_in_md:
        await self._ingest_code(source, search_space_id, code_blocks_in_md)
```

### DT-03 — Fallback cuando Ollama no responde

```python
# surfsense_backend/app/brain/llm_client.py — añadir circuit breaker

import asyncio
from functools import wraps

class OllamaCircuitBreaker:
    """Evita cascada de fallos cuando Ollama está saturado."""

    def __init__(self, failure_threshold=3, recovery_timeout=60):
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open

    def is_open(self) -> bool:
        if self.state == "open":
            if (time.time() - self.last_failure_time) > self.recovery_timeout:
                self.state = "half-open"
                return False
            return True
        return False

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.threshold:
            self.state = "open"

# En BrainLLMClient.generate():
async def generate(self, prompt, system, model=None, **kwargs):
    if self._circuit_breaker.is_open():
        # Fallback: devolver pasaporte parcial sin síntesis LLM
        return f"[Síntesis no disponible — Ollama no responde]\n\n{prompt[:500]}..."

    try:
        result = await self._generate_internal(prompt, system, model, **kwargs)
        self._circuit_breaker.record_success()
        return result
    except Exception as e:
        self._circuit_breaker.record_failure()
        raise
```

### DT-04 — Source Extract sin contaminar re-síntesis

El `Source Extract` en el pasaporte `.md` puede crecer con cada re-síntesis si el sistema no lo limpia. Solución ya documentada en `brain.py` de Second Brain: sanear el Source Extract antes de escribir el nuevo pasaporte.

### DT-05 — Deduplicación cross-search-space

Un mismo fichero subido en dos Search Spaces diferentes genera dos registros en PostgreSQL con el mismo `content_hash`. El sistema debe detectarlo y reusar los vectores Qdrant (mismo contenido, diferente `search_space_id` en el payload).

---

## F7.2 — Tests de integración end-to-end (Día 2-3)

```python
# tests/integration/test_e2e.py

import pytest
import httpx
import asyncio

BASE_URL = "http://localhost:8000"

class TestE2EIngestionQuery:

    @pytest.fixture(autouse=True)
    def setup(self):
        # Login y obtener token
        r = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                       json={"email": "admin@test.com", "password": "test"})
        self.token = r.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        # Search space de test
        r = httpx.post(f"{BASE_URL}/api/v1/search-spaces",
                       json={"name": "test-e2e"}, headers=self.headers)
        self.space_id = r.json()["id"]

    def test_python_file_ingestion_complete_pipeline(self):
        """
        E2E: subir .py → pipeline 3 fases → pasaporte generado → vectores en Qdrant → consulta L2
        """
        python_content = '''
def authenticate_user(username: str, password: str) -> dict:
    """Autentica un usuario contra la base de datos."""
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise AuthenticationError("Credenciales inválidas")
    return {"user_id": user.id, "token": generate_jwt(user.id)}
'''
        # 1. Subir fichero
        files = {"file": ("auth_service.py", python_content.encode(), "text/plain")}
        data = {"search_space_id": self.space_id}
        r = httpx.post(f"{BASE_URL}/api/v1/documents/fileupload",
                       files=files, data=data, headers=self.headers)
        assert r.status_code == 200
        doc_id = r.json()["document_id"]

        # 2. Esperar que se procese (Celery async)
        asyncio.sleep(10)

        # 3. Verificar pasaporte generado
        r = httpx.get(f"{BASE_URL}/api/brain/auth-service", headers=self.headers)
        assert r.status_code == 200
        passport = r.json()["passport_md"]
        assert "authenticate_user" in passport
        assert "## 🧩 Core Knowledge" in passport

        # 4. Verificar vectores en Qdrant
        r = httpx.get(f"{BASE_URL}/api/admin/qdrant/collections", headers=self.headers)
        collections = r.json()
        assert collections["code"]["vectors_count"] > 0

        # 5. Consulta — debe resolverse en L2
        r = httpx.post(f"{BASE_URL}/api/brain/query",
                       json={
                           "question": "¿Cómo funciona la autenticación de usuarios?",
                           "search_space_id": self.space_id,
                       },
                       headers=self.headers)
        assert r.status_code == 200
        result = r.json()
        assert result["level"] in [1, 2]
        assert "auth-service" in result["sources"]
        assert "authenticate" in result["answer"].lower()

    def test_query_sin_contexto_va_a_l0(self):
        """Consulta sobre algo no indexado debe ir a L0."""
        r = httpx.post(f"{BASE_URL}/api/brain/query",
                       json={
                           "question": "¿Cuál es la capital de Mongolia?",
                           "search_space_id": self.space_id,
                       },
                       headers=self.headers)
        assert r.json()["level"] == 0
        assert r.json()["level_label"] == "🤖 LLM"

    def test_resynthesis_no_usa_pasaporte_como_fuente(self):
        """DT-01: re-síntesis debe usar fuente original, no el .md."""
        r = httpx.post(f"{BASE_URL}/api/brain/auth-service/resynthesize",
                       headers=self.headers)
        assert r.status_code == 200
        # El pasaporte resultante debe contener el código original, no citar el .md
        asyncio.sleep(15)
        r = httpx.get(f"{BASE_URL}/api/brain/auth-service", headers=self.headers)
        passport = r.json()["passport_md"]
        assert "passport_path" not in passport  # no hay referencia al .md
        assert "authenticate_user" in passport   # sí el código original
```

---

## F7.3 — Runbook operacional (Día 4)

```markdown
# BrainSense — Runbook Operacional

## Arranque del sistema
docker compose up -d
sleep 60
curl http://localhost:8000/health  # debe devolver 200

## Verificar estado de todos los servicios
docker exec brainsense-backend supervisorctl status
curl http://localhost:6333/health  # Qdrant
curl http://host.docker.internal:11434/api/tags  # Ollama

## Backup diario
./scripts/backup.sh  # PostgreSQL + /data/brain/*.md

## Actualizar BrainSense
./scripts/backup.sh
git pull origin main
docker compose build backend
docker compose up -d backend celery-worker
docker exec brainsense-backend alembic upgrade head

## Re-indexar todo desde cero
# 1. Recrear colecciones Qdrant (borra vectores, mantiene pasaportes)
curl -X POST http://localhost:8000/api/admin/qdrant/collection/brain/recreate
curl -X POST http://localhost:8000/api/admin/qdrant/collection/knowledge/recreate
curl -X POST http://localhost:8000/api/admin/qdrant/collection/code/recreate
# 2. Re-indexar desde los pasaportes .md existentes
curl -X POST http://localhost:8000/api/admin/brain/reindex-all

## Problemas comunes

### Ollama no responde
systemctl status ollama
ollama list  # verificar que los modelos están descargados
ollama pull nomic-embed-text  # re-descargar si falta

### Celery worker bloqueado
docker restart brainsense-celery
# Ver tareas activas:
docker exec brainsense-celery celery -A app.celery_app inspect active

### Qdrant colección corrupta
# Recrear la colección afectada y re-indexar
curl -X POST http://localhost:8000/api/admin/qdrant/collection/{name}/recreate
# Los pasaportes .md están en /data/brain/ — no se pierden

### Pasaporte contaminado (DT-01)
# Borrar el pasaporte corrupto y re-sintetizar desde la fuente
curl -X DELETE http://localhost:8000/api/brain/{source}
curl -X POST http://localhost:8000/api/brain/{source}/resynthesize
```

---

## F7.4 — Monitoring básico (Día 5)

```bash
# healthcheck.sh — ejecutar cada 5 minutos vía cron

#!/bin/bash
check_backend() {
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)
  [ "$code" = "200" ] || { echo "BACKEND DOWN"; docker restart brainsense-backend; }
}

check_qdrant() {
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:6333/health)
  [ "$code" = "200" ] || echo "QDRANT DOWN — alertar"
}

check_celery() {
  active=$(docker exec brainsense-celery \
    celery -A app.celery_app inspect active 2>/dev/null | grep -c "worker")
  [ "$active" -gt 0 ] || echo "CELERY SIN WORKERS"
}

check_passport_watcher() {
  running=$(docker exec brainsense-backend \
    ps aux | grep -c brain_watcher)
  [ "$running" -gt 0 ] || echo "BRAIN WATCHER CAÍDO"
}

check_backend && check_qdrant && check_celery && check_passport_watcher
```

---

## Checklist F7

- [ ] DT-01 resuelto: re-síntesis no contamina desde pasaporte .md
- [ ] DT-02 resuelto: bloques código en .md → colección code
- [ ] DT-03: circuit breaker para Ollama
- [ ] DT-04: Source Extract saneado en re-síntesis
- [ ] Tests E2E pasando (al menos 5 escenarios críticos)
- [ ] Runbook operacional escrito y revisado por el equipo
- [ ] Healthcheck script con cron cada 5 minutos
- [ ] Backup automatizado (daily cron)
- [ ] Migración Alembic de producción sin downtime documentada
- [ ] Documento de deuda técnica DT-05 a DT-09 priorizado para siguiente sprint

---

**Anterior:** [F6 — UI Integrada](./F6-ui-integracion.md)  
**Volver al plan maestro:** [00 — Plan Maestro](./00-plan-maestro.md)

---

## Estado de la plataforma al final de F7

```
BrainSense Platform — Production Ready

Ingesta:  16 formatos · 25+ conectores · pipeline 3 fases
Síntesis: multi-call · quality_trigger · 14 tipos de prompts
Vectores: Qdrant · 3 colecciones · 768d + 2560d
Query:    L1→L2→L0 cascade · RRF fusion · reranking FlashRank
UI:       Next.js · Brain Chat · Wiki · Graph · Ingest · Admin
Ops:      Celery · Redis · PostgreSQL · Watcher · Healthcheck
```
