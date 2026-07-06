"""factory.py
ExtractorFactory — registro central de extractores por extensión de fichero.
Añadir soporte para un nuevo formato = una línea en _MAP.
"""
from pathlib import Path
from .base import BaseExtractor
from .pdf import PDFExtractor
from .docx import DocxExtractor
from .xlsx import XlsxExtractor
from .pptx import PptxExtractor
from .web import WebExtractor
from .python_file import PythonExtractor
from .sql_file import SqlExtractor
from .drawio import DrawioExtractor
from .json_fabric import JsonFabricExtractor
from .ipynb import IpynbExtractor
from .xml_ext import XmlExtractor
from .markdown import MarkdownExtractor
from .csv import CsvExtractor
from .txt import TxtExtractor
from .confluence import ConfluenceExtractor
from .jira_ticket import JiraTicketExtractor
from .github_file import GitHubFileExtractor
from .fallback import FallbackExtractor


class ExtractorFactory:
    """
    Registro central de extractores por extensión de fichero.

    Uso recomendado:
        blocks = ExtractorFactory.extract(filename)

    Esto llama al extractor correcto Y aplica UniversalCleaner sobre todos
    los bloques (encoding, unicode, control chars, idioma, PII, quality score).
    Los extractores docx y pdf ya incluyen el cleaner internamente — en ese
    caso la segunda pasada es idempotente (el texto ya está limpio).
    """

    _MAP = {
        ".pdf":      PDFExtractor,
        ".docx":     DocxExtractor,
        ".xlsx":     XlsxExtractor,
        ".pptx":     PptxExtractor,
        ".html":     WebExtractor,
        ".htm":      WebExtractor,
        ".py":       PythonExtractor,
        ".sql":      SqlExtractor,
        ".ipynb":    IpynbExtractor,
        ".xml":      XmlExtractor,
        ".drawio":   DrawioExtractor,
        ".json":     JsonFabricExtractor,
        ".md":       MarkdownExtractor,
        ".markdown": MarkdownExtractor,
        ".csv":      CsvExtractor,
        ".txt":        TxtExtractor,
        # ── Conectores API externos (formato canónico virtual) ──────────────
        # Añadir un nuevo conector = una línea aquí
        ".confluence":  ConfluenceExtractor,
        ".jira_ticket": JiraTicketExtractor,
        ".github_file": GitHubFileExtractor,
    }

    @classmethod
    def get(cls, filename: str) -> BaseExtractor:
        """Devuelve una instancia del extractor para el formato dado.
        Para formatos no reconocidos devuelve FallbackExtractor en lugar de lanzar excepción.
        """
        ext = Path(filename).suffix.lower()
        extractor_class = cls._MAP.get(ext)
        if not extractor_class:
            return FallbackExtractor()
        return extractor_class()

    @classmethod
    def is_known(cls, ext: str) -> bool:
        """True si existe un extractor específico para la extensión dada."""
        return ext.lower() in cls._MAP

    @classmethod
    def extract(cls, filename: str) -> list[dict]:
        """
        Extrae bloques del fichero y aplica UniversalCleaner sobre todos ellos.

        Este es el punto de entrada recomendado para el flujo de ingesta.
        Garantiza que TODOS los extractores (independientemente de si llaman
        internamente a UniversalCleaner o no) producen bloques limpios con:
          - encoding corregido (ftfy)
          - unicode normalizado (NFC, soft-hyphens, NBSP)
          - caracteres de control eliminados
          - espacios normalizados
          - idioma detectado (metadata['language'])
          - PII detectado (metadata['sensitive_data'])
          - quality_score calculado (metadata['quality_score'])

        Args:
            filename: ruta al fichero a extraer

        Returns:
            lista de bloques limpios {content, content_type, page, metadata, ...}
        """
        extractor = cls.get(filename)
        blocks = extractor.extract(filename)
        return extractor._clean_blocks(blocks)

    @classmethod
    def supported_extensions(cls) -> list[str]:
        return list(cls._MAP.keys())
