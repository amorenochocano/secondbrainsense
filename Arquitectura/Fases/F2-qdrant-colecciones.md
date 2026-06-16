# F2 â€” Qdrant y Tres Colecciones
**DuraciÃ³n:** 1 semana  
**Equipo:** Backend Senior (1) + Backend Mid (1)  
**Dependencias:** F1 completada (incluida F1.4: `app/brain/chunking.py` creado)  
**Entregable:** Qdrant con tres colecciones activas (`brain`, `knowledge`, `code`) con `search_space_id` en todos los payloads. Multi-tenancy vectorial operativo. `QdrantManager` singleton inicializado en lifespan. PostgreSQL mantiene lo relacional.

---

## Objetivo

Sustituir pgvector como almacÃ©n vectorial por Qdrant con tres colecciones diferenciadas. PostgreSQL sigue siendo la fuente de verdad para todo lo relacional. Solo los vectores migran.

**Antes (SurfSense):**
```
document_segment (PostgreSQL + pgvector)
  â†’ 1 colecciÃ³n implÃ­cita, dimensiÃ³n fija, sin diferenciaciÃ³n por tipo, sin multi-tenancy
```

**DespuÃ©s (BrainSense F2):**
```
Qdrant:brain      â†’ embeddings de pasaportes .md (768d, nomic-embed-text)
Qdrant:knowledge  â†’ embeddings de chunks de texto (768d, nomic-embed-text)
Qdrant:code       â†’ embeddings de bloques de cÃ³digo (dim dinÃ¡mica segÃºn EMBED_MODEL_CODE)
PostgreSQL        â†’ usuarios, documentos, conectores, chats (sin cambios)
```

**Invariante de seguridad multi-tenant (F2â†’F4):**  
Todos los puntos Qdrant en las tres colecciones deben incluir `search_space_id` en el payload. Sin este campo, el router L2 (F4) no puede aislar datos entre tenants.

---

## F2.0 â€” Estado del codebase: quÃ© existe y quÃ© es nuevo (DÃ­a 0)

Antes de modificar nada, verificar el estado real:

| Fichero | Estado | AcciÃ³n F2 |
|---------|--------|-----------|
| `app/brain/collections.py` | âœ… Existe y es correcto | Solo verificar `EMBED_MODEL_CODE` en `.env` |
| `app/brain/ingest_router.py` | âœ… Existe, es sÃ­ncrono y completo | Extender: aÃ±adir `search_space_id` + fix import |
| `app/brain/brain_ingest.py` | âœ… Existe, usa `metadata` dict | Sin cambio â€” `search_space_id` se inyecta vÃ­a `metadata` |
| `app/brain/qdrant_manager.py` | âŒ No existe | **CREAR** |
| `app/brain/chunking.py` | âŒ No existe (prerreq. F1.4) | **YA CREADO en F1** â€” F2 depende de Ã©l |

**Prerrequisito crÃ­tico (F1.4 debe estar completo):**  
`ingest_router.py` lÃ­nea 30 tiene `from chunking import get_chunks` â€” broken en Docker.  
F1.4 creÃ³ `app/brain/chunking.py`. F2 debe confirmar que el import estÃ¡ corregido a:
```python
from app.brain.chunking import get_chunks  # â† verificar en ingest_router.py lÃ­nea 30
```

---

## F2.1 â€” `collections.py`: verificar y fijar modelo CODE (DÃ­a 0)

**Fichero:** `surfsense_backend/app/brain/collections.py` â† **ya existe, NO refactorizar**

> **âš ï¸ DecisiÃ³n de diseÃ±o**: El documento original proponÃ­a refactorizar a `Enum + dataclass`. **No se hace.** El fichero real usa string constants (`BRAIN`, `KNOWLEDGE`, `CODE`) que son importados en `ingest_router.py`, `brain_ingest.py` y otros mÃ³dulos. Cambiar la estructura rompe todos esos imports sin valor aÃ±adido.

El fichero actual es correcto. Solo hay una discrepancia a resolver en variables de entorno:

