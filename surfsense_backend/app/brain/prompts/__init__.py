"""
brain/prompts/__init__.py
--------------------------
Paquete de prompts v6 — sin dependencia de prompts_legacy.py.

Contiene:
  1. Constantes globales (SYNTHESIS_CHUNK_TRIGGER, _MAX_CHARS, etc.)
  2. Plantillas de prompts (FOCUSED, OVERVIEW, CORE_KNOWLEDGE_CHUNK)
  3. Instrucciones por tipo de fichero (_TYPE_INSTRUCTIONS_FOCUSED)
  4. Utilidades: terminología canónica, sanitizado
  5. Funciones públicas LEGACY:
       - get_focused_synthesis_prompt
       - get_overview_synthesis_prompt
       - get_chunk_synthesis_prompt
       - get_synthesis_prompt (deprecated)
       - split_doc_text_for_chunked
  6. Re-export de preprocesado modular (paquete preprocessing/)
  7. API v5: get_synthesis_calls, plan_synthesis, type_specs, etc.

El código existente (synthesizer.py, passport_builder.py, etc.) sigue funcionando
sin cambios — todos los imports anteriores se resuelven aquí.
"""

import os as _os
import re as _re
import json as _json

from brain.vocabulary import VOCAB_LIST


# ===========================================================================
# RE-EXPORT DE PREPROCESADO MODULAR
# ===========================================================================
# El paquete preprocessing/ contiene un fichero por tipo de documento.
# Aquí re-exportamos todo para mantener la API que usa el resto del sistema.

from .preprocessing import (
    preprocess_text,
    build_doc_focused_text,
    _MAX_CHARS,
    _DEFAULT_MAX_CHARS,
    _preprocess_text,           # alias legacy
    _preprocess_code,
    _preprocess_sql,
    _preprocess_json,
    _preprocess_fabric_pipeline,
    _preprocess_xml,
    _preprocess_drawio_xml,
    _preprocess_document,
    _preprocess_html,
    _preprocess_md,
    _preprocess_txt,
)


# ===========================================================================
# PROMPTS GLOBALES — plantillas usadas por todas las funciones de síntesis
# ===========================================================================

FOCUSED_SYNTHESIS_PROMPT = """Eres un experto en gestión del conocimiento técnico.

Tienes un pasaporte semántico PARCIAL que ya contiene:
- Frontmatter (id, title, type, domain, source, metadata)
- Entities (funciones, tablas, actividades)
- Tags y drill-down triggers

TU TAREA: Completa ÚNICAMENTE las secciones narrativas marcadas como [LLM: ...].

SECCIONES A COMPLETAR:

1. # 📌 Summary
   - Resumen ejecutivo en 5-10 líneas
   - Qué es, para qué sirve, en qué contexto
   - Si el contexto tiene documentación explícita, úsala literalmente

2. # 🧩 Core Knowledge
   - REGLA CRÍTICA: NO hagas 3 bullets genéricos que resuman todo el documento
   - La instrucción específica del tipo de fichero (ver abajo) define el formato exacto
   - Mínimo de profundidad: si el documento tiene N funciones/secciones/columnas,
     Core Knowledge debe documentar cada una individualmente, no colapsarlas

3. # 🧠 Key Insights
   - Lo que NO está explícito pero se infiere del diseño o la estructura
   - Decisiones técnicas: por qué se hizo así y no de otra forma
   - Trade-offs, optimizaciones, patrones detectados
   - Mínimo 4 puntos específicos, no observaciones triviales

4. # 🔗 Relationships
   - Dependencias: "Depends on" (artefactos/componentes necesarios)
   - Relacionado: "Related to" (referencias a otros documentos)
   - Habilita: "Enables" (qué hace posible este artefacto)

5. # ⚙️ Practical Usage
   - Cómo se usa en la práctica con pasos concretos
   - Ejemplo mínimo funcional basado en el contenido real del documento
   - Parámetros clave con sus valores típicos

6. # ⚠️ Pitfalls / Risks
   - Problemas reales detectados en el código/documento (no genéricos)
   - Dependencias implícitas que pueden fallar silenciosamente
   - Condiciones de borde conocidas
   - Mínimo 3 puntos específicos al documento

7. # 📄 Source Extract
   - Cita textual breve (5-10 líneas) del fragmento más representativo del documento fuente
   - Texto literal del documento, NO paráfrasis
   - IMPORTANTE: NO incluyas las cabeceras internas del contexto ("## Documentación",
     "## Firmas de funciones", "## Estructura", "## Campos documentados") en la cita
     — esas son marcas de preprocesado, no forman parte del documento original
   - Para notebooks/código: cita el párrafo introductorio o el fragmento más ilustrativo
     de la lógica principal (sin encabezados ## del contexto)

RESTRICCIONES ABSOLUTAS:
- Mantén el frontmatter EXACTAMENTE como está (NO MODIFIQUES tags ni entities)
- En # 📄 Source Extract NO uses bloques de código triple-backtick (```); cita en texto plano
- Responde SOLO en Markdown, sin meta-comentarios
- NUNCA generes bullets vacíos como "- Funciona con X" sin explicar el cómo/por qué

REGLA DE ORO: El contexto del documento ya contiene la información — tu trabajo es
estructurarla y articularla con profundidad, no inventar ni resumir superficialmente.
"""

OVERVIEW_SYNTHESIS_PROMPT = """Eres un experto en gestión del conocimiento técnico.

Tienes un pasaporte semántico PARCIAL que ya contiene:
- Frontmatter (id, title, type, domain, source, metadata)
- Entities (funciones, tablas, actividades) — lista COMPLETA del documento
- Tags y drill-down triggers

TU TAREA: Completa las secciones narrativas marcadas como [LLM: ...],
EXCEPTO # 🧩 Core Knowledge (que se procesará por separado con el contenido completo).

SECCIONES A COMPLETAR:

1. # 📌 Summary
   - Resumen ejecutivo en 5-10 líneas
   - Qué es, para qué sirve, en qué contexto
   - Si el contexto tiene documentación explícita, úsala literalmente

2. # 🧩 Core Knowledge
   IMPORTANTE: NO generes contenido aquí.
   Escribe EXACTAMENTE esta línea y nada más: [procesando-chunked]

3. # 🧠 Key Insights
   - Lo que NO está explícito pero se infiere del diseño o la estructura
   - NOTA: el frontmatter ya lista TODAS las entities del documento completo — úsalas
   - Decisiones técnicas: por qué se hizo así y no de otra forma
   - Trade-offs, optimizaciones, patrones detectados
   - Mínimo 4 puntos específicos, no observaciones triviales

4. # 🔗 Relationships
   - Dependencias: "Depends on" (artefactos/componentes necesarios)
   - Relacionado: "Related to" (referencias a otros documentos)
   - Habilita: "Enables" (qué hace posible este artefacto)

5. # ⚙️ Practical Usage
   - Cómo se usa en la práctica con pasos concretos
   - Ejemplo mínimo funcional basado en el contenido real del documento
   - Parámetros clave con sus valores típicos

6. # ⚠️ Pitfalls / Risks
   - Problemas reales detectados en el código/documento (no genéricos)
   - Dependencias implícitas que pueden fallar silenciosamente
   - Condiciones de borde conocidas
   - Mínimo 3 puntos específicos al documento

7. # 📄 Source Extract
   - Cita textual breve (5-10 líneas) del fragmento más representativo del documento fuente
   - Texto literal del documento, NO paráfrasis
   - IMPORTANTE: NO incluyas las cabeceras internas del contexto ("## Documentación",
     "## Firmas de funciones", "## Estructura", "## Campos documentados") en la cita
   - Para notebooks/código: cita el párrafo introductorio más ilustrativo

RESTRICCIONES ABSOLUTAS:
- Mantén el frontmatter EXACTAMENTE como está (NO MODIFIQUES tags ni entities)
- En # 📄 Source Extract NO uses bloques de código triple-backtick (```); cita en texto plano
- Responde SOLO en Markdown, sin meta-comentarios
- NUNCA generes bullets vacíos como "- Funciona con X" sin explicar el cómo/por qué
"""

