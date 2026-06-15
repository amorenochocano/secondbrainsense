"""
universal_cleaner.py
--------------------
Capa 1 — Limpieza universal de texto para RAG.

Aplica sobre cualquier bloque de texto ANTES de chunking y vectorización:
  1. fix_encoding      — ftfy corrige mojibake, ligaduras rotas, HTML entities
  2. normalize_unicode — NFC + soft hyphens + NBSP
  3. remove_control    — elimina caracteres de control invisibles
  4. normalize_spaces  — espacios y saltos de línea redundantes
  5. detect_language   — lingua-py (más preciso que langdetect en textos cortos)
  6. detect_sensitive  — presidio-analyzer (DNI, IBAN, emails, teléfonos)
  7. score_quality     — quality score compuesto con umbrales por content_type

Diseñado para ser importado desde cualquier extractor actual SIN reescribirlos:

    from rag_lib.layer1_universal import UniversalCleaner
    cleaner = UniversalCleaner()
    for block in blocks:
        result = cleaner.clean(block["content"], content_type=block.get("content_type", "text"))
        block["content"] = result.text
        block["text"] = result.text
        block.setdefault("metadata", {}).update({
            "language":       result.language,
            "sensitive_data": result.sensitive_data_detected,
            "quality_score":  result.quality_score,
        })

Dependencias opcionales — degradan a modo sin esa funcionalidad si no están instaladas:
  ftfy              → fix_encoding hace NFC básico como fallback
  lingua-py         → detect_language devuelve "unknown"
  presidio-analyzer → detect_sensitive devuelve []
"""

import re
import unicodedata
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resultado de la limpieza
# ---------------------------------------------------------------------------

@dataclass
class CleanResult:
    text: str
    language: str = "unknown"
    sensitive_data_detected: list = field(default_factory=list)
    quality_score: float = 1.0
    transformations_applied: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# UniversalCleaner
# ---------------------------------------------------------------------------