```python
# collections.py â€” estructura actual, SIN CAMBIOS
BRAIN     = os.getenv("COLLECTION_BRAIN",     "brain")
KNOWLEDGE = os.getenv("COLLECTION_KNOWLEDGE", "knowledge")
CODE      = os.getenv("COLLECTION_CODE",      "code")

ALL_COLLECTIONS = [BRAIN, KNOWLEDGE, CODE]

EMBED_DIMS = {
    "nomic-embed-text": 768,
    "nomic-embed-code": 768,
    "qwen3-embedding:4b": 2560,   # â† confirmar que estÃ¡ en EMBED_DIMS
    ...
}

COLLECTION_CONFIG = {
    BRAIN: {
        "embed_model": "nomic-embed-text",
        "description": "Nivel 1 â€” Pasaportes semÃ¡nticos .md",
    },
    KNOWLEDGE: {
        "embed_model": "nomic-embed-text",
        "description": "Nivel 2 semÃ¡ntico â€” Docs, PDFs, webs, contratos",
    },
    CODE: {
        "embed_model": os.getenv("EMBED_MODEL_CODE", "nomic-embed-code"),
        "description": "Nivel 2 tÃ©cnico â€” CÃ³digo fuente, APIs, SQL, scripts",
    },
}
```

**AcciÃ³n requerida en `.env.dev`:**
```bash
# Cambiar default de nomic-embed-code (768d) a qwen3-embedding:4b (2560d)
EMBED_MODEL_CODE=qwen3-embedding:4b
```

**Comportamiento de auto-recreaciÃ³n de CODE** (ya implementado en `_ingest_code`):  
Si la colecciÃ³n `code` existe con 768d y se cambia `EMBED_MODEL_CODE` a `qwen3-embedding:4b` (2560d), `_ingest_code` detecta el mismatch, elimina la colecciÃ³n y la recrea. Esto es correcto y no requiere cambio de cÃ³digo â€” solo requiere que `.env.dev` tenga el modelo correcto **antes** de la primera ingesta.

---

## F2.2 â€” `QdrantManager`: cliente centralizado (DÃ­a 1)

**Fichero:** `surfsense_backend/app/brain/qdrant_manager.py` â† **NUEVO**

> **âš ï¸ DecisiÃ³n de diseÃ±o**: `QdrantManager` **no reemplaza** el `QdrantClient` que usa `IngestRouter`. ActÃºa como capa de inicializaciÃ³n y gestiÃ³n (colecciones, Ã­ndices, delete). El `IngestRouter` sigue recibiendo `QdrantClient` en su constructor â€” se le pasa `qdrant_manager.client`.

