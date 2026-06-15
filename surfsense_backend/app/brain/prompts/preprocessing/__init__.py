"""
brain/prompts/preprocessing/__init__.py
========================================
Despachador central de preprocesado por tipo de fichero.

ARQUITECTURA
------------
Cada tipo de fichero tiene su propio módulo en este paquete. Cada módulo expone:

  preprocess(full_text, max_chars) -> str
      Preprocesado desde texto plano. Útil cuando no hay bloques del extractor
      o como fallback. Inyecta headers ## para que el chunker semántico corte
      correctamente.

  build_focused(blocks, full_text, max_chars) -> str   [opcional]
      Preprocesado RICO desde los bloques estructurados del extractor. Preserva
      la jerarquía real de secciones (headings de todos los niveles, secciones
      de slides, hojas de Excel, etc.) que el texto plano pierde. Siempre mejor
      que preprocess() cuando hay bloques disponibles.

FLUJO EN EL SYNTHESIZER
-----------------------
  1. Si el tipo tiene build_focused Y hay bloques → build_doc_focused_text()
  2. Si no → preprocess_text() sobre full_text

MAPEO TIPO → MÓDULO
-------------------
  .py      → py.py           preprocess only
  .ipynb   → ipynb.py        preprocess + build_focused
  .sql     → sql.py          preprocess only
  .json    → json.py         preprocess + build_focused (fabric pipeline / schema)
  .xml     → xml.py          preprocess + build_focused
  .drawio  → drawio.py       preprocess only (xml delega aquí si detecta mxfile)
  .pdf     → pdf.py          preprocess + build_focused (headings por font size)
  .docx    → docx.py         preprocess + build_focused (headings reales de Word)
  .pptx    → pptx.py         preprocess + build_focused (secciones lógicas)
  .csv     → csv.py          preprocess + build_focused (catálogo de columnas)
  .xlsx    → xlsx.py         preprocess + build_focused (por hoja)
  .md      → md.py           preprocess only
  .html    → html.py         preprocess only
  .txt     → txt.py          preprocess only

MAX_CHARS POR TIPO
------------------
_MAX_CHARS define el TECHO POR DEFECTO de texto a procesar cuando NO hay
información del modelo activo. El synthesizer puede sobreescribirlo con el
presupuesto real del modelo (profile.context_chars).

IMPORTANTE: estos valores se usan como fallback, NO como límite absoluto.
Si el modelo tiene contexto suficiente (ej. llama3.1:8b con 114K chars),
el synthesizer usará profile.context_chars en su lugar, no este valor.

Los valores reflejan el tamaño típico de texto ÚTIL para el LLM por tipo:
  - Notebooks: 18K (pueden tener 15+ funciones documentadas)
  - Documentos ricos (docx/pdf): 50K (build_focused genera mucho más que el
    full_text plano; con 8K se perdían los subservicios, ANS y tablas)
  - Código (py/sql): 7K-6K (el LLM necesita ver funciones completas)
  - Datos (csv/xlsx): 5K-4K (catálogo + muestra es suficiente)
  - Web/HTML: 7K (el contenido relevante está al principio)

COMPAT LEGACY
-------------
Los alias _preprocess_* se mantienen para que el código existente
(synthesizer.py, etc.) siga funcionando sin cambios.
"""

# ===========================================================================
# MAX_CHARS POR TIPO — techo por defecto cuando el modelo no especifica
# ===========================================================================

