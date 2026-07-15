"""
brain_ingest_tasks.py
---------------------
Tareas Celery para el pipeline de ingesta Brain.

Progreso en tiempo real: cada tarea publica eventos JSON en Redis Pub/Sub
en el canal  brain:ingest:<task_id>  y finaliza con el sentinel "[DONE]".
El endpoint GET /api/v1/brain/ingest/stream suscribe ese canal y hace
streaming SSE al cliente sin cambios en el frontend.

Canal Redis: brain:ingest:<task_id>
TTL resultado: 3600s (1h) en brain:ingest:result:<task_id>
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os

import redis as redis_sync

from app.celery_app import celery_app

log = logging.getLogger(__name__)

_CHANNEL_PREFIX = "brain:ingest:"
_RESULT_PREFIX  = "brain:ingest:result:"
_RESULT_TTL     = 3600  # 1 hora


def _get_redis() -> redis_sync.Redis:
    url = os.getenv("REDIS_APP_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    return redis_sync.from_url(url, decode_responses=True)


def _publish(r: redis_sync.Redis, channel: str, phase: str, status: str,
             chunks: int = 0, detail: str = "") -> None:
    event = json.dumps({"phase": phase, "status": status, "chunks": chunks, "detail": detail},
                       ensure_ascii=False)
    r.publish(channel, event)
    log.debug("[brain_ingest] channel=%s phase=%s status=%s chunks=%d", channel, phase, status, chunks)


def _finish(r: redis_sync.Redis, channel: str, result: dict) -> None:
    """Publica [DONE] y persiste el resultado para clientes que lleguen tarde."""
    r.publish(channel, "[DONE]")
    r.setex(f"{_RESULT_PREFIX}{result['task_id']}", _RESULT_TTL,
            json.dumps(result, ensure_ascii=False))


def _register_redis_timestamps(r: redis_sync.Redis, search_space_id: int, source: str) -> None:
    try:
        now_iso = datetime.datetime.utcnow().isoformat()
        r.set(f"brain:last_ingest:space:{search_space_id}", now_iso)
        r.set(f"brain:last_ingest_doc:space:{search_space_id}", source)
    except Exception as exc:
        log.warning("[brain_ingest] Redis timestamps error space=%d: %s", search_space_id, exc)


def _synthesize_and_index(
    r: redis_sync.Redis,
    channel: str,
    task_id: str,
    blocks: list,
    source: str,
    file_type: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
    ingest_metadata: dict | None = None,
) -> dict:
    """
    Fases 2 (síntesis) + 3 (indexación) compartidas por todas las tareas.
    Retorna el dict final de resultado.
    """
    from app.brain.synthesizer import DocumentSynthesizer
    from app.brain.writer import BrainWriter
    from app.brain.ingest_router import IngestRouter
    from app.brain.qdrant_manager import QdrantManager

    synth_model    = model    or os.getenv("SYNTHESIS_MODEL",    "qwen2.5-coder:3b")
    synth_provider = provider or os.getenv("BRAIN_LLM_PROVIDER", "ollama")

    # ── FASE 2: Síntesis LLM ─────────────────────────────────────────────────
    _publish(r, channel, "synthesis", "running")
    full_text = "\n\n".join(
        b.get("content", "") for b in blocks if b.get("content")
    ).strip()
    if not full_text:
        _publish(r, channel, "synthesis", "error", detail="Contenido extraído vacío")
        return {"task_id": task_id, "status": "error", "chunks": 0, "detail": "Contenido vacío"}

    kwargs: dict = dict(
        source=source,
        file_type=file_type,
        full_text=full_text,
        blocks=blocks,
        model=synth_model,
        provider=synth_provider,
        search_space_id=str(search_space_id),
    )
    if ingest_metadata:
        kwargs["ingest_metadata"] = ingest_metadata

    result = DocumentSynthesizer().synthesize(**kwargs)
    new_md = result.get("md_content", "") if isinstance(result, dict) else str(result)
    if not new_md.strip():
        _publish(r, channel, "synthesis", "error", detail="Sintetizador devolvió contenido vacío")
        return {"task_id": task_id, "status": "error", "chunks": 0, "detail": "Síntesis vacía"}

    BrainWriter().write(source, new_md)
    _publish(r, channel, "synthesis", "ok")
    log.info("[brain_ingest] channel=%s synthesis OK chars=%d", channel, len(new_md))

    # ── FASE 3: Indexación Qdrant ─────────────────────────────────────────────
    _publish(r, channel, "indexing", "running")
    route_results = IngestRouter(QdrantManager.get_instance().client).route(
        md_content=new_md,
        blocks=blocks,
        source=source,
        search_space_id=str(search_space_id),
    )
    total_chunks = sum(
        v.get("chunks_created", 0)
        for v in (route_results or {}).values()
        if isinstance(v, dict)
    )

    _register_redis_timestamps(r, search_space_id, source)
    _publish(r, channel, "indexing", "ok", chunks=total_chunks)
    log.info("[brain_ingest] channel=%s DONE source='%s' chunks=%d", channel, source, total_chunks)

    return {"task_id": task_id, "status": "ok", "chunks": total_chunks}


# ── Tarea URL ─────────────────────────────────────────────────────────────────

@celery_app.task(name="brain_ingest_url", bind=True)
def brain_ingest_url_task(
    self,
    task_id: str,
    url: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
) -> dict:
    """Ingesta de URL: extracción → síntesis LLM → indexación Qdrant."""
    r = _get_redis()
    channel = f"{_CHANNEL_PREFIX}{task_id}"
    final: dict = {"task_id": task_id, "status": "error", "chunks": 0}

    try:
        _publish(r, channel, "extraction", "running")
        from app.brain.extractors.web import WebExtractor
        blocks = WebExtractor().extract_url(url)
        if not blocks:
            _publish(r, channel, "extraction", "error", detail="Sin contenido extraíble de la URL")
            final["detail"] = "Sin contenido extraíble"
            return final
        _publish(r, channel, "extraction", "ok", chunks=len(blocks))
        log.info("[brain_ingest_url] task=%s extraction OK blocks=%d", task_id, len(blocks))

        final = _synthesize_and_index(
            r=r, channel=channel, task_id=task_id,
            blocks=blocks, source=url, file_type="html",
            search_space_id=search_space_id,
            model=model, provider=provider,
        )
        return final

    except Exception as exc:
        log.error("[brain_ingest_url] task=%s ERROR: %s", task_id, exc, exc_info=True)
        _publish(r, channel, "indexing", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    finally:
        _finish(r, channel, final)


# ── Tarea fichero ─────────────────────────────────────────────────────────────

@celery_app.task(name="brain_ingest_file", bind=True)
def brain_ingest_file_task(
    self,
    task_id: str,
    filename: str,
    content_b64: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
) -> dict:
    """
    Ingesta de fichero subido (bytes en base64).
    Escribe a fichero temporal, extrae, sintetiza e indexa.
    """
    import pathlib
    import tempfile

    r = _get_redis()
    channel = f"{_CHANNEL_PREFIX}{task_id}"
    final: dict = {"task_id": task_id, "status": "error", "chunks": 0}
    tmp_path = None

    try:
        _publish(r, channel, "extraction", "running")

        content = base64.b64decode(content_b64)
        suffix  = pathlib.Path(filename).suffix.lower() or ".bin"
        file_type = suffix.lstrip(".")

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        from app.brain.extractors.factory import ExtractorFactory
        blocks = ExtractorFactory.extract(tmp_path)
        if not blocks:
            _publish(r, channel, "extraction", "error", detail="Sin contenido extraíble del fichero")
            final["detail"] = "Sin contenido extraíble"
            return final
        _publish(r, channel, "extraction", "ok", chunks=len(blocks))
        log.info("[brain_ingest_file] task=%s extraction OK blocks=%d file=%s", task_id, len(blocks), filename)

        final = _synthesize_and_index(
            r=r, channel=channel, task_id=task_id,
            blocks=blocks, source=filename, file_type=file_type,
            search_space_id=search_space_id,
            model=model, provider=provider,
            ingest_metadata={"ingest_origin": "file_upload", "ingest_path": filename},
        )
        return final

    except Exception as exc:
        log.error("[brain_ingest_file] task=%s ERROR: %s", task_id, exc, exc_info=True)
        _publish(r, channel, "indexing", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        _finish(r, channel, final)


# ── Tarea ruta local ──────────────────────────────────────────────────────────

@celery_app.task(name="brain_ingest_path", bind=True)
def brain_ingest_path_task(
    self,
    task_id: str,
    local_path: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
) -> dict:
    """Ingesta desde ruta local del servidor."""
    import pathlib

    r = _get_redis()
    channel = f"{_CHANNEL_PREFIX}{task_id}"
    final: dict = {"task_id": task_id, "status": "error", "chunks": 0}

    try:
        _publish(r, channel, "extraction", "running")

        path_obj  = pathlib.Path(local_path)
        file_type = path_obj.suffix.lower().lstrip(".") or "bin"

        from app.brain.extractors.factory import ExtractorFactory
        blocks = ExtractorFactory.extract(local_path)
        if not blocks:
            _publish(r, channel, "extraction", "error", detail="Sin contenido extraíble del fichero")
            final["detail"] = "Sin contenido extraíble"
            return final
        _publish(r, channel, "extraction", "ok", chunks=len(blocks))
        log.info("[brain_ingest_path] task=%s extraction OK blocks=%d path=%s", task_id, len(blocks), local_path)

        final = _synthesize_and_index(
            r=r, channel=channel, task_id=task_id,
            blocks=blocks, source=local_path, file_type=file_type,
            search_space_id=search_space_id,
            model=model, provider=provider,
            ingest_metadata={"ingest_origin": "local", "ingest_path": local_path},
        )
        return final

    except Exception as exc:
        log.error("[brain_ingest_path] task=%s ERROR: %s", task_id, exc, exc_info=True)
        _publish(r, channel, "indexing", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    finally:
        _finish(r, channel, final)


# ── Tarea conector ────────────────────────────────────────────────────────────

@celery_app.task(name="brain_ingest_connector", bind=True)
def brain_ingest_connector_task(
    self,
    task_id: str,
    connector_type: str,
    connector_id: int,
    connector_config: dict,
    item_id: str,
    filename: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
) -> dict:
    """
    Ingesta desde conector SurfSense upstream (storage/record/chat).
    Solo familia 'storage' tiene descarga automática implementada.
    """
    import asyncio

    r = _get_redis()
    channel = f"{_CHANNEL_PREFIX}{task_id}"
    final: dict = {"task_id": task_id, "status": "error", "chunks": 0}

    try:
        from app.brain.connectors.surfsense_adapter import SurfSenseStorageAdapter, get_family

        family = get_family(connector_type)

        _publish(r, channel, "extraction", "running")

        if family == "storage":
            class _FakeConnector:
                def __init__(self, cid, ctype, cfg):
                    self.id = cid
                    self.connector_type = ctype
                    self.config = cfg

            fake_conn = _FakeConnector(connector_id, connector_type, connector_config)
            adapter   = SurfSenseStorageAdapter()

            loop    = asyncio.new_event_loop()
            adapted = loop.run_until_complete(
                adapter.fetch_item(connector_record=fake_conn, item_id=item_id,
                                   filename=filename, db=None)
            )
            loop.close()

        else:
            # Familia 'record' (Jira, Confluence) y 'chat' usan rutas separadas:
            # - Jira/Confluence → POST /ingest/native/{connector_type} → brain_ingest_native_task
            # - Chat → no implementado aún
            _publish(r, channel, "extraction", "error",
                     detail=f"Familia '{family}' no soportada en este endpoint. "
                            f"Usa /ingest/native/ para Jira y Confluence.")
            final["detail"] = f"Familia '{family}' no soportada en brain_ingest_connector"
            return final

        blocks = adapted.blocks
        if not blocks:
            _publish(r, channel, "extraction", "error", detail="Sin contenido extraíble del conector")
            final["detail"] = "Sin contenido extraíble"
            return final
        _publish(r, channel, "extraction", "ok", chunks=len(blocks))
        log.info("[brain_ingest_connector] task=%s extraction OK blocks=%d family=%s",
                 task_id, len(blocks), family)

        final = _synthesize_and_index(
            r=r, channel=channel, task_id=task_id,
            blocks=blocks, source=adapted.source_name, file_type=adapted.file_type,
            search_space_id=search_space_id,
            model=model, provider=provider,
            ingest_metadata={
                "ingest_origin": "connector",
                "connector_type": connector_type,
                "item_id": item_id,
                **adapted.metadata,
            },
        )
        return final

    except PermissionError as exc:
        log.error("[brain_ingest_connector] task=%s AUTH ERROR: %s", task_id, exc)
        _publish(r, channel, "extraction", "error",
                 detail=f"Token expirado: {exc}. Re-autentícalo en Configuración → Conectores.")
        final["detail"] = str(exc)
        return final

    except Exception as exc:
        log.error("[brain_ingest_connector] task=%s ERROR: %s", task_id, exc, exc_info=True)
        _publish(r, channel, "indexing", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    finally:
        _finish(r, channel, final)


# ── Tarea ingesta nativa Jira / Confluence ────────────────────────────────────

@celery_app.task(name="brain_ingest_native", bind=True)
def brain_ingest_native_task(
    self,
    task_id: str,
    connector_type: str,
    item_id: str,
    filename: str,
    search_space_id: int,
    model: str | None,
    provider: str | None,
) -> dict:
    """
    Ingesta un ítem de Jira o Confluence al Brain usando credenciales nativas.

    Usa JiraConnector / ConfluenceConnector que leen JIRA_URL/JIRA_USER/JIRA_API_TOKEN
    (o CONFLUENCE_*) directamente de env vars — sin token MCP OAuth.

    Esta tarea es independiente de brain_ingest_connector_task, que gestiona
    los conectores storage/MCP del usuario (OneDrive, Drive, Dropbox…).
    """
    r = _get_redis()
    channel = f"{_CHANNEL_PREFIX}{task_id}"
    final: dict = {"task_id": task_id, "status": "error", "chunks": 0}

    try:
        ctype = connector_type.upper()
        _publish(r, channel, "extraction", "running")

        if ctype == "JIRA_CONNECTOR":
            from app.brain.connectors.jira_connector import JiraConnector
            from app.brain.extractors.factory import ExtractorFactory
            import tempfile
            from pathlib import Path

            connector = JiraConnector()
            fetched = connector.fetch_item(item_id)
            virtual_ext = fetched.virtual_ext

            with tempfile.NamedTemporaryFile(
                suffix=virtual_ext, delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(fetched.text)
                tmp_path = tmp.name

            try:
                blocks = ExtractorFactory.extract(tmp_path)
            finally:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

            source_name = fetched.slug
            file_type = virtual_ext.lstrip(".")
            extra_meta = fetched.metadata

        elif ctype == "CONFLUENCE_CONNECTOR":
            from app.brain.connectors.confluence_connector import ConfluenceConnector
            from app.brain.extractors.factory import ExtractorFactory
            import tempfile
            from pathlib import Path

            connector = ConfluenceConnector()
            fetched = connector.fetch_item(item_id)
            virtual_ext = fetched.virtual_ext

            with tempfile.NamedTemporaryFile(
                suffix=virtual_ext, delete=False, mode="w", encoding="utf-8"
            ) as tmp:
                tmp.write(fetched.text)
                tmp_path = tmp.name

            try:
                blocks = ExtractorFactory.extract(tmp_path)
            finally:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

            source_name = fetched.slug
            file_type = virtual_ext.lstrip(".")
            extra_meta = fetched.metadata

        else:
            _publish(r, channel, "extraction", "error",
                     detail=f"Conector nativo '{ctype}' no implementado.")
            final["detail"] = f"Conector nativo '{ctype}' no soportado"
            return final

        if not blocks:
            _publish(r, channel, "extraction", "error",
                     detail="Sin contenido extraíble del ítem")
            final["detail"] = "Sin contenido extraíble"
            return final

        _publish(r, channel, "extraction", "ok", chunks=len(blocks))
        log.info("[brain_ingest_native] task=%s connector=%s item_id=%s blocks=%d",
                 task_id, ctype, item_id, len(blocks))

        final = _synthesize_and_index(
            r=r, channel=channel, task_id=task_id,
            blocks=blocks, source=source_name, file_type=file_type,
            search_space_id=search_space_id,
            model=model, provider=provider,
            ingest_metadata={
                "ingest_origin": "native_connector",
                "connector_type": ctype,
                "item_id": item_id,
                **extra_meta,
            },
        )
        return final

    except ValueError as exc:
        log.error("[brain_ingest_native] task=%s NOT FOUND: %s", task_id, exc)
        _publish(r, channel, "extraction", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    except ConnectionError as exc:
        log.error("[brain_ingest_native] task=%s CONNECTION ERROR: %s", task_id, exc)
        _publish(r, channel, "extraction", "error",
                 detail=f"Error de conexión con la API: {exc}")
        final["detail"] = str(exc)
        return final

    except Exception as exc:
        log.error("[brain_ingest_native] task=%s ERROR: %s", task_id, exc, exc_info=True)
        _publish(r, channel, "indexing", "error", detail=str(exc))
        final["detail"] = str(exc)
        return final

    finally:
        _finish(r, channel, final)