class UniversalCleaner:
    """
    Limpieza universal de texto para bloques RAG.

    Instanciar una vez y reutilizar — el detector de idioma y presidio
    se inicializan lazy (solo si están disponibles) y se cachean en la instancia.
    """

    def __init__(self, enable_language_detection: bool = True, enable_pii_detection: bool = True):
        self._enable_lang = enable_language_detection
        self._enable_pii = enable_pii_detection
        self._lang_detector = None   # lazy init
        self._pii_analyzer = None    # lazy init
        self._lang_available = None  # None = no comprobado aún
        self._pii_available = None

    # ------------------------------------------------------------------
    # 1. Fix encoding — ftfy
    # ------------------------------------------------------------------

    def fix_encoding(self, text: str) -> str:
        """
        ftfy.fix_text() corrige mojibake, ligaduras HTML, caracteres mal decodificados.
        Fallback: normalización NFC básica si ftfy no está instalado.
        """
        try:
            import ftfy
            return ftfy.fix_text(text)
        except ImportError:
            if self._lang_available is None:
                log.debug("[UniversalCleaner] ftfy no disponible — usando NFC como fallback")
            return unicodedata.normalize("NFC", text)

    # ------------------------------------------------------------------
    # 2. Normalización unicode
    # ------------------------------------------------------------------

    def normalize_unicode(self, text: str) -> str:
        """
        NFC + eliminar soft hyphens (U+00AD) y NBSP (U+00A0).
        Los soft hyphens son invisibles pero rompen búsquedas de tokens.
        """
        text = unicodedata.normalize("NFC", text)
        text = text.replace("\u00ad", "")   # soft hyphen
        text = text.replace("\u00a0", " ")  # non-breaking space
        text = text.replace("\ufeff", "")   # BOM
        return text

    # ------------------------------------------------------------------
    # 3. Eliminar caracteres de control
    # ------------------------------------------------------------------

    def remove_control_chars(self, text: str) -> str:
        """
        Elimina U+0000–U+001F excepto \\n (0x0A), \\r (0x0D), \\t (0x09).
        También elimina U+007F (DEL).
        """
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # ------------------------------------------------------------------
    # 4. Normalización de espacios
    # ------------------------------------------------------------------

    def normalize_spaces(self, text: str) -> str:
        """Colapsa espacios/tabs múltiples y reduce saltos de línea excesivos."""
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ------------------------------------------------------------------
    # 5. Detección de idioma — lingua-py
    # ------------------------------------------------------------------

    def _get_lang_detector(self):
        """Inicializa lingua lazy. Devuelve None si no está disponible."""
        if self._lang_available is False:
            return None
        if self._lang_detector is not None:
            return self._lang_detector
        try:
            from lingua import LanguageDetectorBuilder
            self._lang_detector = LanguageDetectorBuilder.from_all_languages().build()
            self._lang_available = True
            return self._lang_detector
        except ImportError:
            log.debug("[UniversalCleaner] lingua-py no disponible — language_detected='unknown'")
            self._lang_available = False
            return None

    def detect_language(self, text: str) -> str:
        """
        Detecta el idioma del texto (primeros 500 chars).
        Devuelve código ISO 639-1 en minúsculas ('es', 'en', ...) o 'unknown'.
        lingua-py es más preciso que langdetect en textos cortos (<50 palabras).
        """
        if not self._enable_lang:
            return "unknown"
        detector = self._get_lang_detector()
        if detector is None:
            return "unknown"
        try:
            lang = detector.detect_language_of(text[:500])
            return lang.iso_code_639_1.name.lower() if lang else "unknown"
        except Exception as exc:
            log.debug("[UniversalCleaner] detect_language error: %s", exc)
            return "unknown"

    # ------------------------------------------------------------------
    # 6. Detección de datos sensibles — presidio
    # ------------------------------------------------------------------

    def _get_pii_analyzer(self):
        """Inicializa presidio lazy. Devuelve None si no está disponible."""
        if self._pii_available is False:
            return None
        if self._pii_analyzer is not None:
            return self._pii_analyzer
        try:
            # Silenciar los logs verbosos de presidio (carga de reconocedores, spaCy, etc.)
            import logging as _logging
            for _noisy in ("presidio-analyzer", "presidio_analyzer", "presidio", "spacy"):
                _logging.getLogger(_noisy).setLevel(_logging.ERROR)
            
            # También silenciar warnings a nivel de warnings module
            import warnings
            warnings.filterwarnings("ignore", module="presidio.*")
            warnings.filterwarnings("ignore", module="spacy.*")

            from presidio_analyzer import AnalyzerEngine
            
            # Inicializar con logging silenciado
            self._pii_analyzer = AnalyzerEngine()
            
            self._pii_available = True
            log.debug("[UniversalCleaner] presidio-analyzer inicializado (PII detection activa)")
            return self._pii_analyzer
        except ImportError:
            log.debug("[UniversalCleaner] presidio-analyzer no disponible — PII detection desactivada")
            self._pii_available = False
            return None

    def detect_sensitive_data(self, text: str, language: str = "es") -> list[str]:
        """
        Detecta entidades sensibles: PERSON, EMAIL_ADDRESS, PHONE_NUMBER,
        IBAN_CODE, CREDIT_CARD, NRP (DNI/NIE en español), IP_ADDRESS.
        Devuelve lista de tipos únicos encontrados. Lista vacía si presidio no disponible.
        """
        if not self._enable_pii:
            return []
        analyzer = self._get_pii_analyzer()
        if analyzer is None:
            return []
        # Presidio acepta 'es' o 'en'; fallback a 'en' para otros idiomas
        lang_code = language if language in ("es", "en") else "en"
        try:
            results = analyzer.analyze(text=text[:2000], language=lang_code)
            return list({r.entity_type for r in results})
        except Exception as exc:
            log.debug("[UniversalCleaner] detect_sensitive_data error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 7. Quality score
    # ------------------------------------------------------------------

    def score_quality(
        self,
        text: str,
        content_type: str = "text",
        source_format: str = "",
    ) -> float:
        """
        Quality score compuesto [0.0, 1.0], calibrado por formato.

        Para código y tablas el ratio alfabético no aplica igual que para texto narrativo.
        Para PPTX y HTML: penaliza menos el contenido corto con bullets/métricas/navegación.
        Para texto narrativo (PDF, DOCX, MD): fórmula completa original.
        """
        stripped = text.strip()
        if not stripped:
            return 0.0

        if content_type in ("code", "table", "callout", "footnote"):
            return 1.0 if len(stripped) > 20 else 0.1

        # Calcular ratio alfabético
        alpha_ratio = sum(c.isalpha() for c in stripped) / max(len(stripped), 1)

        # PPTX, HTML: contenido estructurado corto (bullets, títulos, métricas, navegación)
        if source_format in ("pptx", "html"):
            # Si tiene ratio alfabético aceptable (>= 0.25), aceptar incluso si es corto
            if len(stripped) > 20 and alpha_ratio >= 0.25:
                return min(1.0, 0.5 + alpha_ratio * 0.5)  # [0.5, 1.0]
            # Si es muy corto pero tiene mucho alfabético, puede ser un título o métrica
            if alpha_ratio >= 0.40:
                return min(1.0, 0.6 + alpha_ratio * 0.4)  # [0.6, 1.0]
            # Contenido corto con poco alfabético: probablemente UI noise o navegación
            return max(0.1, alpha_ratio)

        # Texto narrativo (PDF, DOCX, MD, etc.): fórmula original (más restrictiva)
        length_score = min(1.0, len(stripped) / 200)
        return min(1.0, alpha_ratio * 1.2 * length_score)

    # ------------------------------------------------------------------
    # Pipeline completo
    # ------------------------------------------------------------------

    def clean(
        self,
        text: str,
        content_type: str = "text",
        mode: str = "text",
        source_format: str = "",
    ) -> CleanResult:
        """
        Pipeline completo de limpieza.

        Args:
            text:         Texto crudo del bloque.
            content_type: 'text' | 'code' | 'table' | 'callout' | ...
                          Usado para ajustar el quality score.
            mode:         'text' | 'code' | 'docstring'.
                          En mode='code' evita ftfy y normalizaciones agresivas.
            source_format: 'html' | 'pptx' | 'pdf' | etc.
                           Usado para calibrar quality score según formato.

        Returns:
            CleanResult con texto limpio + metadata de la limpieza.
        """
        if not text:
            return CleanResult(text="", quality_score=0.0)

        transformations: list[str] = []
        original = text

        if mode != "code":
            # 1. Encoding
            text = self.fix_encoding(text)
            if text != original:
                transformations.append("encoding_fixed")

            # 2. Unicode
            before = text
            text = self.normalize_unicode(text)
            if text != before:
                transformations.append("unicode_normalized")
        else:
            transformations.append("code_mode")

        # 3. Control chars
        before = text
        text = self.remove_control_chars(text)
        if text != before:
            transformations.append("control_chars_removed")

        # 4. Espacios
        before = text
        if mode == "code":
            text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE).strip("\n")
        else:
            text = self.normalize_spaces(text)
        if text != before:
            transformations.append("spaces_normalized")

        # 5. Idioma (sobre texto ya limpio)
        language = self.detect_language(text)

        # 6. PII (sobre texto ya limpio, con idioma detectado)
        sensitive = self.detect_sensitive_data(text, language=language)
        if sensitive:
            transformations.append("pii_detected")

        # 7. Quality score — calibrado por formato
        quality = self.score_quality(text, content_type=content_type, source_format=source_format)

        return CleanResult(
            text=text,
            language=language,
            sensitive_data_detected=sensitive,
            quality_score=quality,
            transformations_applied=transformations,
        )

    def apply_to_blocks(self, blocks: list[dict]) -> list[dict]:
        """
        Método de conveniencia: aplica clean() a una lista de bloques en-place.
        Actualiza content, text, y añade campos de metadata.

        Usar al final del extract() de cualquier extractor actual:

            blocks = self.apply_to_blocks(blocks)
            return blocks
        """
        pii_count = 0
        transformed_count = 0

        for block in blocks:
            raw = block.get("content") or block.get("text", "")
            if not raw:
                continue
            content_type = block.get("content_type", "text")
            source_format = block.get("format", "").lower() or ""
            mode = "code" if content_type == "code" else "text"
            result = self.clean(raw, content_type=content_type, mode=mode, source_format=source_format)

            block["content"] = result.text
            block["text"] = result.text  # backward-compat

            meta = block.setdefault("metadata", {})
            meta["language"] = result.language
            meta["sensitive_data"] = result.sensitive_data_detected
            meta["quality_score"] = result.quality_score
            if result.transformations_applied:
                meta["transformations"] = result.transformations_applied
                transformed_count += 1
            if result.sensitive_data_detected:
                pii_count += 1

        log.debug(
            "[UniversalCleaner] %d bloques procesados — transformados: %d, PII detectado: %d",
            len(blocks), transformed_count, pii_count,
        )
        return blocks
