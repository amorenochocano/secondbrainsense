# F5 — Conectores SurfSense → Pipeline Brain
**Duración:** 1 semana  
**Equipo:** Backend Senior (1) + Backend Mid (1)  
**Dependencias:** F4 completada  
**Entregable:** Los 25+ conectores de SurfSense alimentan el pipeline de tres fases de Second Brain

---

## Objetivo

Conectar los conectores de SurfSense (GitHub, Jira, Confluence, Slack...) con el pipeline de tres fases implementado en F1-F3. Actualmente los conectores escriben directamente en pgvector con el pipeline genérico. Después de F5, todo documento ingestado por cualquier conector pasa por ExtractorFactory → UniversalCleaner → Preprocesador → Synthesizer → Qdrant.

---

## F5.1 — Análisis del flujo actual de conectores (Día 1)

Los conectores de SurfSense siguen este flujo:

```
Conector (GitHub/Jira/Slack...)
    ↓
connector.fetch_documents() → list[ConnectorDocument]
    ↓
tasks/connectors/{connector}_task.py
    ↓
process_connector_document()   ← PUNTO DE INTEGRACIÓN
    ↓
document_converters.py (pipeline genérico) ← YA MODIFICADO EN F1
    ↓
pgvector                       ← YA MIGRADO A QDRANT EN F2
```

La buena noticia: el punto de integración ya está resuelto. `process_document_content()` en `document_converters.py` ya usa el pipeline de tres fases (F1) y escribe en Qdrant (F2-F3). Lo que falta es asegurarse de que los conectores llaman a esta función correctamente con el tipo de fichero identificado.

---

## F5.2 — ConnectorBridge: adaptador de tipos (Día 1-2)

El problema es que los conectores de SurfSense producen texto plano, no ficheros con extensión. Necesitamos mapear el tipo de conector al tipo de fichero equivalente para que el `DocumentProcessorFactory` seleccione el preprocesador correcto.

**Fichero:** `surfsense_backend/app/brain/connector_bridge.py` ← NUEVO

```python
# connector_bridge.py
"""
Mapea tipos de ConnectorDocument al tipo de fichero equivalente
para que DocumentProcessorFactory seleccione el preprocesador correcto.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class ConnectorType(str, Enum):
    GITHUB_CODE = "github_code"
    GITHUB_README = "github_readme"
    GITHUB_WIKI = "github_wiki"
    GITHUB_ISSUE = "github_issue"
    JIRA_TICKET = "jira_ticket"
    CONFLUENCE_PAGE = "confluence_page"
    SLACK_CHANNEL = "slack_channel"
    NOTION_PAGE = "notion_page"
    GOOGLE_DRIVE_DOC = "google_drive_doc"
    LINEAR_ISSUE = "linear_issue"
    YOUTUBE_TRANSCRIPT = "youtube_transcript"
    GENERIC_TEXT = "generic_text"

# Mapa: tipo de conector → extensión equivalente para el preprocesador
CONNECTOR_TO_EXTENSION = {
    ConnectorType.GITHUB_CODE:       ".py",    # se reemplaza por detect_language()
    ConnectorType.GITHUB_README:     ".md",
    ConnectorType.GITHUB_WIKI:       ".md",
    ConnectorType.GITHUB_ISSUE:      ".md",
    ConnectorType.JIRA_TICKET:       ".md",
    ConnectorType.CONFLUENCE_PAGE:   ".md",
    ConnectorType.SLACK_CHANNEL:     ".txt",
    ConnectorType.NOTION_PAGE:       ".md",
    ConnectorType.GOOGLE_DRIVE_DOC:  ".md",
    ConnectorType.LINEAR_ISSUE:      ".md",
    ConnectorType.YOUTUBE_TRANSCRIPT:".txt",
    ConnectorType.GENERIC_TEXT:      ".txt",
}

def detect_code_extension(filename: str, content: str) -> str:
    """Detecta la extensión real de un fichero de código."""
    ext_map = {
        ".py": ["def ", "import ", "class ", "async def"],
        ".sql": ["SELECT ", "CREATE TABLE", "INSERT INTO", "PROCEDURE"],
        ".js": ["function ", "const ", "let ", "require("],
        ".ts": ["interface ", "type ", "export ", ": string"],
        ".go": ["func ", "package ", "import ("],
        ".java": ["public class", "private ", "void ", "@Override"],
        ".rs": ["fn ", "use ", "impl ", "struct "],
    }
    content_upper = content[:500].upper()
    for ext, signals in ext_map.items():
        if any(s.upper() in content_upper for s in signals):
            return ext
    # Fallback a extensión del fichero si existe
    from pathlib import Path
    detected = Path(filename).suffix.lower() if filename else ""
    return detected or ".txt"

@dataclass
class ConnectorDocument:
    """Documento normalizado que viene de cualquier conector."""
    source_id: str          # ID único en el sistema origen
    title: str              # Título legible
    content: str            # Contenido texto plano
    connector_type: ConnectorType
    url: str = ""
    metadata: dict = None
    filename: str = ""      # Nombre de fichero si aplica (para código)

def get_extension_for_connector_doc(doc: ConnectorDocument) -> str:
    """Determina la extensión correcta para el preprocesador."""
    if doc.connector_type == ConnectorType.GITHUB_CODE:
        return detect_code_extension(doc.filename, doc.content)
    return CONNECTOR_TO_EXTENSION.get(doc.connector_type, ".txt")

def slugify_connector_doc(doc: ConnectorDocument, search_space_id: str) -> str:
    """Genera el slug canónico para un documento de conector."""
    import re
    raw = f"{search_space_id}_{doc.connector_type}_{doc.source_id}"
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
```

