"""
brain/connectors/jira_connector.py
====================================
Conector para Jira (Atlassian Cloud y Server).

AUTENTICACIÓN
-------------
Variables de entorno requeridas:
  JIRA_URL          URL base de la instancia (ej. https://empresa.atlassian.net)
  JIRA_USER         Email del usuario (cloud) o username (server)
  JIRA_API_TOKEN    API token de Atlassian (cloud) o contraseña (server)
  JIRA_AUTH_TYPE    "basic" (default) | "token" (PAT para server/DC)

Variables opcionales:
  JIRA_CUSTOM_FIELDS_MAP  Mapeo de campos custom en formato:
                          "customfield_10020:sprint,customfield_10016:story_points"
  JIRA_BOT_PATTERN        Regex adicional para detectar comentarios de bots.
                          Default: detecta Jira Automation, bitbucket-pipelines, etc.

OPERACIONES
-----------
  fetch_item(issue_key)
      Descarga un issue por su key (ej. "TEC-1234").
      Incluye descripción y comentarios humanos (bots filtrados).
      Serializa al formato canónico .jira_ticket.

  fetch_batch(project_key, params)
      Descarga issues de un proyecto vía JQL.
      Parámetros opcionales:
        jql (str):            JQL personalizado (sobreescribe el default)
        status (str):         filtrar por estado (ej. "Done")
        issue_type (str):     filtrar por tipo (ej. "Story,Bug")
        updated_after (str):  ISO 8601
        limit (int, default 50): issues por request
        max_issues (int, default 200): techo total

FORMATO CANÓNICO .jira_ticket
------------------------------
  ## Metadata
  key: TEC-1234
  tipo: Story
  estado: Done
  ...

  ## Descripción
  Texto de la descripción.

  ## Comentarios
  **Juan García** (2026-01-15T10:00:00.000+0000):
  Texto del comentario.
  ---
  **María López** (2026-01-18T14:30:00.000+0000):
  Otro comentario.

FILTRADO DE BOTS
----------------
Los comentarios de bots automatizados se eliminan antes de serializar.
Un comentario es de bot si el autor coincide con cualquiera de:
  - Patrones predefinidos: "jira.automation", "bitbucket-pipelines", "bamboo",
    "jenkins", "github-actions", "gitlab-ci", "bot", "automation", "service-account"
  - Patrón personalizado en JIRA_BOT_PATTERN (env var, regex)
"""

from __future__ import annotations
import os
import re
import logging
from .base import BaseConnector, FetchedItem, BatchResult

log = logging.getLogger(__name__)

# Patrones de bots predefinidos (case-insensitive)
_DEFAULT_BOT_PATTERNS = re.compile(
    r"jira[\.\-]?automation|bitbucket[\.\-]?pipelines|bamboo|jenkins|"
    r"github[\.\-]?actions|gitlab[\.\-]?ci|service[\.\-]?account|"
    r"\bbot\b|automation[\.\-]?user|ci[\.\-]?cd",
    re.IGNORECASE,
)


