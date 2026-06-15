"""
format_detector.py: Detección de Formato Base (Capa 0)
=======================================================

RESPONSABILIDAD
---------------
Identificar el formato base de un documento analizando magic bytes,
sin depender del nombre del fichero (que puede estar mal o renombrado).

ESTRATEGIA DE DETECCIÓN (en orden)
----------------------------------
1. Magic bytes → formato específico (PDF, JSON, XML, ZIP-based, OLE, etc.)
2. ZIP interno → docx/xlsx/pptx (inspección de marcadores)
3. OLE + extensión → doc/xls/ppt antiguo
4. Extensión → formato (fallback para ambigüedades)
5. unknown → no se puede determinar

FORMATOS SOPORTADOS
-------------------
  Binarios:
    • pdf          → Magic: %PDF
    • docx, xlsx, pptx → Magic: PK (ZIP + marcadores internos)
    • doc, xls, ppt (OLE) → Magic: \xd0\xcf\x11\xe0
  
  Texto/Markup:
    • xml          → Magic: <?xml
    • drawio       → Magic: <mxGraphModel o <mxfile
    • json         → Magic: {, [, o BOM+{ (con variaciones espacios)
    • markdown     → Extensión .md, .markdown
    • html         → Magic: <html... (detectado como xml/text fallback)
  
  Datos:
    • csv          → Extensión .csv
    • txt          → Extensión .txt
    • python       → Extensión .py
    • sql          → Extensión .sql
    • ipynb        → Extensión .ipynb (JSON notebook)
  
  Otros:
    • zip          → ZIP genérico no identificado
    • unknown      → No determinable

CASOES DE USO ESPECIALES
------------------------
  • file.docx sin extensión → Detectado por magic ZIP + marcador interno
  • data.json renombrado como .xyz → Detectado por magic JSON
  • corrupted.pdf → Detectado por magic bytes %PDF
  • .ipynb → Prioridad a extensión (aunque sea JSON internamente)
  • archivo.xlsx → Detectado por ZIP + marcador xl/workbook.xml

RENDIMIENTO
-----------
  • Solo lee los primeros 32 bytes del archivo
  • Búsqueda de magic bytes: O(1) en la mayoría de casos
  • ZIP interno: requiere lectura del índice ZIP (negligible vs tamaño documento)
  • NO carga el contenido completo del archivo

INTEGRACIÓN CON UniversalCleaner
--------------------------------
  UniversalCleaner.process(file_path)
    ↓
  FormatDetector.detect(file_path) → base_format
    ↓
  SubtypeDetector.detect(file_path, base_format) → subtipo
    ↓
  ProcessorRegistry.get_processors(base_format, subtipo) → [procesadores...]

VERSION: 1.1
AUTOR: Second Brain Core
DEPENDENCIAS: zipfile (stdlib), logging (stdlib)
"""
import re
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Magic bytes → formato base
# Orden importa: más específico primero
_MAGIC_BYTES: list[tuple[bytes, str]] = [
    (b"%PDF",          "pdf"),
    (b"PK\x03\x04",    "zip_based"),   # docx / xlsx / pptx — inspección interna
    (b"\xd0\xcf\x11\xe0", "ole"),      # Formato OLE (doc/xls/ppt antiguo)
    (b"<?xml",         "xml"),
    (b"<mxGraphModel", "xml"),         # drawio directo sin wrapper
    (b"<mxfile",       "xml"),         # drawio moderno
    (b"{\n",           "json"),
    (b"{ ",            "json"),
    (b"{\"",           "json"),
    (b"[\n",           "json"),
    (b"[ ",            "json"),
    (b"[{",            "json"),
    (b"\xef\xbb\xbf{", "json"),        # BOM + JSON
    (b"#",             "text_maybe"),  # Markdown o shell script
]

# Marcadores internos en ficheros ZIP para distinguir docx/xlsx/pptx
_ZIP_INTERNAL_MARKERS: list[tuple[str, str]] = [
    ("word/document.xml",    "docx"),
    ("xl/workbook.xml",      "xlsx"),
    ("ppt/presentation.xml", "pptx"),
]

# Extensión → formato cuando los magic bytes no bastan
_EXTENSION_MAP: dict[str, str] = {
    ".pdf":      "pdf",
    ".docx":     "docx",
    ".doc":      "docx",
    ".xlsx":     "xlsx",
    ".xls":      "xlsx",
    ".pptx":     "pptx",
    ".ppt":      "pptx",
    ".xml":      "xml",
    ".drawio":   "xml",
    ".json":     "json",
    ".md":       "markdown",
    ".markdown": "markdown",
    ".csv":      "csv",
    ".txt":      "txt",
    ".html":     "html",
    ".htm":      "html",
    ".py":       "python",
    ".sql":      "sql",
    ".ipynb":    "ipynb",
}