---

## F5.3 — Adaptar los tasks de conectores de SurfSense (Día 2-4)

Los tasks de Celery de cada conector necesitan usar el `ConnectorBridge` para pasar el tipo correcto al pipeline.

**Patrón estándar para cualquier task de conector:**

```python
# surfsense_backend/app/tasks/connectors/github_task.py
# Patrón aplicable a TODOS los conectores

from app.brain.connector_bridge import (
    ConnectorDocument, ConnectorType,
    get_extension_for_connector_doc, slugify_connector_doc
)
from app.utils.document_converters import process_document_content
from app.brain.llm_client import BrainLLMClient
import asyncio, tempfile, os

def process_github_document(
    raw_doc: dict,
    search_space_id: str,
    connector_config: dict,
):
    """
    Procesa un documento de GitHub a través del pipeline Brain.
    Este patrón es idéntico para Jira, Confluence, Slack, Notion...
    Solo cambia el ConnectorType y la construcción del ConnectorDocument.
    """

    # 1. Normalizar al formato ConnectorDocument
    is_code = raw_doc.get("type") == "code"
    conn_doc = ConnectorDocument(
        source_id=raw_doc["sha"] or raw_doc["id"],
        title=raw_doc.get("path") or raw_doc.get("name", ""),
        content=raw_doc["content"],
        connector_type=ConnectorType.GITHUB_CODE if is_code else ConnectorType.GITHUB_README,
        url=raw_doc.get("html_url", ""),
        filename=raw_doc.get("path", ""),
        metadata={
            "repo": raw_doc.get("repo"),
            "branch": raw_doc.get("branch", "main"),
            "commit": raw_doc.get("sha", ""),
            "connector": "github",
        }
    )

    # 2. Determinar extensión y slug canónico
    ext = get_extension_for_connector_doc(conn_doc)
    source_slug = slugify_connector_doc(conn_doc, search_space_id)

    # 3. Pasar al pipeline de tres fases
    # El contenido ya es texto — escribimos en fichero temporal con la extensión correcta
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False, mode='w', encoding='utf-8') as tmp:
        tmp.write(conn_doc.content)
        tmp_path = tmp.name

    try:
        llm_client = BrainLLMClient()
        result = asyncio.run(process_document_content(
            file_content=conn_doc.content.encode("utf-8"),
            filename=f"{source_slug}{ext}",
            metadata={
                **conn_doc.metadata,
                "source_id": conn_doc.source_id,
                "title": conn_doc.title,
                "url": conn_doc.url,
                "search_space_id": search_space_id,
            },
            llm_client=llm_client,
            search_space_id=search_space_id,
            synthesis_enabled=True,
        ))

        # 4. Guardar metadata en PostgreSQL
        _save_connector_document_to_db(
            source_slug=source_slug,
            conn_doc=conn_doc,
            result=result,
            search_space_id=search_space_id,
        )

    finally:
        os.unlink(tmp_path)
```