```python
# qdrant_manager.py
import os
import logging
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, FieldCondition, Filter,
    FilterSelector, MatchValue, PayloadSchemaType
)
from app.brain.collections import BRAIN, KNOWLEDGE, CODE, COLLECTION_CONFIG, EMBED_DIMS

logger = logging.getLogger(__name__)

_COLLECTION_DIMS = {
    BRAIN:     768,
    KNOWLEDGE: 768,
    # CODE: dim dinÃ¡mica â€” se resuelve en _ingest_code segÃºn EMBED_MODEL_CODE
}

class QdrantManager:
    """
    GestiÃ³n centralizada de colecciones Qdrant.
    - InicializaciÃ³n idempotente con Ã­ndices de payload
    - ExposiciÃ³n de .client para compatibilidad con IngestRouter y BrainIngestor
    - delete_by_source con filtro multi-tenant
    """

    _instance: "QdrantManager | None" = None

    def __init__(self) -> None:
        self.client = QdrantClient(
            host=os.getenv("QDRANT_HOST", "qdrant"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
        )

    @classmethod
    def get_instance(cls) -> "QdrantManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def ensure_collections(self) -> None:
        """
        Crear BRAIN y KNOWLEDGE si no existen y crear Ã­ndices de payload.
        CODE se omite aquÃ­ porque su dimensiÃ³n es dinÃ¡mica (EMBED_MODEL_CODE).
        _ingest_code en IngestRouter gestiona la creaciÃ³n/recreaciÃ³n de CODE.
        """
        existing = {c.name for c in self.client.get_collections().collections}

        for col_name, dim in _COLLECTION_DIMS.items():
            if col_name not in existing:
                self.client.create_collection(
                    collection_name=col_name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
                logger.info("ColecciÃ³n Qdrant creada: %s (%dd)", col_name, dim)
            else:
                # Verificar dimensiÃ³n â€” mismatch indica configuraciÃ³n incorrecta
                info = self.client.get_collection(col_name)
                actual_dim = info.config.params.vectors.size
                if actual_dim != dim:
                    raise ValueError(
                        f"ColecciÃ³n '{col_name}' tiene {actual_dim}d "
                        f"pero se esperan {dim}d. "
                        f"Ejecutar POST /admin/qdrant/collection/{col_name}/recreate"
                    )

            # Ãndices de payload para filtrado eficiente (idempotente)
            self._ensure_payload_index(col_name, "source",          PayloadSchemaType.KEYWORD)
            self._ensure_payload_index(col_name, "search_space_id", PayloadSchemaType.KEYWORD)

        logger.info("ensure_collections OK â€” brain:%s knowledge:%s",
                    BRAIN in existing, KNOWLEDGE in existing)

    def _ensure_payload_index(
        self, collection_name: str, field_name: str,
        field_schema: PayloadSchemaType
    ) -> None:
        """Crear Ã­ndice de payload si no existe. Idempotente."""
        try:
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=field_schema,
            )
        except Exception:
            pass  # Ya existe â€” ignorar

    def delete_by_source(self, source: str, search_space_id: str) -> None:
        """
        Borrar todos los vectores de un documento en las tres colecciones.
        Filtra por AMBOS source y search_space_id para garantizar aislamiento multi-tenant.
        Un usuario no puede borrar vectores de otro search_space aunque conozca el source.
        """
        for col_name in [BRAIN, KNOWLEDGE, CODE]:
            try:
                self.client.delete(
                    collection_name=col_name,
                    points_selector=FilterSelector(
                        filter=Filter(
                            must=[
                                FieldCondition(key="source",          match=MatchValue(value=source)),
                                FieldCondition(key="search_space_id", match=MatchValue(value=search_space_id)),
                            ]
                        )
                    ),
                )
            except Exception as exc:
                logger.warning("delete_by_source colecciÃ³n='%s': %s", col_name, exc)
        logger.info("Vectores borrados â€” source='%s' space='%s'", source, search_space_id)
```

---

## F2.3 â€” `IngestRouter`: aÃ±adir `search_space_id` (DÃ­a 1-2)

**Fichero:** `surfsense_backend/app/brain/ingest_router.py` â† **EXTENDER, no reemplazar**

> **âš ï¸ Decisiones de diseÃ±o**:
> - El `IngestRouter` real es **sÃ­ncrono** y usa `_embed()` local. **No se convierte a async** â€” eso afectarÃ­a todo el pipeline Celery y estÃ¡ fuera del alcance de F2.
> - El constructor sigue siendo `IngestRouter(qdrant_client: QdrantClient)`. Se le pasa `qdrant_manager.client`.
> - El mÃ©todo `route_raw()` ya existe y es correcto â€” solo aÃ±adir `search_space_id`.

**Cambios concretos en `ingest_router.py`:**

**1. Fix import roto (lÃ­nea ~30) â€” prerrequisito de F1.4:**
```python
# ANTES (roto en Docker PYTHONPATH=/app):
from chunking import get_chunks

# DESPUÃ‰S:
from app.brain.chunking import get_chunks
```

**2. AÃ±adir `search_space_id` al mÃ©todo `route()`:**
```python
# ANTES:
def route(self, md_content: str, blocks: list[dict], source: str, ingest_metadata: dict = None) -> dict:

# DESPUÃ‰S:
def route(
    self,
    md_content: str,
    blocks: list[dict],
    source: str,
    search_space_id: str,           # â† NUEVO â€” requerido
    ingest_metadata: dict = None,
) -> dict:
```

