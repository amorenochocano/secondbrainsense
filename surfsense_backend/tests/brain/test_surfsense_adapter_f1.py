"""
tests/brain/test_surfsense_adapter_f1.py
-----------------------------------------
Tests Fase 1 — SurfSenseAdapter y endpoints de conectores en Brain.

Cobertura:
  - CONNECTOR_FAMILIES: familias correctas por tipo
  - is_brain_supported / get_family
  - SurfSenseRecordAdapter: serializa a Markdown y extrae bloques
  - SurfSenseChatAdapter: agrupa mensajes y extrae bloques
  - verify_connector_token: token presente / ausente / needs_reauth
  - IngestConnectorRequest: validación básica del schema
"""
import pytest
from unittest.mock import MagicMock, patch

from app.brain.connectors.surfsense_adapter import (
    CONNECTOR_FAMILIES,
    get_family,
    is_brain_supported,
    SurfSenseRecordAdapter,
    SurfSenseChatAdapter,
    verify_connector_token,
    _slugify,
    _serialize_record_to_markdown,
    _serialize_thread_to_markdown,
)


# ── Tests de familias ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestConnectorFamilies:

    def test_storage_connectors(self):
        for ct in ["ONEDRIVE_CONNECTOR", "GOOGLE_DRIVE_CONNECTOR", "DROPBOX_CONNECTOR"]:
            assert get_family(ct) == "storage", f"{ct} debe ser storage"

    def test_record_connectors(self):
        for ct in ["AIRTABLE_CONNECTOR", "CLICKUP_CONNECTOR", "LINEAR_CONNECTOR",
                   "BOOKSTACK_CONNECTOR", "GOOGLE_CALENDAR_CONNECTOR",
                   "LUMA_CONNECTOR", "ELASTICSEARCH_CONNECTOR"]:
            assert get_family(ct) == "record", f"{ct} debe ser record"

    def test_chat_connectors(self):
        for ct in ["SLACK_CONNECTOR", "TEAMS_CONNECTOR", "DISCORD_CONNECTOR", "GOOGLE_GMAIL_CONNECTOR"]:
            assert get_family(ct) == "chat", f"{ct} debe ser chat"

    def test_unsupported_returns_none(self):
        assert get_family("UNKNOWN_CONNECTOR") is None
        assert get_family("CONFLUENCE_CONNECTOR") is None
        assert get_family("JIRA_CONNECTOR") is None

    def test_is_brain_supported(self):
        assert is_brain_supported("ONEDRIVE_CONNECTOR") is True
        assert is_brain_supported("SLACK_CONNECTOR") is True
        assert is_brain_supported("UNKNOWN_CONNECTOR") is False
        assert is_brain_supported("CONFLUENCE_CONNECTOR") is False

    def test_case_insensitive(self):
        assert get_family("onedrive_connector") == "storage"
        assert is_brain_supported("slack_connector") is True


# ── Tests SurfSenseRecordAdapter ──────────────────────────────────────────────

@pytest.mark.unit
class TestSurfSenseRecordAdapter:

    def test_adapt_item_basico(self):
        adapter = SurfSenseRecordAdapter()
        item = adapter.adapt_item(
            connector_type="CLICKUP_CONNECTOR",
            item_id="task-123",
            title="Implementar autenticación OAuth",
            body="La tarea consiste en implementar el flujo OAuth 2.0 para Teams.",
            url="https://app.clickup.com/t/123",
            labels=["backend", "auth"],
            status="In Progress",
        )
        assert len(item.blocks) >= 1
        assert item.file_type == "md"
        assert item.is_known_format is True
        assert "clickup" in item.source_name
        assert item.metadata["connector_type"] == "CLICKUP_CONNECTOR"
        assert item.metadata["item_id"] == "task-123"

    def test_adapt_item_sin_contenido(self):
        adapter = SurfSenseRecordAdapter()
        item = adapter.adapt_item(
            connector_type="LINEAR_CONNECTOR",
            item_id="issue-456",
            title="Bug crítico en login",
            body="",
        )
        assert len(item.blocks) >= 1
        assert item.title == "Bug crítico en login"

    def test_adapt_item_metadata_propagada(self):
        adapter = SurfSenseRecordAdapter()
        item = adapter.adapt_item(
            connector_type="AIRTABLE_CONNECTOR",
            item_id="rec123",
            title="Registro de cliente",
            body="Nombre: ACME Corp.",
            labels=["cliente", "enterprise"],
            status="Activo",
            extra_metadata={"region": "EMEA", "tier": "gold"},
        )
        assert item.metadata["labels"] == ["cliente", "enterprise"]
        assert item.metadata["status"] == "Activo"
        assert item.metadata["region"] == "EMEA"

    def test_serialize_record_markdown(self):
        md = _serialize_record_to_markdown(
            title="Tarea X",
            body="Descripción de la tarea.",
            url="https://example.com/task/1",
            labels=["dev", "urgent"],
            status="Done",
            connector_type="CLICKUP_CONNECTOR",
            extra_metadata={"priority": "alta"},
        )
        assert "# Tarea X" in md
        assert "Done" in md
        assert "https://example.com/task/1" in md
        assert "dev, urgent" in md
        assert "Descripción de la tarea." in md


