"""
brain/connectors/confluence_connector.py
=========================================
Conector para Confluence (Atlassian Cloud y Server).

AUTENTICACIÓN
-------------
Variables de entorno requeridas:
  CONFLUENCE_URL        URL base de la instancia (ej. https://empresa.atlassian.net)
  CONFLUENCE_USER       Email del usuario (cloud) o username (server)
  CONFLUENCE_API_TOKEN  API token de Atlassian (cloud) o contraseña (server)
  CONFLUENCE_AUTH_TYPE  "basic" (default) | "token" (PAT para server/DC)

OPERACIONES
-----------
  fetch_item(page_id)
      Descarga una página por su ID numérico.
      Convierte el cuerpo HTML a texto plano con estructura markdown.
      Serializa metadata + cuerpo al formato canónico .confluence.

  fetch_batch(space_key, params)
      Descarga todas las páginas de un space.
      Parámetros opcionales:
        limit (int, default 50):  páginas por request
        labels (str):             filtrar por label (ej. "architecture")
        updated_after (str):      ISO 8601 (ej. "2026-01-01T00:00:00Z")
        max_pages (int, default 200): máximo total a recuperar

FORMATO CANÓNICO .confluence
-----------------------------
El texto serializado tiene siempre este formato:

  ## Metadata
  space: TEC
  title: Nombre de la Página
  labels: architecture, data-pipeline
  author: Juan García
  last_modified: 2026-01-15T10:30:00.000Z
  url: https://empresa.atlassian.net/wiki/spaces/TEC/pages/123456789
  parent_page: Arquitectura > Decisiones Técnicas
  children_count: 3

  ## Contenido
  # Primera sección

  Texto del cuerpo convertido a texto plano.

  ```python
  def ejemplo():
      return "valor"
  ```

  Más texto.

El ConfluenceExtractor (api/extractors/confluence.py) lee exactamente este formato.
"""

from __future__ import annotations
import os
import re
import logging
from .base import BaseConnector, FetchedItem, BatchResult

log = logging.getLogger(__name__)