**3. Propagar `search_space_id` a `_ingest_brain`** (vÃ­a metadata dict, `BrainIngestor` hace `**metadata`):
```python
# En route(), dentro de: results[BRAIN] = self._ingest_brain(...)
# ANTES:
results[BRAIN] = self._ingest_brain(md_content, canonical_source, meta, ingest_metadata)

# DESPUÃ‰S:
results[BRAIN] = self._ingest_brain(
    md_content, canonical_source, meta, ingest_metadata,
    search_space_id=search_space_id,
)
```

**Firma actualizada de `_ingest_brain`:**
```python
def _ingest_brain(self, md_content, source, meta, ingest_metadata=None, *, search_space_id: str = "") -> dict:
    ...
    metadata = {
        "kb_id":          meta.get("id", ""),
        "type":           meta.get("type", ""),
        "domain":         meta.get("domain", ""),
        "subdomain":      meta.get("subdomain", ""),
        "importance":     meta.get("importance", "medium"),
        "confidence":     float(meta.get("confidence", 0.5)),
        "refresh_policy": meta.get("refresh_policy", "never"),
        "projects":       meta.get("projects") or [],
        "search_space_id": search_space_id,   # â† NUEVO
    }
```

**4. AÃ±adir `search_space_id` al payload de `_ingest_knowledge_raw` (en el loop):**
```python
# En el PointStruct de _ingest_knowledge_raw:
payload={
    "text":           chunk,
    "source":         source,
    "search_space_id": search_space_id,   # â† NUEVO
    "kb_id":          meta.get("id", ""),
    ...
}
```
> Requiere que `_ingest_knowledge_raw` reciba `search_space_id` como parÃ¡metro y que `route()` lo propague.

**5. AÃ±adir `search_space_id` al payload de `_upsert_text_chunks`:**
```python
# En _upsert_text_chunks â€” aÃ±adir parÃ¡metro search_space_id y propagarlo al payload
payload={
    "text":           chunk,
    "source":         source,
    "search_space_id": search_space_id,   # â† NUEVO
    ...
}
```

**6. AÃ±adir `search_space_id` al payload de `_ingest_code`:**
```python
payload={
    "text":        chunk,
    "source":      source,
    "search_space_id": search_space_id,   # â† NUEVO
    ...
}
```

**7. AÃ±adir `search_space_id` al payload de `_ingest_full_document`:**
```python
payload={
    "text":    all_text,
    "source":  source,
    "search_space_id": search_space_id,   # â† NUEVO
    ...
}
```

**8. Actualizar `route_raw()` con `search_space_id`:**
```python
# ANTES:
def route_raw(self, blocks: list[dict], source: str, ingest_metadata: dict = None) -> dict:

# DESPUÃ‰S:
def route_raw(
    self,
    blocks: list[dict],
    source: str,
    search_space_id: str,           # â† NUEVO
    ingest_metadata: dict = None,
) -> dict:
```

**Resumen de cambios en `ingest_router.py`:**

| MÃ©todo | Cambio |
|--------|--------|
| `route()` | + parÃ¡metro `search_space_id: str` |
| `route_raw()` | + parÃ¡metro `search_space_id: str` |
| `_ingest_brain()` | + kwarg `search_space_id`, aÃ±ade a `metadata` dict |
| `_ingest_knowledge_raw()` | + parÃ¡metro `search_space_id`, aÃ±ade a payload PointStruct |
| `_ingest_knowledge_extract()` | + propaga a `_upsert_text_chunks` |
| `_upsert_text_chunks()` | + parÃ¡metro `search_space_id`, aÃ±ade a payload |
| `_ingest_code()` | + parÃ¡metro `search_space_id`, aÃ±ade a payload |
| `_ingest_full_document()` | + parÃ¡metro `search_space_id`, aÃ±ade a payload |
| import lÃ­nea ~30 | `from chunking` â†’ `from app.brain.chunking` |

---

## F2.4 â€” InicializaciÃ³n en lifespan y adaptaciÃ³n del task Celery (DÃ­a 2-3)