CORE_KNOWLEDGE_CHUNK_PROMPT = """Eres un experto en documentación técnica.

Recibes un fragmento de documentación de un artefacto técnico.

TU ÚNICA TAREA: Para cada unidad de contenido presente en el fragmento,
produce una subsección "###" con su documentación detallada.

REGLAS CRÍTICAS:
- Produce ÚNICAMENTE subsecciones ###, sin headers # ni ##
- NO añadas texto introductorio, títulos de sección ni conclusiones
- NO repitas el nombre del fichero ni del tipo de documento
- Extrae la información LITERALMENTE del fragmento — no inventes ni parafrasees
- Si el fragmento documenta N funciones/secciones → produce exactamente N subsecciones ###
- Si el fragmento tiene muy poco contenido útil, produce al menos una subsección con lo disponible
{type_instructions}
"""


# ===========================================================================
# CONFIG SÍNTESIS FRAGMENTADA
# ===========================================================================

SYNTHESIS_CHUNK_TRIGGER: int = int(_os.getenv("SYNTHESIS_CHUNK_TRIGGER", "6000"))
SYNTHESIS_CHUNK_SIZE:    int = int(_os.getenv("SYNTHESIS_CHUNK_SIZE",    "3000"))
SYNTHESIS_OVERVIEW_EXCERPT: int = int(_os.getenv("SYNTHESIS_OVERVIEW_EXCERPT", "2000"))


# ===========================================================================
# TERMINOLOGÍA CANÓNICA
# ===========================================================================

_TERMINOLOGY_PRIORITY = [
    "microsoft-fabric", "azure-data-factory", "azure-synapse", "databricks",
    "pyspark", "spark-sql", "delta-lake", "data-engineering", "data-pipeline",
    "medallion", "etl", "python", "sql", "docker", "kubernetes", "ci-cd",
    "terraform", "rest-api", "jira", "confluence", "xray", "testing",
    "test-automation", "architecture", "machine-learning", "llm", "rag",
    "aws", "s3", "power-bi",
]
_TERMINOLOGY_HINT = ", ".join(t for t in _TERMINOLOGY_PRIORITY if t in set(VOCAB_LIST))


def _canonical_terminology_note() -> str:
    if not _TERMINOLOGY_HINT:
        return ""
    return (
        "\nTERMINOLOGÍA CANÓNICA:\n"
        "Cuando menciones tecnologías o conceptos en la narrativa, usa su nombre "
        "canónico en lugar de variantes (p. ej. 'pyspark' no 'spark', "
        "'microsoft-fabric' no 'fabric', 'ci-cd' no 'ci/cd'). Términos preferentes: "
        f"{_TERMINOLOGY_HINT}."
    )


# ===========================================================================
# INSTRUCCIONES POR TIPO DE FICHERO (para Core Knowledge focalizado)
# ===========================================================================

