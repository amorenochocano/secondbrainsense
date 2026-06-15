# M6 — Desarrollos de Bajo Calado
> Nivel: Dev · Tiempo estimado: variable por mejora  
> Objetivo: mejoras concretas sin tocar el core — máximo impacto, mínimo esfuerzo

---

## Mejora 1 — Webhook para re-indexar al instante

**Problema:** los conectores sincronizan por scheduler (cada 30-60 min). Un push a GitHub no se refleja hasta la próxima ejecución.

**Solución:** microservicio Python ligero que recibe el webhook de GitLab/GitHub y dispara la re-indexación inmediatamente.

```python
# webhook_indexer.py
# Requisitos: pip install fastapi uvicorn httpx

from fastapi import FastAPI, Request, Header
import httpx
import os

app = FastAPI()

SURFSENSE_URL = os.getenv("SURFSENSE_URL", "http://localhost:8000")
SURFSENSE_EMAIL = os.getenv("SURFSENSE_EMAIL", "admin@empresa.com")
SURFSENSE_PASS = os.getenv("SURFSENSE_PASS", "tupassword")

# Mapa repo → connector_id de SurfSense
# Obtén los IDs desde: GET /api/v1/search-spaces/{id}/connectors
REPO_CONNECTOR_MAP = {
    "backend-api": "connector-id-aqui",
    "shared-libs": "otro-connector-id",
}

def get_token() -> str:
    r = httpx.post(f"{SURFSENSE_URL}/api/v1/auth/login",
                   json={"email": SURFSENSE_EMAIL, "password": SURFSENSE_PASS})
    return r.json()["access_token"]

def trigger_reindex(connector_id: str, token: str):
    r = httpx.post(
        f"{SURFSENSE_URL}/api/v1/connectors/{connector_id}/index",
        headers={"Authorization": f"Bearer {token}"}
    )
    return r.status_code == 200

@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(None)
):
    if x_github_event not in ("push", "pull_request"):
        return {"status": "ignored"}

    payload = await request.json()
    repo_name = payload.get("repository", {}).get("name", "")
    connector_id = REPO_CONNECTOR_MAP.get(repo_name)

    if not connector_id:
        return {"status": "no_connector", "repo": repo_name}

    token = get_token()
    success = trigger_reindex(connector_id, token)

    return {"status": "ok" if success else "error", "repo": repo_name}

@app.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_event: str = Header(None)
):
    if x_gitlab_event != "Push Hook":
        return {"status": "ignored"}

    payload = await request.json()
    repo_name = payload.get("repository", {}).get("name", "")
    connector_id = REPO_CONNECTOR_MAP.get(repo_name)

    if not connector_id:
        return {"status": "no_connector"}

    token = get_token()
    success = trigger_reindex(connector_id, token)
    return {"status": "ok" if success else "error"}

# Arrancar:
# uvicorn webhook_indexer:app --host 0.0.0.0 --port 9000

# Configurar en GitHub:
# Settings → Webhooks → URL: http://TU_IP:9000/webhook/github
# Events: Push, Pull requests

# Configurar en GitLab:
# Settings → Webhooks → URL: http://TU_IP:9000/webhook/gitlab
# Triggers: Push events
```

---

## Mejora 2 — Conector custom para sistema interno

**Problema:** tienes una herramienta interna (wiki propia, sistema de tickets legacy, intranet) sin conector oficial.

**Solución:** script Python que extrae datos de tu sistema y los sube via API de SurfSense. No necesitas tocar el código de SurfSense.