**Fichero:** `surfsense_backend/app/app.py` â€” secciÃ³n `lifespan` (ya modificada en F1.4)

AÃ±adir `QdrantManager.ensure_collections()` en el startup, junto a lo de F1.4:

```python
# En lifespan(), secciÃ³n startup (junto a BrainMetadataService de F1.4):
from app.brain.qdrant_manager import QdrantManager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... cÃ³digo F1.4 (BrainMetadataService, BrainWatcher) ...

    # F2 â€” Inicializar colecciones Qdrant
    try:
        qdrant_mgr = QdrantManager.get_instance()
        qdrant_mgr.ensure_collections()
        logger.info("QdrantManager: colecciones inicializadas")
    except Exception as exc:
        logger.error("QdrantManager: error en ensure_collections: %s", exc)
        # No bloqueamos el arranque â€” _ingest_code gestiona CODE dinÃ¡micamente

    yield
    # ... shutdown ...
```

**Task Celery â€” construcciÃ³n de `IngestRouter`:**
```python
# En el task de ingesta â€” pasar QdrantManager.client al constructor
from app.brain.qdrant_manager import QdrantManager
from app.brain.ingest_router import IngestRouter

qdrant_mgr = QdrantManager.get_instance()
router = IngestRouter(qdrant_client=qdrant_mgr.client)   # â† constructor real sin cambios

result = router.route(
    md_content=synthesis_result["summary"],
    blocks=processed_result["blocks"],
    source=source_slug,
    search_space_id=search_space_id,           # â† NUEVO â€” requerido desde F2
    ingest_metadata={
        "ingest_origin": "file_upload",
        "ingest_date":   date.today().isoformat(),
        ...
    }
)
```

> **Nota sobre F3**: En F3, `passport_md` sustituye a `synthesis_result["summary"]`. En F2 se puede usar el `summary` como placeholder â€” el campo `search_space_id` en los payloads es lo crÃ­tico para desbloquear F4.

---

## F2.5 â€” Endpoint admin para gestiÃ³n de colecciones (DÃ­a 3-4)

**Fichero:** `surfsense_backend/app/routes/admin_routes.py` â† aÃ±adir endpoints

```python
from app.brain.qdrant_manager import QdrantManager
from app.brain.collections import BRAIN, KNOWLEDGE, CODE, COLLECTION_CONFIG

@router.get("/admin/qdrant/collections")
async def list_qdrant_collections(
    current_user: User = Depends(get_current_admin_user)
):
    """Lista las colecciones Qdrant con estadÃ­sticas."""
    qdrant = QdrantManager.get_instance()
    result = {}
    for col_name in [BRAIN, KNOWLEDGE, CODE]:
        try:
            info = qdrant.client.get_collection(col_name)
            result[col_name] = {
                "vectors_count": info.vectors_count,
                "vector_size":   info.config.params.vectors.size,
                "embed_model":   COLLECTION_CONFIG.get(col_name, {}).get("embed_model", ""),
                "status":        "ok",
            }
        except Exception:
            result[col_name] = {"status": "not_created"}
    return result

@router.post("/admin/qdrant/collection/{collection_name}/recreate")
async def recreate_collection(
    collection_name: str,
    current_user: User = Depends(get_current_admin_user),
):
    """Recrear una colecciÃ³n (borra todos los vectores). Solo admin. Irreversible."""
    if collection_name not in [BRAIN, KNOWLEDGE, CODE]:
        raise HTTPException(status_code=404, detail=f"ColecciÃ³n desconocida: {collection_name}")

    qdrant = QdrantManager.get_instance()
    qdrant.client.delete_collection(collection_name)
    qdrant.ensure_collections()   # recrea BRAIN/KNOWLEDGE con Ã­ndices
    logger.warning("ColecciÃ³n '%s' recreada por admin '%s'", collection_name, current_user.email)
    return {"status": "recreated", "collection": collection_name}

@router.delete("/admin/qdrant/document/{source}")
async def delete_document_vectors(
    source: str,
    search_space_id: str,          # query param â€” filtro multi-tenant obligatorio
    current_user: User = Depends(get_current_admin_user),
):
    """
    Borrar todos los vectores de un documento en las tres colecciones.
    Requiere search_space_id para garantizar aislamiento multi-tenant.
    """
    qdrant = QdrantManager.get_instance()
    qdrant.delete_by_source(source=source, search_space_id=search_space_id)
    return {"status": "deleted", "source": source, "search_space_id": search_space_id}
```

