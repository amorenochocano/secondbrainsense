# M7 — Crear Conectores Nuevos
> Nivel: Dev Avanzado · Tiempo estimado: 4h  
> Objetivo: extender SurfSense para cualquier fuente de datos propia

---

## 7.1 Dos enfoques — cuál elegir

### Enfoque A — API Wrapper (sin tocar el código de SurfSense)
Tu conector es un script/servicio externo que extrae datos de tu fuente y los sube via `POST /api/v1/files/`.

**Elige este cuando:**
- Fuente simple con API REST
- Quieres tenerlo funcionando en pocas horas
- No necesitas configuración desde la UI de SurfSense
- Quieres mantenerlo independiente del core

**Esfuerzo:** 1-4 horas según la complejidad de la fuente

### Enfoque B — Conector nativo (fork del repositorio)
Añades el conector al código fuente de SurfSense siguiendo los patrones internos.

**Elige este cuando:**
- Necesitas OAuth flow gestionado por SurfSense
- Quieres configuración desde la UI (no hardcoded)
- Necesitas sync periódica integrada en Celery Beat
- Planeas contribuirlo al proyecto upstream

**Esfuerzo:** 1-2 días

> Para el 90% de casos en empresa IT → **Enfoque A**. Más rápido, más fácil de mantener, y no depende de versiones del core.

---

## 7.2 Enfoque A — Conector para Jira On-Premise

Jira Server / Data Center (no el Cloud) no tiene conector oficial en SurfSense. Este es el patrón completo:

```python
# jira_onprem_connector.py
# pip install httpx python-dotenv

import httpx
import json
import os
from base64 import b64encode
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configuración
JIRA_URL = os.getenv("JIRA_URL", "https://jira.tuempresa.com")
JIRA_USER = os.getenv("JIRA_USER", "admin@empresa.com")
JIRA_TOKEN = os.getenv("JIRA_TOKEN")
SURFSENSE_URL = os.getenv("SURFSENSE_URL", "http://localhost:8000")
SURFSENSE_SPACE = os.getenv("SURFSENSE_SPACE_ID")
SURFSENSE_EMAIL = os.getenv("SURFSENSE_EMAIL")
SURFSENSE_PASS = os.getenv("SURFSENSE_PASS")

# ── Autenticación Jira ──────────────────────────────────────

def jira_headers() -> dict:
    creds = b64encode(f"{JIRA_USER}:{JIRA_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json"
    }

# ── Autenticación SurfSense ─────────────────────────────────

def get_surfsense_token() -> str:
    r = httpx.post(
        f"{SURFSENSE_URL}/api/v1/auth/login",
        json={"email": SURFSENSE_EMAIL, "password": SURFSENSE_PASS},
        timeout=30
    )
    r.raise_for_status()
    return r.json()["access_token"]

# ── Extracción de Jira ──────────────────────────────────────

def get_issues(project: str, max_results: int = 200) -> list:
    """Obtiene issues de un proyecto"""
    issues = []
    start = 0

    while True:
        r = httpx.get(
            f"{JIRA_URL}/rest/api/2/search",
            headers=jira_headers(),
            params={
                "jql": f"project={project} ORDER BY updated DESC",
                "startAt": start,
                "maxResults": 50,
                "fields": "summary,description,status,assignee,updated,labels,priority,comment"
            },
            timeout=30
        )
        data = r.json()
        batch = data.get("issues", [])
        issues.extend(batch)

        if len(issues) >= max_results or len(batch) == 0:
            break
        start += len(batch)

    return issues[:max_results]

def format_issue(issue: dict) -> tuple[str, str]:
    """Convierte un issue Jira a texto indexable"""
    f = issue["fields"]
    key = issue["key"]

    # Extraer comentarios
    comments = f.get("comment", {}).get("comments", [])
    comments_text = ""
    if comments:
        comments_text = "\n\nCOMENTARIOS:\n"
        for c in comments[-5:]:  # últimos 5 comentarios
            author = c.get("author", {}).get("displayName", "Anónimo")
            body = c.get("body", "")[:500]
            comments_text += f"\n[{author}]: {body}\n"

    title = f"{key}: {f['summary']}"
    content = f"""ISSUE: {key}
TÍTULO: {f['summary']}
PROYECTO: {key.split('-')[0]}
ESTADO: {f['status']['name']}
PRIORIDAD: {f.get('priority', {}).get('name', 'Sin prioridad')}
ASIGNADO A: {f.get('assignee', {}).get('displayName', 'Sin asignar')}
LABELS: {', '.join(f.get('labels', [])) or 'Sin labels'}
ÚLTIMA ACTUALIZACIÓN: {f.get('updated', '').split('T')[0]}

DESCRIPCIÓN:
{f.get('description', 'Sin descripción') or 'Sin descripción'}
{comments_text}"""

    return title, content.strip()

# ── Subida a SurfSense ──────────────────────────────────────

def upload_to_surfsense(
    title: str,
    content: str,
    token: str,
    metadata: dict
) -> bool:
    files = {
        "file": (f"{title.replace(':', '-')}.txt",
                 content.encode("utf-8"),
                 "text/plain")
    }
    data = {
        "search_space_id": SURFSENSE_SPACE,
        "document_metadata": json.dumps(metadata)
    }
    r = httpx.post(
        f"{SURFSENSE_URL}/api/v1/files/",
        headers={"Authorization": f"Bearer {token}"},
        files=files,
        data=data,
        timeout=30
    )
    return r.status_code == 200

# ── Sincronización principal ────────────────────────────────

def sync_project(project: str, max_issues: int = 200):
    print(f"\n🔄 Sincronizando proyecto {project}...")
    token = get_surfsense_token()
    issues = get_issues(project, max_issues)
    print(f"   {len(issues)} issues encontrados")

    ok, fail = 0, 0
    for issue in issues:
        title, content = format_issue(issue)
        metadata = {
            "source": "jira_onprem",
            "project": project,
            "issue_key": issue["key"],
            "status": issue["fields"]["status"]["name"],
            "synced_at": datetime.now().isoformat()
        }
        if upload_to_surfsense(title, content, token, metadata):
            ok += 1
            if ok % 10 == 0:
                print(f"   ... {ok}/{len(issues)}")
        else:
            fail += 1
            print(f"   ❌ Error en {issue['key']}")

    print(f"   ✅ {ok} indexados, ❌ {fail} errores")

def sync_all():
    """Sincroniza todos los proyectos configurados"""
    projects = os.getenv("JIRA_PROJECTS", "BACK,INFRA").split(",")
    for project in projects:
        sync_project(project.strip())
    print("\n✅ Sincronización completa")

if __name__ == "__main__":
    sync_all()
```

