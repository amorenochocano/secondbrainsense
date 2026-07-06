"""
brain/connectors/surfsense_adapter.py
======================================
Adaptadores que conectan los conectores upstream de SurfSense con el
pipeline de ingesta del Brain.

PRINCIPIO: Conector ≠ Extractor.
  - El conector upstream (SearchSourceConnector) = transporte. Descarga bytes.
  - El adaptador (este módulo) = puente. Convierte lo descargado en FetchedItem.
  - ExtractorFactory = procesamiento por formato. Lee bytes → bloques.

FAMILIAS DE CONECTORES:
  Storage  (OneDrive, Google Drive, Dropbox):
    → descarga bytes del fichero → tmp → ExtractorFactory.extract(tmp)
    → extensión real del fichero → extractor específico o FallbackExtractor
  Record   (Airtable, ClickUp, Linear, BookStack, Calendar, Luma, Elasticsearch):
    → serializa {title, body, url, labels, status} → Markdown → MarkdownExtractor
  Chat/Mensajes (Slack, Teams, Discord, Gmail):
    → agrupa mensajes por hilo/canal → Markdown con timestamps → MarkdownExtractor

CÓMO AÑADIR SOPORTE PARA UN NUEVO CONECTOR:
  1. Determina la familia (storage/record/chat).
  2. Añade el connector_type a CONNECTOR_FAMILIES.
  3. Si es storage: implementa _download_bytes() en la clase de la familia.
     Si es record/chat: el adaptador genérico ya funciona, solo necesitas
     que el caller pase los datos en el formato esperado (ver fetch_item_data).
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── Familias de conectores ────────────────────────────────────────────────────

CONNECTOR_FAMILIES: dict[str, str] = {
    # Storage — descargan ficheros binarios con extensión real
    "ONEDRIVE_CONNECTOR":        "storage",
    "GOOGLE_DRIVE_CONNECTOR":    "storage",
    "DROPBOX_CONNECTOR":         "storage",
    # Record — entidades estructuradas serializadas a Markdown
    "AIRTABLE_CONNECTOR":        "record",
    "CLICKUP_CONNECTOR":         "record",
    "LINEAR_CONNECTOR":          "record",
    "BOOKSTACK_CONNECTOR":       "record",
    "GOOGLE_CALENDAR_CONNECTOR": "record",
    "LUMA_CONNECTOR":            "record",
    "ELASTICSEARCH_CONNECTOR":   "record",
    # Chat/Mensajes — hilos y canales serializados a Markdown
    "SLACK_CONNECTOR":           "chat",
    "TEAMS_CONNECTOR":           "chat",
    "DISCORD_CONNECTOR":         "chat",
    "GOOGLE_GMAIL_CONNECTOR":    "chat",
}


def get_family(connector_type: str) -> str | None:
    """Devuelve la familia del conector o None si no está soportado."""
    return CONNECTOR_FAMILIES.get(connector_type.upper())


def is_brain_supported(connector_type: str) -> bool:
    """True si el conector tiene soporte en el pipeline Brain."""
    return connector_type.upper() in CONNECTOR_FAMILIES


# ── Contrato de salida del adaptador ─────────────────────────────────────────

@dataclass
class AdaptedItem:
    """
    Resultado del adaptador listo para entrar en el pipeline Brain.

    blocks:      lista de bloques {content, content_type, page, ...} ya extraídos.
    source_name: nombre del ítem (slug para el pasaporte, filename para el tmp).
    title:       título legible del ítem.
    file_type:   extensión sin punto para DocumentSynthesizer (ej: "docx", "md").
    metadata:    metadatos adicionales para el frontmatter.
    is_known_format: True si ExtractorFactory tiene un extractor específico (no fallback).
    """
    blocks: list[dict]
    source_name: str
    title: str
    file_type: str
    metadata: dict = field(default_factory=dict)
    is_known_format: bool = True


# ── Adaptador Storage ─────────────────────────────────────────────────────────

class SurfSenseStorageAdapter:
    """
    Adaptador para conectores tipo storage (OneDrive, Google Drive, Dropbox).

    El conector upstream descarga bytes del fichero. El adaptador guarda esos
    bytes en un fichero temporal y llama a ExtractorFactory.extract() con la
    extensión real del fichero. Cero extractores nuevos necesarios.

    Uso:
        adapter = SurfSenseStorageAdapter()
        item = await adapter.fetch_item(
            connector_record=connector,   # SearchSourceConnector ORM
            item_id="1234",               # ID del fichero en el servicio
            filename="informe.docx",      # nombre original con extensión
            db=session,
        )
    """

    async def fetch_item(
        self,
        connector_record: Any,
        item_id: str,
        filename: str,
        db: Any,
    ) -> AdaptedItem:
        """
        Descarga un fichero del conector storage e ingesta con el extractor adecuado.

        Args:
            connector_record: Instancia SQLAlchemy de SearchSourceConnector.
            item_id:          ID del fichero en el servicio externo.
            filename:         Nombre original del fichero con extensión.
            db:               AsyncSession de SQLAlchemy (para refresh de token).

        Returns:
            AdaptedItem con bloques extraídos listos para el pipeline Brain.
        """
        from app.brain.extractors.factory import ExtractorFactory

        connector_type = str(connector_record.connector_type).upper()
        log.info(
            "[storage_adapter] fetch_item connector=%s item_id=%s filename='%s'",
            connector_type, item_id, filename,
        )

        file_bytes = await self._download_bytes(connector_record, item_id, filename, db)
        suffix = Path(filename).suffix.lower() or ".bin"
        is_known = ExtractorFactory.is_known(suffix)

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            blocks = await _run_in_thread(ExtractorFactory.extract, tmp_path)
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        log.info(
            "[storage_adapter] extracted connector=%s filename='%s' blocks=%d is_known=%s",
            connector_type, filename, len(blocks), is_known,
        )

        slug = _slugify(f"{connector_type.lower().replace('_connector', '')}-{Path(filename).stem}")

        return AdaptedItem(
            blocks=blocks,
            source_name=f"{slug}{suffix}",
            title=filename,
            file_type=suffix.lstrip("."),
            metadata={
                "ingest_origin": "connector",
                "connector_type": connector_type,
                "item_id": item_id,
                "original_filename": filename,
            },
            is_known_format=is_known,
        )

    async def _download_bytes(
        self,
        connector_record: Any,
        item_id: str,
        filename: str,
        db: Any,
    ) -> bytes:
        """
        Descarga los bytes del fichero desde el conector upstream.

        Delega al método específico de cada tipo de conector.
        Lanza ConnectorAuthError si el token está expirado y no puede refrescarse.
        """
        connector_type = str(connector_record.connector_type).upper()

        if connector_type == "ONEDRIVE_CONNECTOR":
            return await self._download_onedrive(connector_record, item_id, db)
        elif connector_type == "GOOGLE_DRIVE_CONNECTOR":
            return await self._download_gdrive(connector_record, item_id, db)
        elif connector_type == "DROPBOX_CONNECTOR":
            return await self._download_dropbox(connector_record, item_id, db)
        else:
            raise NotImplementedError(
                f"Storage download no implementado para {connector_type}. "
                f"Implementar _download_{connector_type.lower().replace('_connector', '')}()."
            )

    async def _download_onedrive(self, connector_record: Any, item_id: str, db: Any) -> bytes:
        """Descarga un fichero de OneDrive usando la Microsoft Graph API."""
        import httpx
        from app.utils.oauth_security import TokenEncryption
        from app.config import config as app_config

        config_data = dict(connector_record.config or {})
        encryption = TokenEncryption(app_config.SECRET_KEY)

        access_token = config_data.get("access_token", "")
        if access_token:
            try:
                access_token = encryption.decrypt_token(access_token)
            except Exception:
                pass

        if not access_token:
            raise ValueError(
                f"OneDrive: token de acceso no encontrado para conector {connector_record.id}. "
                "El usuario debe re-autenticar el conector."
            )

        graph_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/content"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                graph_url,
                headers={"Authorization": f"Bearer {access_token}"},
                follow_redirects=True,
            )

        if resp.status_code == 401:
            raise PermissionError(
                f"OneDrive token expirado para conector {connector_record.id}. "
                "El usuario debe re-autenticar."
            )
        resp.raise_for_status()
        return resp.content

    async def _download_gdrive(self, connector_record: Any, item_id: str, db: Any) -> bytes:
        """Descarga un fichero de Google Drive usando la API de Google."""
        import httpx
        from app.utils.oauth_security import TokenEncryption
        from app.config import config as app_config

        config_data = dict(connector_record.config or {})
        encryption = TokenEncryption(app_config.SECRET_KEY)

        access_token = config_data.get("access_token", "")
        if access_token:
            try:
                access_token = encryption.decrypt_token(access_token)
            except Exception:
                pass

        if not access_token:
            raise ValueError(
                f"Google Drive: token de acceso no encontrado para conector {connector_record.id}."
            )

        download_url = f"https://www.googleapis.com/drive/v3/files/{item_id}?alt=media"
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                download_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            raise PermissionError(
                f"Google Drive token expirado para conector {connector_record.id}."
            )
        resp.raise_for_status()
        return resp.content

    async def _download_dropbox(self, connector_record: Any, item_id: str, db: Any) -> bytes:
        """Descarga un fichero de Dropbox usando la API v2."""
        import httpx
        from app.utils.oauth_security import TokenEncryption
        from app.config import config as app_config

        config_data = dict(connector_record.config or {})
        encryption = TokenEncryption(app_config.SECRET_KEY)

        access_token = config_data.get("access_token", "")
        if access_token:
            try:
                access_token = encryption.decrypt_token(access_token)
            except Exception:
                pass

        if not access_token:
            raise ValueError(
                f"Dropbox: token de acceso no encontrado para conector {connector_record.id}."
            )

        # Dropbox download: item_id es el path del fichero
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://content.dropboxapi.com/2/files/download",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Dropbox-API-Arg": f'{{"path": "{item_id}"}}',
                },
            )

        if resp.status_code == 401:
            raise PermissionError(
                f"Dropbox token expirado para conector {connector_record.id}."
            )
        resp.raise_for_status()
        return resp.content


# ── Adaptador Record ──────────────────────────────────────────────────────────

class SurfSenseRecordAdapter:
    """
    Adaptador para conectores tipo record (Airtable, ClickUp, Linear, etc.).

    Serializa entidades estructuradas {title, body, url, labels, status}
    a Markdown y las procesa con MarkdownExtractor. Cero extractores nuevos.

    El caller es responsable de obtener los datos de la API del conector.
    Este adaptador solo serializa y extrae.
    """

    def adapt_item(
        self,
        connector_type: str,
        item_id: str,
        title: str,
        body: str,
        url: str = "",
        labels: list[str] | None = None,
        status: str = "",
        extra_metadata: dict | None = None,
    ) -> AdaptedItem:
        """
        Serializa un ítem de conector record a Markdown y extrae bloques.

        Args:
            connector_type: tipo del conector (ej: "CLICKUP_CONNECTOR").
            item_id:        ID del ítem en la API externa.
            title:          título del ítem (tarea, issue, evento...).
            body:           contenido principal en texto plano o Markdown.
            url:            URL del ítem en la plataforma.
            labels:         etiquetas/tags del ítem.
            status:         estado del ítem (ej: "Done", "In Progress").
            extra_metadata: campos adicionales del ítem.

        Returns:
            AdaptedItem con bloques Markdown extraídos.
        """
        from app.brain.extractors.markdown import MarkdownExtractor

        labels = labels or []
        extra_metadata = extra_metadata or {}
        connector_prefix = connector_type.lower().replace("_connector", "")

        md = _serialize_record_to_markdown(
            title=title,
            body=body,
            url=url,
            labels=labels,
            status=status,
            connector_type=connector_type,
            extra_metadata=extra_metadata,
        )

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(md)
            tmp_path = tmp.name

        try:
            blocks = MarkdownExtractor().extract(tmp_path)
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        slug = _slugify(f"{connector_prefix}-{title}")

        log.info(
            "[record_adapter] adapt_item connector=%s item_id=%s blocks=%d",
            connector_type, item_id, len(blocks),
        )

        return AdaptedItem(
            blocks=blocks,
            source_name=f"{slug}.md",
            title=title,
            file_type="md",
            metadata={
                "ingest_origin": "connector",
                "connector_type": connector_type,
                "item_id": item_id,
                "url": url,
                "labels": labels,
                "status": status,
                **extra_metadata,
            },
            is_known_format=True,
        )


def _serialize_record_to_markdown(
    title: str,
    body: str,
    url: str,
    labels: list[str],
    status: str,
    connector_type: str,
    extra_metadata: dict,
) -> str:
    """Serializa un ítem record al formato Markdown canónico."""
    lines = [f"# {title}", ""]

    if status:
        lines.append(f"**Estado**: {status}")
    if url:
        lines.append(f"**URL**: {url}")
    if labels:
        lines.append(f"**Etiquetas**: {', '.join(labels)}")
    if extra_metadata:
        for k, v in extra_metadata.items():
            if v is not None and k not in ("id", "item_id"):
                lines.append(f"**{k}**: {v}")

    lines.extend(["", "---", "", body or "(sin contenido)"])
    return "\n".join(lines)


# ── Adaptador Chat/Mensajes ───────────────────────────────────────────────────

class SurfSenseChatAdapter:
    """
    Adaptador para conectores tipo chat (Slack, Teams, Discord, Gmail).

    Agrupa mensajes por hilo/canal como Markdown con timestamps y los procesa
    con MarkdownExtractor.
    """

    def adapt_thread(
        self,
        connector_type: str,
        thread_id: str,
        channel_name: str,
        messages: list[dict],
        extra_metadata: dict | None = None,
    ) -> AdaptedItem:
        """
        Serializa un hilo de mensajes a Markdown y extrae bloques.

        Args:
            connector_type: tipo del conector (ej: "SLACK_CONNECTOR").
            thread_id:      ID del hilo o canal.
            channel_name:   nombre legible del canal/hilo.
            messages:       lista de {author, timestamp, text}.
            extra_metadata: campos adicionales.

        Returns:
            AdaptedItem con bloques Markdown extraídos.
        """
        from app.brain.extractors.markdown import MarkdownExtractor

        extra_metadata = extra_metadata or {}
        connector_prefix = connector_type.lower().replace("_connector", "")

        md = _serialize_thread_to_markdown(
            channel_name=channel_name,
            messages=messages,
            connector_type=connector_type,
        )

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(md)
            tmp_path = tmp.name

        try:
            blocks = MarkdownExtractor().extract(tmp_path)
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        slug = _slugify(f"{connector_prefix}-{channel_name}-{thread_id[:8]}")

        log.info(
            "[chat_adapter] adapt_thread connector=%s thread_id=%s messages=%d blocks=%d",
            connector_type, thread_id, len(messages), len(blocks),
        )

        return AdaptedItem(
            blocks=blocks,
            source_name=f"{slug}.md",
            title=f"{channel_name} — hilo {thread_id[:8]}",
            file_type="md",
            metadata={
                "ingest_origin": "connector",
                "connector_type": connector_type,
                "thread_id": thread_id,
                "channel_name": channel_name,
                "message_count": len(messages),
                **extra_metadata,
            },
            is_known_format=True,
        )


def _serialize_thread_to_markdown(
    channel_name: str,
    messages: list[dict],
    connector_type: str,
) -> str:
    """Serializa un hilo de mensajes al formato Markdown canónico."""
    lines = [f"# {channel_name}", f"**Fuente**: {connector_type}", ""]

    for msg in messages:
        author = msg.get("author", "?")
        ts = msg.get("timestamp", "")
        text = msg.get("text", "").strip()
        if not text:
            continue
        lines.append(f"**{author}** — {ts}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


# ── Verificación de token ─────────────────────────────────────────────────────

async def verify_connector_token(connector_record: Any) -> dict:
    """
    Verifica que el conector tiene un token válido sin hacer una llamada completa.

    Returns:
        {"ok": bool, "detail": str, "needs_reauth": bool}
    """
    connector_type = str(connector_record.connector_type).upper()
    config_data = connector_record.config or {}

    family = get_family(connector_type)
    if not family:
        return {"ok": False, "detail": f"Conector {connector_type} no soportado en Brain.", "needs_reauth": False}

    if family == "storage":
        from app.utils.oauth_security import TokenEncryption
        from app.config import config as app_config
        enc = TokenEncryption(app_config.SECRET_KEY)
        raw_token = config_data.get("access_token", "")
        if not raw_token:
            return {"ok": False, "detail": "Token de acceso no encontrado.", "needs_reauth": True}
        try:
            enc.decrypt_token(raw_token)
            return {"ok": True, "detail": "Token presente.", "needs_reauth": False}
        except Exception:
            return {"ok": False, "detail": "Token cifrado inválido.", "needs_reauth": True}

    # Record y chat: verificación básica (el config tiene credenciales)
    if not config_data:
        return {"ok": False, "detail": "Config vacío.", "needs_reauth": True}

    return {"ok": True, "detail": "Config presente.", "needs_reauth": False}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Normaliza texto a slug válido como nombre de fichero."""
    import re
    text = text.lower().strip()
    for src, dst in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:80]


async def _run_in_thread(func, *args):
    """Ejecuta una función síncrona en el thread pool del event loop."""
    import asyncio
    return await asyncio.to_thread(func, *args)
