"""
brain/prompts/type_specs.py
============================
Fichas técnicas por tipo de fichero. SOLO DATOS — sin prompts.

PROPÓSITO
---------
Cada TypeSpec declara las características de un tipo de documento que el
planner, builder y synthesizer necesitan para tomar decisiones. Separa
"qué sé del tipo" de "cómo se lo digo al LLM" (que está en __init__.py).

CAMPOS
------
  extension:    extensión canónica sin punto ('py', 'docx', 'csv'...)

  unidad:       qué es la unidad mínima de conocimiento para Core Knowledge.
                Ejemplo: 'función', 'sección del índice', 'columna', 'actividad'

  naturaleza:   'homogéneo' | 'heterogéneo'
                Homogéneo:  estructura uniforme y repetitiva (py, sql, csv, drawio).
                            El modelo puede procesar el doc completo sin degradar.
                            → single-call es suficiente para calidad óptima.
                Heterogéneo: estructura narrativa con secciones de contenido distinto
                            (docx, pdf, pptx, ipynb, md, json, html, txt).
                            El modelo degrada si ve demasiado texto mezclado.
                            → quality_trigger activa chunked cuando el doc es denso.

  quality_trigger: chars a partir de los cuales un doc HETEROGÉNEO degrada calidad
                en single-call o 2-calls aunque quepa en el contexto del modelo.
                El modelo puede leer el doc completo pero al pedirle que genere
                Core Knowledge sobre 50K chars de texto variado, tiende a resumir
                superficialmente en lugar de documentar en profundidad.
                NONE para tipos homogéneos (nunca aplica quality_trigger).

                Valores calibrados empíricamente:
                  32_000: tipos de documento narrativo (docx, pdf, pptx, md, html, txt)
                          → más de 32K chars = 8K tokens de input denso para el LLM
                  20_000: ipynb (código + markdown mezclado, más difícil de razonar)
                  15_000: json heterogéneo (pipelines complejos con muchas actividades)
                  None:   homogéneos (py, sql, csv, xlsx, drawio, web)

  usage_style:  'código' | 'consulta' | 'proceso' | 'navegación'
                Hint para el builder sobre cómo orientar Usage y Practical Usage.

  ck_format:    plantilla concisa de cada subsección ### del Core Knowledge.
                El builder la inyecta en el prompt para guiar la estructura.

  source_primary: qué bloque del processed_text es la fuente principal de docs.
                  Ayuda al LLM a saber dónde buscar primero.

  prohibitions: lista de cosas que NO pedir al LLM para este tipo.
                Evita instrucciones contraproducentes en el prompt.

  has_subtypes: True si el tipo tiene subtipos con prompts especializados.
                Ejemplo: json → pipeline_adf | schema | config | openapi

CÓMO AÑADIR UN TIPO NUEVO
--------------------------
1. Añade una entrada en _SPECS con los campos obligatorios.
2. Define naturaleza y quality_trigger según la tabla de calibración.
3. Añade las instrucciones del tipo en _TYPE_INSTRUCTIONS_FOCUSED en __init__.py.
4. Si tiene subtipos, añade lógica en subtypes.py y pon has_subtypes=True.
5. Añade el preprocesador correspondiente en preprocessing/ si es necesario.
6. Añade _MAX_CHARS para el tipo en preprocessing/__init__.py.

Todo lo demás (planner, synthesizer, ingest_router) se adapta automáticamente.

QUALITY_TRIGGER — CALIBRACIÓN
-------------------------------
El objetivo es que cada chunk de Core Knowledge tenga ~10K chars de input:
  - El modelo ve una sección temática completa
  - Tiene espacio de output suficiente (~25K tokens libres para llama3.1:8b)
  - Genera subsecciones ### con profundidad real, no resúmenes

Si quality_trigger=32K y chunk_size=10K → un RFP de 50K genera 5 chunks:
  Chunk 1: Descripción general + Antecedentes (~10K)
  Chunk 2: Alcance + Modelo operativo (~10K)
  Chunk 3: Subservicios técnicos Azure/Wintel/PP (~10K)
  Chunk 4: ANS + Penalizaciones (~10K)
  Chunk 5: Criterios + Planificación (~10K)
Cada chunk produce subsecciones ### detalladas del contenido real de esa sección.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TypeSpec:
    """Ficha técnica de un tipo de fichero. Ver docstring del módulo."""

    # Identidad
    extension: str

    # Concepto fundamental
    unidad: str
    naturaleza: str          # "homogéneo" | "heterogéneo"
    usage_style: str         # "código" | "consulta" | "proceso" | "navegación"

    # Quality trigger — cuándo chunked mejora la calidad aunque el doc quepa
    # None = nunca aplica (tipos homogéneos)
    # int  = umbral en chars para tipos heterogéneos
    quality_trigger: Optional[int]

    # Generación
    ck_format: str
    source_primary: str
    prohibitions: tuple

    # Holístico
    summary_hint: str
    insights_hint: str
    usage_hint: str
    pitfalls_hint: str

    # Detección de subtipos
    has_subtypes: bool = False


# ===========================================================================
# CONSTANTE GLOBAL — quality_trigger por defecto para heterogéneos
# Sobreescribible por tipo si necesita un valor distinto.
# ===========================================================================

_QT_HETEROGENEO_DEFAULT = 32_000  # 32K chars ≈ 8K tokens de input denso
_QT_IPYNB               = 20_000  # código+markdown mezclado: más difícil
_QT_JSON_HETERO         = 15_000  # pipelines con muchas actividades
_QT_NONE                = None    # homogéneos: nunca chunked por calidad


# ===========================================================================
# REGISTRO DE FICHAS POR TIPO
# ===========================================================================

_SPECS: dict[str, TypeSpec] = {

    # ── CÓDIGO ─────────────────────────────────────────────────────────────────
    # Homogéneos: estructura repetitiva (función1, función2...).
    # El modelo maneja bien el conjunto aunque sean muchas funciones.
    # quality_trigger=None → single-call siempre suficiente.

    "py": TypeSpec(
        extension="py",
        unidad="función o clase pública",
        naturaleza="homogéneo",
        usage_style="código",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### `nombre()`\n"
            "- **Propósito**: qué hace en 1-2 frases\n"
            "- **Parámetros**: nombre: tipo → descripción\n"
            "- **Retorna**: tipo y descripción\n"
            "- **Cuándo usar**: contexto vs cuándo NO usar"
        ),
        source_primary="docstrings del bloque '## Documentación'",
        prohibitions=("audiencia humana", "complejidad algorítmica abstracta"),
        summary_hint="qué módulo o script es, qué capa del sistema sirve, si es importable o ejecutable",
        insights_hint="patrones de diseño detectados, efectos secundarios, estado global, acoplamiento",
        usage_hint="import + ejemplo mínimo REAL con código de las funciones documentadas",
        pitfalls_hint="excepciones no capturadas, dependencias de entorno, side effects silenciosos",
    ),

    "ipynb": TypeSpec(
        extension="ipynb",
        unidad="función documentada o paso del pipeline",
        naturaleza="heterogéneo",
        usage_style="código",
        quality_trigger=_QT_IPYNB,  # código+markdown mezclado: chunked antes
        ck_format=(
            "### `nombre()`\n"
            "- **Propósito**: qué hace\n"
            "- **Parámetros clave**: nombre → para qué sirve\n"
            "- **Retorna**: qué devuelve o produce\n"
            "- **Cuándo usar**: caso típico"
        ),
        source_primary="celdas markdown del bloque '## Documentación'",
        prohibitions=("invención de funciones que no estén en firmas",),
        summary_hint="tipo de notebook (biblioteca, pipeline ETL, análisis, ingesta), contexto de uso",
        insights_hint="decisiones de diseño, trade-offs entre funciones, patrones de uso combinado",
        usage_hint="flujo paso a paso con las funciones documentadas",
        pitfalls_hint="parámetros obligatorios, dependencias de Spark/Fabric, celdas con estado",
    ),

    "sql": TypeSpec(
        extension="sql",
        unidad="CTE, vista o fase lógica",
        naturaleza="homogéneo",
        usage_style="consulta",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### CTE/Vista `nombre`\n"
            "- **Qué selecciona**: descripción de los datos\n"
            "- **Lógica clave**: joins, filtros, agregaciones\n"
            "- **Depende de**: tablas fuente o CTEs previas"
        ),
        source_primary="comentarios -- del bloque '## DOCUMENTACIÓN'",
        prohibitions=("ejemplo de uso en Python", "audiencia"),
        summary_hint="qué tabla/vista produce, granularidad, para qué proceso",
        insights_hint="lógica de negocio en filtros, criterios de deduplicación, decisiones de join",
        usage_hint="cuándo ejecutar, dependencias previas (tablas que deben existir)",
        pitfalls_hint="joins que multiplican filas, NULLs en claves, filtros que eliminan datos válidos",
    ),

    # ── DOCUMENTO RICO ─────────────────────────────────────────────────────────
    # Heterogéneos con quality_trigger=32K: chunked cuando el doc es denso.

    "pdf": TypeSpec(
        extension="pdf",
        unidad="sección del índice (TOC)",
        naturaleza="heterogéneo",
        usage_style="proceso",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Título exacto de sección del TOC]\n"
            "- Contenido real de esa sección\n"
            "- Datos, cifras o decisiones explícitas\n"
            "- Condiciones, requisitos o criterios mencionados"
        ),
        source_primary="bloque '## Índice del documento' como esqueleto + contenido",
        prohibitions=(
            "ejemplos de código inventados",
            "inventar secciones que no estén en el TOC",
        ),
        summary_hint="tipo de documento (RFP, informe, manual, paper), propósito, audiencia, fecha si disponible",
        insights_hint="conclusiones implícitas, decisiones que el doc no explicita, riesgos no enumerados",
        usage_hint="cuándo consultar, qué decisión ayuda a tomar, quién debe leerlo",
        pitfalls_hint="secciones incompletas, datos sin fecha de validez, referencias a documentos externos",
        has_subtypes=True,
    ),

    "docx": TypeSpec(
        extension="docx",
        unidad="sección del documento",
        naturaleza="heterogéneo",
        usage_style="proceso",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Título exacto de sección]\n"
            "- Contenido real (requisitos, descripción, condiciones)\n"
            "- Datos específicos: sistemas, cifras, plazos\n"
            "- Decisiones o criterios explícitos"
        ),
        source_primary="bloque '## Índice del documento' + contenido por secciones",
        prohibitions=(
            "ejemplos de código inventados",
            "inventar secciones que no estén en el documento",
        ),
        summary_hint="tipo (RFP, especificación, acta, procedimiento), propósito, emisor",
        insights_hint="compromisos documentados, dependencias con otros sistemas, riesgos implícitos",
        usage_hint="cuándo consultar, para qué decisión, quién debe seguirlo o aprobarlo",
        pitfalls_hint="secciones sin completar, referencias a anexos externos, ambigüedades en requisitos",
        has_subtypes=True,
    ),

    "pptx": TypeSpec(
        extension="pptx",
        unidad="bloque temático (grupo de slides con coherencia)",
        naturaleza="heterogéneo",
        usage_style="proceso",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Bloque temático]\n"
            "- Mensaje principal del bloque\n"
            "- Datos, métricas o argumentos presentados\n"
            "- Conclusión o llamada a la acción si existe"
        ),
        source_primary="títulos de slides agrupados por tema",
        prohibitions=("código inventado", "análisis técnico profundo"),
        summary_hint="tipo de presentación (propuesta, formación, informe), evento, audiencia, objetivo",
        insights_hint="mensajes que se repiten o enfatizan, datos sorprendentes, propuestas concretas",
        usage_hint="cuándo presentar, qué decisión apoya, cómo actualizar",
        pitfalls_hint="diapositivas solo con imágenes (sin texto), datos sin fecha",
    ),

    "html": TypeSpec(
        extension="html",
        unidad="sección detectada por heading",
        naturaleza="heterogéneo",
        usage_style="consulta",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Heading detectado]\n"
            "- Contenido informativo de esa sección\n"
            "- Tablas o procedimientos si existen"
        ),
        source_primary="secciones extraídas por headings",
        prohibitions=("código inventado",),
        summary_hint="tipo de contenido (docs técnica, informe exportado, guía), fuente/sistema origen",
        insights_hint="información accionable, procedimientos paso a paso, configuraciones relevantes",
        usage_hint="cuándo y cómo consultar, si requiere acceso a sistema externo",
        pitfalls_hint="contenido dinámico perdido, elementos visuales sin texto alternativo",
    ),

    # ── DATOS TABULARES ────────────────────────────────────────────────────────
    # Homogéneos: estructura uniforme (columna1, columna2...).
    # quality_trigger=None → single-call siempre suficiente.

    "csv": TypeSpec(
        extension="csv",
        unidad="columna del dataset",
        naturaleza="homogéneo",
        usage_style="consulta",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### Qué representa cada fila\n"
            "Una frase: cada fila es un/una [entidad]\n\n"
            "### Catálogo de columnas\n"
            "- **nombre_columna** (`tipo`): qué representa, valores típicos\n\n"
            "### Claves y relaciones\n"
            "- Clave candidata, partición natural, posibles joins"
        ),
        source_primary="bloque '## Columnas' del preprocesado",
        prohibitions=("ejemplo de código Python", "audiencia"),
        summary_hint="dominio funcional (ventas, operaciones, RRHH...), granularidad temporal, origen probable",
        insights_hint="columnas con alta cardinalidad, posibles duplicados, calidad de datos en la muestra",
        usage_hint="filtros útiles, agregaciones recomendadas, columnas para pivotado",
        pitfalls_hint="nulos en campos clave, separadores en valores de texto, encoding de fechas",
    ),

    "xlsx": TypeSpec(
        extension="xlsx",
        unidad="hoja (si múltiples) o columna (si una)",
        naturaleza="homogéneo",
        usage_style="consulta",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### Hoja `nombre` (si múltiples hojas)\n"
            "- **Qué contiene**: descripción\n"
            "- **Columnas clave**: las más relevantes con significado\n"
            "- **Relación con otras hojas**: claves compartidas\n\n"
            "(si solo una hoja, usa el formato de CSV)"
        ),
        source_primary="estructura de hojas + cabeceras",
        prohibitions=("código Python", "audiencia"),
        summary_hint="tipo de Excel (catálogo, modelo, informe, config), propósito, hojas principales",
        insights_hint="fórmulas detectadas, reglas de negocio en formato tabular, columnas de control",
        usage_hint="cómo actualizar, dependencias con otros ficheros, frecuencia",
        pitfalls_hint="fórmulas ocultas, hojas protegidas, referencias locales, macros",
    ),

    # ── CONFIG / ORQUESTACIÓN ──────────────────────────────────────────────────

    "json": TypeSpec(
        extension="json",
        unidad="actividad (pipeline) / propiedad (schema) / sección (config)",
        naturaleza="heterogéneo",
        usage_style="código",
        quality_trigger=_QT_JSON_HETERO,  # pipelines con muchas actividades
        ck_format=(
            "(Pipeline) ### Actividad `nombre` [tipo]\n"
            "- **Qué hace**: descripción funcional\n"
            "- **Depende de**: actividades previas\n"
            "- **Parámetros críticos**: los que cambian el resultado\n\n"
            "(Schema) ### Sección `nombre`\n"
            "- **Props obligatorias**: significado\n"
            "- **Props opcionales**: defaults"
        ),
        source_primary="bloque '## Campos documentados' o '## Estructura'",
        prohibitions=("invención de propiedades no presentes",),
        summary_hint="qué artefacto define (pipeline, config, schema), qué proceso orquesta",
        insights_hint="dependencias entre actividades, hardcoded que debería ser variable, puntos de fallo",
        usage_hint="cómo importar/ejecutar, qué configurar antes, entorno requerido",
        pitfalls_hint="timeout por defecto, credenciales sin parametrizar, orden implícito",
        has_subtypes=True,
    ),

    "xml": TypeSpec(
        extension="xml",
        unidad="elemento principal con jerarquía",
        naturaleza="homogéneo",
        usage_style="código",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### Elemento `<nombre>`\n"
            "- **Hijos directos**: subelementos y propósito\n"
            "- **Atributos clave**: nombre → valores válidos\n"
            "- **Restricciones**: validación de schema si existe"
        ),
        source_primary="bloque '## Estructura'",
        prohibitions=("invención de elementos no presentes",),
        summary_hint="qué define (config de servidor, schema, pipeline de integración)",
        insights_hint="namespaces, atributos que cambian comportamiento, defaults implícitos",
        usage_hint="qué sistema lo consume, herramientas de validación",
        pitfalls_hint="encoding, CDATA mal escapado, namespaces conflictivos",
    ),

    # ── MARKDOWN ───────────────────────────────────────────────────────────────

    "md": TypeSpec(
        extension="md",
        unidad="sección H2/H3 del documento original",
        naturaleza="heterogéneo",
        usage_style="consulta",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Sección del .md original]\n"
            "- Síntesis del contenido de esa sección\n"
            "- Si contiene código embebido: trátalo como código"
        ),
        source_primary="estructura propia del .md preservada",
        prohibitions=("eliminar estructura del .md original",),
        summary_hint="tipo (README, ADR, guía operativa, wiki), audiencia, estado",
        insights_hint="aprendizajes documentados, referencias cruzadas, decisiones implícitas",
        usage_hint="cuándo consultar, cómo mantenerlo actualizado",
        pitfalls_hint="información desactualizada, links rotos, TODOs incompletos",
        has_subtypes=True,
    ),

    # ── TEXTO PLANO ────────────────────────────────────────────────────────────

    "txt": TypeSpec(
        extension="txt",
        unidad="elemento según subtipo (patrón error / requisito / decisión)",
        naturaleza="heterogéneo",
        usage_style="consulta",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "(LOG) ### Patrones de error / Secuencia de eventos / Anomalías\n"
            "(ESPECIFICACIÓN) ### [Requisito N]: descripción, condiciones\n"
            "(NOTA) ### Decisiones documentadas / Instrucciones operativas"
        ),
        source_primary="texto plano clasificado por subtipo",
        prohibitions=(),
        summary_hint="tipo detectado + contexto operativo (sistema, proceso, fecha)",
        insights_hint="patrones repetidos, anomalías críticas, decisiones implícitas",
        usage_hint="cómo consultarlo, para qué decisiones sirve",
        pitfalls_hint="ausencia de timestamps, trazas sin contexto, información ambigua",
        has_subtypes=True,
    ),

    # ── VISUAL ─────────────────────────────────────────────────────────────────

    "drawio": TypeSpec(
        extension="drawio",
        unidad="nodo con alto grado de conexión o flujo end-to-end",
        naturaleza="homogéneo",
        usage_style="navegación",
        quality_trigger=_QT_NONE,
        ck_format=(
            "### Componentes principales\n"
            "- **`nombre`**: qué representa, tecnología, rol en el sistema\n"
            "- **Conectado con**: entradas y salidas\n\n"
            "### Flujos principales\n"
            "- Flujo 1: A → B → C → D (descripción)"
        ),
        source_primary="bloque de componentes y flujos del preprocesado",
        prohibitions=("documentar nodos sin conexiones como protagonistas",),
        summary_hint="qué representa (arquitectura, flujo de proceso, modelo), scope, stakeholders",
        insights_hint="cuellos de botella, puntos únicos de fallo, decisiones de diseño visibles",
        usage_hint="cuándo consultar, qué decisiones orienta, quién debe revisarlo ante cambios",
        pitfalls_hint="suposiciones dibujadas como hechos, componentes sin detallar, versión desactualizada",
    ),

    # ── WEB ────────────────────────────────────────────────────────────────────

    "web": TypeSpec(
        extension="web",
        unidad="sección detectada en la página",
        naturaleza="heterogéneo",
        usage_style="consulta",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,
        ck_format=(
            "### [Sección]\n"
            "- Conceptos explicados, procedimientos, referencias"
        ),
        source_primary="secciones extraídas del HTML",
        prohibitions=("código inventado",),
        summary_hint="tipo de contenido, fuente/URL origen, relevancia para el proyecto",
        insights_hint="puntos accionables, diferencias con documentación interna, advertencias",
        usage_hint="cuándo consultar, qué problema resuelve",
        pitfalls_hint="contenido que puede cambiar sin aviso, fecha de publicación relevante",
    ),
    # ── CONECTORES API EXTERNOS ─────────────────────────────────────────────────
    # Heterogéneos: página wiki y ticket tienen secciones de contenido distinto.
    # quality_trigger calibrado para activar chunked antes de degradar.
    # Añadir un nuevo conector = una entrada aquí.

    "confluence": TypeSpec(
        extension="confluence",
        unidad="sección de página wiki",
        naturaleza="heterogéneo",
        usage_style="consulta",
        quality_trigger=_QT_HETEROGENEO_DEFAULT,  # 32K: páginas largas con muchas secciones
        ck_format=(
            "### [Título de sección detectado en el contenido]\n"
            "- Conceptos, procedimientos o decisiones en esa sección\n"
            "- Si hay tablas: qué representa cada columna clave\n"
            "- Si hay código embebido: tratar como Familia CÓDIGO"
        ),
        source_primary="## Contenido (cuerpo de la página) + ## Metadata (labels, space)",
        prohibitions=("inventar secciones que no estén en el contenido",),
        summary_hint="tipo de página (especificación, ADR, guía, runbook), espacio de origen, estado",
        insights_hint="decisiones implícitas, diferencias con documentación local, contradicciones",
        usage_hint="cuándo consultar, qué decisión ayuda a tomar",
        pitfalls_hint="secciones TODO/WIP, referencias a páginas no indexadas, fechas de validez implícitas",
    ),

    "jira_ticket": TypeSpec(
        extension="jira_ticket",
        unidad="el ticket como artefacto completo + comentarios individuales",
        naturaleza="heterogéneo",
        usage_style="proceso",
        quality_trigger=None,  # Los tickets son cortos por naturaleza; single-call suficiente
        ck_format=(
            "### Descripción del ticket\n"
            "- Propósito, alcance, criterios de aceptación\n\n"
            "### Comentarios relevantes\n"
            "- Decisiones técnicas documentadas en los hilos\n"
            "- Bloqueantes, acuerdos y cambios de alcance"
        ),
        source_primary="## Descripción + ## Comentarios (decisiones técnicas en hilos)",
        prohibitions=("inventar criterios de aceptación no documentados",),
        summary_hint="tipo de ticket (Story, Bug, Task, Epic), proyecto, estado, prioridad, sprint",
        insights_hint="decisiones de diseño en comentarios, cambios de alcance, bloqueantes resueltos",
        usage_hint="cuándo consultar, qué implementación documenta, versión en que se resolvió",
        pitfalls_hint="tickets sin descripción, criterios de aceptación incompletos, comentarios de bots filtrados",
    ),

    "github_file": TypeSpec(
        extension="github_file",
        unidad="fichero del repositorio (módulo, script, notebook, config)",
        naturaleza="heterogéneo",
        usage_style="código",
        quality_trigger=None,  # el preprocesador delega en el tipo del lenguaje
        ck_format=(
            "[Hereda el formato del lenguaje: py/sql/md/json según la extensión]\n"
            "### `nombre_funcion()` / ### Sección / ### Actividad..."
        ),
        source_primary="## Contenido (código o texto del fichero) + ## Metadata (repo, path, url)",
        prohibitions=("inventar funciones o secciones no presentes en el fichero",),
        summary_hint="tipo de fichero (script, notebook, config, doc), repositorio y ruta, propósito en el proyecto",
        insights_hint="dependencias detectadas en imports, patrones de diseño, relación con otros ficheros del repo",
        usage_hint="cómo importar o ejecutar, contexto dentro del repositorio",
        pitfalls_hint="fichero desactualizado vs rama, dependencias no documentadas, secrets en el código",
    ),
}


def get_spec(file_type: str) -> "TypeSpec | None":
    """Retorna la ficha técnica para el tipo de fichero dado."""
    ft = file_type.lower().lstrip(".")
    return _SPECS.get(ft)


def list_types() -> list[str]:
    """Lista todos los tipos con ficha registrada."""
    return sorted(_SPECS.keys())
