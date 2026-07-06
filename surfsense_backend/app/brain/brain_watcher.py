"""
brain_watcher.py
----------------
Watcher asíncrono que monitoriza BRAIN_DIR y re-ingesta en la colección brain
cualquier .md que sea creado o modificado manualmente.

Flujo:
  1. Usuario edita /data/brain/documento.md
  2. Watcher detecta el cambio (evento modify/add) vía watchfiles.awatch
  3. Debounce de DEBOUNCE_SECONDS para evitar disparos múltiples por un guardado
  4. _reingest_md() borra chunks viejos del slug y re-vectoriza el .md

Decisión de diseño (DT-F3-watcher):
  BrainIngestor genera IDs deterministas con md5(source+idx) → upsert idempotente.
  search_space_id se lee del frontmatter YAML del .md (fuente de verdad).
  Fallback: se rescata del payload Qdrant de los chunks existentes antes de borrarlos.

Variables de entorno relevantes:
  BRAIN_DIR       — directorio de pasaportes .md (default: /data/brain)
  QDRANT_HOST     — host de Qdrant (default: localhost)
  QDRANT_PORT     — puerto de Qdrant (default: 6333)
  COLLECTION_BRAIN — nombre de la colección brain (default: brain)
  EMBED_MODEL     — modelo de embedding para brain (default: nomic-embed-text)
"""
import asyncio
import logging
import os
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


def _delete_old_chunks(qdrant_client, slug: str) -> str:
    """
    Borra en c_brain todos los chunks cuyo source coincida con el slug del .md.
    Devuelve el search_space_id encontrado en los chunks existentes (o "" si no hay).
    """
    from qdrant_client.models import PointIdsList

    try:
        log.debug("[watcher] Buscando chunks existentes para slug '%s' en '%s'", slug, C_BRAIN)
        result, _ = qdrant_client.scroll(
            collection_name=C_BRAIN,
            with_payload=True,
            limit=10000,
        )
        from app.brain.writer import _slugify
        matching = [
            p for p in result
            if p.payload and _slugify(p.payload.get("source", "")) == slug
        ]
        # Rescatar search_space_id antes de borrar (fallback si frontmatter no lo tiene)
        search_space_id = ""
        for p in matching:
            sid = p.payload.get("search_space_id", "")
            if sid:
                search_space_id = sid
                break

        ids = [p.id for p in matching]
        if ids:
            qdrant_client.delete(
                collection_name=C_BRAIN,
                points_selector=PointIdsList(points=ids),
            )
            log.info(
                "[watcher] Borrados %d chunks de c_brain para slug '%s' (space=%s)",
                len(ids), slug, search_space_id or "—",
            )
        else:
            log.info(
                "[watcher] No se encontraron chunks previos para slug '%s' "
                "(total puntos en coleccion: %d)",
                slug, len(result),
            )
        return search_space_id
    except Exception as exc:
        log.error("[watcher] Error borrando chunks para '%s': %s", slug, exc, exc_info=True)
        return ""


def _reingest_md(md_path: str):
    """
    Lee el .md del disco y lo re-vectoriza en la colección brain.

    Usa _embed de ingest_router — la misma función que usa IngestRouter en el
    pipeline principal. El modelo de embedding se lee de EMBED_MODEL (entorno)
    para garantizar coherencia con las dimensiones de la colección brain.

    search_space_id se obtiene del frontmatter YAML (fuente de verdad).
    Fallback: se rescata del payload Qdrant existente antes de borrar.
    """
    from qdrant_client import QdrantClient
    from app.brain.ingest_router import _embed
    from app.brain.brain_ingest import BrainIngestor

    embed_model_name: str = os.getenv("EMBED_MODEL", "nomic-embed-text")

    slug = _source_from_md_path(md_path)
    log.info("[watcher] Re-ingestión de '%s' (slug='%s', embed_model='%s')",
             md_path, slug, embed_model_name)

    try:
        content = Path(md_path).read_text(encoding="utf-8").lstrip("﻿")
        log.debug("[watcher] Leídos %d caracteres de '%s'", len(content), md_path)
    except Exception as exc:
        log.error("[watcher] No se pudo leer '%s': %s", md_path, exc, exc_info=True)
        return

    if not content.strip():
        log.warning("[watcher] '%s' está vacío, se omite.", md_path)
        return

    # Fuente de verdad: search_space_id del frontmatter YAML
    search_space_id_from_fm = ""
    try:
        import yaml as _yaml
        import re as _re
        fm_match = _re.search(r"^---\n(.*?)\n---", content, _re.DOTALL)
        if fm_match:
            fm = _yaml.safe_load(fm_match.group(1)) or {}
            search_space_id_from_fm = str(fm.get("search_space_id", "") or "")
    except Exception:
        pass

    try:
        qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

        # Borrar chunks previos; retorna search_space_id del payload Qdrant (fallback)
        search_space_id_from_qdrant = _delete_old_chunks(qdrant_client, slug)

        # Fuente de verdad: frontmatter. Fallback: payload Qdrant existente.
        search_space_id = search_space_id_from_fm or search_space_id_from_qdrant
        if not search_space_id:
            log.warning(
                "[watcher] search_space_id no encontrado para '%s' — "
                "documento no visible en ningún space", slug,
            )

        embed_model = type("EmbedModel", (), {
            "embed": staticmethod(lambda t: _embed(t, embed_model_name))
        })
        ingestor = BrainIngestor(qdrant_client, embed_model, collection=C_BRAIN)

        metadata: dict = {"md_path": md_path}
        if search_space_id:
            metadata["search_space_id"] = search_space_id

        log.debug(
            "[watcher] Vectorizando '%s' en colección '%s' (space=%s)",
            slug, C_BRAIN, search_space_id or "—",
        )
        chunks_created = ingestor.ingest_md(
            source=slug, md_content=content, metadata=metadata
        )
        log.info(
            "[watcher] Re-ingestión completada: path='%s' slug='%s' "
            "colección='%s' chunks=%d chars=%d space=%s",
            md_path, slug, C_BRAIN, chunks_created, len(content),
            search_space_id or "—",
        )
    except Exception as exc:
        log.error("[watcher] Error re-ingestando '%s': %s", md_path, exc, exc_info=True)


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
