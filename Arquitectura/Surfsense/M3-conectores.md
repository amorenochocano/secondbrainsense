# M3 — Conectores: Configuración Completa
> Nivel: Admin · Tiempo estimado: 4h  
> Objetivo: conectar todas tus fuentes de conocimiento y mantenerlas sincronizadas

---

## 3.1 Mapa de conectores disponibles

### Dev / IT (prioritarios para tu caso)
| Conector | Qué indexa | Autenticación |
|----------|-----------|---------------|
| **GitHub** | Repos, READMEs, wikis, código, issues, PRs | Personal Access Token |
| **Jira Cloud** | Tickets, sprints, epics, comentarios | API Token (Atlassian) |
| **Linear** | Issues, proyectos, documentos | API Key |
| **ClickUp** | Tareas, docs, espacios | API Key |
| **Confluence** | Espacios, páginas, adjuntos | API Token (Atlassian) |
| **BookStack** | Wikis self-hosted | API Token |

### Comunicación
| Conector | Qué indexa | Autenticación |
|----------|-----------|---------------|
| **Slack** | Canales, hilos, ficheros | OAuth App |
| **Discord** | Servidores y canales | Bot Token |
| **Gmail** | Emails (¡cuidado GDPR!) | OAuth2 |
| **Microsoft Teams** | Canales, mensajes | Azure AD App |

### Almacenamiento
| Conector | Qué indexa | Autenticación |
|----------|-----------|---------------|
| **Google Drive** | Docs, Sheets, Slides, PDFs | Service Account / OAuth2 |
| **OneDrive** | Ficheros Microsoft | Azure AD App |
| **Dropbox** | Ficheros | OAuth2 |
| **Local Folder** | Carpetas locales, vault Obsidian | Sin auth — ruta del sistema |

### Búsqueda web (modo Researcher)
| Conector | Notas |
|----------|-------|
| **Tavily** | 1000 req/mes gratis — tavily.com |
| **LinkUp** | Alternativa a Tavily |
| **SearXNG** | Self-hosted, 100% privado ⭐ recomendado |

### Productividad
| Conector | Qué indexa |
|----------|-----------|
| **Notion** | Páginas, bases de datos, wikis |
| **Airtable** | Bases de datos y vistas |
| **Google Calendar** | Eventos y contexto temporal |
| **YouTube** | Transcripción automática de vídeos |

---

## 3.2 Configurar GitHub

### Paso 1 — Crear Personal Access Token

1. Ir a `github.com` → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**
2. Permisos mínimos necesarios:
   - `Contents: Read-only`
   - `Metadata: Read-only`
   - `Issues: Read-only` (si quieres indexar issues)
   - `Pull requests: Read-only` (si quieres PRs)
3. Copiar el token generado

### Paso 2 — Añadir conector en SurfSense

Desde la UI: Search Space → Connectors → Add Connector → GitHub → pegar token → seleccionar repos

### Paso 3 — Vía API (para múltiples repos)

```bash
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub - Backend Repos",
    "connector_type": "GITHUB_CONNECTOR",
    "is_indexable": true,
    "config": {
      "access_token": "github_pat_...",
      "repos": ["mi-org/backend-api", "mi-org/shared-libs"],
      "index_issues": true,
      "index_pull_requests": false
    },
    "periodic_indexing_enabled": true,
    "indexing_frequency_minutes": 60
  }'

# Forzar primera indexación inmediata
curl -X POST "http://localhost:8000/api/v1/connectors/{CONNECTOR_ID}/index" \
  -H "Authorization: Bearer $TOKEN"
```

### Verificar indexación

```bash
# Ver estado del conector
curl "http://localhost:8000/api/v1/connectors/{CONNECTOR_ID}" \
  -H "Authorization: Bearer $TOKEN"

# Ver documentos indexados de este conector
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT title, created_at FROM document
      WHERE connector_id = '{CONNECTOR_ID}'
      ORDER BY created_at DESC LIMIT 10;"
```

---

## 3.3 Configurar Jira

```bash
# Obtener API Token de Atlassian:
# id.atlassian.com → Security → Create and manage API tokens

curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Jira - Proyecto Backend",
    "connector_type": "JIRA_CONNECTOR",
    "config": {
      "jira_url": "https://tu-empresa.atlassian.net",
      "email": "admin@empresa.com",
      "api_token": "tu-api-token-atlassian",
      "projects": ["BACK", "INFRA"],
      "index_comments": true
    },
    "periodic_indexing_enabled": true,
    "indexing_frequency_minutes": 60
  }'
```