_TYPE_INSTRUCTIONS_FOCUSED: dict[str, str] = {

    "ipynb": """
FAMILIA: CÓDIGO — Jupyter/PySpark Notebook

Estructura que genera el preprocesador (IpynbExtractor + build_focused):
  - "### `nombre_funcion(args)`":  función documentada — FUENTE PRIMARIA
  - Texto bajo el ###:             docstring y descripción de la función (celda markdown)
  - Bloque ```python```:          código real de la función (celda de código)
  El contexto NO tiene '## Documentación' separado — la documentación ya está en cada ###.

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Para CADA ### `funcion()` del contexto, documenta una subsección:

  ### `nombre_funcion()`
  - **Propósito**: qué hace en 1-2 frases (del texto del ### en el contexto)
  - **Parámetros clave**: nombre → para qué sirve (sólo los relevantes)
  - **Retorna**: qué devuelve o produce
  - **Cuándo usar**: caso de uso típico vs alternativa si existe

Si hay 7 funciones ### en el contexto → produce 7 subsecciones ### en Core Knowledge.
Extrae la información del texto que sigue a cada ### (es el docstring real).

OTRAS SECCIONES:
- Summary: tipo de notebook (biblioteca, pipeline ETL, análisis, ingesta) + contexto de uso
- Key Insights: decisiones de diseño, trade-offs entre funciones, patrones de uso combinado
- Usage: flujo de trabajo típico paso a paso usando las funciones documentadas
- Pitfalls: parámetros obligatorios, dependencias de Spark/Fabric/Delta, celdas con estado
""",

    "py": """
FAMILIA: CÓDIGO — Script Python (módulo, biblioteca, CLI, pipeline)

El contexto puede incluir estos bloques generados por el preprocesador:
  - "## MODULO": docstring del módulo + __all__ + __version__ — FUENTE PRIMARIA de descripción
  - "## IMPORTS": imports agrupados (Stdlib / Terceros / Locales) — dependencias del módulo
  - "## CONSTANTES DE MODULO": variables UPPER_CASE de nivel módulo
  - "### `NombreClase`(herencia) [@decoradores]": cabecera de clase con métodos ####
  - "### `nombre_funcion(args) -> ReturnType`": función top-level con firma completa
  - "#### `metodo(args)` [@decorador]": método de clase

IDENTIFICA EL TIPO DE MÓDULO antes de documentar:

  A) BIBLIOTECA DE FUNCIONES (contiene varias ### `funcion()`)
     → Documenta CADA función pública:
       ### `nombre_funcion(arg1: tipo, arg2: tipo = default) -> ReturnType`
       - **Propósito**: qué hace en 1-2 frases (del docstring si existe)
       - **Parámetros clave**: nombre → para qué sirve (solo los relevantes)
       - **Retorna**: qué devuelve o produce
       - **Cuándo usar**: caso de uso típico

  B) MÓDULO CON CLASES (contiene ### `NombreClase`)
     → Documenta CADA clase con sus métodos principales:
       ### `NombreClase(BaseClass)` [@dataclass/@abstractclass]
       - **Propósito**: qué representa o hace
       - **`__init__`**: parámetros y estado que inicializa
       - **Métodos públicos**: para cada #### del contexto:
         - `metodo(args)` [@decorador]: qué hace, cuándo usar

  C) SCRIPT/CLI (sin clases, pocas funciones, lógica directa)
     → Documenta el flujo principal y las funciones de entrada

  D) MÓDULO DE CONSTANTES (contiene ## CONSTANTES DE MODULO, pocas funciones)
     → Documenta las constantes agrupadas por dominio semántico

REGLA CRÍTICA:
  - Usa ## MODULO como descripción del módulo
  - Usa ## IMPORTS para documentar dependencias en Relationships
  - Documenta CADA ### del contexto — no las colapses
  - Los type hints en la firma son información valiosa — úsalos
  - Para funciones async: indica explícitamente que es asíncrona y su patrón de uso
  - Para @property: documenta qué valor expone y cuándo cambia
  - Para @staticmethod/@classmethod: indica la diferencia de uso vs métodos de instancia
  - Funciones _privadas que aparezcan como #### en clases: incluirlas brevemente

OTRAS SECCIONES:
- Summary: tipo de módulo (biblioteca, pipeline, CLI, config), qué capa del sistema sirve, si es importable o ejecutable
- Key Insights: patrones de diseño usados (factory, decorator, dataclass), dependencias críticas de Spark/Fabric, estado global, efectos secundarios
- Usage: import + ejemplo mínimo REAL con las funciones/clases del contexto
- Pitfalls: excepciones no capturadas, dependencias de entorno (SparkSession, credenciales), funciones con estado, side effects silenciosos
""",

    "sql": """
FAMILIA: CÓDIGO — SQL Script (T-SQL, Spark SQL, Synapse, procedimiento almacenado, DDL)

El contexto puede incluir:
  - "## DOCUMENTACION": comentarios del script — FUENTE PRIMARIA (descripción, parámetros, tipos)
  - "### PROCEDURE `nombre`": cabecera del procedimiento con firma y parámetros
  - "### Fase N: NOMBRE": fases lógicas internas del procedimiento
  - "### TABLE `nombre`", "### VIEW `nombre`": DDL de tablas y vistas
  - "### CTE `nombre`": Common Table Expressions
  - "### INSERT/UPDATE/DELETE": operaciones DML

IDENTIFICA EL TIPO DE SCRIPT antes de documentar:

  A) PROCEDIMIENTO ALMACENADO (contiene ### PROCEDURE o ### Fase N:)
     → Documenta CADA FASE como subsección:
       ### Fase N: [nombre de la fase]
       - **Qué hace**: descripción funcional de esta fase
       - **Operaciones clave**: CREATE/INSERT/UPDATE/SELECT relevantes en esta fase
       - **Condiciones y validaciones**: IF/WHERE/THROW importantes
       - **Efecto**: qué produce o modifica esta fase
     → Documenta también los PARÁMETROS del procedimiento si están en ## DOCUMENTACION:
       ### Parámetros
       - **@nombre** (tipo, default): descripción y propósito

  B) SCRIPT CON CTEs (contiene ### CTE `nombre`)
     → Documenta cada CTE:
       ### CTE `nombre`
       - **Qué selecciona**: descripción de los datos
       - **Lógica clave**: joins, filtros, agregaciones importantes
       - **Depende de**: tablas fuente o CTEs previas

  C) DDL PURO (contiene ### TABLE, ### VIEW, ### INDEX)
     → Documenta cada artefacto:
       ### TABLE `schema.nombre`
       - **Propósito**: qué representa esta tabla en el modelo de datos
       - **Columnas clave**: las más importantes con su tipo y significado
       - **Restricciones**: PKs, FKs, UNIQUE, CHECK relevantes

  D) DML STANDALONE (INSERT/UPDATE/DELETE sin procedimiento)
     → Documenta por operación y su propósito

REGLA CRÍTICA: El contexto ya tiene el script dividido en secciones ###.
Documenta CADA sección ### del contexto. No las inventes ni las fusiones.
Si el contexto tiene 9 fases → produce 9 subsecciones ### en Core Knowledge.

OTRAS SECCIONES:
- Summary: qué tipo de script es (SP, DDL, CTE, DML), qué produce o hace, plataforma (T-SQL/Fabric/Synapse)
- Key Insights: modos de operación (ej: table vs json), validaciones de integridad, rollback automático, optimizaciones
- Usage: cómo ejecutar con parámetros reales, prerequisitos, entorno requerido
- Pitfalls: tipos de datos no soportados, NULLs en claves, tablas temporales que no se limpian, timeouts
""",

    "json": """
FAMILIA: CONFIG/ORQUESTACIÓN — JSON (pipeline ADF/Fabric, schema, configuración)

Estructura que genera el preprocesador (json.py) según tipo detectado:

  Si es PIPELINE ADF/Fabric (ARM Template o actividades directas):
    - "## Pipeline: nombre":           cabecera con variables y parámetros
    - "## Flujo de ejecución":         árbol de dependencias → actividad
    - "### Actividad `nombre` [tipo]": cada actividad con params y dependencias

  Si es JSON SCHEMA ($schema o type=object+properties):
    - "## JSON Schema: título":         cabecera con descripción y obligatorios
    - "### Propiedad `nombre` (tipo)": cada propiedad con descripción y valores

  Si es CONFIG GENÉRICA:
    - "## Configuración":               cabecera
    - "### `clave`":                    cada clave de primer nivel con valor

  Si tiene campos doc embebidos (description, name, label...):
    - "## Campos documentados":         campos con documentación extraída
    - "## Estructura":                  estructura JSON a continuación

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Identifica el tipo por las secciones ## del contexto:

  Si ## Pipeline + ### Actividad → PIPELINE:
    Documenta ## Flujo de ejecución como orientación de secuencia.
    Documenta CADA ### Actividad:
    ### Actividad `nombre` [tipo]
    - **Qué hace**: descripción funcional
    - **Depende de**: actividades previas (del contexto)
    - **Parámetros críticos**: los más importantes

  Si ## JSON Schema + ### Propiedad → SCHEMA:
    ### Propiedad `nombre` (tipo)
    - **Descripción**: qué representa
    - **Obligatorio/Opcional**: y valor por defecto si existe

  Si ## Configuración + ### `clave` → CONFIG:
    ### `nombre_clave`
    - **Valor/Estructura**: descripción
    - **Impacto**: qué cambia si se modifica

OTRAS SECCIONES:
- Summary: qué artefacto define (pipeline, schema, config), qué proceso orquesta o configura
- Key Insights: dependencias entre actividades, hardcoded que debería ser variable, orden implícito
- Usage: cómo importar/ejecutar, qué configurar antes, entorno requerido
- Pitfalls: timeout por defecto en actividades, credenciales sin parametrizar, propiedades obligatorias sin default
""",

    "xml": """
FAMILIA: CONFIG/ORQUESTACIÓN — XML (configuración, schema XSD, datos estructurados)

Estructura que genera el preprocesador (xml.py):
  - "## Elemento raíz `<tag>` (N hijos)": cabecera con el root y número de hijos directos
  - "### Elemento `<tag>`":               cada hijo directo del root
    - "- **atrib**: valor"                atributos relevantes (no style/class/id)
    - "- **Contenido**: texto"            texto interno si existe
    - "- **Hijos**: tag1, tag2..."        subelementos de nivel 2
  Si tiene comentarios XML:
    - "## Comentarios":                   comentarios <!-- --> — FUENTE PRIMARIA
  Si es drawio (.drawio): delega en drawio.py (ver prompt drawio)

REGLA CRÍTICA PARA CORE KNOWLEDGE:

  ### Elemento raíz `<nombre>`  (del ## del contexto)
  - **Propósito**: qué representa en el sistema
  - **Número de hijos**: N (del contexto)

  Para CADA ### Elemento del contexto:
  ### Elemento `<nombre>`
  - **Atributos clave**: nombre → significado (del contexto)
  - **Contenido**: qué datos contiene
  - **Hijos**: subelementos y su función

Si hay ## Comentarios úsalos como fuente primaria de descripción del propósito.

OTRAS SECCIONES:
- Summary: qué define (config de servidor, schema, pipeline de integración), sistema que lo consume
- Key Insights: namespaces relevantes, atributos que cambian el comportamiento, defaults implícitos
- Usage: qué sistema lo consume, cómo modificarlo, cómo validarlo
- Pitfalls: encoding, CDATA mal escapado, namespaces conflictivos, schema externo requerido
""",

    "csv": """
FAMILIA: DATOS TABULARES — CSV (dataset, catálogo, fichero de datos)

Estructura que genera el preprocesador (csv.py):
  - "## Catálogo de columnas (N cols, M filas)": cabecera con dimensiones
  - "### Columna `nombre` (tipo)": cada columna con tipo inferido (numeric/date/categorical)
  - "## Claves y relaciones": clave candidata, partición natural, joins posibles
  Si hay muchas columnas (>20): una ### por columna
  Si hay pocas columnas (<20): lista compacta de - **nombre** (tipo): descripción

REGLA CRÍTICA PARA CORE KNOWLEDGE:

  ### Qué representa cada fila
  [Una frase: "cada fila es un/una [pedido / lectura de sensor / cliente / transacción]"]
  [Infiere del nombre del fichero y las columnas del contexto]

  ### Catálogo de columnas
  Para CADA columna del contexto documenta:
  - **nombre_columna** (`tipo`): qué representa, valores típicos o rango esperado

  ### Claves y relaciones
  - Clave candidata: columna(s) que identifican una fila de forma única
  - Partición natural: fecha, región, categoría si existe
  - Posibles joins con: otros datasets relacionables

OTRAS SECCIONES:
- Summary: dominio funcional (ventas, operaciones, RRHH...), granularidad temporal, origen probable
- Key Insights: columnas con alta cardinalidad, posibles duplicados, calidad de datos observable
- Usage: filtros útiles, agregaciones recomendadas, columnas para pivotado
- Pitfalls: nulos en campos clave, separadores en valores de texto, encoding de fechas
""",

    "xlsx": """
FAMILIA: DATOS TABULARES — Excel (modelo de datos, configuración, catálogo, informe)

Estructura que genera el preprocesador (xlsx.py):
  Si tiene múltiples hojas (build_focused con blocks):
    - "## Hojas (N)":                     cabecera con número total de hojas
    - "### Hoja `nombre` (M columnas)":   cada hoja con sus columnas
    - "Columnas: col1, col2..."           lista de columnas
    - "Muestra: [4 primeras filas]"       muestra de datos
  Si es una sola hoja (sin blocks, vía csv.py):
    - "## Catálogo de columnas (N cols, M filas)"
    - "### Columna `nombre` (tipo)" o lista compacta

REGLA CRÍTICA PARA CORE KNOWLEDGE:

  Si el contexto tiene ## Hojas (N) → múltiples hojas:
    Documenta CADA ### Hoja del contexto:
    ### Hoja `nombre`
    - **Qué contiene**: descripción en 1 frase inferida de las columnas
    - **Columnas clave**: las más relevantes con su significado
    - **Relación con otras hojas**: columnas compartidas que permiten join

  Si el contexto tiene ## Catálogo de columnas → hoja única:
    ### Qué representa cada fila
    ### Catálogo de columnas
    ### Claves y relaciones

OTRAS SECCIONES:
- Summary: tipo de Excel (catálogo, modelo, informe, config), propósito, hojas principales
- Key Insights: relaciones entre hojas, propósito de cada hoja, fórmulas si se mencionan
- Usage: cómo actualizar, dependencias con otros ficheros, frecuencia de actualización
- Pitfalls: fórmulas ocultas no visibles en el texto, hojas protegidas, referencias locales
""",

    "pdf": """
FAMILIA: DOCUMENTO RICO — PDF (informe, manual, RFP, paper, propuesta)

Estructura que genera el preprocesador:
  - "## [Sección principal]":   sección de nivel 1 del documento (Heading 1)
  - "### [Subsección]":        sección de nivel 2 (Heading 2)
  - "#### [Subsubsección]":    sección de nivel 3-4 (subservicios, items)
  - Texto sin header:          contenido de la sección inmediatamente anterior

REGLA CRÍTICA PARA CORE KNOWLEDGE:
El contexto ya tiene el documento dividido en secciones ## y ###.
Documenta CADA sección ## del contexto como subsección del pasaporte:

  ### [Título exacto de la sección ##]
  - Contenido real: datos, cifras, decisiones explícitas de esa sección
  - Si tiene subsecciones #### (ej: subservicios, módulos): docúmenta cada una brevemente
  - Métricas, fechas, valores numéricos: inclúyelos literalmente

NO colapses todas las secciones en 3 bullets genéricos.
Si el contexto tiene 15 secciones ## → produce 15 subsecciones ### en Core Knowledge.
Los #### son subservicios/subítems dentro de una sección — docúmenta cada uno.

OTRAS SECCIONES:
- Summary: tipo de documento (RFP, informe, manual, paper), propósito, audiencia, fecha si disponible
- Key Insights: conclusiones implícitas, decisiones que el doc no explicita, riesgos no enumerados
- Usage: cuándo consultar, qué decisión ayuda a tomar, quién debe leerlo
- Pitfalls: secciones incompletas, datos sin fecha de validez, referencias a documentos externos
""",

    "docx": """
FAMILIA: DOCUMENTO RICO — Word (especificación, procedimiento, RFP, acta, informe)

Estructura que genera el preprocesador (headings reales de Word):
  - "## [Sección principal]":   Heading 1 del documento
  - "### [Subsección]":        Heading 2
  - "#### [Subsubsección]":    Heading 3-4 (subservicios, subsecciones detalladas)
  - Texto sin header:          contenido de la sección inmediatamente anterior

REGLA CRÍTICA PARA CORE KNOWLEDGE:
El contexto ya tiene el documento dividido en secciones ## y ####.
Documenta CADA sección ## del contexto como subsección:

  ### [Título exacto de la sección ##]
  - Contenido real: requisitos, descripciones, condiciones de esa sección
  - Datos específicos: sistemas mencionados, cifras, plazos, responsables
  - Si tiene subsecciones #### (ej: subservicios técnicos, módulos):
    docúmenta cada #### individualmente con sus características

NO colapses todas las secciones.
Si el contexto tiene secciones #### como "Subservicio Ingeniería Azure",
"Subservicio Ingeniería Wintel", etc. → docúmenta CADA UNO por separado.

OTRAS SECCIONES:
- Summary: tipo (RFP, especificación, acta, procedimiento), propósito, emisor, fecha
- Key Insights: compromisos documentados, dependencias con otros sistemas, riesgos implícitos, ANS y penalizaciones si existen
- Usage: cuándo consultar, para qué decisión, quién debe seguirlo o aprobarlo
- Pitfalls: secciones sin completar, referencias a anexos externos, ambigüedades en requisitos
""",

    "pptx": """
FAMILIA: DOCUMENTO RICO — PowerPoint (presentación, propuesta, formación)

Estructura que genera el preprocesador:
  - "## [Sección lógica]":  grupo de slides de la misma sección temática
  - "### [Título del slide]": slide individual dentro de la sección
  - Texto sin header:         contenido del slide (bullets, notas del orador)

REGLA CRÍTICA PARA CORE KNOWLEDGE:
El contexto tiene los slides agrupados por secciones ## temáticas.
Documenta CADA sección ## como bloque temático:

  ### [Sección temática]
  - Mensaje principal del bloque
  - Datos, métricas o argumentos presentados en los slides de esa sección
  - Conclusión o llamada a la acción si existe

Si hay slides individuales ### relevantes dentro de una sección, docúmenta los más importantes.

OTRAS SECCIONES:
- Summary: tipo de presentación (propuesta, formación, informe), evento, audiencia, objetivo
- Key Insights: mensajes que se repiten o enfatizan, datos sorprendentes, propuestas concretas
- Usage: cuándo presentar, qué decisión apoya, cómo actualizar
- Pitfalls: diapositivas solo con imágenes (sin texto extraíble), datos sin fecha de validez
""",

    "html": """
FAMILIA: DOCUMENTO RICO — HTML (documentación web, exportación, informe renderizado)

Estructura que genera el preprocesador (html.py):
  Si el HTML tiene secciones numeradas (ej: 01 — Título, 02 — Título):
    - "## Documento":              cabecera del documento
    - "## Índice (N secciones)":   lista de todas las secciones
    - "## Sección NN: Título":     cada sección con su contenido
  Si el HTML tiene módulos numerados (01\nTítulo):
    - "## Módulo NN: Título":      cada módulo con su contenido
  Sin estructura detectada:
    - "## Sección N: primera_frase": párrafos largos como secciones

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Documenta CADA ## Sección o ## Módulo del contexto:

  ### [Título exacto de ## Sección NN o ## Módulo NN]
  - Contenido informativo de esa sección
  - Datos, procedimientos, configuraciones si existen

Si existe ## Índice úsalo para entender la estructura global.
NO documentes el ## Índice en sí — documenta las secciones con contenido.

OTRAS SECCIONES:
- Summary: tipo de contenido (documentación técnica, informe exportado, guía), fuente o sistema origen
- Key Insights: información accionable, procedimientos paso a paso, configuraciones relevantes
- Usage: cuándo y cómo consultar, si requiere acceso a sistema externo
- Pitfalls: contenido dinámico perdido en exportación, elementos visuales sin texto alternativo
""",

    "md": """
FAMILIA: MARKDOWN — (README, guía, wiki, nota técnica, arquitectura)

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Respeta la estructura de secciones del documento original — son intencionales.
  ### [Sección del .md original]
  - Síntesis del contenido: qué aporta esta sección
  - Si contiene código embebido: trátalo como Familia CÓDIGO

Si el documento es una decisión técnica (ADR):
  - Contexto: situación que motivó la decisión
  - Decisión tomada y alternativas descartadas
  - Consecuencias aceptadas

OTRAS SECCIONES:
- Summary: tipo de markdown (README, guía operativa, ADR, wiki de proceso), audiencia, estado
- Key Insights: aprendizajes o patrones documentados, referencias cruzadas a otros recursos, decisiones implícitas
- Usage: cuándo consultar, cómo mantenerlo actualizado
- Pitfalls: información desactualizada, links rotos, secciones incompletas marcadas con TODO
""",

    "txt": """
FAMILIA: TEXTO PLANO — (log, especificación, nota técnica, dump)

Estructura que genera el preprocesador (txt.py) según subtipo detectado:

  Si es LOG (timestamps + ERROR/WARN/INFO/FATAL):
    - "## Errores críticos":       líneas ERROR/FATAL
    - "## Trazas de excepción":    Traceback y Exception
    - "## Advertencias":           líneas WARN
    - "## Eventos informativos":   líneas INFO

  Si es ESPECIFICACIÓN (REQ-001, RF-1.2, Must/Debe...):
    - "### REQ-001" / "### RF-1.2": cada requisito como subsección
    - Texto del requisito bajo el ###

  Si es NOTA TÉCNICA (párrafos largos sin estructura formal):
    - "## Sección N: primera_frase": párrafos temáticos

  Si es DUMP: texto plano limpio sin estructura

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Identifica el subtipo por los headers del contexto y aplica:

  Si LOG (contiene ## Errores / ## Trazas / ## Advertencias):
    ### Errores críticos detectados
    - Patrón de error + causa inferida para cada línea ERROR del contexto
    ### Anomalías y excepciones
    - Trazas relevantes con su contexto

  Si ESPECIFICACIÓN (contiene ### REQ- o ### RF-):
    Documenta CADA ### del contexto:
    ### REQ-001: [descripción]
    - Condición, excepción, criterio de aceptación

  Si NOTA TÉCNICA (contiene ## Sección N:):
    Documenta CADA ## como subsección:
    ### [Sección N: tema]
    - Decisión o instrucción documentada

OTRAS SECCIONES:
- Summary: subtipo detectado (LOG/SPEC/NOTA) + contexto operativo (sistema, proceso, fecha)
- Key Insights: patrones repetidos en errores, requisitos contradictorios, decisiones implícitas
- Usage: cómo consultarlo, para qué decisiones sirve, frecuencia de actualización
- Pitfalls: ausencia de timestamps en logs, trazas sin contexto, requisitos ambiguos sin criterio
""",

    "drawio": """
FAMILIA: VISUAL — Diagrama draw.io (arquitectura de sistema o pipeline de actividades)

Estructura que genera el preprocesador (drawio.py) según tipo detectado:

  Si es PIPELINE (actividades TridentNotebook, SetVariable, ForEach...):
    - "## Diagrama: nombre":         cabecera
    - "### Rama `entry_point`":       cada rama de entrada del pipeline
      - "→ [tipo] actividad"          árbol de dependencias indentado
    - "### Actividades adicionales": nodos sin conexiones entrantes

  Si es ARQUITECTURA (Lakehouses, Pipelines, Notebooks, Warehouses...):
    - "## Diagrama: nombre":          cabecera
    - "### Lakehouses (N)":           lista con capa [raw/std/rch]
    - "### Pipelines (N)":            lista de pipelines
    - "### Notebooks (N)":            lista de notebooks
    - "### Warehouses (N)":           lista de warehouses
    - "### Fuentes externas":         GitHub, SharePoint, On-premise...
    - "### Por capa":                 agrupación medallion [raw/std/rch/ctrl]
    - "### Flujos":                   conexiones A → B [etiqueta]

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Identifica el tipo por los headers ### del contexto:

  Si contiene ### Rama `nombre` → es PIPELINE:
    ### Rama `nombre`
    - **Flujo**: secuencia de actividades del árbol → del contexto
    - **Tipos de actividad**: TridentNotebook / SetVariable / ForEach... detectados
    - **Punto de entrada**: qué dispara esta rama

  Si contiene ### Lakehouses / ### Pipelines → es ARQUITECTURA:
    ### Lakehouses
    - Lista con su capa (raw/std/rch) y propósito inferido del nombre
    ### Pipelines
    - Lista con su función en el flujo de datos
    ### Flujos clave
    - Las conexiones A → B más importantes y qué representan
    ### Por capa (medallion)
    - Qué artefactos hay en cada capa y qué hace esa capa

OTRAS SECCIONES:
- Summary: tipo de diagrama (pipeline / arquitectura medallion), scope, número de componentes
- Key Insights: cuellos de botella (nodos con muchas entradas), puntos únicos de fallo, capas desproporcionadas
- Usage: cuándo consultar, qué decisiones orienta, quién debe revisarlo ante cambios
- Pitfalls: componentes sin etiquetar, suposiciones dibujadas como hechos, versión desactualizada
""",

    "web": """
FAMILIA: DOCUMENTO RICO — Página web (documentación externa, artículo técnico)

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Organiza por secciones detectadas:
  ### [Sección]
  - Conceptos explicados, procedimientos descritos, referencias

OTRAS SECCIONES:
- Summary: tipo de contenido, fuente/URL origen, relevancia para el proyecto
- Key Insights: puntos accionables, diferencias con documentación interna, advertencias del autor
- Usage: cuándo consultar, qué problema resuelve
- Pitfalls: contenido que puede cambiar sin aviso, requiere acceso externo, fecha de publicación relevante
""",

    # ── CONECTORES API EXTERNOS ─────────────────────────────────────────────────
    # Añadir un nuevo conector = una entrada aquí con las marcas que genera su preprocesador.

    "confluence": """
FAMILIA: CONFLUENCE — Página wiki corporativa

Estructura que genera el preprocesador (confluence.py):
  - "## Metadata":              metadatos de la página (space, labels, author, url...)
  - "## [Sección del cuerpo]": cada sección del cuerpo convertida a ##
                               (Heading 1 del cuerpo → ##, Heading 2 → ###, Heading 3+ → ####)
  - "## Sección N: primera_frase": si la página no tiene headings, por párrafo largo

SEÑALES SEMÁNTICAS IMPORTANTES:
  - El "space" del ## Metadata indica el dominio organizativo (TEC, OPS, GOV, PROJ...)
  - Los "labels" son taxonomía corporativa oficial — úsalos como tags primarios
  - El "parent_page" da contexto jerárquico del área de conocimiento
  - El autor y fecha de modificación indican vigencia y ownership

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Documenta CADA ## del contexto (excepto ## Metadata) como subsección:

  ### [Título exacto de la ## sección del cuerpo]
  - Conceptos, procedimientos o decisiones documentadas en esa sección
  - Si hay tablas: qué representa cada columna clave
  - Si hay código embebido: tratar como Familia CÓDIGO con su lenguaje

Si la página es una decisión de arquitectura (ADR, RFC):
  ### Contexto
  ### Decisión tomada
  ### Alternativas descartadas
  ### Consecuencias

Si la página es un procedimiento operativo (runbook, how-to):
  Para CADA paso numerado o sección:
  ### Paso N: [nombre]
  - Qué hace, prerequisitos, resultado esperado

OTRAS SECCIONES:
- Summary: tipo de página (especificación, ADR, guía operativa, referencia técnica),
  espacio de origen, estado (vigente/borrador/deprecado si lo indica), audiencia
- Key Insights: decisiones implícitas detectadas, información que no está en
  documentos locales equivalentes, contradicciones con otras fuentes si detectas
- Usage: cuándo consultar esta página, qué decisión ayuda a tomar
- Pitfalls: secciones marcadas TODO/WIP/BORRADOR, referencias a páginas Confluence
  que no están en el corpus, fechas de validez implícitas
""",

    "jira_ticket": """
FAMILIA: JIRA — Ticket de trabajo (Story, Bug, Task, Epic)

Estructura que genera el preprocesador (jira_ticket.py):
  - "## KEY — Tipo — Estado":  cabecera del ticket con campos clave
    seguida de **campo**: valor para prioridad, asignado, reporter...
  - "## Descripción":          cuerpo del ticket (criterios de aceptación, detalle técnico)
  - "### Comentario N: Autor":  cada comentario humano como subsección
    seguido de la fecha y el texto del comentario
  Los comentarios de bots ya han sido filtrados por JiraConnector.

REGLA CRÍTICA PARA CORE KNOWLEDGE:

  ### Descripción del ticket
  - Propósito del trabajo: qué se implementa o soluciona
  - Alcance y criterios de aceptación (del ## Descripción)
  - Dependencias técnicas mencionadas

  Para CADA ### Comentario N: Autor del contexto que contenga información técnica:
  ### Comentario N: [Autor]
  - Decisión técnica o acuerdo documentado en ese comentario
  - Cambios de alcance o bloqueantes mencionados
  - Información que no está en la descripción original

  Si los comentarios son solo actualizaciones de estado sin contenido técnico,
  agrúpalos en una sola subsección:
  ### Hilo de seguimiento
  - Resumen del progreso y decisiones del hilo

OTRAS SECCIONES:
- Summary: tipo de ticket (Story, Bug, Task, Epic), proyecto y componente,
  estado (Done/In Progress/...), prioridad, sprint o versión si está disponible
- Key Insights: decisiones de diseño documentadas en comentarios, cambios de alcance
  respecto a la descripción original, patrones de error o causas raíz (si es Bug)
- Usage: qué implementación describe, en qué versión se resolvió, cómo reproducir (si Bug)
- Pitfalls: criterios de aceptación incompletos o ambiguos, decisiones tomadas solo
  en comentarios sin actualizar la descripción, dependencias no documentadas
""",

    "github_file": """
FAMILIA: GITHUB — Fichero de repositorio (código, notebook, config, documentación)

Estructura que genera el preprocesador (github_file.py):
  - "## owner/repo: ruta/al/fichero.ext":  cabecera con repo + ruta
    seguida de **Lenguaje**, **URL**, **Último commit**
  - Contenido preprocesado del fichero según su lenguaje:
    - .py  → ## MODULO, ## IMPORTS, ## CONSTANTES, ### `Clase`, ### `funcion()`
    - .sql → ## DOCUMENTACION, ### PROCEDURE, ### Fase N:
    - .md  → headers originales preservados
    - .json → ## Pipeline: o ## Configuración
    - otros → texto limpio con ## Sección N:

REGLA CRÍTICA PARA CORE KNOWLEDGE:
Identifica el lenguaje por la cabecera ## del contexto y aplica el
mismo estándar de documentación que para ese tipo de fichero:

  Si el fichero es Python (.py) → aplica el estándar py:
    ### `nombre_funcion(args) -> ReturnType`
    - **Propósito**, **Parámetros clave**, **Retorna**, **Cuándo usar**

  Si el fichero es SQL (.sql) → aplica el estándar sql:
    ### Fase N: / ### CTE `nombre` / ### TABLE `nombre`

  Si el fichero es Markdown (.md) → aplica el estándar md:
    ### [Sección del .md original]

  Si el fichero es JSON (.json) → aplica el estándar json:
    ### Actividad `nombre` / ### Propiedad `nombre`

  Para cualquier lenguaje: añade siempre al inicio de Core Knowledge:
    ### Contexto del repositorio
    - **Repo**: owner/repo (del ## Metadata del contexto)
    - **Ruta**: ruta/al/fichero (propósito inferido por la posición en el repo)
    - **Último cambio**: commit + autor si están disponibles

OTRAS SECCIONES:
- Summary: lenguaje y tipo de fichero, repositorio de origen, propósito en el proyecto,
  rama y fecha del último commit
- Key Insights: dependencias detectadas en imports/requires, patrones de diseño,
  relación con otros ficheros del repo inferida por la ruta, secrets o configuración hardcodeada
- Usage: cómo importar o ejecutar, dependencias previas, cómo clonarlo o acceder al repo
- Pitfalls: fichero en rama no-main, dependencias no documentadas en el propio fichero,
  configuración sensible visible en el código, imports de módulos internos no indexados
""",
}