```python
# custom_connector.py
# Plantilla adaptable a cualquier fuente de datos interna

import httpx
import json
import os
from datetime import datetime

SURFSENSE_URL = os.getenv("SURFSENSE_URL", "http://localhost:8000")
SPACE_ID = os.getenv("SURFSENSE_SPACE_ID", "tu-space-id")

def get_surfsense_token() -> str:
    """Autenticación en SurfSense"""
    r = httpx.post(f"{SURFSENSE_URL}/api/v1/auth/login",
                   json={"email": os.getenv("SURFSENSE_EMAIL"),
                         "password": os.getenv("SURFSENSE_PASS")})
    return r.json()["access_token"]

def upload_document(
    title: str,
    content: str,
    token: str,
    metadata: dict = None
) -> bool:
    """Sube un documento de texto a SurfSense"""
    headers = {"Authorization": f"Bearer {token}"}
    files = {
        "file": (f"{title}.txt", content.encode("utf-8"), "text/plain")
    }
    data = {
        "search_space_id": SPACE_ID,
        "document_metadata": json.dumps(metadata or {})
    }

    r = httpx.post(
        f"{SURFSENSE_URL}/api/v1/files/",
        headers=headers,
        files=files,
        data=data,
        timeout=30
    )
    return r.status_code == 200

# ── EJEMPLO: Indexar artículos de una wiki interna ──────────

def fetch_wiki_articles() -> list[dict]:
    """Adapta esto a tu sistema interno"""
    # Ejemplo con una API REST genérica
    r = httpx.get(
        "https://wiki-interna.empresa.com/api/articles",
        headers={"Authorization": "Bearer TU_TOKEN_WIKI"}
    )
    return r.json()["articles"]

def sync_wiki():
    token = get_surfsense_token()
    articles = fetch_wiki_articles()
    ok, fail = 0, 0

    for article in articles:
        title = article["title"]
        content = f"""
WIKI: {title}
URL: {article['url']}
CATEGORÍA: {article.get('category', 'Sin categoría')}
ÚLTIMA ACTUALIZACIÓN: {article.get('updated_at', 'Desconocida')}

{article['content']}
"""
        metadata = {
            "source": "wiki_interna",
            "url": article["url"],
            "category": article.get("category"),
            "synced_at": datetime.now().isoformat()
        }

        if upload_document(title, content.strip(), token, metadata):
            print(f"  ✅ {title}")
            ok += 1
        else:
            print(f"  ❌ {title}")
            fail += 1

    print(f"\nResultado: {ok} OK, {fail} errores")

if __name__ == "__main__":
    sync_wiki()

# Automatizar con cron:
# 0 */4 * * * python custom_connector.py  # cada 4 horas
```

---

## Mejora 3 — Pasaporte semántico por documento

**Problema:** el RAG de SurfSense busca solo por similitud de chunks. Si preguntas por el "módulo de autenticación" pero en el código se llama `AuthService`, puede no encontrarlo.

**Solución:** antes de indexar, generar metadatos enriquecidos con Ollama para cada documento (resumen, entidades, temas) y subirlos junto con el contenido.

```python
# passport_generator.py
# Requiere: pip install httpx

import httpx
import json
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
SURFSENSE_URL = os.getenv("SURFSENSE_URL", "http://localhost:8000")
SPACE_ID = os.getenv("SURFSENSE_SPACE_ID")

PASSPORT_PROMPT = """Analiza el siguiente documento y extrae metadatos en JSON.
Responde SOLO con JSON válido, sin texto adicional ni bloques de código.

{
  "summary": "resumen de 2-3 frases del propósito del documento",
  "entities": ["lista de clases, funciones, sistemas o personas clave"],
  "topics": ["tema1", "tema2", "tema3"],
  "dependencies": ["otros módulos, servicios o docs que se mencionan"],
  "doc_type": "code|adr|runbook|spec|ticket|wiki"
}

DOCUMENTO:
{content}
"""

def generate_passport(content: str) -> dict:
    """Genera el pasaporte semántico usando Ollama"""
    r = httpx.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": PASSPORT_PROMPT.format(content=content[:3000]),
            "stream": False
        },
        timeout=60
    )
    response_text = r.json()["response"].strip()

    # Limpiar posibles backticks si el modelo los añade
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return {"summary": content[:200], "topics": [], "entities": []}

def get_token() -> str:
    r = httpx.post(f"{SURFSENSE_URL}/api/v1/auth/login",
                   json={"email": os.getenv("SURFSENSE_EMAIL"),
                         "password": os.getenv("SURFSENSE_PASS")})
    return r.json()["access_token"]

def upload_with_passport(title: str, content: str, source_path: str = None):
    """Genera pasaporte y sube el documento enriquecido"""
    print(f"  🧠 Generando pasaporte para: {title}...")
    passport = generate_passport(content)
    print(f"     Temas: {passport.get('topics', [])}")
    print(f"     Entidades: {passport.get('entities', [])[:3]}...")

    token = get_token()
    metadata = {
        "passport": passport,
        "source_path": source_path,
        "pipeline": "passport_v1"
    }

    files = {"file": (f"{title}.txt", content.encode(), "text/plain")}
    data = {
        "search_space_id": SPACE_ID,
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

# Uso:
# upload_with_passport(
#     title="auth/jwt_service.py",
#     content=open("auth/jwt_service.py").read(),
#     source_path="auth/jwt_service.py"
# )
```

