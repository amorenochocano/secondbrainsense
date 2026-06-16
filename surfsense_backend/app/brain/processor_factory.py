# processor_factory.py
"""
Router central del pipeline de tres fases.
Mapea extensión de fichero → (extractor, preprocesador, chunker, prompt).

CORRECCIONES respecto al diseño original:
  - Import correcto: app.brain.prompts.preprocessing (no app.brain.preprocessing)
  - extract_from_text(): path alternativo para conectores API (sin fichero en disco)
  - get_metadata_from_blocks(): llama a normalize_tags() del BrainMetadataService
  - search_space_id propagado a todos los bloques como campo de payload

NOTA: El documento propone TYPE_SPECS pero en el código real solo existe get_spec().
Se usa get_spec() como equivalente exacto.
"""
from pathlib import Path
from typing import Optional
from app.brain.extractors import get_extractor_for_extension
# CORRECCIÓN CRÍTICA: el path real en SecondBrainSense es prompts/preprocessing/
from app.brain.prompts.preprocessing import (
    py     as prep_py,
    sql    as prep_sql,
    pdf    as prep_pdf,
    docx   as prep_docx,
    pptx   as prep_pptx,
    xlsx   as prep_xlsx,
    ipynb  as prep_ipynb,
    html   as prep_html,
    md     as prep_md,
    txt    as prep_txt,
    csv    as prep_csv,
    json   as prep_json,
    xml    as prep_xml,
    drawio as prep_drawio,
)
from app.brain.prompts.type_specs import get_spec as _get_spec
import logging

logger = logging.getLogger(__name__)


# Mapa extensión → módulo preprocesador
PREPROCESSOR_MAP = {
    ".py":          prep_py,
    ".sql":         prep_sql,
    ".pdf":         prep_pdf,
    ".docx":        prep_docx,
    ".doc":         prep_docx,
    ".pptx":        prep_pptx,
    ".ppt":         prep_pptx,
    ".xlsx":        prep_xlsx,
    ".xls":         prep_xlsx,
    ".ipynb":       prep_ipynb,
    ".html":        prep_html,
    ".htm":         prep_html,
    ".md":          prep_md,
    ".markdown":    prep_md,
    ".txt":         prep_txt,
    ".csv":         prep_csv,
    ".json":        prep_json,
    ".xml":         prep_xml,
    ".drawio":      prep_drawio,
    # Conectores API (tipos virtuales del ExtractorFactory)
    ".confluence":  prep_md,    # páginas Confluence → tratar como markdown estructurado
    ".jira_ticket": prep_txt,   # tickets Jira → texto plano con estructura
    ".github_file": prep_py,    # ficheros GitHub → inferir según contenido en F5
}