### Variables de entorno necesarias

Añade a tu `.env`:
```bash
# Jira On-Premise
JIRA_URL=https://jira.tuempresa.com
JIRA_USER=admin@empresa.com
JIRA_TOKEN=tu-api-token-jira
JIRA_PROJECTS=BACK,INFRA,OPS

# SurfSense (ya los tienes)
SURFSENSE_URL=http://localhost:8000
SURFSENSE_SPACE_ID=tu-space-id
SURFSENSE_EMAIL=admin@empresa.com
SURFSENSE_PASS=tupassword
```

### Automatizar la sync

```bash
# Ejecutar manualmente
python jira_onprem_connector.py

# Cron cada hora de lunes a viernes
0 * * * 1-5 cd /ruta/conectores && python jira_onprem_connector.py >> /var/log/jira-sync.log 2>&1
```

---

## 7.3 Enfoque A — Conector para Obsidian (workaround)

El conector oficial de Obsidian tiene bugs en algunas versiones. Este script es más fiable:

```python
# obsidian_connector.py
# Indexa un vault de Obsidian directamente

import os
import httpx
import json
from pathlib import Path
from datetime import datetime

VAULT_PATH = os.getenv("OBSIDIAN_VAULT", "/Users/tuusuario/Obsidian/Mi Vault")
SURFSENSE_URL = os.getenv("SURFSENSE_URL", "http://localhost:8000")
SPACE_ID = os.getenv("SURFSENSE_SPACE_ID")

def get_all_notes(vault_path: str) -> list[Path]:
    """Obtiene todos los ficheros .md del vault"""
    return list(Path(vault_path).rglob("*.md"))

def get_token() -> str:
    r = httpx.post(f"{SURFSENSE_URL}/api/v1/auth/login",
                   json={"email": os.getenv("SURFSENSE_EMAIL"),
                         "password": os.getenv("SURFSENSE_PASS")})
    return r.json()["access_token"]

def upload_note(path: Path, token: str) -> bool:
    title = path.stem  # nombre del fichero sin extensión
    content = path.read_text(encoding="utf-8")

    # Extraer tags de Obsidian (#tag en el cuerpo)
    tags = [w[1:] for w in content.split() if w.startswith("#") and len(w) > 1]

    metadata = {
        "source": "obsidian",
        "vault_path": str(path.relative_to(VAULT_PATH)),
        "tags": list(set(tags))[:20],
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    }

    files = {"file": (f"{title}.md", content.encode("utf-8"), "text/markdown")}
    data = {"search_space_id": SPACE_ID,
            "document_metadata": json.dumps(metadata)}

    r = httpx.post(f"{SURFSENSE_URL}/api/v1/files/",
                   headers={"Authorization": f"Bearer {token}"},
                   files=files, data=data, timeout=30)
    return r.status_code == 200

def sync_vault():
    notes = get_all_notes(VAULT_PATH)
    token = get_token()
    print(f"📚 {len(notes)} notas encontradas en el vault")
    ok, fail = 0, 0

    for note in notes:
        if upload_note(note, token):
            ok += 1
        else:
            fail += 1
            print(f"  ❌ Error: {note.name}")

    print(f"\n✅ {ok} notas indexadas, ❌ {fail} errores")

if __name__ == "__main__":
    sync_vault()
```