# ===========================================================================
# UTILIDADES
# ===========================================================================

def _sanitize_current_md_for_prompt(current_md: str) -> str:
    """Limpia pasaporte existente antes de usarlo en modo mejora."""
    sanitized = _re.sub(
        r"^---\s*\n.*?\n---\s*\n?",
        "",
        current_md,
        count=1,
        flags=_re.DOTALL,
    )
    sanitized = _re.sub(
        r"\n#\s+📄\s+Source Extract\s*\n.*?(?=\n#\s+|\Z)",
        "\n# 📄 Source Extract\n[Se regenera desde el contenido original en esta ejecución]\n",
        sanitized,
        flags=_re.DOTALL,
    )
    sanitized = _re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized


# ===========================================================================
# CONTRATOS PÚBLICOS — funciones que el synthesizer llama
# ===========================================================================

def get_focused_synthesis_prompt(
    partial_passport: str,
    source: str,
    file_type: str,
    processed_text: str,
    metadata: dict | None = None,
    current_md: str | None = None,
    model_profile: str = "large",
) -> tuple[str, str]:
    """
    Construye prompt focalizado que pide al LLM completar SOLO secciones narrativas.

    Args:
        partial_passport: pasaporte pre-rellenado (frontmatter + entities + placeholders)
        source: nombre del fichero
        file_type: extensión
        processed_text: texto preprocesado (sin ruido)
        metadata: dict adicional
        current_md: contenido actual del .md en disco (activa modo mejora si se proporciona)
        model_profile: small/medium/large/claude (acepta el arg de v3 aunque no lo use aquí)

    Returns:
        (system_prompt, user_msg) para LLMClient.generate()
    """
    ft = file_type.lower().lstrip(".")
    metadata = metadata or {}

    system = FOCUSED_SYNTHESIS_PROMPT + _canonical_terminology_note()

    type_instr = _TYPE_INSTRUCTIONS_FOCUSED.get(ft, "")
    if type_instr:
        system = system + "\n" + type_instr.strip()

    if current_md and current_md.strip():
        _MAX_CURRENT_MD = 3000
        current_md_sanitized = _sanitize_current_md_for_prompt(current_md)
        current_md_truncated = (
            current_md_sanitized[:_MAX_CURRENT_MD] + "\n\n[... .md truncado por longitud ...]"
            if len(current_md_sanitized) > _MAX_CURRENT_MD
            else current_md_sanitized
        )
        user_msg = f"""Documento: {source}
Tipo: {file_type}

PASAPORTE ACTUAL (ya existente en disco):
{current_md_truncated}

PASAPORTE PARCIAL REGENERADO (frontmatter + entities actualizados):
{partial_passport}

CONTEXTO DEL DOCUMENTO (chunks originales):
---
{processed_text}
---

NOTA SOBRE EL CONTEXTO:
- Si hay un bloque "## Documentación": contiene la documentación oficial extraída del fichero fuente — es tu FUENTE PRIMARIA, úsala literalmente
- Si hay un bloque "## Firmas de funciones": contiene las firmas def/class — úsala para identificar y validar entidades
- Si hay un bloque "## Estructura": contiene la estructura del fichero (cabeceras, campos) — úsala como referencia
- Si hay un bloque "## Índice del documento": contiene el TOC del documento — úsalo como esqueleto de Core Knowledge
- Si el contexto tiene N funciones/columnas/secciones documentadas: Core Knowledge debe tener N entradas, una por cada una. NO las colapses en bullets genéricos.
- Extensión mínima por sección narrativa: 5-10 líneas (más si hay más contenido)

TAREA: Mejora y completa el pasaporte. Usando el pasaporte actual como base:
• Conserva las secciones que ya estén bien escritas
• Completa o reescribe las secciones [LLM: ...] o que estén vacías/incompletas
• Actualiza cualquier información que haya cambiado según los chunks originales
• Mantén el frontmatter del PASAPORTE PARCIAL REGENERADO (tiene los metadatos más actuales)

Responde SOLO con el Markdown completo mejorado, sin meta-comentarios."""
    else:
        user_msg = f"""Documento: {source}
Tipo: {file_type}

PASAPORTE PARCIAL (pre-rellenado):
{partial_passport}

CONTEXTO DEL DOCUMENTO:
---
{processed_text}
---

NOTA SOBRE EL CONTEXTO:
- Si hay un bloque "## Documentación": contiene la documentación oficial extraída del fichero fuente — es tu FUENTE PRIMARIA, úsala literalmente
- Si hay un bloque "## Firmas de funciones": contiene las firmas def/class — úsala para identificar y validar entidades
- Si hay un bloque "## Estructura": contiene la estructura del fichero (cabeceras, campos) — úsala como referencia
- Si hay un bloque "## Índice del documento": contiene el TOC del documento — úsalo como esqueleto de Core Knowledge
- Si el contexto tiene N funciones/columnas/secciones documentadas: Core Knowledge debe tener N entradas, una por cada una. NO las colapses en bullets genéricos.
- Extensión mínima por sección narrativa: 5-10 líneas (más si hay más contenido)

Completa las secciones [LLM: ...] en el pasaporte anterior.
Responde SOLO con el Markdown completo actualizado, sin meta-comentarios."""

    return system, user_msg


