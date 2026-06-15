"""
brain/connectors/github_connector.py
======================================
Conector para GitHub (repositorios públicos y privados).

AUTENTICACIÓN
-------------
Variables de entorno requeridas:
  GITHUB_TOKEN    Personal Access Token de GitHub (classic o fine-grained)
                  Permisos mínimos: repo:read (privados) o sin permisos (públicos)

Variables opcionales:
  GITHUB_API_URL  URL base de la API (default: https://api.github.com)
                  Para GitHub Enterprise: https://github.empresa.com/api/v3

OPERACIONES
-----------
  fetch_item(item_id)
      Recupera un fichero o directorio de un repositorio.
      Formato de item_id:  "owner/repo/ruta/al/fichero.py"
                           "owner/repo"           → README del repo
                           "owner/repo@branch"    → con rama específica
      Devuelve el contenido del fichero serializado al formato .github_file.

  fetch_batch(source_id, params)
      Indexa todos los ficheros relevantes de un repositorio.
      source_id:  "owner/repo"  o  "owner/repo@branch"
      Parámetros opcionales:
        extensions (str): extensiones a incluir separadas por coma
                          Default: "py,sql,ipynb,md,json,yaml,yml,txt"
        path (str):       subdirectorio dentro del repo (default: raíz)
        max_files (int):  máximo de ficheros a ingestar (default: 200)
        exclude_patterns (str): patrones a excluir separados por coma
                                Default: "__pycache__,.git,node_modules,dist,build"

FORMATO CANÓNICO .github_file
------------------------------
  ## Metadata
  repo: owner/repo
  path: ruta/al/fichero.py
  branch: main
  language: python
  size_bytes: 1234
  url: https://github.com/owner/repo/blob/main/ruta/al/fichero.py
  last_commit: Mensaje del último commit
  last_author: Nombre del autor
  last_date: 2026-01-15T10:30:00Z

  ## Contenido
  [contenido del fichero]

El GitHubExtractor (api/extractors/github_file.py) lee exactamente este formato.
"""

from __future__ import annotations
import os
import re
import base64
import logging
from .base import BaseConnector, FetchedItem, BatchResult

log = logging.getLogger(__name__)

# Extensiones por defecto — ficheros de código y documentación
_DEFAULT_EXTENSIONS = {
    "py", "sql", "ipynb", "md", "json", "yaml", "yml",
    "txt", "js", "ts", "java", "go", "rs", "sh", "r",
    "toml", "ini", "cfg", "xml", "csv",
}

# Patrones de directorios/ficheros a excluir por defecto
_DEFAULT_EXCLUDES = {
    "__pycache__", ".git", "node_modules", "dist", "build",
    ".venv", "venv", ".env", ".eggs", "*.egg-info",
    ".pytest_cache", ".mypy_cache", ".tox",
}

# Mapa de extensión → nombre de lenguaje para metadata
_EXT_TO_LANG = {
    "py": "python", "sql": "sql", "ipynb": "jupyter",
    "md": "markdown", "json": "json", "yaml": "yaml", "yml": "yaml",
    "js": "javascript", "ts": "typescript", "java": "java",
    "go": "go", "rs": "rust", "sh": "bash", "r": "r",
    "toml": "toml", "ini": "ini", "cfg": "ini", "xml": "xml",
    "csv": "csv", "txt": "text",
}