class FormatDetector:
    """
    Detecta el formato base de un documento sin depender del nombre del fichero.
    
    CARACTERÍSTICAS
    ===============
    ✓ Robusto — Magic bytes primero, fallback a extensión
    ✓ Seguro — Nunca lanza excepciones, siempre devuelve string
    ✓ Rápido — Solo lee cabecera (32 bytes)
    ✓ ZIP-aware — Inspecciona contenido interno para docx/xlsx/pptx
    ✓ Logging completo — Trazabilidad de decisión de formato
    
    USO
    ===
    >>> detector = FormatDetector()
    >>> fmt = detector.detect("/path/to/document.bin")
    >>> print(fmt)  # "pdf", "json", "docx", "unknown", etc.
    
    GARANTÍAS
    =========
    • Devuelve siempre un string (nunca None)
    • Los valores devueltos coinciden con registry_config.yaml keys
    • ZIP-based files se inspeccionan internamente si es necesario
    • OLE files se resuelven por extensión cuando magic bytes no bastan
    """

    def detect(self, file_path: str) -> str:
        """
        Detecta y devuelve el formato base del archivo.
        
        ARGUMENTOS
        ==========
        file_path : str
            Ruta absoluta o relativa al archivo a analizar.
            No requiere que la extensión sea correcta.
        
        RETORNA
        =======
        str : Formato base del documento
            Posibles valores: pdf, docx, xlsx, pptx, doc (→docx), xls (→xlsx),
            xml, drawio, json, markdown, csv, txt, html, python, sql, ipynb,
            zip, unknown
        
        COMPORTAMIENTO
        ===============
        • Nunca lanza excepciones (logs warnings en su lugar)
        • Si el archivo no existe → "unknown"
        • Si no se puede leer → "unknown" + warning log
        • Si magic bytes + contenido ambiguo → usa extensión
        • Detecta ZIP internos (docx/xlsx/pptx) si es necesario
        
        EJEMPLO
        =======
        >>> detector = FormatDetector()
        >>> detector.detect("/path/to/document.pdf")     # "pdf"
        >>> detector.detect("/path/to/data")             # "json" (si es JSON)
        >>> detector.detect("/path/to/corrupted.bin")   # "unknown"
        >>> detector.detect("/path/to/file.docx")      # "docx" (ZIP + word/document.xml)
        """
        path = Path(file_path)

        # Algunos formatos JSON-like necesitan prioridad por extensión.
        # Ejemplo: .ipynb tiene magic de JSON pero debe procesarse como notebook.
        ext = path.suffix.lower()
        if ext == ".ipynb":
            return "ipynb"

        # Leer cabecera del fichero
        try:
            with open(file_path, "rb") as f:
                header = f.read(32)
        except OSError as e:
            log.warning("[format_detector] No se puede leer '%s': %s", file_path, e)
            return "unknown"

        # Comparar magic bytes
        for magic, fmt in _MAGIC_BYTES:
            if header[:len(magic)].lower() == magic.lower() or header[:len(magic)] == magic:
                if fmt == "zip_based":
                    result = self._detect_zip_internal(file_path)
                    log.debug(
                        "[format_detector] '%s' → '%s' (ZIP interno)",
                        path.name, result,
                    )
                    return result
                if fmt == "ole":
                    # OLE antiguo — usar extensión para distinguir doc/xls/ppt
                    ext = path.suffix.lower()
                    result = _EXTENSION_MAP.get(ext, "unknown")
                    log.debug(
                        "[format_detector] '%s' → '%s' (OLE + extensión)",
                        path.name, result,
                    )
                    return result
                if fmt == "text_maybe":
                    # '#' puede ser markdown o comentario — delegar a extensión
                    break
                log.debug(
                    "[format_detector] '%s' → '%s' (magic bytes)",
                    path.name, fmt,
                )
                return fmt

        # Fallback a extensión del fichero
        ext = path.suffix.lower()
        detected = _EXTENSION_MAP.get(ext)
        if detected:
            log.debug(
                "[format_detector] '%s' → formato '%s' (por extensión)", file_path, detected
            )
            return detected

        log.debug("[format_detector] '%s' → formato desconocido", file_path)
        return "unknown"

    def _detect_zip_internal(self, file_path: str) -> str:
        """Inspecciona el contenido de un ZIP para identificar docx/xlsx/pptx."""
        import zipfile
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                names = z.namelist()
                for marker, fmt in _ZIP_INTERNAL_MARKERS:
                    if marker in names:
                        log.debug(
                            "[format_detector] ZIP '%s' → '%s' (marcador: %s)",
                            file_path, fmt, marker
                        )
                        return fmt
            # ZIP genérico no reconocido
            return "zip"
        except zipfile.BadZipFile:
            log.warning("[format_detector] '%s' tiene magic ZIP pero no es ZIP válido", file_path)
            # Fallback a extensión
            ext = Path(file_path).suffix.lower()
            return _EXTENSION_MAP.get(ext, "unknown")
        except Exception as e:
            log.warning("[format_detector] Error inspeccionando ZIP '%s': %s", file_path, e)
            return "unknown"