def get_overview_synthesis_prompt(
    partial_passport: str,
    source: str,
    file_type: str,
    doc_text_excerpt: str,
    metadata: dict | None = None,
    model_profile: str = "large",
) -> tuple[str, str]:
    """
    Prompt para la Call A de síntesis fragmentada (chunked).
    Pide al LLM todas las secciones EXCEPTO Core Knowledge.
    """
    ft = file_type.lower().lstrip(".")
    metadata = metadata or {}

    system = OVERVIEW_SYNTHESIS_PROMPT + _canonical_terminology_note()
    full_instr = _TYPE_INSTRUCTIONS_FOCUSED.get(ft, "")
    if full_instr and "OTRAS SECCIONES:" in full_instr:
        otras = full_instr.split("OTRAS SECCIONES:", 1)[1].strip()
        system = system + "\nINSTRUCCIONES PARA SECCIONES HOLÍSTICAS:\n" + otras

    user_msg = f"""Documento: {source}
Tipo: {file_type}

PASAPORTE PARCIAL (pre-rellenado — contiene TODAS las entities del documento completo):
{partial_passport}

EXTRACTO DEL DOCUMENTO (inicio del contenido — suficiente para secciones holísticas):
---
{doc_text_excerpt}
---

NOTA: El frontmatter ya lista TODAS las entities detectadas en el documento completo.
Úsalas para escribir Key Insights, Relationships y Usage con conocimiento del conjunto.

Completa las secciones [LLM: ...] EXCEPTO Core Knowledge (escribe solo "[procesando-chunked]" allí).
Responde SOLO con el Markdown completo, sin meta-comentarios."""

    return system, user_msg