class GitHubConnector(BaseConnector):

    # ── Identidad ──────────────────────────────────────────────────────────

    @property
    def connector_id(self) -> str:
        return "github"

    @property
    def virtual_extension(self) -> str:
        return ".github_file"

    # ── Inicialización ─────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._token   = os.getenv("GITHUB_TOKEN", "")
        self._api_url = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self._session = None

    def apply_credentials(self, credentials: dict) -> None:
        """Aplica credenciales de la UI sobreescribiendo las de env vars."""
        if "token" in credentials:   self._token   = credentials["token"]
        if "api_url" in credentials: self._api_url = credentials["api_url"].rstrip("/")
        self._session = None  # forzar recreación de sesión

    def _get_session(self):
        if self._session is not None:
            return self._session

        import requests
        session = requests.Session()
        session.headers["Accept"] = "application/vnd.github+json"
        session.headers["X-GitHub-Api-Version"] = "2022-11-28"
        if self._token:
            session.headers["Authorization"] = f"Bearer {self._token}"

        verify_ssl = os.getenv("HTTP_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
        session.verify = verify_ssl
        self._session = session
        return session

    # ── validate_connection ────────────────────────────────────────────────

    def validate_connection(self) -> dict:
        """
        Verifica el token de GitHub con GET /user.
        Para repositorios públicos funciona incluso sin token.
        """
        try:
            session = self._get_session()
            resp = session.get(f"{self._api_url}/user", timeout=10)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "ok": True,
                    "connector": self.connector_id,
                    "detail": f"Conexión OK — usuario: {data.get('login')}",
                    "info": {
                        "login":      data.get("login"),
                        "name":       data.get("name"),
                        "api_url":    self._api_url,
                        "scopes":     resp.headers.get("X-OAuth-Scopes", "none"),
                        "rate_limit": resp.headers.get("X-RateLimit-Remaining", "?"),
                    },
                }
            elif resp.status_code == 401:
                return {
                    "ok": False,
                    "connector": self.connector_id,
                    "detail": "Token inválido o expirado. Genera uno nuevo en GitHub → Settings → Developer settings.",
                }
            else:
                # Sin token → acceso anónimo (60 req/hora)
                return {
                    "ok": True,
                    "connector": self.connector_id,
                    "detail": "Acceso anónimo (sin GITHUB_TOKEN). Límite: 60 req/hora. Solo repos públicos.",
                    "info": {
                        "rate_limit": resp.headers.get("X-RateLimit-Remaining", "?"),
                        "api_url":    self._api_url,
                    },
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
        Recupera un fichero de GitHub por su ruta en el repositorio.

        Args:
            item_id: "owner/repo/ruta/al/fichero.py"
                     "owner/repo"              → README
                     "owner/repo@branch"       → rama específica del README
                     "owner/repo/path@branch"  → fichero en rama específica

        Returns:
            FetchedItem con el contenido del fichero en formato .github_file.

        Raises:
            ValueError: si el fichero no existe o no es texto.
            ConnectionError: si la API no responde.
        """
        owner, repo, file_path, branch = self._parse_item_id(item_id)
        session = self._get_session()

        # Si no hay file_path → buscar README
        if not file_path:
            file_path = self._find_readme(owner, repo, branch, session)
            if not file_path:
                raise ValueError(f"No se encontró README en '{owner}/{repo}'.")

        # GET /repos/{owner}/{repo}/contents/{path}?ref={branch}
        url = f"{self._api_url}/repos/{owner}/{repo}/contents/{file_path}"
        params = {}
        if branch:
            params["ref"] = branch

        try:
            resp = session.get(url, params=params, timeout=30)
        except Exception as exc:
            raise ConnectionError(f"Error de red al acceder a GitHub: {exc}") from exc

        if resp.status_code == 404:
            raise ValueError(f"Fichero '{file_path}' no encontrado en '{owner}/{repo}'.")
        if resp.status_code == 403:
            raise ConnectionError("Rate limit de GitHub alcanzado. Configura GITHUB_TOKEN para 5000 req/hora.")
        if resp.status_code != 200:
            raise ConnectionError(f"GitHub API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()

        # Verificar que es un fichero (no directorio)
        if data.get("type") != "file":
            raise ValueError(f"'{file_path}' es un directorio, no un fichero. Especifica un fichero concreto.")

        # Decodificar contenido base64
        content_b64 = data.get("content", "")
        try:
            content = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"No se pudo decodificar '{file_path}': {exc}") from exc

        if not content.strip():
            raise ValueError(f"El fichero '{file_path}' está vacío.")

        # Metadata del último commit
        # NOTA: en batch esta llamada se omite por defecto para evitar N HTTP calls extra
        # Se puede activar con params["include_commit_info"] = True
        include_commit = getattr(self, "_include_commit_info", True)
        if include_commit:
            last_commit, last_author, last_date = self._get_last_commit(
                owner, repo, file_path, branch, session
            )
        else:
            last_commit, last_author, last_date = "", "", ""

        # Determinar rama efectiva
        effective_branch = branch or self._get_default_branch(owner, repo, session)
        file_url = f"https://github.com/{owner}/{repo}/blob/{effective_branch}/{file_path}"
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        language = _EXT_TO_LANG.get(ext, "unknown")
        size_bytes = data.get("size", len(content))

        text = self._serialize(
            repo=f"{owner}/{repo}",
            path=file_path,
            branch=effective_branch,
            language=language,
            size_bytes=size_bytes,
            url=file_url,
            last_commit=last_commit,
            last_author=last_author,
            last_date=last_date,
            content=content,
        )

        title = f"{owner}/{repo}: {file_path}"
        virt_fn = self._virtual_filename(f"{owner}-{repo}-{file_path.replace('/', '-')}")

        return FetchedItem(
            item_id=item_id,
            slug=virt_fn,
            title=title,
            text=text,
            virtual_ext=self.virtual_extension,
            metadata={
                "repo":         f"{owner}/{repo}",
                "path":         file_path,
                "branch":       effective_branch,
                "language":     language,
                "size_bytes":   size_bytes,
                "source_url":   file_url,
                "last_commit":  last_commit,
                "last_author":  last_author,
                "last_date":    last_date,
                "ingest_origin": "github_api",
            },
        )

    # ── fetch_batch ────────────────────────────────────────────────────────

    def fetch_batch(
        self,
        source_id: str,
        params: dict | None = None,
    ) -> BatchResult:
        """
        Indexa todos los ficheros relevantes de un repositorio GitHub.

        Args:
            source_id: "owner/repo" o "owner/repo@branch"
            params:    dict opcional con:
                       - extensions (str): "py,sql,md" (default: ver _DEFAULT_EXTENSIONS)
                       - path (str): subdirectorio dentro del repo (default: raíz "")
                       - max_files (int): máximo de ficheros (default: 200)
                       - exclude_patterns (str): patrones a excluir (separados por coma)
        """
        params = params or {}

        # Parsear source_id
        if "@" in source_id:
            repo_part, branch = source_id.rsplit("@", 1)
        else:
            repo_part, branch = source_id, ""

        parts = repo_part.split("/")
        if len(parts) != 2:
            raise ValueError(f"source_id debe ser 'owner/repo' o 'owner/repo@branch'. Recibido: '{source_id}'")
        owner, repo = parts

        # Parámetros
        ext_param = params.get("extensions", "")
        extensions = {e.strip().lower().lstrip(".") for e in ext_param.split(",")} if ext_param else _DEFAULT_EXTENSIONS
        sub_path   = params.get("path", "").strip("/")
        max_files  = int(params.get("max_files", 200))
        excl_param = params.get("exclude_patterns", "")
        excludes   = {p.strip() for p in excl_param.split(",")} if excl_param else _DEFAULT_EXCLUDES

        session = self._get_session()
        if not branch:
            branch = self._get_default_branch(owner, repo, session)

        log.info("[github] batch: %s/%s@%s path=%s exts=%s", owner, repo, branch, sub_path or "/", extensions)

        # Desactivar commit info en batch para evitar N llamadas HTTP extra
        self._include_commit_info = bool(params.get("include_commit_info", False))

        result = BatchResult()

        # Obtener árbol completo del repo (recursivo)
        tree_url = f"{self._api_url}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        try:
            resp = session.get(tree_url, timeout=30)
            if resp.status_code != 200:
                result.errors.append(f"HTTP {resp.status_code} al obtener árbol del repo: {resp.text[:200]}")
                return result
            tree_data = resp.json()
        except Exception as exc:
            result.errors.append(f"Error de red: {exc}")
            return result

        # Filtrar ficheros del árbol
        all_files = [
            item["path"] for item in tree_data.get("tree", [])
            if item.get("type") == "blob"
        ]
        result.total = len(all_files)

        # Aplicar filtros
        filtered = []
        for path in all_files:
            # Filtro de subdirectorio
            if sub_path and not path.startswith(sub_path + "/") and path != sub_path:
                continue
            # Filtro de extensión
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            if ext not in extensions:
                continue
            # Filtro de exclusiones
            if any(excl in path for excl in excludes):
                continue
            filtered.append(path)

        log.info("[github] batch: %d/%d ficheros después de filtros", len(filtered), result.total)

        for file_path in filtered[:max_files]:
            item_id = f"{owner}/{repo}/{file_path}@{branch}"
            try:
                item = self.fetch_item(item_id)
                result.items.append(item)
                result.fetched += 1
            except ValueError as exc:
                log.debug("[github] batch: omitiendo '%s': %s", file_path, exc)
                result.skipped += 1
            except Exception as exc:
                log.error("[github] batch: error en '%s': %s", file_path, exc)
                result.errors.append(f"Error en {file_path}: {exc}")
                result.skipped += 1

        log.info(
            "[github] batch DONE: fetched=%d skipped=%d errors=%d",
            result.fetched, result.skipped, len(result.errors),
        )
        return result

    # ── Helpers privados ───────────────────────────────────────────────────

    @staticmethod
    def _parse_item_id(item_id: str) -> tuple[str, str, str, str]:
        """
        Parsea el item_id al formato (owner, repo, file_path, branch).

        Formatos soportados:
          "owner/repo"                    → branch="", path=""
          "owner/repo@branch"             → branch=branch, path=""
          "owner/repo/path/file.py"       → branch="", path="path/file.py"
          "owner/repo/path/file.py@branch" → branch=branch, path="path/file.py"
        """
        branch = ""
        if "@" in item_id:
            item_id, branch = item_id.rsplit("@", 1)

        parts = item_id.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"item_id inválido: '{item_id}'. Formato: 'owner/repo' o 'owner/repo/ruta'")

        owner = parts[0]
        repo  = parts[1]
        path  = "/".join(parts[2:]) if len(parts) > 2 else ""

        return owner, repo, path, branch

    def _find_readme(self, owner: str, repo: str, branch: str, session) -> str:
        """Encuentra el README del repositorio."""
        for candidate in ["README.md", "readme.md", "README.rst", "README.txt", "README"]:
            url = f"{self._api_url}/repos/{owner}/{repo}/contents/{candidate}"
            params = {"ref": branch} if branch else {}
            resp = session.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return candidate
        return ""

    def _get_default_branch(self, owner: str, repo: str, session) -> str:
        """Obtiene la rama por defecto del repositorio."""
        url = f"{self._api_url}/repos/{owner}/{repo}"
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("default_branch", "main")
        except Exception:
            pass
        return "main"

    def _get_last_commit(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str,
        session,
    ) -> tuple[str, str, str]:
        """Obtiene el último commit que tocó el fichero."""
        url = f"{self._api_url}/repos/{owner}/{repo}/commits"
        params = {"path": path, "per_page": 1}
        if branch:
            params["sha"] = branch
        try:
            resp = session.get(url, params=params, timeout=10)
            if resp.status_code == 200 and resp.json():
                commit = resp.json()[0]
                message = (commit.get("commit", {}).get("message", "") or "").split("\n")[0][:120]
                author  = (commit.get("commit", {}).get("author", {}) or {}).get("name", "")
                date    = (commit.get("commit", {}).get("author", {}) or {}).get("date", "")
                return message, author, date
        except Exception:
            pass
        return "", "", ""

    def _serialize(
        self,
        repo: str,
        path: str,
        branch: str,
        language: str,
        size_bytes: int,
        url: str,
        last_commit: str,
        last_author: str,
        last_date: str,
        content: str,
    ) -> str:
        """Serializa al formato canónico .github_file."""
        lines = ["## Metadata"]
        lines.append(f"repo: {repo}")
        lines.append(f"path: {path}")
        if branch:      lines.append(f"branch: {branch}")
        if language:    lines.append(f"language: {language}")
        lines.append(f"size_bytes: {size_bytes}")
        if url:         lines.append(f"url: {url}")
        if last_commit: lines.append(f"last_commit: {last_commit}")
        if last_author: lines.append(f"last_author: {last_author}")
        if last_date:   lines.append(f"last_date: {last_date}")

        lines.append("")
        lines.append("## Contenido")
        lines.append(content)

        return "\n".join(lines)
