from abc import ABC, abstractmethod
from dataclasses import dataclass, field

try:
    from rag_lib.layer1_universal import UniversalCleaner as _UniversalCleaner
    _CLEANER = _UniversalCleaner()
except Exception:
    _CLEANER = None


@dataclass
class ExtractedBlock:
    """
    Contrato unificado de salida para todos los extractores.
    Sustituye la inconsistencia previa entre {text, page}, {content, page} y {content, text, page}.

    Campos obligatorios: content, content_type, page.
    Campos opcionales con defaults: language, module, function, metadata.
    Campos generados por Capa 1 (UniversalCleaner, no los rellena el extractor):
        quality_score, transformations_applied, language_detected, sensitive_data_detected.
    """
    # --- Obligatorios ---
    content: str
    content_type: str   # text | code | table | callout | footnote | figure_caption | glossary_entry
    page: int

    # --- Opcionales ---
    language: str = ""          # Para code blocks: python, sql, json, unknown
    module: str = ""            # Para código: nombre del módulo/fichero
    function: str = ""          # Para código: nombre de función/clase
    metadata: dict = field(default_factory=dict)

    # --- Generados por Capa 1 (no rellenar en el extractor) ---
    quality_score: float = 1.0
    transformations_applied: list = field(default_factory=list)
    language_detected: str = ""
    sensitive_data_detected: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializa a dict con alias 'text' para compatibilidad con IngestRouter."""
        return {
            "content":      self.content,
            "content_type": self.content_type,
            "page":         self.page,
            "text":         self.content,   # alias backward-compat
            "language":     self.language,
            "module":       self.module,
            "function":     self.function,
            "metadata":     self.metadata,
            "quality_score":             self.quality_score,
            "transformations_applied":   self.transformations_applied,
            "language_detected":         self.language_detected,
            "sensitive_data_detected":   self.sensitive_data_detected,
        }


class BaseExtractor(ABC):

    def _clean_blocks(self, blocks: list[dict]) -> list[dict]:
        """
        Aplica UniversalCleaner a todos los bloques antes de devolverlos.
        Llamar al final de extract() en lugar de aplicar el cleaner manualmente.

        Hace:
          - fix_encoding: corrige mojibake, HTML entities
          - normalize_unicode: NFC, soft-hyphens, NBSP
          - remove_control_chars: caracteres invisibles
          - normalize_spaces: espacios redundantes
          - detect_language: idioma del bloque (es, en...)
          - detect_sensitive_data: PII (DNI, IBAN, email...)
          - score_quality: puntuación 0.0-1.0 por bloque

        Si UniversalCleaner no está disponible devuelve los bloques sin cambios.
        Los extractores que ya llaman a _CLEANER.apply_to_blocks() directamente
        (docx, pdf) pueden migrar a este método o dejarlo como está — el resultado
        es idéntico.
        """
        if _CLEANER is None:
            return blocks
        return _CLEANER.apply_to_blocks(blocks)

    def _make_block(
        self,
        content: str,
        content_type: str = "text",
        page: int = 1,
        language: str = "",
        module: str = "",
        function: str = "",
        metadata: dict = None,
    ) -> dict:
        """
        Construye un bloque con todos los campos obligatorios garantizados.
        Usar en lugar de construir dicts a mano en los extractores.
        """
        return ExtractedBlock(
            content=content,
            content_type=content_type,
            page=page,
            language=language,
            module=module,
            function=function,
            metadata=metadata or {},
        ).to_dict()

    @abstractmethod
    def extract(self, source: str) -> list[dict]:
        """
        Retorna lista de bloques conformes a ExtractedBlock.to_dict():
            {content, content_type, page, text, language, module, function, metadata, ...}
        Todos los campos 'content' y 'content_type' son obligatorios.

        NOTA: No es necesario llamar a _clean_blocks() manualmente.
        La clase factory (ExtractorFactory) llama a _clean_blocks() sobre
        el resultado de extract() antes de devolverlo al caller.
        Los extractores que ya llaman a _CLEANER directamente (docx, pdf)
        son idempotentes — UniversalCleaner detecta texto ya limpio y no
        aplica transformaciones redundantes.
        """
        raise NotImplementedError
