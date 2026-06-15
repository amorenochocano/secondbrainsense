"""
brain_watcher.py
----------------
Watcher asíncrono que monitoriza la carpeta /data/brain y re-ingesta en c_brain
cualquier .md que sea creado o modificado manualmente.

Flujo:
  1. Usuario edita brain/documento.md
  2. Watcher detecta el cambio (evento modify/add)
  3. Borra chunks viejos de ese source en c_brain
  4. Re-vectoriza el .md actualizado con BrainIngestor

No toca c_doc_secondbrain (nivel 2). Solo actúa sobre c_brain (nivel 1).
"""
import asyncio
import logging
import os
import traceback
from pathlib import Path

log = logging.getLogger(__name__)

BRAIN_DIR = os.getenv("BRAIN_DIR", "/data/brain")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
# Nombre de la colección brain: debe coincidir con COLLECTION_BRAIN del docker-compose
C_BRAIN = os.getenv("COLLECTION_BRAIN", "brain")

# Debounce en segundos: espera este tiempo tras el último evento antes de re-ingestar.
# Evita disparos múltiples mientras el editor guarda el fichero.
DEBOUNCE_SECONDS = 2.0


def _source_from_md_path(md_path: str) -> str:
    """
    Convierte la ruta del .md a un 'source' compatible con el payload de Qdrant.
    El source almacenado en Qdrant es el nombre original del fichero (ej: "doc.pdf").
    El slug del .md se genera desde ese nombre, así que hacemos la correspondencia
    buscando en los payloads de Qdrant directamente por slug.
    Devuelve el slug (nombre del .md sin extensión) como identificador de búsqueda.
    """
    return Path(md_path).stem  # ej: "mi-documento"


def _delete_old_chunks(qdrant_client, slug: str):
    """
    Borra en c_brain todos los chunks cuyo source coincida con el slug del .md.
    Primero intenta match exacto por slug, luego por source que contenga el slug.
    """
    from qdrant_client.models import PointIdsList, Filter, FieldCondition, MatchValue

    try:
        log.debug("[watcher] Buscando chunks existentes para slug '%s' en '%s'", slug, C_BRAIN)
        result, _ = qdrant_client.scroll(
            collection_name=C_BRAIN,
            with_payload=True,
            limit=10000,
        )
        # Busca chunks cuyo source genere el mismo slug
        from app.brain.writer import _slugify
        ids = [
            p.id for p in result
            if p.payload and _slugify(p.payload.get("source", "")) == slug
        ]
        if ids:
            qdrant_client.delete(
                collection_name=C_BRAIN,
                points_selector=PointIdsList(points=ids),
            )
            log.info("[watcher] Borrados %d chunks de c_brain para slug '%s'", len(ids), slug)
        else:
            log.info("[watcher] No se encontraron chunks previos para slug '%s' (total puntos en coleccion: %d)", slug, len(result))
        return ids
    except Exception as exc:
        log.error("[watcher] Error borrando chunks para '%s': %s\n%s", slug, exc, traceback.format_exc())
        return []


def _reingest_md(md_path: str):
    """
    Lee el .md del disco y lo re-vectoriza en c_brain.
    El 'source' que se graba en el payload es el slug del .md (nombre del fichero sin .md).
    """
    from qdrant_client import QdrantClient
    from ingest_utils import get_embedding
    from app.brain.brain_ingest import BrainIngestor

    slug = _source_from_md_path(md_path)
    log.info("[watcher] Iniciando re-ingestión de '%s' (slug='%s')", md_path, slug)

    try:
        content = Path(md_path).read_text(encoding="utf-8")
        content = content.lstrip("\ufeff")  # Eliminar BOM si el fichero fue creado con UTF-8 BOM
        log.debug("[watcher] Leídos %d caracteres de '%s'", len(content), md_path)
    except Exception as exc:
        log.error("[watcher] No se pudo leer '%s': %s\n%s", md_path, exc, traceback.format_exc())
        return

    if not content.strip():
        log.warning("[watcher] El fichero '%s' está vacío, se omite.", md_path)
        return

    try:
        qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        # Borra los chunks viejos
        _delete_old_chunks(qdrant_client, slug)

        # Re-vectoriza
        embed_model = type("EmbedModel", (), {"embed": staticmethod(get_embedding)})
        ingestor = BrainIngestor(qdrant_client, embed_model, collection=C_BRAIN)
        # Usamos el slug como source para mantener coherencia con lo que hay en Qdrant
        log.debug("[watcher] Vectorizando '%s' en colección '%s'", slug, C_BRAIN)
        chunks_created = ingestor.ingest_md(source=slug, md_content=content, metadata={"md_path": md_path})
        log.info(
            "[watcher] Re-ingestión COMPLETADA '%s' → colección='%s', slug='%s', chunks=%d, chars=%d",
            md_path, C_BRAIN, slug, chunks_created, len(content)
        )
    except Exception as exc:
        log.error("[watcher] Error re-ingestando '%s': %s\n%s", md_path, exc, traceback.format_exc())


async def watch_brain_dir():
    """
    Tarea asyncio que monitoriza BRAIN_DIR.
    Usa watchfiles.awatch (ya instalado como dependencia de uvicorn).
    Aplica debounce para evitar disparos múltiples por un mismo guardado.
    """
    try:
        from watchfiles import awatch, Change
    except ImportError:
        log.error("[watcher] 'watchfiles' no está instalado. El watcher no arrancará.")
        return

    brain_path = Path(BRAIN_DIR)
    if not brain_path.exists():
        brain_path.mkdir(parents=True, exist_ok=True)

    log.info("[watcher] Monitorizando cambios en '%s'", BRAIN_DIR)

    # Mapa de ficheros pendientes de re-ingestar (debounce)
    pending: dict[str, asyncio.TimerHandle] = {}
    loop = asyncio.get_running_loop()

    def schedule_reingest(md_path: str):
        """Cancela el timer anterior y programa uno nuevo (debounce)."""
        if md_path in pending:
            log.debug("[watcher] Debounce: cancelando timer previo para '%s'", md_path)
            pending[md_path].cancel()

        def do_reingest():
            pending.pop(md_path, None)
            log.info("[watcher] Debounce completado (%.1fs). Lanzando re-ingestión para '%s'", DEBOUNCE_SECONDS, md_path)
            # Ejecutar en thread pool para no bloquear el event loop
            loop.run_in_executor(None, _reingest_md, md_path)

        log.debug("[watcher] Debounce: programando re-ingestión en %.1fs para '%s'", DEBOUNCE_SECONDS, md_path)
        handle = loop.call_later(DEBOUNCE_SECONDS, do_reingest)
        pending[md_path] = handle

    async for changes in awatch(BRAIN_DIR):
        for change_type, changed_path in changes:
            if not changed_path.endswith(".md"):
                log.debug("[watcher] Cambio ignorado (no .md): %s", changed_path)
                continue
            if change_type in (Change.modified, Change.added):
                log.info("[watcher] Cambio detectado (%s): '%s'", change_type.name, changed_path)
                schedule_reingest(changed_path)
            else:
                log.debug("[watcher] Evento no procesado (%s): '%s'", change_type.name, changed_path)