def get_chunk_synthesis_prompt(
    file_type: str,
    chunk: str,
    chunk_index: int = 1,
    total_chunks: int = 1,
    model_profile: str = "large",
) -> tuple[str, str]:
    """
    Prompt para una Call B..N de síntesis fragmentada.
    Pide al LLM producir ÚNICAMENTE las subsecciones ### de Core Knowledge.
    """
    ft = file_type.lower().lstrip(".")

    full_instr = _TYPE_INSTRUCTIONS_FOCUSED.get(ft, "")
    if "OTRAS SECCIONES:" in full_instr:
        ck_instr = full_instr.split("OTRAS SECCIONES:")[0].strip()
    else:
        ck_instr = full_instr.strip()

    type_block = f"\nINSTRUCCIÓN ESPECÍFICA DEL TIPO:\n{ck_instr}" if ck_instr else ""

    system = CORE_KNOWLEDGE_CHUNK_PROMPT.format(type_instructions=type_block)

    user_msg = f"""Fragmento {chunk_index}/{total_chunks}:
---
{chunk}
---

Produce las subsecciones ### de Core Knowledge para cada unidad de contenido de este fragmento.
Solo subsecciones ###, sin ningún otro texto."""

    return system, user_msg


def get_synthesis_prompt(source: str, file_type: str, full_text: str, metadata: dict) -> tuple[str, str]:
    """Contrato DEPRECATED (compatibilidad). Usa get_focused_synthesis_prompt."""
    ft = file_type.lower().lstrip(".")
    max_chars = _MAX_CHARS.get(ft, _DEFAULT_MAX_CHARS)
    processed = preprocess_text(full_text, ft, max_chars)

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    partial = (
        "---\n"
        f"source: {source}\n"
        f"format: {file_type}\n"
        f"created_at: {now}\n"
        "---\n"
        "# 📌 Summary\n[LLM]\n\n"
        "# 🧩 Core Knowledge\n[LLM]\n\n"
        "# 🧠 Key Insights\n[LLM]\n"
    )

    return get_focused_synthesis_prompt(partial, source, file_type, processed, metadata)


