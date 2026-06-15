"""
sharepoint_client.py
--------------------
Descarga ficheros de SharePoint usando Microsoft Graph API con token
obtenido desde el Azure CLI local (az account get-access-token).

Requisitos:
  - Azure CLI instalado (az) y sesión activa (az login)
  - Permisos de usuario sobre el fichero en SharePoint
  - En Docker: montar ~/.azure como volumen read-only y az instalado en la imagen

Uso:
    from app.brain.sharepoint_client import download_sharepoint_file
    tmp_path, filename = download_sharepoint_file("https://company.sharepoint.com/.../file.pdf")
    # ... procesar tmp_path ...
    os.remove(tmp_path)
"""
import base64
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def _get_graph_token() -> str:
    """
    Obtiene un Bearer token para Microsoft Graph API usando el Azure CLI.
    Requiere que el usuario haya ejecutado 'az login' previamente.
    """
    try:
        result = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://graph.microsoft.com"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        token = data.get("accessToken")
        if not token:
            raise RuntimeError("az CLI devolvió respuesta sin accessToken")
        log.info("Token Graph API obtenido correctamente vía az CLI")
        return token
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else ""
        raise RuntimeError(
            f"Error al obtener token con az CLI. ¿Has ejecutado 'az login'?\n"
            f"Detalle: {stderr}"
        ) from exc
    except FileNotFoundError:
        raise RuntimeError(
            "Azure CLI ('az') no encontrado en el PATH. "
            "Instálalo: https://learn.microsoft.com/cli/azure/install-azure-cli"
        ) from None
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Respuesta inesperada de az CLI: {exc}") from exc


def _encode_sharepoint_url(url: str) -> str:
    """
    Codifica la URL de SharePoint en el formato requerido por Graph API /shares.
    Formato: u!<base64url_sin_padding>
    """
    b64 = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b64}"


def _extract_filename_from_response(resp: requests.Response, fallback_url: str) -> str:
    """Extrae el nombre del fichero de la cabecera Content-Disposition o de la URL."""
    cd = resp.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        # Soporta: filename="foo.pdf" y filename*=UTF-8''foo.pdf
        for part in cd.split(";"):
            part = part.strip()
            if part.lower().startswith("filename="):
                name = part.split("=", 1)[1].strip().strip('"').strip("'")
                if name:
                    return name
    # Fallback: extraer de la URL antes del '?'
    clean_url = fallback_url.split("?")[0].rstrip("/")
    name = Path(clean_url).name
    if name and "." in name:
        return name
    return "sharepoint_document"


def download_sharepoint_file(sharepoint_url: str, filename: str = None) -> tuple[str, str]:
    """
    Descarga un fichero de SharePoint por su URL de navegador.

    Args:
        sharepoint_url: URL completa del fichero en SharePoint.
        filename:       Nombre a usar como source (opcional).
                        Si se omite, se extrae de las cabeceras HTTP o de la URL.

    Returns:
        Tupla (ruta_tempfile, nombre_fichero).
        El tempfile debe ser eliminado por el llamador tras procesar.

    Raises:
        RuntimeError: si la autenticación falla, no hay permisos, o el fichero no existe.
    """
    token = _get_graph_token()
    headers = {"Authorization": f"Bearer {token}"}

    encoded = _encode_sharepoint_url(sharepoint_url)
    graph_url = f"https://graph.microsoft.com/v1.0/shares/{encoded}/driveItem/content"

    log.info("Descargando fichero de SharePoint vía Graph API: %s", sharepoint_url)
    resp = requests.get(
        graph_url,
        headers=headers,
        allow_redirects=True,
        timeout=120,
        stream=True,
    )

    if resp.status_code == 401:
        raise RuntimeError(
            "Token expirado o credenciales inválidas. Ejecuta 'az login' para renovar la sesión."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "Sin permisos para acceder al fichero. "
            "Verifica que tu usuario tiene acceso en SharePoint."
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "Fichero no encontrado en SharePoint. Verifica que la URL es correcta."
        )
    resp.raise_for_status()

    # Resolver nombre definitivo
    final_name = filename or _extract_filename_from_response(resp, sharepoint_url)
    ext = Path(final_name).suffix.lower()
    if not ext:
        ext = ".pdf"

    # Descargar a fichero temporal
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
    finally:
        tmp.close()

    log.info("Fichero '%s' descargado correctamente a '%s'", final_name, tmp.name)
    return tmp.name, final_name