_MAX_CHARS: dict[str, int] = {
    # Familia CÓDIGO
    "ipynb":  18000,  # notebooks: 15+ funciones documentadas → necesita espacio
    "py":      7000,  # scripts Python: funciones + docstrings
    "sql":     6000,  # SQL: CTEs, vistas, procedimientos
    # Familia CONFIG / ORQUESTACIÓN
    "json":    8000,  # pipelines ADF/Fabric: N actividades; schemas complejos
    "xml":     6000,  # configs XML: jerarquía de elementos
    "drawio":  6000,  # diagramas: nodos + flujos
    # Familia DOCUMENTOS RICOS
    # NOTA: build_focused puede generar mucho más que el full_text plano.
    # Un DOCX/PDF con 40 secciones necesita 50K para que el LLM vea todo el
    # contenido. Con 8K se perdían subservicios, ANS y tablas en documentos
    # como el RFP DWP. El synthesizer limita además por profile.context_chars.
    "pdf":    50000,  # PDFs: headings por font size + tablas + callouts
    "docx":   50000,  # Word: todos los niveles de heading (H1-H4) + tablas
    "pptx":   20000,  # PPTX: secciones lógicas + slides con contenido
    "html":    7000,  # HTML: secciones P1/P2; contenido relevante al principio
    # Familia DATOS TABULARES
    "xlsx":    5000,  # Excel: catálogo por hoja; no necesita más
    "csv":     5000,  # CSV: catálogo de columnas + muestra de filas
    # Familia MARKDOWN / TEXTO PLANO
    "md":      8000,  # Markdown: README, guías, wikis; pueden ser largos
    "txt":     6000,  # Texto plano: logs, especificaciones, notas
    # Familia WEB
    "web":     7000,  # Páginas web: documentación, artículos técnicos
    "html":    7000,  # alias de web
    # Familia CONECTORES API EXTERNOS
    "confluence":  32000,  # Páginas wiki: pueden ser largas y estructuradas
    "jira_ticket":  5000,  # Tickets: descripción + comentarios; compactos por naturaleza
    "github_file":  20000, # Ficheros de repo: delega en el lenguaje; notebooks pueden ser largos
}

_DEFAULT_MAX_CHARS = 8000  # fallback para tipos no registrados


# ===========================================================================
# IMPORTS — cada módulo expone preprocess() y opcionalmente build_focused()
# ===========================================================================

from .py     import preprocess as _preprocess_py
from .ipynb  import preprocess as _preprocess_ipynb,  build_focused as _build_ipynb_focused
from .sql    import preprocess as _preprocess_sql_mod
from .json   import preprocess as _preprocess_json_mod, preprocess_fabric_pipeline as _preprocess_fabric_pipeline_mod
from .xml    import preprocess as _preprocess_xml_mod
from .drawio import preprocess as _preprocess_drawio_mod
from .pdf    import preprocess as _preprocess_pdf,    build_focused as _build_pdf_focused
from .docx   import preprocess as _preprocess_docx,   build_focused as _build_docx_focused
from .pptx   import preprocess as _preprocess_pptx,   build_focused as _build_pptx_focused
from .csv    import preprocess as _preprocess_csv,    build_focused as _build_csv_focused
from .xlsx   import preprocess as _preprocess_xlsx,   build_focused as _build_xlsx_focused
from .md     import preprocess as _preprocess_md_mod
from .html   import preprocess as _preprocess_html_mod, build_focused as _build_html_focused
from .txt    import preprocess as _preprocess_txt
from .confluence   import preprocess as _preprocess_confluence
from .jira_ticket  import preprocess as _preprocess_jira_ticket
from .github_file  import preprocess as _preprocess_github_file


# ===========================================================================
# preprocess_text() — dispatcher principal sobre full_text plano
# ===========================================================================

def preprocess_text(full_text: str, file_type: str, max_chars: int) -> str:
    """
    Preprocesa texto según el tipo de fichero enrutando al módulo correcto.

    Usado como fallback cuando no hay bloques del extractor, o para tipos
    que no tienen build_focused. Cada módulo inyecta headers ## para que
    el chunker semántico corte en límites de sección.

    Args:
        full_text:  texto raw del documento
        file_type:  extensión sin punto ('docx', 'pdf', 'py', ...)
        max_chars:  límite de chars del resultado (calculado por el synthesizer
                    combinando _MAX_CHARS y el presupuesto del modelo activo)

    Returns:
        Texto preprocesado listo para el LLM, máximo max_chars chars.
    """
    ft = file_type.lower().lstrip(".")

    if ft == "py":                return _preprocess_py(full_text, max_chars)
    if ft == "ipynb":             return _preprocess_ipynb(full_text, max_chars)
    if ft == "sql":               return _preprocess_sql_mod(full_text, max_chars)
    if ft == "json":              return _preprocess_json_mod(full_text, max_chars)
    if ft in ("xml", "drawio"):   return _preprocess_xml_mod(full_text, max_chars)
    if ft == "pdf":               return _preprocess_pdf(full_text, max_chars)
    if ft == "docx":              return _preprocess_docx(full_text, max_chars)
    if ft == "pptx":              return _preprocess_pptx(full_text, max_chars)
    if ft == "csv":               return _preprocess_csv(full_text, max_chars)
    if ft == "xlsx":              return _preprocess_xlsx(full_text, max_chars)
    if ft == "md":                return _preprocess_md_mod(full_text, max_chars)
    if ft in ("html", "web", "htm"): return _preprocess_html_mod(full_text, max_chars)
    if ft == "txt":               return _preprocess_txt(full_text, max_chars)
    if ft == "confluence":         return _preprocess_confluence(full_text, max_chars)
    if ft == "jira_ticket":        return _preprocess_jira_ticket(full_text, max_chars)
    if ft == "github_file":         return _preprocess_github_file(full_text, max_chars)

    # Fallback genérico: truncado directo
    if len(full_text) > max_chars:
        return full_text[:max_chars] + "\n\n[... truncado por longitud ...]"
    return full_text