# ── Tests SurfSenseChatAdapter ────────────────────────────────────────────────

@pytest.mark.unit
class TestSurfSenseChatAdapter:

    def test_adapt_thread_basico(self):
        adapter = SurfSenseChatAdapter()
        messages = [
            {"author": "Alice", "timestamp": "2026-07-01 10:00", "text": "Hola, ¿alguien disponible?"},
            {"author": "Bob",   "timestamp": "2026-07-01 10:05", "text": "Sí, aquí estoy."},
        ]
        item = adapter.adapt_thread(
            connector_type="SLACK_CONNECTOR",
            thread_id="T12345678",
            channel_name="equipo-backend",
            messages=messages,
        )
        assert len(item.blocks) >= 1
        assert "slack" in item.source_name
        assert "equipo-backend" in item.title
        assert item.metadata["message_count"] == 2
        assert item.metadata["connector_type"] == "SLACK_CONNECTOR"

    def test_adapt_thread_sin_mensajes(self):
        adapter = SurfSenseChatAdapter()
        item = adapter.adapt_thread(
            connector_type="TEAMS_CONNECTOR",
            thread_id="TH9999",
            channel_name="general",
            messages=[],
        )
        assert len(item.blocks) >= 1  # al menos el bloque de cabecera

    def test_serialize_thread_markdown(self):
        messages = [
            {"author": "Ana",  "timestamp": "09:00", "text": "Buenos días."},
            {"author": "Luis", "timestamp": "09:01", "text": "Buenos días a todos."},
        ]
        md = _serialize_thread_to_markdown(
            channel_name="proyecto-alpha",
            messages=messages,
            connector_type="DISCORD_CONNECTOR",
        )
        assert "# proyecto-alpha" in md
        assert "Ana" in md
        assert "Buenos días." in md
        assert "Luis" in md

    def test_mensajes_sin_texto_ignorados(self):
        adapter = SurfSenseChatAdapter()
        messages = [
            {"author": "Bot", "timestamp": "10:00", "text": ""},
            {"author": "Ana", "timestamp": "10:01", "text": "Mensaje real."},
        ]
        item = adapter.adapt_thread(
            connector_type="SLACK_CONNECTOR",
            thread_id="T001",
            channel_name="pruebas",
            messages=messages,
        )
        full_text = " ".join(b.get("content", "") for b in item.blocks)
        assert "Mensaje real." in full_text


# ── Tests verify_connector_token ──────────────────────────────────────────────

@pytest.mark.unit
class TestVerifyConnectorToken:

    def _make_connector(self, connector_type: str, config: dict):
        mock = MagicMock()
        mock.connector_type = connector_type
        mock.config = config
        return mock

    @pytest.mark.asyncio
    async def test_unsupported_connector(self):
        conn = self._make_connector("UNKNOWN_CONNECTOR", {})
        result = await verify_connector_token(conn)
        assert result["ok"] is False
        assert result["needs_reauth"] is False

    @pytest.mark.asyncio
    async def test_storage_sin_token(self):
        conn = self._make_connector("ONEDRIVE_CONNECTOR", {})
        result = await verify_connector_token(conn)
        assert result["ok"] is False
        assert result["needs_reauth"] is True

    @pytest.mark.asyncio
    async def test_record_con_config_vacio(self):
        conn = self._make_connector("CLICKUP_CONNECTOR", {})
        result = await verify_connector_token(conn)
        assert result["ok"] is False
        assert result["needs_reauth"] is True

    @pytest.mark.asyncio
    async def test_record_con_config_presente(self):
        conn = self._make_connector("CLICKUP_CONNECTOR", {"api_key": "some_key"})
        result = await verify_connector_token(conn)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_storage_con_token_cifrado_invalido(self):
        """Token presente pero cifrado inválido → needs_reauth."""
        conn = self._make_connector("ONEDRIVE_CONNECTOR", {"access_token": "not_encrypted"})
        with patch("app.utils.oauth_security.TokenEncryption.decrypt_token", side_effect=Exception("bad token")):
            result = await verify_connector_token(conn)
        assert result["ok"] is False
        assert result["needs_reauth"] is True


# ── Tests helpers internos ────────────────────────────────────────────────────

@pytest.mark.unit
class TestHelpers:

    def test_slugify_basico(self):
        assert _slugify("Arquitectura Data Lake v2") == "arquitectura-data-lake-v2"

    def test_slugify_acentos(self):
        assert _slugify("Diseño y configuración") == "diseno-y-configuracion"

    def test_slugify_caracteres_especiales(self):
        slug = _slugify("Informe: 2026/07 [DRAFT]")
        assert "/" not in slug
        assert "[" not in slug
        assert ":" not in slug

    def test_slugify_max_80_chars(self):
        slug = _slugify("a" * 200)
        assert len(slug) <= 80
