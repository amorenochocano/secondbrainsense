"""
subtypes.py
-----------
Detección de subtipos dentro de cada tipo de fichero.
Devuelve un subtype string (o None) que el builder usa para enriquecer el prompt.

Los subtipos se detectan analizando el contenido tras el preprocesado.
Esto permite prompts más finos sin hardcodear, manteniendo la modularidad.
"""

import re
import json


# ===========================================================================
# DOCX/PDF subtypes
# ===========================================================================

_RFP_KEYWORDS = (
    "petición de oferta", "rfp", "request for proposal",
    "oferta técnica", "oferta económica", "condiciones contractuales",
    "criterios de valoración", "alcance del servicio",
)
_ACTA_KEYWORDS = (
    "acta de reunión", "asistentes", "convocados", "orden del día",
    "acuerdos adoptados", "minuta",
)
_SPEC_KEYWORDS = (
    "especificación funcional", "requisitos funcionales", "casos de uso",
    "criterios de aceptación", "historia de usuario",
)
_PROCEDIMIENTO_KEYWORDS = (
    "procedimiento operativo", "instrucción técnica", "manual de operación",
    "pasos a seguir", "responsable de la actividad",
)


def detect_doc_subtype(content: str) -> str | None:
    """Detecta subtipo de un documento (PDF/DOCX) por presencia de keywords."""
    if not content:
        return None
    text_low = content[:5000].lower()  # cabecera del doc suele tener pistas

    def count_hits(keywords):
        return sum(1 for kw in keywords if kw in text_low)

    scores = {
        "rfp":            count_hits(_RFP_KEYWORDS),
        "acta":           count_hits(_ACTA_KEYWORDS),
        "spec":           count_hits(_SPEC_KEYWORDS),
        "procedimiento":  count_hits(_PROCEDIMIENTO_KEYWORDS),
    }

    best = max(scores.items(), key=lambda x: x[1])
    return best[0] if best[1] >= 2 else None


# ===========================================================================
# JSON subtypes
# ===========================================================================