def split_doc_text_for_chunked(doc_text: str, chunk_size: int = 3000) -> list[str]:
    """
    Divide doc_text en chunks de ≤ chunk_size chars, cortando en límites naturales.

    Estrategia:
      1. Corta en headers ## o ### (secciones semánticas del doc_text)
      2. Si hay bloques muy grandes, subdivide en párrafos
      3. Si un bloque solo supera chunk_size, lo incluye como chunk independiente
    """
    normalized = "\n" + doc_text

    raw_blocks = _re.split(r"(?=\n(?:##|###) )", normalized)
    blocks = [b.strip() for b in raw_blocks if b.strip()]

    if not blocks:
        return [doc_text]

    fine_blocks: list[str] = []
    for block in blocks:
        if len(block) <= chunk_size:
            fine_blocks.append(block)
        else:
            paragraphs = [p.strip() for p in _re.split(r"\n\n+", block) if p.strip()]
            sub_buf: list[str] = []
            sub_len = 0
            for para in paragraphs:
                if sub_len + len(para) > chunk_size and sub_buf:
                    fine_blocks.append("\n\n".join(sub_buf))
                    sub_buf = [para]
                    sub_len = len(para)
                else:
                    sub_buf.append(para)
                    sub_len += len(para)
            if sub_buf:
                fine_blocks.append("\n\n".join(sub_buf))

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len: int = 0

    for block in fine_blocks:
        if current_len + len(block) > chunk_size and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = [block]
            current_len = len(block)
        else:
            current_parts.append(block)
            current_len += len(block)

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks if chunks else [doc_text]