**Aplicar el mismo patrón a estos conectores de SurfSense:**

| Conector | ConnectorType | Notas |
|---|---|---|
| GitHub (código) | `GITHUB_CODE` | detect_code_extension() para el lenguaje |
| GitHub (README/wiki) | `GITHUB_README` | ext=`.md` |
| Jira | `JIRA_TICKET` | ext=`.md`, formatear título+descripción+comentarios |
| Confluence | `CONFLUENCE_PAGE` | ext=`.md`, convertir HTML→MD si viene en HTML |
| Slack | `SLACK_CHANNEL` | ext=`.txt`, agrupar mensajes por día |
| Notion | `NOTION_PAGE` | ext=`.md` |
| Google Drive | `GOOGLE_DRIVE_DOC` | ext según mimetype del fichero |
| Linear | `LINEAR_ISSUE` | ext=`.md` |

---

## F5.4 — Deduplicación y re-indexación por conector (Día 4)

Los conectores sincronizan periódicamente. El mismo documento puede ingestarse múltiples veces. El sistema debe detectar cambios y solo re-indexar si el contenido ha cambiado.

```python
# surfsense_backend/app/brain/deduplication.py

import hashlib
from app.db import get_session, Document

def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()

async def should_reindex(source_slug: str, new_content_hash: str) -> bool:
    """
    True si el documento no existe o su contenido ha cambiado.
    Usa el campo content_hash de la tabla Document de PostgreSQL.
    """
    async with get_session() as session:
        doc = await session.get(Document, {"source": source_slug})
        if doc is None:
            return True  # Documento nuevo
        if doc.content_hash != new_content_hash:
            return True  # Contenido modificado
        return False  # Sin cambios — skip
```

---

## F5.5 — Tests de conectores (Día 5)

```python
# tests/integration/test_connectors_f5.py

import pytest
from app.brain.connector_bridge import (
    ConnectorDocument, ConnectorType,
    get_extension_for_connector_doc, detect_code_extension
)

class TestConnectorBridge:

    def test_github_python_detecta_extension(self):
        doc = ConnectorDocument(
            source_id="abc123",
            title="auth/jwt_service.py",
            content="import jwt\n\ndef generate_token(user_id: str) -> str:\n    pass",
            connector_type=ConnectorType.GITHUB_CODE,
            filename="auth/jwt_service.py",
        )
        ext = get_extension_for_connector_doc(doc)
        assert ext == ".py"

    def test_github_sql_detecta_extension(self):
        content = "CREATE PROCEDURE sp_get_user(IN user_id INT)\nBEGIN\nSELECT * FROM users;\nEND"
        ext = detect_code_extension("sp_get_user.sql", content)
        assert ext == ".sql"

    def test_jira_ticket_es_markdown(self):
        doc = ConnectorDocument(
            source_id="BACK-1234",
            title="Error en autenticación",
            content="Descripción del bug...",
            connector_type=ConnectorType.JIRA_TICKET,
        )
        ext = get_extension_for_connector_doc(doc)
        assert ext == ".md"

    def test_slug_es_valido(self):
        from app.brain.connector_bridge import slugify_connector_doc
        doc = ConnectorDocument(
            source_id="repo/file.py@sha123",
            title="test",
            content="",
            connector_type=ConnectorType.GITHUB_CODE,
        )
        slug = slugify_connector_doc(doc, "space-1")
        assert " " not in slug
        assert "/" not in slug
        assert slug.islower()
```

---

## Checklist F5

- [ ] `ConnectorBridge` implementado con mapeo conector → extensión
- [ ] `detect_code_extension()` detecta Python, SQL, JS, TS correctamente
- [ ] Task de GitHub adaptado con el patrón ConnectorBridge
- [ ] Tasks de Jira, Confluence, Slack, Notion adaptados
- [ ] Deduplicación por content_hash funcional
- [ ] Re-indexación automática cuando el contenido cambia
- [ ] `source_slug` consistente entre conectores y búsqueda
- [ ] Tests de ConnectorBridge pasando
- [ ] Sync periódica (Celery Beat) funcional para todos los conectores

---

**Anterior:** [F4 — Router Multinivel](./F4-router-multinivel.md)  
**Siguiente:** [F6 — UI Integrada](./F6-ui-integracion.md)