---

## 7.4 Estructura de la API de SurfSense (referencia)

Los endpoints más útiles para construir conectores:

```bash
# ── Autenticación ──────────────────────────────────────────
POST /api/v1/auth/login
  Body: {"email": "...", "password": "..."}
  Returns: {"access_token": "..."}

# ── Subir documento ────────────────────────────────────────
POST /api/v1/files/
  Headers: Authorization: Bearer {token}
  Body (multipart):
    - file: (nombre.ext, contenido, mimetype)
    - search_space_id: "id-del-space"
    - document_metadata: '{"key": "value"}'  # JSON string

# ── Listar documentos ──────────────────────────────────────
GET /api/v1/search-spaces/{space_id}/documents
  Headers: Authorization: Bearer {token}

# ── Eliminar documento ─────────────────────────────────────
DELETE /api/v1/documents/{document_id}
  Headers: Authorization: Bearer {token}

# ── Listar conectores ──────────────────────────────────────
GET /api/v1/search-spaces/{space_id}/connectors
  Headers: Authorization: Bearer {token}

# ── Forzar indexación ──────────────────────────────────────
POST /api/v1/connectors/{connector_id}/index
  Headers: Authorization: Bearer {token}

# ── Documentación completa (Swagger) ──────────────────────
# Abre en navegador:
# http://localhost:8000/docs
```

---

## Checklist del módulo

- [ ] Identificadas las fuentes de datos sin conector oficial
- [ ] Elegido el enfoque (A o B) para cada fuente
- [ ] Al menos un conector custom implementado y probado
- [ ] Automatización con cron configurada
- [ ] Logs de sync monitorizados

---

**Anterior:** [M6 — Mejoras de Bajo Calado](./M6-mejoras-bajo-calado.md)  
**Volver al inicio:** [00 — Índice](./00-indice.md)

---

## Siguientes pasos recomendados

Una vez dominados estos 7 módulos, los pasos naturales son:

1. **Integrar con Claude Code via MCP** — cuando SurfSense publique su MCP server oficial (en roadmap), o construir un bridge MCP manual usando la API (patrón del M6)
2. **Añadir Open WebUI encima** — como capa de UI adicional con SSO y pipelines custom, apuntando al mismo PostgreSQL
3. **Implementar el pasaporte semántico completo** — el pipeline de dos capas diseñado en la conversación anterior
4. **Monitorización con Prometheus + Grafana** — conectar las métricas expuestas por FastAPI a un dashboard de Grafana