# ===========================================================================
# API v5 — planner, type_specs, builder, subtypes
# ===========================================================================

from .type_specs import (
    TypeSpec,
    get_spec,
    list_types,
)
from .planner import (
    SynthesisPlan,
    CallSpec,
    plan_synthesis,
    section_display_name,
    explain_plan,
)
from .subtypes import detect_subtype
from .builder  import build_calls, build_chunk_call


def _extract_entities(partial_passport: str) -> list[str]:
    """Lee la lista de entities del frontmatter YAML del partial passport."""
    entities: list[str] = []
    try:
        m = _re.search(r"entities:\s*(\[.*?\])", partial_passport, _re.DOTALL)
        if m:
            return _json.loads(m.group(1))

        in_entities = False
        for line in partial_passport.split("\n"):
            stripped = line.strip()
            if stripped == "entities:":
                in_entities = True
                continue
            if in_entities:
                if stripped.startswith("- "):
                    entity = stripped[2:].strip().strip('"').strip("'")
                    entities.append(entity)
                elif stripped and not line.startswith((" ", "\t")):
                    break
    except Exception:
        pass
    return entities


def get_synthesis_calls(
    partial_passport: str,
    source: str,
    file_type: str,
    processed_text: str,
    metadata: dict | None = None,
    current_md: str | None = None,
    model_profile: str = "large",
    context_chars: int = 0,
) -> tuple[list[tuple[str, str]], SynthesisPlan]:
    """
    Entry point principal v5 — genera la lista de llamadas LLM óptimas.

    Args:
        context_chars: presupuesto real del modelo (ModelProfile.context_chars).
                       Si se pasa, el planner lo usa para calcular el chunked_trigger
                       correcto para el modelo activo en lugar del valor hardcodeado.

    Returns:
        (calls, plan) — lista de tuplas (system, user_msg) + SynthesisPlan usado
    """
    ft = file_type.lower().lstrip(".")
    spec = get_spec(ft)
    if spec is None:
        spec = get_spec("txt")

    entities = _extract_entities(partial_passport)
    n_entities = len(entities)
    doc_chars = len(processed_text)

    plan = plan_synthesis(
        profile=model_profile,
        doc_chars=doc_chars,
        n_entities=n_entities,
        naturaleza=spec.naturaleza,
        context_chars=context_chars,
        quality_trigger=spec.quality_trigger,
    )

    calls = build_calls(
        partial_passport=partial_passport,
        source=source,
        file_type=file_type,
        processed_text=processed_text,
        spec=spec,
        plan=plan,
        profile=model_profile,
        entities=entities,
        current_md=current_md,
    )

    return calls, plan


__all__ = [
    # Constantes
    "SYNTHESIS_CHUNK_TRIGGER",
    "SYNTHESIS_CHUNK_SIZE",
    "SYNTHESIS_OVERVIEW_EXCERPT",
    "_MAX_CHARS",
    "_DEFAULT_MAX_CHARS",
    # Preprocesado
    "preprocess_text",
    "build_doc_focused_text",
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
    # Funciones de prompts legacy
    "get_focused_synthesis_prompt",
    "get_overview_synthesis_prompt",
    "get_chunk_synthesis_prompt",
    "get_synthesis_prompt",
    "split_doc_text_for_chunked",
    "_sanitize_current_md_for_prompt",
    "_canonical_terminology_note",
    # API v5
    "get_synthesis_calls",
    "TypeSpec",
    "get_spec",
    "list_types",
    "SynthesisPlan",
    "CallSpec",
    "plan_synthesis",
    "section_display_name",
    "explain_plan",
    "detect_subtype",
    "build_calls",
    "build_chunk_call",
]