---

## F2.6 â€” Tests F2 (DÃ­a 4-5)

**Fichero:** `tests/brain/test_qdrant_f2.py`

```python
# tests/brain/test_qdrant_f2.py
import pytest
from unittest.mock import MagicMock, patch, call
from app.brain.qdrant_manager import QdrantManager
from app.brain.ingest_router import IngestRouter
from app.brain.collections import BRAIN, KNOWLEDGE, CODE


class TestQdrantManager:

    def test_ensure_collections_creates_brain_and_knowledge(self):
        """ensure_collections crea brain y knowledge con dim 768."""
        mock_client = MagicMock()
        mock_client.get_collections.return_value = MagicMock(collections=[])
        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = mock_client

        mgr.ensure_collections()

        calls = [c.kwargs["collection_name"] for c in mock_client.create_collection.call_args_list]
        assert BRAIN in calls
        assert KNOWLEDGE in calls
        assert CODE not in calls   # CODE es dinÃ¡mico, no se crea aquÃ­

    def test_ensure_collections_idempotente_dim_correcta(self):
        """ensure_collections no falla si colecciones ya existen con dim correcta."""
        mock_client = MagicMock()
        brain_col = MagicMock(name=BRAIN)
        know_col  = MagicMock(name=KNOWLEDGE)
        mock_client.get_collections.return_value = MagicMock(
            collections=[brain_col, know_col]
        )
        mock_client.get_collection.return_value.config.params.vectors.size = 768

        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = mock_client
        mgr.ensure_collections()   # No debe lanzar

        mock_client.create_collection.assert_not_called()

    def test_ensure_collections_raises_on_dim_mismatch(self):
        """ensure_collections lanza ValueError si colecciÃ³n existe con dim incorrecta."""
        mock_client = MagicMock()
        brain_col = MagicMock(name=BRAIN)
        mock_client.get_collections.return_value = MagicMock(collections=[brain_col])
        mock_client.get_collection.return_value.config.params.vectors.size = 384  # incorrecto

        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = mock_client

        with pytest.raises(ValueError, match="768d"):
            mgr.ensure_collections()

    def test_delete_by_source_filtra_por_search_space_id(self):
        """delete_by_source filtra por source Y search_space_id â€” no borra otros tenants."""
        mock_client = MagicMock()
        mgr = QdrantManager.__new__(QdrantManager)
        mgr.client = mock_client

        mgr.delete_by_source(source="doc.pdf", search_space_id="space-A")

        assert mock_client.delete.call_count == 3  # brain + knowledge + code
        for call_args in mock_client.delete.call_args_list:
            filter_obj = call_args.kwargs["points_selector"].filter
            keys = [c.key for c in filter_obj.must]
            assert "source" in keys
            assert "search_space_id" in keys


class TestIngestRouterSearchSpaceId:

    def test_route_requiere_search_space_id(self):
        """route() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter.route)
        assert "search_space_id" in sig.parameters, \
            "route() debe tener parÃ¡metro search_space_id"

    def test_route_raw_requiere_search_space_id(self):
        """route_raw() debe recibir search_space_id."""
        import inspect
        sig = inspect.signature(IngestRouter.route_raw)
        assert "search_space_id" in sig.parameters, \
            "route_raw() debe tener parÃ¡metro search_space_id"

    @patch("app.brain.ingest_router._embed", return_value=[0.1] * 768)
    @patch("app.brain.ingest_router.get_chunks", return_value=["chunk1"])
    def test_knowledge_payload_contiene_search_space_id(self, mock_chunks, mock_embed):
        """Payload de knowledge debe incluir search_space_id."""
        mock_client = MagicMock()
        router = IngestRouter(qdrant_client=mock_client)

        text_block = {
            "content": "texto de prueba",
            "content_type": "text",
            "metadata": {},
        }
        router._ingest_knowledge_raw(
            text_blocks=[text_block],
            source="test-doc",
            meta={},
            ingest_metadata=None,
            search_space_id="space-test-001",
        )

        upsert_call = mock_client.upsert.call_args
        points = upsert_call.kwargs["points"]
        assert len(points) > 0
        assert points[0].payload["search_space_id"] == "space-test-001"

    def test_import_chunking_correcto(self):
        """Verificar que el import de get_chunks usa la ruta correcta."""
        import app.brain.ingest_router as ir_module
        import inspect
        source = inspect.getsource(ir_module)
        assert "from app.brain.chunking import get_chunks" in source, \
            "Import de get_chunks debe ser 'from app.brain.chunking import get_chunks'"
        assert "from chunking import get_chunks" not in source, \
            "Import roto 'from chunking import get_chunks' detectado"


class TestColeccionesQdrantIntegracion:
    """Tests de integraciÃ³n contra Qdrant real (requieren QDRANT_HOST disponible)."""

    @pytest.mark.integration
    def test_three_collections_exist_after_ensure(self):
        """Con Qdrant real: las tres colecciones existen tras ensure_collections."""
        mgr = QdrantManager.get_instance()
        mgr.ensure_collections()

        existing = {c.name for c in mgr.client.get_collections().collections}
        assert BRAIN in existing
        assert KNOWLEDGE in existing
        # CODE puede no existir aÃºn (se crea en primer _ingest_code)

    @pytest.mark.integration
    def test_brain_collection_768d(self):
        mgr = QdrantManager.get_instance()
        info = mgr.client.get_collection(BRAIN)
        assert info.config.params.vectors.size == 768

    @pytest.mark.integration
    def test_knowledge_collection_768d(self):
        mgr = QdrantManager.get_instance()
        info = mgr.client.get_collection(KNOWLEDGE)
        assert info.config.params.vectors.size == 768

    @pytest.mark.integration
    def test_upsert_delete_aislamiento_multi_tenant(self):
        """Insertar en space-A, borrar con space-A no afecta space-B."""
        from qdrant_client.models import PointStruct
        mgr = QdrantManager.get_instance()

        # Insertar en space-A y space-B
        mgr.client.upsert(BRAIN, points=[
            PointStruct(id="pt-space-a", vector=[0.1]*768,
                        payload={"source": "doc.md", "search_space_id": "space-A"}),
            PointStruct(id="pt-space-b", vector=[0.2]*768,
                        payload={"source": "doc.md", "search_space_id": "space-B"}),
        ])

        # Borrar solo space-A
        mgr.delete_by_source(source="doc.md", search_space_id="space-A")

        # Verificar space-B intacto
        results = mgr.client.retrieve(BRAIN, ids=["pt-space-b"])
        assert len(results) == 1
        assert results[0].payload["search_space_id"] == "space-B"
```