class DocumentProcessor:
    """Encapsula el pipeline de tres fases para un tipo de documento."""

    def __init__(self, extension: str):
        self.extension = extension.lower()
        if not self.extension.startswith("."):
            self.extension = f".{self.extension}"
        self.extractor = get_extractor_for_extension(self.extension)
        self.preprocessor_module = PREPROCESSOR_MAP.get(self.extension)
        # get_spec() es el equivalente real de TYPE_SPECS.get() del documento
        ext_key = self.extension.lstrip(".")
        self.type_spec = _get_spec(ext_key) or {}

    def extract(self, file_path: str, search_space_id: int = 0) -> list[dict]:
        """
        Fase 1 + Fase 2: extracción desde fichero en disco y limpieza universal.
        - El extractor tiene _CleaningExtractorWrapper aplicado → UniversalCleaner automático.
        - Propaga search_space_id a todos los bloques para multi-tenancy.
        """
        if self.extractor is None:
            raise ValueError(f"No hay extractor para extensión: {self.extension}")
        blocks = self.extractor.extract(file_path)
        blocks = self._inject_search_space(blocks, search_space_id)
        logger.info("[processor] Fase 1+2 completada: %d bloques, space=%s", len(blocks), search_space_id)
        return blocks

    def extract_from_text(
        self,
        text: str,
        search_space_id: int = 0,
        metadata: dict | None = None,
    ) -> list[dict]:
        """
        Fase 1 + Fase 2 para conectores API: el texto ya viene procesado.
        Usado por ConnectorBridge (F5) para GitHub, Jira, Confluence, Slack...

        El extractor virtual (ConfluenceExtractor, JiraTicketExtractor, etc.)
        recibe el texto directamente, aplica UniversalCleaner y devuelve bloques.
        Si no hay extractor virtual, construye un bloque mínimo y aplica cleaner.
        """
        from app.brain.rag_lib.layer1_universal import UniversalCleaner
        cleaner = UniversalCleaner()

        if self.extractor is not None and hasattr(self.extractor, "extract_from_text"):
            # Extractor virtual con soporte nativo para texto directo
            blocks = self.extractor.extract_from_text(text, metadata=metadata or {})
        else:
            # Fallback: construir bloque único y aplicar cleaner
            result = cleaner.clean(text, content_type="text")
            blocks = [{
                "content":      result.text,
                "text":         result.text,
                "content_type": "text",
                "page":         1,
                "metadata": {
                    "quality_score":  result.quality_score,
                    "language":       result.language,
                    "sensitive_data": result.sensitive_data_detected,
                    **(metadata or {}),
                },
            }]

        blocks = self._inject_search_space(blocks, search_space_id)
        logger.info("[processor] Fase 1+2 (texto directo): %d bloques, space=%s",
                    len(blocks), search_space_id)
        return blocks

    def preprocess(self, blocks: list[dict]) -> str:
        """Fase 3: preprocesamiento semántico → processed_text con ##/###/####."""
        if self.preprocessor_module is None:
            logger.warning("[processor] Sin preprocesador para %s, usando fallback", self.extension)
            return "\n\n".join(b.get("content", "") for b in blocks if b.get("content"))

        raw_text = "\n\n".join(b.get("content", "") for b in blocks if b.get("content"))
        processed = self.preprocessor_module.preprocess(raw_text)
        logger.info("[processor] Fase 3 completada: %d chars con marcas semánticas", len(processed))
        return processed

    def get_quality_trigger(self) -> Optional[int]:
        """Umbral de chars para activar síntesis chunked (None = tipo homogéneo)."""
        spec = self.type_spec
        if hasattr(spec, "quality_trigger"):
            return spec.quality_trigger
        return spec.get("quality_trigger") if isinstance(spec, dict) else None

    def get_metadata_from_blocks(
        self,
        blocks: list[dict],
        meta_service=None,          # BrainMetadataService opcional (F1.2)
        search_space_id: int = 0,
    ) -> dict:
        """
        Extrae metadata de calidad de los bloques.
        Si se pasa meta_service, normaliza las tags candidatas contra el vocabulario.
        """
        quality_scores = [
            b.get("metadata", {}).get("quality_score", 1.0) for b in blocks
        ]
        has_pii = any(b.get("metadata", {}).get("sensitive_data") for b in blocks)
        languages = list({b.get("metadata", {}).get("language", "unknown") for b in blocks})

        raw_tags = []
        if meta_service is not None:
            for block in blocks:
                raw_tags.extend(block.get("metadata", {}).get("candidate_tags", []))
            normalized_tags = meta_service.normalize_tags(raw_tags, search_space_id=search_space_id)
        else:
            normalized_tags = []

        return {
            "avg_quality_score": sum(quality_scores) / len(quality_scores) if quality_scores else 1.0,
            "has_pii":           has_pii,
            "languages":         languages,
            "total_blocks":      len(blocks),
            "code_blocks":       sum(1 for b in blocks if b.get("content_type") == "code"),
            "normalized_tags":   normalized_tags,
        }

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _inject_search_space(blocks: list[dict], search_space_id: int) -> list[dict]:
        """
        Añade search_space_id a todos los bloques.
        Garantiza que el campo esté disponible para el payload de Qdrant (F2)
        y el filtrado RBAC en el router (F4).
        """
        if not search_space_id:
            return blocks
        for block in blocks:
            block.setdefault("metadata", {})["search_space_id"] = search_space_id
            block["search_space_id"] = search_space_id  # también en raíz para IngestRouter
        return blocks


class DocumentProcessorFactory:
    """Punto de entrada para obtener el processor correcto por nombre de fichero o extensión."""

    @staticmethod
    def get(filename: str) -> "DocumentProcessor":
        """
        Resuelve extensión a partir del nombre de fichero o extensión directa.
        Ejemplos: "mi_script.py", ".py", "ticket.jira_ticket"
        """
        ext = Path(filename).suffix.lower() if "." in filename else filename.lower()
        return DocumentProcessor(ext)

    @staticmethod
    def supported_extensions() -> list[str]:
        return list(PREPROCESSOR_MAP.keys())

    @staticmethod
    def is_supported(filename: str) -> bool:
        ext = Path(filename).suffix.lower()
        return ext in PREPROCESSOR_MAP