# ===========================================================================
# build_doc_focused_text() — dispatcher rico desde bloques del extractor
# ===========================================================================

def build_doc_focused_text(blocks: list[dict], full_text: str, ft: str, max_chars: int) -> str:
    """
    Construye texto enriquecido aprovechando los bloques estructurados del extractor.

    Cada extractor produce bloques con metadatos semánticos (sección, página,
    nivel de heading, tipo de contenido) que el texto plano pierde. Esta función
    usa esa información para producir un processed_text más rico que preprocess_text.

    Tipos con build_focused propio:
      ipynb  → 1 bloque por función/par markdown-código, firmas AST
      csv    → catálogo de columnas con tipos inferidos
      xlsx   → 1 bloque por hoja con perfil de columnas
      docx   → headings reales de Word (H1-H4), tablas con contexto de sección
      pdf    → headings detectados por font size, callouts, footnotes
      pptx   → secciones lógicas (nativas o heurística), slides agrupados por ##
      json   → pipeline ADF/Fabric: actividades; schema: propiedades clave
      xml    → jerarquía de elementos, drawio: nodos + flujos

    Para tipos sin build_focused propio (py, sql, md, html, txt, drawio):
      delega en preprocess_text(full_text, ft, max_chars).

    Args:
        blocks:    lista de bloques del extractor [{content, content_type, metadata, ...}]
        full_text: texto plano completo (fallback si blocks está vacío o falla)
        ft:        extensión sin punto
        max_chars: límite de chars (calculado por el synthesizer)

    Returns:
        Texto estructurado con ## por sección, listo para el LLM y el chunker.
    """
    ft = ft.lower().lstrip(".")

    # Tipos con build_focused que usa bloques del extractor
    if ft == "ipynb":               return _build_ipynb_focused(blocks, full_text, max_chars)
    if ft == "csv":                 return _build_csv_focused(full_text, max_chars)
    if ft == "xlsx":                return _build_xlsx_focused(blocks, full_text, max_chars)
    if ft == "docx":                return _build_docx_focused(blocks, full_text, max_chars)
    if ft == "pdf":                 return _build_pdf_focused(blocks, full_text, max_chars)
    if ft == "pptx":                return _build_pptx_focused(blocks, full_text, max_chars)

    # HTML: build_focused usa los bloques del WebExtractor
    if ft in ("html", "web", "htm"):
        return _build_html_focused(blocks, full_text, max_chars)

    # JSON y XML tienen build_focused que extrae campos embebidos
    if ft == "json":
        from .json import build_focused as _build_json_focused
        return _build_json_focused(full_text, max_chars)
    if ft in ("xml", "drawio"):
        from .xml import build_focused as _build_xml_focused
        return _build_xml_focused(full_text, max_chars)

    # Resto: delega en preprocess_text
    return preprocess_text(full_text, ft, max_chars)


# ===========================================================================
# COMPAT LEGACY — aliases para código existente
# ===========================================================================

_preprocess_text            = preprocess_text
_preprocess_code            = _preprocess_py
_preprocess_sql             = _preprocess_sql_mod
_preprocess_json            = _preprocess_json_mod
_preprocess_fabric_pipeline = _preprocess_fabric_pipeline_mod
_preprocess_xml             = _preprocess_xml_mod
_preprocess_drawio_xml      = _preprocess_drawio_mod
_preprocess_document        = _preprocess_docx
_preprocess_html            = _preprocess_html_mod
_preprocess_md              = _preprocess_md_mod


__all__ = [
    "preprocess_text",
    "build_doc_focused_text",
    "_MAX_CHARS",
    "_DEFAULT_MAX_CHARS",
    "_preprocess_text",
    "_preprocess_code",
    "_preprocess_sql",
    "_preprocess_json",
    "_preprocess_fabric_pipeline",
    "_preprocess_xml",
    "_preprocess_drawio_xml",
    "_preprocess_document",
    "_preprocess_html",
    "_preprocess_md",
    "_preprocess_txt",
]