class ConfluenceConnector(BaseConnector):

    # ── Identidad ──────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return "confluence"

    @property
    def virtual_extension(self) -> str:
        return ".confluence"

    # ── Inicialización ─────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._base_url  = os.getenv("CONFLUENCE_URL", "").rstrip("/")
        self._user      = os.getenv("CONFLUENCE_USER", "")
        self._token     = os.getenv("CONFLUENCE_API_TOKEN", "")
        self._auth_type = os.getenv("CONFLUENCE_AUTH_TYPE", "basic").lower()
        self._session   = None  # lazy init en _get_session()

    def apply_credentials(self, credentials: dict) -> None:
        """Aplica credenciales de la UI sobreescribiendo las de env vars."""
        if "url" in credentials:       self._base_url  = credentials["url"].rstrip("/")
        if "user" in credentials:      self._user      = credentials["user"]
        if "token" in credentials:     self._token     = credentials["token"]
        if "auth_type" in credentials: self._auth_type = credentials["auth_type"]
        self._session = None  # forzar recreación de sesión

    def _get_session(self):
        """Crea la sesión HTTP con autenticación. Lazy para evitar import en módulo."""
        if self._session is not None:
            return self._session

        import requests
        session = requests.Session()

        if self._auth_type == "token":
            # Personal Access Token (Confluence Server/Data Center)
            session.headers["Authorization"] = f"Bearer {self._token}"
        else:
            # HTTP Basic Auth (Confluence Cloud: user + api_token)
            session.auth = (self._user, self._token)

        session.headers["Accept"] = "application/json"
        session.headers["Content-Type"] = "application/json"

        # Soporte para proxies corporativos con SSL inspection (ej. Netskope)
        verify_ssl = os.getenv("HTTP_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
        session.verify = verify_ssl

        self._session = session
        return session

    # ── validate_connection ────────────────────────────────────────────────

    def validate_connection(self) -> dict:
        """
        Verifica credenciales y conectividad con la API de Confluence.
        Funciona tanto con credenciales de env vars como con credenciales
        aplicadas via apply_credentials() desde la UI.
        """
        # Verificar que tenemos las credenciales mínimas (en atributos, no solo en env vars)
        missing = []
        if not self._base_url: missing.append("URL")
        if not self._user:     missing.append("User")
        if not self._token:    missing.append("API Token")

        if missing:
            return {
                "ok": False,
                "connector": self.connector_id,
                "detail": f"Faltan credenciales: {', '.join(missing)}. Configura env vars o intóducelas en la UI.",
            }

        try:
            session = self._get_session()
            # Cloud: GET /wiki/rest/api/user/current
            url = f"{self._base_url}/wiki/rest/api/user/current"
            resp = session.get(url, timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "ok": True,
                    "connector": self.connector_id,
                    "detail": f"Conexión OK — usuario: {data.get('displayName', self._user)}",
                    "info": {
                        "user":       data.get("displayName"),
                        "account_id": data.get("accountId"),
                        "base_url":   self._base_url,
                        "auth_type":  self._auth_type,
                    },
                }
            elif resp.status_code == 401:
                return {
                    "ok": False,
                    "connector": self.connector_id,
                    "detail": "Credenciales incorrectas (HTTP 401). Verifica usuario y API Token.",
                }
            elif resp.status_code == 403:
                return {
                    "ok": False,
                    "connector": self.connector_id,
                    "detail": "Acceso denegado (HTTP 403). El usuario no tiene permisos en esta instancia.",
                }
            else:
                return {
                    "ok": False,
                    "connector": self.connector_id,
                    "detail": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as exc:
            return {
                "ok": False,
                "connector": self.connector_id,
                "detail": f"Error de conexión: {exc}",
            }

    # ── fetch_item ─────────────────────────────────────────────────────────

    def fetch_item(self, item_id: str) -> FetchedItem:
        """
        Descarga una página de Confluence por su ID y la serializa al formato canónico.

        Args:
            item_id: ID numérico de la página (ej. "123456789").

        Returns:
            FetchedItem con el texto serializado en formato .confluence.

        Raises:
            ValueError: si la página no existe (HTTP 404) o no tiene contenido.
            ConnectionError: si la API no responde.
        """
        session = self._get_session()

        # Expandir: body.view (HTML), version, ancestors, children, metadata
        url = (
            f"{self._base_url}/wiki/rest/api/content/{item_id}"
            f"?expand=body.view,version,ancestors,children.page,metadata.labels,space"
        )

        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:
            raise ConnectionError(f"Error de red al acceder a Confluence: {exc}") from exc

        if resp.status_code == 404:
            raise ValueError(f"Página '{item_id}' no encontrada en Confluence.")
        if resp.status_code != 200:
            raise ConnectionError(f"Confluence API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()

        # ── Extraer campos ─────────────────────────────────────────────────
        title       = data.get("title", "Sin título")
        space_key   = data.get("space", {}).get("key", "")
        space_name  = data.get("space", {}).get("name", "")
        author      = (data.get("version", {}).get("by", {}) or {}).get("displayName", "")
        last_mod    = (data.get("version", {}) or {}).get("when", "")
        page_url    = (
            self._base_url
            + "/wiki"
            + (data.get("_links", {}).get("webui", f"/pages/{item_id}"))
        )
        labels_raw  = (
            data.get("metadata", {})
                .get("labels", {})
                .get("results", [])
        )
        labels      = ", ".join(lbl.get("name", "") for lbl in labels_raw if lbl.get("name"))
        ancestors   = data.get("ancestors", [])
        parent_path = " > ".join(a.get("title", "") for a in ancestors if a.get("title"))
        children    = data.get("children", {}).get("page", {}).get("results", [])
        children_count = len(children)

        # ── HTML → texto plano ─────────────────────────────────────────────
        html_body = data.get("body", {}).get("view", {}).get("value", "")
        plain_body = self._html_to_text(html_body)

        if not plain_body.strip():
            raise ValueError(f"La página '{item_id}' ('{title}') no tiene contenido extraíble.")

        # ── Serializar al formato canónico .confluence ─────────────────────
        text = self._serialize(
            title=title,
            space_key=space_key,
            labels=labels,
            author=author,
            last_modified=last_mod,
            url=page_url,
            parent_page=parent_path,
            children_count=children_count,
            body=plain_body,
        )

        slug    = self._make_slug(title)
        virt_fn = self._virtual_filename(title)

        return FetchedItem(
            item_id=item_id,
            slug=virt_fn,
            title=title,
            text=text,
            virtual_ext=self.virtual_extension,
            metadata={
                "source_url":    page_url,
                "space":         space_key,
                "space_name":    space_name,
                "labels":        [lbl.get("name") for lbl in labels_raw],
                "author":        author,
                "last_modified": last_mod,
                "parent_page":   parent_path,
                "children_count": children_count,
                "confluence_id": item_id,
                "ingest_origin": "confluence_api",
            },
        )

    # ── fetch_batch ────────────────────────────────────────────────────────

    def fetch_batch(
        self,
        source_id: str,
        params: dict | None = None,
    ) -> BatchResult:
        """
        Descarga todas las páginas de un space de Confluence.

        Args:
            source_id: key del space (ej. "TEC", "GOV", "OPS").
            params:    dict opcional con:
                       - limit (int, default 50): páginas por request CQL
                       - labels (str): filtrar por label
                       - updated_after (str): ISO 8601 — solo páginas modificadas después
                       - max_pages (int, default 200): techo de páginas a recuperar

        Returns:
            BatchResult con todos los FetchedItem y estadísticas de la operación.
        """
        params = params or {}
        limit       = int(params.get("limit", 50))
        labels      = params.get("labels", "")
        upd_after   = params.get("updated_after", "")
        max_pages   = int(params.get("max_pages", 200))

        session  = self._get_session()
        result   = BatchResult()
        start    = 0

        # Construir CQL (Confluence Query Language)
        cql_parts = [f'space="{source_id}" AND type=page']
        if labels:
            label_list = [f'"{l.strip()}"' for l in labels.split(",") if l.strip()]
            cql_parts.append(f"label IN ({', '.join(label_list)})")
        if upd_after:
            cql_parts.append(f'lastModified >= "{upd_after}"')
        cql = " AND ".join(cql_parts)

        log.info("[confluence] batch: space=%s CQL=%s", source_id, cql)

        while len(result.items) < max_pages:
            url = (
                f"{self._base_url}/wiki/rest/api/content/search"
                f"?cql={cql}"
                f"&expand=version,ancestors,metadata.labels,space"
                f"&limit={min(limit, max_pages - len(result.items))}"
                f"&start={start}"
            )
            try:
                resp = session.get(url, timeout=30)
                if resp.status_code != 200:
                    result.errors.append(f"HTTP {resp.status_code} en start={start}: {resp.text[:200]}")
                    break
                data = resp.json()
            except Exception as exc:
                result.errors.append(f"Error de red en start={start}: {exc}")
                break

            pages_raw = data.get("results", [])
            if not pages_raw:
                break

            if result.total == 0:
                result.total = data.get("totalSize", len(pages_raw))

            for page_meta in pages_raw:
                page_id = page_meta.get("id")
                try:
                    item = self.fetch_item(page_id)
                    result.items.append(item)
                    result.fetched += 1
                except ValueError as exc:
                    log.warning("[confluence] batch: omitiendo página %s: %s", page_id, exc)
                    result.skipped += 1
                except Exception as exc:
                    log.error("[confluence] batch: error en página %s: %s", page_id, exc)
                    result.errors.append(f"Error en página {page_id}: {exc}")
                    result.skipped += 1

            start += len(pages_raw)
            # Si la API indica que no hay más resultados
            if start >= data.get("totalSize", start + 1):
                break

        log.info(
            "[confluence] batch DONE: fetched=%d skipped=%d errors=%d total=%d",
            result.fetched, result.skipped, len(result.errors), result.total,
        )
        return result

    # ── Helpers privados ───────────────────────────────────────────────────

    def _html_to_text(self, html: str) -> str:
        """
        Convierte HTML de Confluence a texto estructurado en markdown.
        Preserva: headings (h1-h4), párrafos, listas, código, tablas básicas.
        Elimina: scripts, estilos, atributos HTML, macros de Confluence.
        """
        if not html:
            return ""

        # Intentar usar html2text si disponible (mejor calidad)
        try:
            import html2text
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.ignore_emphasis = False
            h.body_width = 0  # Sin límite de línea
            h.protect_links = True
            result = h.handle(html)
            # Limpiar macros de Confluence residuales
            result = re.sub(r"\{[^}]+\}", "", result)
            result = re.sub(r"\n{4,}", "\n\n\n", result)
            return result.strip()
        except ImportError:
            pass  # Fallback a regex básico

        # Fallback: limpieza con regex
        # Preservar headings — fix: usar función named para evitar bug de closure en lambda
        def _make_heading_repl(level):
            prefix = "#" * level
            def _repl(m):
                return f"{prefix} {m.group(1)}\n"
            return _repl

        for i in range(4, 0, -1):
            html = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                _make_heading_repl(i),
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # Preservar bloques de código
        html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<pre[^>]*>(.*?)</pre>",
                      lambda m: f"\n```\n{m.group(1)}\n```\n",
                      html, flags=re.DOTALL | re.IGNORECASE)

        # Listas
        html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", html, flags=re.DOTALL | re.IGNORECASE)

        # Párrafos → newlines
        html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)

        # Eliminar resto de tags
        html = re.sub(r"<[^>]+>", " ", html)

        # Limpiar entidades HTML
        html = re.sub(r"&nbsp;", " ", html)
        html = re.sub(r"&amp;", "&", html)
        html = re.sub(r"&lt;", "<", html)
        html = re.sub(r"&gt;", ">", html)
        html = re.sub(r"&quot;", '"', html)
        html = re.sub(r"&#\d+;", "", html)
        html = re.sub(r"&[a-zA-Z]+;", "", html)

        # Limpiar macros de Confluence
        html = re.sub(r"\{[^}]+\}", "", html)

        # Normalizar espacios
        html = re.sub(r"[ \t]+", " ", html)
        html = re.sub(r"\n{4,}", "\n\n\n", html)

        return html.strip()

    def _serialize(
        self,
        title: str,
        space_key: str,
        labels: str,
        author: str,
        last_modified: str,
        url: str,
        parent_page: str,
        children_count: int,
        body: str,
    ) -> str:
        """
        Serializa todos los campos al formato canónico .confluence.
        El ConfluenceExtractor lee exactamente este formato.
        """
        lines = ["## Metadata"]
        lines.append(f"space: {space_key}")
        lines.append(f"title: {title}")
        if labels:
            lines.append(f"labels: {labels}")
        if author:
            lines.append(f"author: {author}")
        if last_modified:
            lines.append(f"last_modified: {last_modified}")
        if url:
            lines.append(f"url: {url}")
        if parent_page:
            lines.append(f"parent_page: {parent_page}")
        if children_count > 0:
            lines.append(f"children_count: {children_count}")

        lines.append("")
        lines.append("## Contenido")
        lines.append(body)

        return "\n".join(lines)