class JiraConnector(BaseConnector):

    # ── Identidad ──────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return "jira"

    @property
    def virtual_extension(self) -> str:
        return ".jira_ticket"

    # ── Inicialización ─────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._base_url    = os.getenv("JIRA_URL", "").rstrip("/")
        self._user        = os.getenv("JIRA_USER", "")
        self._token       = os.getenv("JIRA_API_TOKEN", "")
        self._auth_type   = os.getenv("JIRA_AUTH_TYPE", "basic").lower()
        self._session     = None

        # Mapeo de campos custom: "customfield_10020:sprint,customfield_10016:story_points"
        custom_map_raw = os.getenv("JIRA_CUSTOM_FIELDS_MAP", "")
        self._custom_fields: dict[str, str] = {}
        for pair in custom_map_raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                field_id, alias = pair.split(":", 1)
                self._custom_fields[field_id.strip()] = alias.strip()

        # Patrón adicional de bots
        extra_bot = os.getenv("JIRA_BOT_PATTERN", "")
        if extra_bot:
            try:
                self._bot_re = re.compile(
                    f"(?:{_DEFAULT_BOT_PATTERNS.pattern})|(?:{extra_bot})",
                    re.IGNORECASE,
                )
            except re.error:
                log.warning("[jira] JIRA_BOT_PATTERN inválido, usando patrones default")
                self._bot_re = _DEFAULT_BOT_PATTERNS
        else:
            self._bot_re = _DEFAULT_BOT_PATTERNS

    def apply_credentials(self, credentials: dict) -> None:
        """Aplica credenciales de la UI sobreescribiendo las de env vars."""
        if "url" in credentials:       self._base_url  = credentials["url"].rstrip("/")
        if "user" in credentials:      self._user      = credentials["user"]
        if "token" in credentials:     self._token     = credentials["token"]
        if "auth_type" in credentials: self._auth_type = credentials["auth_type"]
        self._session = None  # forzar recreación de sesión

    def _get_session(self):
        if self._session is not None:
            return self._session

        import requests
        session = requests.Session()

        if self._auth_type == "token":
            session.headers["Authorization"] = f"Bearer {self._token}"
        else:
            session.auth = (self._user, self._token)

        session.headers["Accept"] = "application/json"
        verify_ssl = os.getenv("HTTP_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
        session.verify = verify_ssl
        self._session = session
        return session

    # ── validate_connection ────────────────────────────────────────────────

    def validate_connection(self) -> dict:
        # Verificar credenciales en atributos (no solo env vars)
        missing = []
        if not self._base_url: missing.append("URL")
        if not self._user:     missing.append("User")
        if not self._token:    missing.append("API Token")

        if missing:
            return {
                "ok": False,
                "connector": self.connector_id,
                "detail": f"Faltan credenciales: {', '.join(missing)}. Configura env vars o intróducelas en la UI.",
            }

        try:
            session = self._get_session()
            # Intentar API v3 (Cloud) primero, luego v2 (Server)
            for api_version in ("3", "2"):
                url = f"{self._base_url}/rest/api/{api_version}/myself"
                resp = session.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "ok": True,
                        "connector": self.connector_id,
                        "detail": f"Conexión OK — usuario: {data.get('displayName', self._user)}",
                        "info": {
                            "user":        data.get("displayName"),
                            "email":       data.get("emailAddress"),
                            "account_id":  data.get("accountId"),
                            "base_url":    self._base_url,
                            "auth_type":   self._auth_type,
                            "api_version": api_version,
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
        Descarga un issue de Jira por su key y lo serializa al formato canónico.

        Args:
            item_id: issue key (ej. "TEC-1234").

        Returns:
            FetchedItem con el texto serializado en formato .jira_ticket.

        Raises:
            ValueError: si el issue no existe (HTTP 404) o no tiene contenido.
            ConnectionError: si la API no responde.
        """
        session = self._get_session()

        # Campos a recuperar, incluyendo custom fields mapeados
        fields = [
            "summary", "issuetype", "status", "priority",
            "assignee", "reporter", "components", "fixVersions",
            "labels", "created", "resolutiondate", "description",
            "comment",
        ]
        fields.extend(self._custom_fields.keys())
        fields_param = ",".join(fields)

        url = f"{self._base_url}/rest/api/3/issue/{item_id}?fields={fields_param}"

        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:
            raise ConnectionError(f"Error de red al acceder a Jira: {exc}") from exc

        if resp.status_code == 404:
            raise ValueError(f"Issue '{item_id}' no encontrado en Jira.")
        if resp.status_code != 200:
            raise ConnectionError(f"Jira API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        f = data.get("fields", {})

        # ── Extraer campos estándar ────────────────────────────────────────
        summary     = f.get("summary", "Sin título")
        issue_type  = (f.get("issuetype", {}) or {}).get("name", "")
        status      = (f.get("status", {}) or {}).get("name", "")
        priority    = (f.get("priority", {}) or {}).get("name", "")
        assignee    = (f.get("assignee", {}) or {}).get("displayName", "")
        reporter    = (f.get("reporter", {}) or {}).get("displayName", "")
        components  = ", ".join(c.get("name", "") for c in (f.get("components") or []))
        fix_versions = ", ".join(v.get("name", "") for v in (f.get("fixVersions") or []))
        labels      = ", ".join(f.get("labels") or [])
        created     = f.get("created", "")
        resolved    = f.get("resolutiondate", "")

        # ── Descripción (Atlassian Document Format → texto) ────────────────
        desc_adf = f.get("description")
        description = self._adf_to_text(desc_adf) if desc_adf else ""

        # ── Comentarios (filtrar bots) ─────────────────────────────────────
        comments_raw = (f.get("comment", {}) or {}).get("comments", [])
        human_comments = self._filter_human_comments(comments_raw)

        # ── Campos custom ──────────────────────────────────────────────────
        custom_values: dict[str, str] = {}
        for field_id, alias in self._custom_fields.items():
            val = f.get(field_id)
            if val is not None:
                if isinstance(val, dict):
                    val = val.get("name") or val.get("value") or str(val)
                custom_values[alias] = str(val)

        if not description.strip() and not human_comments:
            raise ValueError(
                f"El issue '{item_id}' ('{summary}') no tiene descripción ni comentarios humanos."
            )

        # ── Serializar ─────────────────────────────────────────────────────
        text = self._serialize(
            key=item_id,
            issue_type=issue_type,
            status=status,
            priority=priority,
            assignee=assignee,
            reporter=reporter,
            components=components,
            fix_versions=fix_versions,
            labels=labels,
            created=created,
            resolved=resolved,
            description=description,
            comments=human_comments,
            custom_fields=custom_values,
        )

        virt_fn = self._virtual_filename(f"{item_id} {summary}")

        return FetchedItem(
            item_id=item_id,
            slug=virt_fn,
            title=f"{item_id}: {summary}",
            text=text,
            virtual_ext=self.virtual_extension,
            metadata={
                "issue_key":    item_id,
                "summary":      summary,
                "issue_type":   issue_type,
                "status":       status,
                "priority":     priority,
                "assignee":     assignee,
                "reporter":     reporter,
                "components":   components,
                "labels":       f.get("labels", []),
                "created":      created,
                "resolved":     resolved,
                "source_url":   f"{self._base_url}/browse/{item_id}",
                "ingest_origin": "jira_api",
                **{f"custom_{k}": v for k, v in custom_values.items()},
            },
        )

    # ── fetch_batch ────────────────────────────────────────────────────────

    def fetch_batch(
        self,
        source_id: str,
        params: dict | None = None,
    ) -> BatchResult:
        """
        Descarga issues de un proyecto de Jira.

        Args:
            source_id: key del proyecto (ej. "TEC", "OPS").
            params:    dict opcional con:
                       - jql (str): JQL personalizado (sobreescribe el default)
                       - status (str): filtrar por estado (ej. "Done")
                       - issue_type (str): filtrar por tipo (ej. "Story,Bug")
                       - updated_after (str): ISO 8601
                       - limit (int, default 50): issues por request
                       - max_issues (int, default 200): techo total
        """
        params     = params or {}
        limit      = int(params.get("limit", 50))
        max_issues = int(params.get("max_issues", 200))

        # Construir JQL
        if "jql" in params:
            jql = params["jql"]
        else:
            jql_parts = [f"project = {source_id}"]
            if params.get("status"):
                statuses = ", ".join(f'"{s.strip()}"' for s in params["status"].split(","))
                jql_parts.append(f"status IN ({statuses})")
            if params.get("issue_type"):
                types = ", ".join(f'"{t.strip()}"' for t in params["issue_type"].split(","))
                jql_parts.append(f"issuetype IN ({types})")
            if params.get("updated_after"):
                jql_parts.append(f'updated >= "{params["updated_after"]}"')
            jql_parts.append("ORDER BY updated DESC")
            jql = " AND ".join(jql_parts[:-1]) + " " + jql_parts[-1]

        log.info("[jira] batch: project=%s JQL=%s", source_id, jql)

        session = self._get_session()
        result  = BatchResult()
        start   = 0

        while len(result.items) < max_issues:
            url = f"{self._base_url}/rest/api/3/search"
            payload = {
                "jql":        jql,
                "startAt":    start,
                "maxResults": min(limit, max_issues - len(result.items)),
                "fields":     ["summary", "key"],
            }
            try:
                resp = session.post(url, json=payload, timeout=30)
                if resp.status_code != 200:
                    result.errors.append(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    break
                data = resp.json()
            except Exception as exc:
                result.errors.append(f"Error de red en start={start}: {exc}")
                break

            issues_raw = data.get("issues", [])
            if not issues_raw:
                break

            if result.total == 0:
                result.total = data.get("total", len(issues_raw))

            for issue_meta in issues_raw:
                issue_key = issue_meta.get("key")
                try:
                    item = self.fetch_item(issue_key)
                    result.items.append(item)
                    result.fetched += 1
                except ValueError as exc:
                    log.warning("[jira] batch: omitiendo %s: %s", issue_key, exc)
                    result.skipped += 1
                except Exception as exc:
                    log.error("[jira] batch: error en %s: %s", issue_key, exc)
                    result.errors.append(f"Error en {issue_key}: {exc}")
                    result.skipped += 1

            start += len(issues_raw)
            if start >= data.get("total", start + 1):
                break

        log.info(
            "[jira] batch DONE: fetched=%d skipped=%d errors=%d total=%d",
            result.fetched, result.skipped, len(result.errors), result.total,
        )
        return result

    # ── Helpers privados ───────────────────────────────────────────────────

    def _adf_to_text(self, adf: dict | str | None) -> str:
        """
        Convierte Atlassian Document Format (ADF) a texto plano.
        ADF es el formato JSON de Jira Cloud para campos de texto enriquecido.
        Jira Server/DC usa markup wiki — si adf es str, lo devuelve tal cual.
        """
        if adf is None:
            return ""
        if isinstance(adf, str):
            # Jira Server: markup wiki, devolver tal cual
            return adf.strip()

        # Jira Cloud: ADF JSON recursivo
        parts: list[str] = []
        self._adf_node_to_text(adf, parts)
        return "\n".join(parts).strip()

    def _adf_node_to_text(self, node: dict, parts: list[str], depth: int = 0) -> None:
        """Recursivo: extrae texto de un nodo ADF."""
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "")

        if node_type == "text":
            parts.append(node.get("text", ""))
            return

        if node_type == "hardBreak":
            parts.append("\n")
            return

        if node_type in ("paragraph", "blockquote"):
            sub: list[str] = []
            for child in node.get("content", []):
                self._adf_node_to_text(child, sub, depth)
            text = "".join(sub).strip()
            if text:
                parts.append(text)
                parts.append("")
            return

        if node_type in ("heading",):
            level = node.get("attrs", {}).get("level", 2)
            sub = []
            for child in node.get("content", []):
                self._adf_node_to_text(child, sub, depth)
            text = "".join(sub).strip()
            if text:
                parts.append(f"{'#' * level} {text}")
            return

        if node_type == "bulletList":
            for item in node.get("content", []):
                sub = []
                for child in item.get("content", []):
                    self._adf_node_to_text(child, sub, depth + 1)
                text = "".join(sub).strip()
                if text:
                    parts.append(f"{'  ' * depth}- {text}")
            return

        if node_type == "orderedList":
            for idx, item in enumerate(node.get("content", []), 1):
                sub = []
                for child in item.get("content", []):
                    self._adf_node_to_text(child, sub, depth + 1)
                text = "".join(sub).strip()
                if text:
                    parts.append(f"{'  ' * depth}{idx}. {text}")
            return

        if node_type == "codeBlock":
            lang = node.get("attrs", {}).get("language", "")
            sub = []
            for child in node.get("content", []):
                self._adf_node_to_text(child, sub, depth)
            code = "".join(sub)
            parts.append(f"```{lang}\n{code}\n```")
            return

        # Nodos contenedor genéricos: procesar hijos
        for child in node.get("content", []):
            self._adf_node_to_text(child, parts, depth)

    def _filter_human_comments(self, comments: list[dict]) -> list[dict]:
        """
        Filtra comentarios de bots basándose en el nombre del autor.
        Retorna solo comentarios de personas humanas.
        """
        human = []
        for comment in comments:
            author_obj = comment.get("author", {}) or {}
            author_name = (
                author_obj.get("displayName", "")
                or author_obj.get("emailAddress", "")
                or ""
            )
            if self._bot_re.search(author_name):
                log.debug("[jira] filtrando comentario de bot: '%s'", author_name)
                continue
            human.append(comment)
        return human

    def _serialize(
        self,
        key: str,
        issue_type: str,
        status: str,
        priority: str,
        assignee: str,
        reporter: str,
        components: str,
        fix_versions: str,
        labels: str,
        created: str,
        resolved: str,
        description: str,
        comments: list[dict],
        custom_fields: dict[str, str],
    ) -> str:
        """
        Serializa todos los campos al formato canónico .jira_ticket.
        El JiraTicketExtractor lee exactamente este formato.
        """
        lines = ["## Metadata"]
        lines.append(f"key: {key}")
        if issue_type:   lines.append(f"tipo: {issue_type}")
        if status:       lines.append(f"estado: {status}")
        if priority:     lines.append(f"prioridad: {priority}")
        if assignee:     lines.append(f"asignado: {assignee}")
        if reporter:     lines.append(f"reporter: {reporter}")
        if components:   lines.append(f"componentes: {components}")
        if fix_versions: lines.append(f"fix_version: {fix_versions}")
        if labels:       lines.append(f"labels: {labels}")
        if created:      lines.append(f"created: {created}")
        if resolved:     lines.append(f"resolved: {resolved}")
        # Campos custom
        for alias, val in custom_fields.items():
            lines.append(f"{alias}: {val}")

        if description.strip():
            lines.append("")
            lines.append("## Descripción")
            lines.append(description.strip())

        if comments:
            lines.append("")
            lines.append("## Comentarios")
            comment_parts = []
            for c in comments:
                author_obj = c.get("author", {}) or {}
                author = author_obj.get("displayName", "Desconocido")
                created_c = c.get("created", "")
                body_adf  = c.get("body")
                body_text = self._adf_to_text(body_adf).strip()
                if body_text:
                    comment_parts.append(f"**{author}** ({created_c}):\n{body_text}")
            lines.append("\n---\n".join(comment_parts))

        return "\n".join(lines)