---

## Checklist F2

### F2.0 â€” Prerrequisitos y estado del codebase
- [ ] Confirmar que `app/brain/chunking.py` existe (creado en F1.4)
- [ ] Verificar que `ingest_router.py` lÃ­nea ~30 usa `from app.brain.chunking import get_chunks` (no `from chunking import get_chunks`)
- [ ] `EMBED_MODEL_CODE=qwen3-embedding:4b` en `.env.dev` antes de primera ingesta de cÃ³digo

### F2.1 â€” `collections.py`
- [ ] `collections.py` NO refactorizado â€” string constants conservados (`BRAIN`, `KNOWLEDGE`, `CODE`)
- [ ] `EMBED_DIMS` incluye `"qwen3-embedding:4b": 2560`

### F2.2 â€” `QdrantManager`
- [ ] `qdrant_manager.py` creado en `app/brain/`
- [ ] `ensure_collections()` crea `brain` (768d) y `knowledge` (768d) â€” CODE omitido (dim dinÃ¡mica)
- [ ] Ãndices de payload `source` y `search_space_id` creados en brain y knowledge
- [ ] `ensure_collections()` idempotente â€” no falla si colecciones ya existen con dim correcta
- [ ] `ensure_collections()` lanza `ValueError` si dim no coincide
- [ ] `delete_by_source(source, search_space_id)` filtra por **ambos** campos â€” seguridad multi-tenant
- [ ] `QdrantManager.client` expuesto â€” usado en `IngestRouter` constructor y `BrainIngestor`

