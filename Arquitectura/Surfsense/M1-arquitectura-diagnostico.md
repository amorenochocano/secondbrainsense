# M1 — Arquitectura y Diagnóstico
> Nivel: Admin · Tiempo estimado: 2h  
> Objetivo: entender qué tienes desplegado antes de tocar nada

---

## 1.1 Anatomía de tu instalación

Tu SurfSense con Ollama tiene esta arquitectura interna. Todo corre dentro de un único contenedor Docker, gestionado por Supervisord:

```
contenedor surfsense
├── PostgreSQL + pgvector  ← base de datos + vector store
├── Redis                  ← cola de tareas (Celery)
├── FastAPI backend        ← API REST, lógica RAG, agentes
├── Next.js frontend       ← UI web
├── Celery workers         ← ingesta asíncrona, indexación
└── Celery Beat            ← scheduler de sync periódica
```

**Flujo de datos:**
```
Usuario → Frontend (Next.js)
              ↓
         FastAPI (backend)
              ↓
    ┌─────────┴──────────┐
    │                    │
Respuesta RAG      Celery (si es
directa            indexación)
    │                    │
    └─────────┬──────────┘
              ↓
    PostgreSQL + pgvector
```

**Concepto clave — Search Space:**  
Todo en SurfSense vive dentro de un Search Space. Es la unidad de aislamiento: cada Space tiene sus propios documentos, conectores, chats y miembros. Piénsalo como un "proyecto" de conocimiento.

---

## 1.2 Comandos de diagnóstico esenciales

### Estado general del contenedor

```bash
# Ver uso de CPU, memoria y red en tiempo real
docker stats surfsense

# Ver configuración completa del contenedor
docker inspect surfsense

# Ver todos los servicios internos y su estado
docker exec surfsense supervisorctl status
```

### Logs por servicio

```bash
# Logs del backend FastAPI
docker exec surfsense supervisorctl tail -f backend

# Logs del worker de indexación
docker exec surfsense supervisorctl tail -f celery-worker

# Logs del scheduler periódico
docker exec surfsense supervisorctl tail -f celery-beat

# Logs combinados filtrando solo errores y avisos
docker logs -f surfsense 2>&1 | grep -E "(ERROR|WARNING|INFO)"
```

### Verificar salud de la API

```bash
# Health check básico (debe devolver HTTP 200)
curl http://localhost:8000/health

# Ver todos los endpoints disponibles (Swagger UI)
# Abre en el navegador:
open http://localhost:8000/docs
```

---

## 1.3 Diagnóstico de la base de datos

```bash
# Ver todas las tablas creadas
docker exec surfsense psql -U surfsense -d surfsense -c "\dt"

# Cuántos documentos hay indexados
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as total_documentos FROM document;"

# Cuántos chunks (fragmentos) hay indexados
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as total_chunks FROM document_segment;"

# Documentos por Search Space
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT search_space_id, count(*) as docs
      FROM document
      GROUP BY search_space_id;"

# Verificar que todos los chunks tienen embedding generado
# (debe devolver 0 — ningún chunk sin embedding)
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as sin_embedding
      FROM document_segment
      WHERE embedding IS NULL;"
```

---

## 1.4 Verificar Ollama conectado

```bash
# Desde fuera del contenedor — listar modelos disponibles
curl http://localhost:11434/api/tags

# Desde dentro del contenedor (usa host.docker.internal)
docker exec surfsense curl http://host.docker.internal:11434/api/tags

# Test rápido de inferencia
curl http://localhost:11434/api/generate \
  -d '{"model":"llama3.2:latest","prompt":"hola","stream":false}'
```

**En tu `.env` debe estar:**
```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

> ⚠️ En Linux, `host.docker.internal` no funciona por defecto.  
> Usa la IP real del host: `OLLAMA_BASE_URL=http://172.17.0.1:11434`  
> (obtén la IP con `ip route | grep docker`)

### Modelos recomendados para empresa IT con Ollama

| Uso | Modelo | Notas |
|-----|--------|-------|
| LLM principal | `llama3.2:latest` | Equilibrio calidad/velocidad |
| LLM alta calidad | `qwen2.5:14b` | Context 128k, mejor razonamiento |
| LLM ligero | `mistral:latest` | Rápido, bueno para contextos largos |
| Embeddings | `nomic-embed-text` | Óptimo calidad/velocidad/tamaño |

```bash
# Descargar modelos recomendados
ollama pull llama3.2:latest
ollama pull nomic-embed-text
```

---

## 1.5 Checklist de diagnóstico inicial

Ejecuta esto al arrancar por primera vez o tras cualquier problema:

- [ ] `docker stats surfsense` — el contenedor está corriendo
- [ ] `curl http://localhost:8000/health` — devuelve 200
- [ ] `curl http://localhost:11434/api/tags` — Ollama responde
- [ ] `docker exec surfsense supervisorctl status` — todos los servicios `RUNNING`
- [ ] Abre `http://localhost:3000` en el navegador — la UI carga
- [ ] Cuenta de admin creada (primer usuario = admin automáticamente)

---

## Resumen del módulo

Tienes una instalación all-in-one donde PostgreSQL + pgvector hace de vector store, Redis gestiona las colas de indexación, y Ollama corre fuera del contenedor como LLM. Los comandos de diagnóstico más importantes son `supervisorctl status` para los servicios y las queries de PostgreSQL para verificar la indexación.

**Siguiente:** [M2 — Search Spaces y RBAC](./M2-search-spaces-rbac.md)
