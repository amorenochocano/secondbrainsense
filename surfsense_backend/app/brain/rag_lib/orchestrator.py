"""
orchestrator.py
---------------
Capa de orquestación — punto de entrada único del pipeline de preprocesado RAG.

Pipeline explícito de 3 fases:
    1. FormatDetector + SubtypeDetector  → formato y subtipo
    2. Extractor (api/extractors/)       → list[dict] de bloques
    3. UniversalCleaner                  → limpieza + quality_score
    4. ChunkAssembler (opcional)         → list[CleanChunk] para IngestRouter

Convivencia con ExtractorFactory durante la migración:
    - process()               → misma interfaz que extractor.extract()
                                compatible con el IngestRouter actual
    - process_to_clean_chunks() → devuelve CleanChunk para el IngestRouter nuevo

NOTA: El pipeline genérico de "cadena de procesadores" del YAML se reserva
para cuando todos los procesadores tengan interfaz .process() unificada (Fase 5+).
Durante la migración se usa la interfaz actual .extract() de los extractores.
"""

import logging
import os
from pathlib import Path

from .detector import FormatDetector, SubtypeDetector, ProcessorRegistry
from .layer1_universal import UniversalCleaner
from .layer4_assembler import ChunkAssembler, CleanChunk

log = logging.getLogger(__name__)

# Ruta por defecto al YAML de registro (relativa a este fichero)
_DEFAULT_REGISTRY = Path(__file__).parent / "config" / "registry_config.yaml"

# Mapa formato → extensión de fichero (para ExtractorFactory)
_FORMAT_TO_EXT: dict[str, str] = {
    "pdf":      ".pdf",
    "docx":     ".docx",
    "xlsx":     ".xlsx",
    "pptx":     ".pptx",
    "xml":      ".xml",
    "json":     ".json",
    "markdown": ".md",
    "csv":      ".csv",
    "txt":      ".txt",
    "html":     ".html",
    "python":   ".py",
    "sql":      ".sql",
    "ipynb":    ".ipynb",
}


class RAGOrchestrator:
    """
    Punto de entrada único del pipeline de preprocesado.
    Compatible con ExtractorFactory — puede convivir durante la migración.
    """

    def __init__(self, registry_path: str | None = None):
        self.format_detector = FormatDetector()
        self.subtype_detector = SubtypeDetector()
        self.registry = ProcessorRegistry(
            registry_path=registry_path or str(_DEFAULT_REGISTRY)
        )
        self._cleaner = UniversalCleaner()
        self.assembler = ChunkAssembler()

    def _get_extractor(self, base_format: str, subtype: str = "generic"):
        """
        Devuelve una instancia del extractor vía ExtractorFactory.
        Para XML con subtipo drawio usa DrawioExtractor directamente.
        """
        from api.extractors.factory import ExtractorFactory

        # DrawIO es un subtipo de XML con extractor propio
        if base_format == "xml" and subtype == "drawio":
            from api.extractors.drawio import DrawioExtractor
            return DrawioExtractor()

        ext = _FORMAT_TO_EXT.get(base_format)
        if not ext:
            log.warning("[rag_lib] Formato '%s' sin extractor mapeado", base_format)
            return None

        try:
            return ExtractorFactory.get(f"file{ext}")
        except ValueError as e:
            log.warning("[rag_lib] ExtractorFactory: %s", e)
            return None

    def process(self, file_path: str) -> list:
        """
        Pipeline completo: detecta formato → extrae → limpia.
        Devuelve list[dict] compatible con el IngestRouter actual.

        Args:
            file_path: Ruta absoluta al fichero a procesar.

        Returns:
            Lista de dicts (ExtractedBlock como dict) limpios y con quality_score.
        """
        file_path = str(file_path)
        base_format = self.format_detector.detect(file_path)
        subtype = self.subtype_detector.detect(file_path, base_format)

        # Cadena de nombres del YAML — solo para log; la ejecución usa fases explícitas
        chain_names = " → ".join(
            self.registry.get_processor_names(base_format, subtype)
        ) or "(sin registro)"
        log.info(
            "[rag_lib] %s | formato=%s subtipo=%s | cadena: %s",
            os.path.basename(file_path), base_format, subtype, chain_names,
        )

        # Fase 1: Extracción
        extractor = self._get_extractor(base_format, subtype)
        if extractor is None:
            log.warning(
                "[rag_lib] Sin extractor para formato='%s'. Devolviendo vacío.", base_format
            )
            return []

        try:
            blocks = extractor.extract(file_path)
        except Exception as exc:
            log.error(
                "[rag_lib] Error en extractor %s para '%s': %s",
                type(extractor).__name__, os.path.basename(file_path), exc,
                exc_info=True,
            )
            return []

        if not blocks:
            log.warning(
                "[rag_lib] Extractor produjo 0 bloques para '%s'",
                os.path.basename(file_path),
            )
            return blocks

        # Fase 2: Limpieza universal
        try:
            blocks = self._cleaner.apply_to_blocks(blocks)
        except Exception as exc:
            log.error(
                "[rag_lib] Error en UniversalCleaner para '%s': %s",
                os.path.basename(file_path), exc,
                exc_info=True,
            )
            # Continuar sin limpieza — mejor datos sin limpiar que nada

        return blocks

    def process_to_clean_chunks(
        self, file_path: str, doc_meta: dict
    ) -> list:
        """
        Pipeline completo con ChunkAssembler al final.
        Devuelve list[CleanChunk] para el IngestRouter nuevo.

        Args:
            file_path: Ruta absoluta al fichero.
            doc_meta:  Metadatos conocidos en preprocesado (source, format,
                       total_pages, etc.). Sin datos de pasaporte.

        Returns:
            Lista de CleanChunk listos para vectorizar.
        """
        blocks = self.process(file_path)
        if not blocks:
            return []

        # Enriquecer doc_meta con formato detectado si no viene incluido
        if "format" not in doc_meta:
            doc_meta = {
                **doc_meta,
                "format": self.format_detector.detect(file_path),
            }
        if "source" not in doc_meta:
            doc_meta = {
                **doc_meta,
                "source": os.path.basename(file_path),
            }

        return self.assembler.assemble(blocks, doc_meta)

