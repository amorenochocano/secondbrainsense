"""
brain/connectors/__init__.py
Exporta los componentes públicos de la capa de conectores.
"""
from .base import BaseConnector, FetchedItem, BatchResult
from .factory import ConnectorFactory

__all__ = [
    "BaseConnector",
    "FetchedItem",
    "BatchResult",
    "ConnectorFactory",
]