def detect_json_subtype(raw_text: str) -> str | None:
    """Detecta subtipo de JSON por análisis estructural."""
    try:
        data = json.loads(raw_text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # ADF / Fabric pipeline
    resources = data.get("resources", [])
    if isinstance(resources, list) and resources:
        first = resources[0]
        if isinstance(first, dict) and "activities" in first.get("properties", {}):
            return "fabric_pipeline"

    if "activities" in data.get("properties", {}):
        return "fabric_pipeline"

    # JSON Schema
    if data.get("$schema") or (data.get("type") == "object" and "properties" in data):
        return "schema"

    # OpenAPI / Swagger
    if data.get("openapi") or data.get("swagger") or "paths" in data:
        return "openapi"

    # package.json
    if "dependencies" in data and ("name" in data or "version" in data):
        return "package_manifest"

    # pyproject style (TOML viene como dict tras parseo distinto, pero por si acaso)
    if "tool" in data or "project" in data:
        return "project_config"

    return "config_generic"


# ===========================================================================
# MD subtypes
# ===========================================================================

_ADR_PATTERNS = (
    r"^#+\s*(contexto|context)\b",
    r"^#+\s*(decisión|decision)\b",
    r"^#+\s*(consecuencias|consequences)\b",
    r"^#+\s*(alternativas|alternatives)\b",
    r"^#+\s*status\b",
)
_README_PATTERNS = (
    r"^#+\s*(installation|instalación|install)\b",
    r"^#+\s*(usage|uso|getting started)\b",
    r"^#+\s*(features|características)\b",
    r"^#+\s*(license|licencia)\b",
)
_PASSPORT_PATTERNS = (
    r"^#\s+📌\s+summary",
    r"^#\s+🧩\s+core knowledge",
    r"^#\s+🧠\s+key insights",
)


def detect_md_subtype(content: str) -> str | None:
    """Detecta subtipo de markdown por patrones de headers."""
    if not content:
        return None
    text_low = content.lower()

    def hits(patterns):
        return sum(
            1 for p in patterns
            if re.search(p, text_low, re.MULTILINE | re.IGNORECASE)
        )

    # Pasaporte existente (caso meta — el contenido ya es un pasaporte)
    if hits(_PASSPORT_PATTERNS) >= 2:
        return "passport_existente"

    if hits(_ADR_PATTERNS) >= 2:
        return "adr"

    if hits(_README_PATTERNS) >= 2:
        return "readme"

    return None


# ===========================================================================
# TXT subtypes
# ===========================================================================

_LOG_PATTERNS = (
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",   # timestamps ISO
    r"\b(ERROR|WARN|INFO|DEBUG|FATAL|TRACE)\b",   # niveles de log
    r"Traceback|Exception|\bat\s+[\w.]+\(",       # stack traces
)
_SPEC_LINEAL_PATTERNS = (
    r"^\s*(?:REQ|RF|RNF)[-_]?\d+",                # requisitos numerados
    r"^\s*\d+\.\d+(\.\d+)?\s+\w",                 # numeración jerárquica
    r"^\s*(?:Debe|Deberá|Must|Shall)\s+",          # lenguaje de spec
)


def detect_txt_subtype(content: str) -> str | None:
    """Detecta subtipo de TXT por patrones."""
    if not content:
        return None
    sample = content[:3000]

    log_hits = sum(1 for p in _LOG_PATTERNS if re.search(p, sample, re.MULTILINE))
    if log_hits >= 2:
        return "log"

    spec_hits = sum(1 for p in _SPEC_LINEAL_PATTERNS if re.search(p, sample, re.MULTILINE))
    if spec_hits >= 2:
        return "spec_lineal"

    # Heurística simple para "nota": párrafos sin patrones técnicos
    paragraphs = [p for p in sample.split("\n\n") if len(p) > 50]
    if len(paragraphs) >= 2:
        return "nota"

    return "dump"


# ===========================================================================
# PY subtypes
# ===========================================================================

def detect_py_subtype(content: str) -> str | None:
    """Detecta subtipo de Python por patrones de código."""
    if not content:
        return None
    sample = content[:5000]

    # Tests
    if re.search(r"^\s*(def\s+test_|class\s+Test\w+|@pytest\.fixture|import\s+pytest)",
                 sample, re.MULTILINE):
        return "tests"

    # CLI script
    if re.search(r"argparse|click\.command|@app\.command|if __name__\s*==", sample):
        return "cli"

    # Web service / API
    if re.search(r"@app\.(route|get|post|put|delete)|@router\.|FastAPI\(|Flask\(",
                 sample):
        return "web_service"

    # Config (pydantic/dataclasses puro)
    if re.search(r"BaseSettings|BaseModel|@dataclass", sample) and \
       not re.search(r"def\s+\w+\s*\([^)]*\):", sample):
        return "config"

    return None  # módulo genérico


# ===========================================================================
# IPYNB subtypes
# ===========================================================================

def detect_ipynb_subtype(content: str) -> str | None:
    """Detecta subtipo de notebook por patrones."""
    if not content:
        return None
    sample = content[:5000].lower()

    if "import pyspark" in sample or "from pyspark" in sample or "spark.sql" in sample:
        return "pyspark_pipeline"

    if any(kw in sample for kw in ("import pandas", "pd.read_", "df.")):
        if any(kw in sample for kw in ("seaborn", "matplotlib", "plotly", "plt.")):
            return "analisis_eda"
        return "data_processing"

    if any(kw in sample for kw in ("sklearn", "tensorflow", "torch", "xgboost",
                                    "model.fit", "model.predict")):
        return "ml_experiment"

    if sample.count("def ") >= 5:
        return "biblioteca_funciones"

    return None


# ===========================================================================
# Despachador principal
# ===========================================================================

_DETECTORS = {
    "pdf":   detect_doc_subtype,
    "docx":  detect_doc_subtype,
    "json":  detect_json_subtype,
    "md":    detect_md_subtype,
    "txt":   detect_txt_subtype,
    "py":    detect_py_subtype,
    "ipynb": detect_ipynb_subtype,
}


def detect_subtype(file_type: str, content: str) -> str | None:
    """
    Detecta subtipo dentro del tipo de fichero dado.

    Args:
        file_type: extensión (con o sin punto)
        content:   texto del documento (preprocesado o crudo)

    Returns:
        subtipo identificado o None
    """
    ft = file_type.lower().lstrip(".")
    detector = _DETECTORS.get(ft)
    if not detector:
        return None
    return detector(content)