---

## Mejora 4 — Dashboard de uso del equipo

**Problema:** no sabes si el equipo usa SurfSense, qué preguntan, ni qué documentos son más consultados.

**Solución:** script que consulta la API y genera un informe semanal en Markdown.

```python
# usage_report.py
# Genera un informe semanal de uso de SurfSense

import httpx
import json
from datetime import datetime, timedelta
from collections import Counter

SURFSENSE_URL = "http://localhost:8000"
SPACE_ID = "tu-space-id"

def get_token():
    r = httpx.post(f"{SURFSENSE_URL}/api/v1/auth/login",
                   json={"email": "admin@empresa.com", "password": "pass"})
    return r.json()["access_token"]

def generate_report(space_id: str, days: int = 7) -> str:
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    since = (datetime.now() - timedelta(days=days)).isoformat()

    # Obtener chats del periodo
    chats = httpx.get(
        f"{SURFSENSE_URL}/api/v1/search-spaces/{space_id}/chats",
        headers=headers,
        params={"since": since}
    ).json()

    # Obtener documentos indexados
    docs = httpx.get(
        f"{SURFSENSE_URL}/api/v1/search-spaces/{space_id}/documents",
        headers=headers
    ).json()

    # Obtener conectores
    connectors = httpx.get(
        f"{SURFSENSE_URL}/api/v1/search-spaces/{space_id}/connectors",
        headers=headers
    ).json()

    # Análisis
    users = Counter(c.get("user_id") for c in chats)
    total_messages = sum(len(c.get("messages", [])) for c in chats)
    docs_by_connector = Counter(d.get("connector_id", "manual") for d in docs)

    # Generar reporte
    report = f"""# SurfSense — Informe de uso
**Periodo:** últimos {days} días · Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## Actividad de chat
- Total de conversaciones: {len(chats)}
- Total de mensajes: {total_messages}
- Usuarios activos: {len(users)}

## Top usuarios
"""
    for user_id, count in users.most_common(5):
        report += f"- `{user_id}`: {count} conversaciones\n"

    report += f"\n## Knowledge base\n- Total documentos: {len(docs)}\n\n"
    report += "## Documentos por conector\n"

    connector_names = {c["id"]: c["name"] for c in connectors}
    for connector_id, count in docs_by_connector.most_common():
        name = connector_names.get(connector_id, connector_id or "Subida manual")
        report += f"- {name}: {count} documentos\n"

    return report

if __name__ == "__main__":
    report = generate_report(SPACE_ID)
    filename = f"surfsense-report-{datetime.now().strftime('%Y%m%d')}.md"
    with open(filename, "w") as f:
        f.write(report)
    print(f"✅ Informe generado: {filename}")

# Automatizar:
# 0 8 * * 1 python usage_report.py  # cada lunes a las 8:00
```

---

## Resumen de mejoras

| Mejora | Esfuerzo | Impacto |
|--------|---------|---------|
| Webhook indexación | 2h | Alto — datos siempre actualizados |
| Conector custom | 2-4h | Alto — indexar cualquier fuente |
| Pasaporte semántico | 4h | Muy alto — mejor precisión RAG |
| Dashboard de uso | 2h | Medio — visibilidad del equipo |

---

**Anterior:** [M5 — Backups y Mantenimiento](./M5-backups-mantenimiento.md)  
**Siguiente:** [M7 — Crear Conectores Nuevos](./M7-conectores-custom.md)