---

## 3.4 Configurar Confluence

```bash
# Mismo API Token de Atlassian que Jira
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Confluence - Wiki Técnica",
    "connector_type": "CONFLUENCE_CONNECTOR",
    "config": {
      "confluence_url": "https://tu-empresa.atlassian.net/wiki",
      "email": "admin@empresa.com",
      "api_token": "tu-api-token-atlassian",
      "spaces": ["TECH", "ARCH", "OPS"]
    },
    "periodic_indexing_enabled": true,
    "indexing_frequency_minutes": 360
  }'
```

---

## 3.5 Configurar Slack

```bash
# Crear OAuth App en api.slack.com/apps
# Permisos necesarios (Bot Token Scopes):
#   channels:history, channels:read
#   files:read
#   search:read
#   users:read

curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Slack - Canales técnicos",
    "connector_type": "SLACK_CONNECTOR",
    "config": {
      "bot_token": "xoxb-...",
      "channels": ["#engineering", "#architecture", "#incidents"],
      "index_files": true,
      "days_back": 180
    },
    "periodic_indexing_enabled": true,
    "indexing_frequency_minutes": 30
  }'
```

---

## 3.6 Configurar Local Folder (Obsidian / carpetas locales)

> ⚠️ Nota: este conector puede tener bugs en algunas versiones. Si falla, usa el conector custom del M7 como alternativa.

```bash
# La carpeta debe ser accesible desde dentro del contenedor
# Monta la carpeta en docker-compose.yml o docker run:

# En docker run, añadir:
# -v /Users/tuusuario/Obsidian:/mnt/obsidian:ro

# En docker-compose.yml, añadir en volumes:
# - /Users/tuusuario/Obsidian:/mnt/obsidian:ro

# Luego en SurfSense:
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Obsidian Vault",
    "connector_type": "LOCAL_FOLDER_CONNECTOR",
    "config": {
      "folder_path": "/mnt/obsidian",
      "file_extensions": [".md", ".txt", ".pdf"],
      "recursive": true
    },
    "periodic_indexing_enabled": true,
    "indexing_frequency_minutes": 60
  }'
```

---

## 3.7 Configurar SearXNG (búsqueda web privada)

SearXNG es un metabuscador self-hosted — permite el modo Researcher sin mandar queries a servicios externos.

### Añadir SearXNG al docker-compose.yml

```yaml
services:
  surfsense:
    # ... tu config actual

  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: unless-stopped
    ports:
      - "8888:8080"
    environment:
      SEARXNG_BASE_URL: "http://localhost:8888"
```

```bash
# Arrancar solo SearXNG sin recrear surfsense
docker-compose up -d searxng

# Verificar que funciona
curl "http://localhost:8888/search?q=test&format=json"
```

Luego en SurfSense → Connectors → Add → SearXNG → URL: `http://searxng:8080`

---

## 3.8 Scheduler de indexación

Celery Beat revisa cada minuto si hay conectores pendientes de sincronización.

**Frecuencias recomendadas:**

| Conector | Frecuencia | Razón |
|----------|-----------|-------|
| GitHub | 30-60 min | Código cambia con cada commit |
| Jira | 60 min | Tickets se actualizan durante el día |
| Slack | 15-30 min | Mensajes en tiempo real |
| Confluence | 4-6 horas | Docs cambian poco |
| Google Drive | 2-4 horas | Colaboración esporádica |
| PDFs / Runbooks | 24 horas | Cambian muy poco |

```bash
# Ver tareas Celery activas en este momento
docker exec surfsense celery -A app.celery_app inspect active

# Historial de indexaciones (últimas 20)
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT connector_id, status, created_at, finished_at
      FROM indexing_jobs
      ORDER BY created_at DESC LIMIT 20;"

# Forzar re-indexación de todos los conectores de un Space
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/connectors/reindex-all" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Checklist del módulo

- [ ] GitHub configurado con los repos del equipo
- [ ] Jira / Linear configurado (si aplica)
- [ ] Confluence / Notion configurado (si aplica)
- [ ] Slack configurado para canales técnicos
- [ ] SearXNG desplegado para búsqueda web privada
- [ ] Frecuencias de sync ajustadas por tipo de conector
- [ ] Primera indexación completa verificada en la UI

---

**Anterior:** [M2 — Search Spaces y RBAC](./M2-search-spaces-rbac.md)  
**Siguiente:** [M4 — Tuning del RAG](./M4-tuning-rag.md)