### F2.3 â€” `ingest_router.py`
- [ ] Import corregido: `from app.brain.chunking import get_chunks`
- [ ] `route()` recibe `search_space_id: str`
- [ ] `route_raw()` recibe `search_space_id: str`
- [ ] `_ingest_brain()` propaga `search_space_id` en `metadata` dict (BrainIngestor lo incluye en payload)
- [ ] `_ingest_knowledge_raw()` incluye `search_space_id` en cada PointStruct payload
- [ ] `_upsert_text_chunks()` incluye `search_space_id` en cada PointStruct payload
- [ ] `_ingest_code()` incluye `search_space_id` en cada PointStruct payload
- [ ] `_ingest_full_document()` incluye `search_space_id` en PointStruct payload
- [ ] Constructor `IngestRouter(qdrant_client: QdrantClient)` sin cambios â€” se pasa `qdrant_mgr.client`

### F2.4 â€” Lifespan y Celery
- [ ] `QdrantManager.get_instance().ensure_collections()` en `lifespan` de `app.py` (junto a F1.4)
- [ ] Task Celery: `IngestRouter(qdrant_client=QdrantManager.get_instance().client)`
- [ ] Task Celery: `router.route(..., search_space_id=search_space_id, ...)` con el campo requerido

### F2.5 â€” Admin endpoints
- [ ] `GET /admin/qdrant/collections` funcional con estadÃ­sticas por colecciÃ³n
- [ ] `POST /admin/qdrant/collection/{name}/recreate` requiere rol admin
- [ ] `DELETE /admin/qdrant/document/{source}` requiere `search_space_id` como query param
- [ ] NingÃºn endpoint admin permite borrar vectores de otro tenant

### F2.6 â€” Tests
- [ ] `TestQdrantManager.test_ensure_collections_creates_brain_and_knowledge` pasa
- [ ] `TestQdrantManager.test_ensure_collections_raises_on_dim_mismatch` pasa
- [ ] `TestQdrantManager.test_delete_by_source_filtra_por_search_space_id` pasa (filtra por ambos)
- [ ] `TestIngestRouterSearchSpaceId.test_route_requiere_search_space_id` pasa
- [ ] `TestIngestRouterSearchSpaceId.test_knowledge_payload_contiene_search_space_id` pasa
- [ ] `TestIngestRouterSearchSpaceId.test_import_chunking_correcto` pasa
- [ ] Tests de integraciÃ³n marcados con `@pytest.mark.integration` â€” ejecutar solo con Qdrant disponible

### Criterio de aceptaciÃ³n global F2
- [ ] `docker compose logs backend | grep "QdrantManager"` â†’ `colecciones inicializadas`
- [ ] Un `.py` ingestado â†’ vectores en colecciÃ³n `code` con `search_space_id` en payload
- [ ] Un `.md` ingestado â†’ vectores en colecciÃ³n `brain` con `search_space_id` en payload
- [ ] Un `.pdf` ingestado â†’ vectores en colecciÃ³n `knowledge` con `search_space_id` en payload
- [ ] `delete_by_source("doc.pdf", "space-A")` no borra vectores de `space-B`
- [ ] `document_segment` en PostgreSQL marcada como deprecated en comentario del modelo (no eliminada aÃºn)

---

**Anterior:** [F1 â€” Pipeline de Tres Fases](./F1-pipeline-tres-fases.md)  
**Siguiente:** [F3 â€” SÃ­ntesis Multi-call y Pasaporte SemÃ¡ntico](./F3-sintesis-pasaporte.md)
