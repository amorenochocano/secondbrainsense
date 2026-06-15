"""extractors/__init__.py
Exporta ExtractorFactory (SB-2.6) y mantiene get_extractor_for_extension
para compatibilidad con el código existente (routers/ingest.py, etc.).
"""
from .factory import ExtractorFactory
from .base import BaseExtractor


class _CleaningExtractorWrapper:
    """
    Wrapper que aplica UniversalCleaner sobre el resultado de extract().
    Devuelve la misma interfaz que el extractor original.
    Transparente para el código existente: sólo sobreescribe extract().
    """
    def __init__(self, extractor: BaseExtractor):
        self._extractor = extractor

    def extract(self, source: str) -> list[dict]:
        blocks = self._extractor.extract(source)
        return self._extractor._clean_blocks(blocks)

    def __getattr__(self, name):
        # Delegar cualquier otro método/atributo al extractor original
        return getattr(self._extractor, name)


def get_extractor_for_extension(ext: str):
    """
    Retorna una instancia del extractor para la extensión dada (ej: '.pdf')
    envuelta con UniversalCleaner.

    El extractor resultante tiene la misma interfaz que los extractores
    originales pero su método extract() garantiza que todos los bloques
    pasan por UniversalCleaner antes de devolverse:
      - fix_encoding, normalize_unicode, remove_control_chars
      - detect_language, detect_sensitive_data, score_quality

    Retorna None si no hay extractor registrado para la extensión.
    """
    ext = ext if ext.startswith(".") else f".{ext}"
    extractor_class = ExtractorFactory._MAP.get(ext.lower())
    if extractor_class is None:
        return None
    return _CleaningExtractorWrapper(extractor_class())


__all__ = ["ExtractorFactory", "get_extractor_for_extension", "BaseExtractor"]

