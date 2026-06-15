"""
brain/connectors/factory.py
============================
Registro central de conectores de API externos.

Añadir soporte para un nuevo conector = una línea en _MAP.
Misma filosofía que ExtractorFactory para ficheros locales.

CÓMO REGISTRAR UN NUEVO CONECTOR
----------------------------------
1. Crea brain/connectors/nuevo_connector.py heredando de BaseConnector.
2. Añade una entrada en _MAP:
       "nombre_conector": NuevoConnector,
3. Eso es todo. El router y los endpoints lo detectan automáticamente.

MATCHING
--------
El conector se identifica por su connector_id (clave del _MAP), que debe
coincidir con:
  - La URL del endpoint: /ingest/connector/{connector_id}/...
  - El connector_id property del BaseConnector implementado
  - El prefijo del slug: "{connector_id}-{titulo}"
"""

from __future__ import annotations
import logging
from .base import BaseConnector

log = logging.getLogger(__name__)

# Importaciones lazy para no romper el arranque si faltan dependencias
# de un conector específico (ej. requests no instalado)
def _import_confluence():
    from .confluence_connector import ConfluenceConnector
    return ConfluenceConnector

def _import_jira():
    from .jira_connector import JiraConnector
    return JiraConnector

def _import_github():
    from .github_connector import GitHubConnector
    return GitHubConnector


class ConnectorFactory:
    """
    Registro central de conectores de API externos.

    Uso recomendado:
        connector = ConnectorFactory.get("confluence")
        item = connector.fetch_item("123456789")

    El conector devuelto ya está instanciado y listo para usar.
    """

    # Registro: connector_id → callable que devuelve la clase del conector.
    # Se usa callable (función) en lugar de clase directamente para permitir
    # importaciones lazy y evitar errores de importación si faltan dependencias.
    _MAP: dict[str, callable] = {
        "confluence":  _import_confluence,
        "jira":        _import_jira,
        "github":      _import_github,
        # Nuevos conectores: añadir aquí una línea
        # "notion":   _import_notion,
        # "teams":    _import_teams,
    }

    @classmethod
    def get(cls, connector_id: str) -> BaseConnector:
        """
        Retorna una instancia del conector para el ID dado.

        Args:
            connector_id: identificador del conector (ej. "confluence", "jira").
                          Case-insensitive.

        Returns:
            Instancia del BaseConnector correspondiente, ya inicializada.

        Raises:
            ValueError: si el connector_id no está registrado en _MAP.
            ImportError: si las dependencias del conector no están instaladas.
            EnvironmentError: si las credenciales requeridas no están configuradas.
        """
        cid = connector_id.lower().strip()
        loader = cls._MAP.get(cid)
        if loader is None:
            available = sorted(cls._MAP.keys())
            raise ValueError(
                f"Conector '{cid}' no registrado. "
                f"Disponibles: {available}"
            )
        try:
            connector_class = loader()
            return connector_class()
        except ImportError as exc:
            raise ImportError(
                f"No se pudieron cargar las dependencias del conector '{cid}': {exc}. "
                f"Instala las dependencias requeridas (ej. pip install requests)."
            ) from exc

    @classmethod
    def list_connectors(cls) -> list[str]:
        """Retorna la lista de connector_ids registrados."""
        return sorted(cls._MAP.keys())

    @classmethod
    def is_registered(cls, connector_id: str) -> bool:
        """True si el connector_id está registrado en _MAP."""
        return connector_id.lower().strip() in cls._MAP
