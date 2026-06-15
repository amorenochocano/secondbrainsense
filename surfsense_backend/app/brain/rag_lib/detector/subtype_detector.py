"""
subtype_detector.py: Detección de Subtipo Específico (Capa 0)
=============================================================

RESPONSABILIDAD
---------------
Identificar el subtipo dentro de un formato base para enrutar
al procesador especializado de Capa 3 (ej: fabric_pipeline, drawio, arm_template).

PRINCIPIO DE DISEÑO
-------------------
La detección de subtipo es INDEPENDIENTE del procesamiento del contenido.
Solo inspecciona la estructura (JSON keys, XML root tags, $schema) para
decisiones de enrutamiento, sin parsear ni validar el contenido.

SUBTIPOS SOPORTADOS
-------------------

  JSON (por clave de primer nivel o $schema)
  ==========================================
    • fabric_pipeline     → ADF/Synapse Pipelines (clave: "activities")
    • fabric_dataflow     → Azure Dataflows (clave: "typeProperties")
    • fabric_lakehouse    → Lakehouse configs (clave: "tables")
    • fabric_report       → Power BI Reports (clave: "sections")
    • fabric_dataset      → Dataset configs (clave: "partitions")
    • arm_template        → ARM templates ($schema contiene "deploymenttemplate")
    • arm_parameters      → ARM parameters ($schema contiene "deploymentparameters")
    • generic             → JSON estándar sin subtipo específico
  
  XML/DrawIO (por root tag)
  ========================
    • drawio              → Diagramas DrawIO (root: mxGraphModel, mxfile)
    • generic             → XML genérico sin subtipo específico
  
  Otros formatos
  ==============
    • generic             → No subtipo específico (usa procesador genérico)

ESTRATEGIA DE DETECCIÓN JSON
-----------------------------
1. Parsear JSON (primeros 8KB, fallback a completo si hay error)
2. Buscar $schema → patrón ARM → arm_template/arm_parameters
3. Buscar claves Fabric en root → fabric_*
4. Buscar claves Fabric en properties.* → fabric_* (anidado)
5. Buscar resources[].type == "pipelines" → fabric_pipeline
6. Fallback → generic

CASOS DE USO TÍPICOS
--------------------
  • pipeline_def.json → JSON con "activities" → fabric_pipeline
  • template.json → JSON con $schema ARM → arm_template
  • diagram.drawio → XML con root mxGraphModel → drawio
  • data.json → JSON genérico → generic (usa ChunkAssembler)
  • report.json → JSON con "sections" → fabric_report

RENDIMIENTO
-----------
  • JSON: Parsea primeros 8KB, fallback a completo (no cargar todo por defecto)
  • XML: Regex en primeros 512 bytes para root tag
  • Fallback a lxml si disponible (más robusto para XML malformado)
  • Siempre devuelve "generic" en caso de error (fail-safe)

INTEGRACIÓN CON UniversalCleaner
--------------------------------
  FormatDetector.detect(file_path) → base_format ("json", "xml", etc.)
    ↓
  SubtypeDetector.detect(file_path, base_format) → subtipo ("fabric_pipeline", etc.)
    ↓
  ProcessorRegistry.get_processors(base_format, subtipo) → [procesadores...]

DIFERENCIA CON FormatDetector
-----------------------------
  • FormatDetector      → ¿Qué es el archivo? (pdf, json, xml, etc.)
  • SubtypeDetector     → ¿Qué tipo específico de json/xml? (fabric, ARM, drawio, etc.)

VERSION: 1.1
AUTOR: Second Brain Core
DEPENDENCIAS: json, re, logging (stdlib), lxml (opcional)
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Claves de primer nivel en JSON → subtipo Fabric
_FABRIC_KEY_MAP: dict[str, str] = {
    "activities":     "fabric_pipeline",
    "typeProperties": "fabric_dataflow",
    "tables":         "fabric_lakehouse",
    "sections":       "fabric_report",
    "partitions":     "fabric_dataset",
}

# Fragmentos en $schema → subtipo ARM
_ARM_SCHEMA_MAP: list[tuple[str, str]] = [
    ("deploymenttemplate",          "arm_template"),
    ("deploymentparameters",        "arm_parameters"),
    ("managementinfrastructure",    "arm_template"),
    ("schema.management.azure",     "arm_template"),
]

# Tags root de XML → subtipo
_XML_ROOT_TAG_MAP: dict[str, str] = {
    "mxGraphModel": "drawio",
    "mxfile":       "drawio",
}


class SubtypeDetector:
    """
    Detecta el subtipo específico de un documento dentro de su formato base.
    
    CARACTERÍSTICAS
    ===============
    ✓ Enrutamiento inteligente — Dirige a procesadores especializados
    ✓ Fail-safe — Devuelve "generic" si la detección falla
    ✓ Sin excepciones — Nunca lanza errores
    ✓ Bajo overhead — Inspección mínima (no procesa contenido completo)
    ✓ Extensible — Solo actualizar mapeos de claves/tags para nuevos subtipos
    
    GARANTÍAS
    =========
    • Devuelve siempre un string (nunca None)
    • Los valores devueltos coinciden con registry_config.yaml keys
    • "generic" = no se aplica procesamiento de subtipo especial
    • Detecta Fabric JSON (pipeline, dataflow, lakehouse, etc.)
    • Detecta ARM templates y parameters
    • Detecta diagramas DrawIO
    
    USO
    ===
    >>> detector = SubtypeDetector()
    >>> subtipo = detector.detect("/path/to/pipeline.json", "json")
    >>> print(subtipo)  # "fabric_pipeline", "arm_template", "generic", etc.
    
    NOTAS
    =====
    • El subtipo determina qué procesador de Capa 3 se usa
    • "generic" significa: solo ChunkAssembler estándar (Capas 1, 2, 4)
    • La detección es independiente de la arquitectura o validez del JSON
    """

    def detect(self, file_path: str, base_format: str) -> str:
        """
        Detecta el subtipo del documento basado en su formato base.
        
        ARGUMENTOS
        ==========
        file_path : str
            Ruta al archivo a analizar (resultado de FormatDetector.detect)
        base_format : str
            Formato base del archivo ("json", "xml", "drawio", etc.)
            Generalmente obtenido de FormatDetector.detect(file_path)
        
        RETORNA
        =======
        str : Subtipo específico para enrutamiento a procesador de Capa 3
            Para JSON: fabric_pipeline, fabric_dataflow, fabric_lakehouse,
                       fabric_report, fabric_dataset, arm_template,
                       arm_parameters, generic
            Para XML: drawio, generic
            Otros formatos: generic
        
        COMPORTAMIENTO
        ===============
        • Nunca lanza excepciones
        • Si no puede determinar subtipo → devuelve "generic"
        • Si archivo no existe → devuelve "generic" + warning
        • Inspección mínima del contenido (no parsea completo si es posible)
        • Ignora errores de JSON malformado (intenta lectura completa después)
        
        EJEMPLO
        =======
        >>> detector = SubtypeDetector()
        >>> detector.detect("/path/to/pipeline.json", "json")  # "fabric_pipeline"
        >>> detector.detect("/path/to/template.json", "json") # "arm_template"
        >>> detector.detect("/path/to/diagram.drawio", "xml") # "drawio"
        >>> detector.detect("/path/to/data.json", "json")      # "generic"
        """
        try:
            if base_format == "json":
                return self._detect_json_subtype(file_path)
            if base_format in ("xml", "drawio"):
                return self._detect_xml_subtype(file_path)
        except Exception as e:
            log.warning(
                "[subtype_detector] Error detectando subtipo de '%s' (formato=%s): %s",
                file_path, base_format, e
            )
        return "generic"

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def _detect_json_subtype(self, file_path: str) -> str:
        """
        Detecta subtipos JSON:
        - fabric_pipeline, fabric_dataflow, fabric_lakehouse,
          fabric_report, fabric_dataset (por clave de primer nivel)
        - arm_template, arm_parameters (por $schema)
        - generic (por defecto)
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                # Leer solo los primeros 8KB para no cargar ficheros enormes
                raw = f.read(8192)
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            # Puede ser JSON inválido o truncado — intentar con texto parcial
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    data = json.load(f)
            except Exception:
                return "generic"

        if not isinstance(data, dict):
            return "generic"

        # Detectar ARM por $schema
        schema_url = str(data.get("$schema", "")).lower()
        if schema_url:
            for pattern, subtype in _ARM_SCHEMA_MAP:
                if pattern in schema_url:
                    log.debug(
                        "[subtype_detector] '%s' → subtipo '%s' (ARM $schema)",
                        file_path, subtype
                    )
                    return subtype

        # Detectar Fabric por clave de primer nivel
        for key, subtype in _FABRIC_KEY_MAP.items():
            if key in data:
                log.debug(
                    "[subtype_detector] '%s' → subtipo '%s' (clave Fabric: %s)",
                    file_path, subtype, key
                )
                return subtype

        # Detectar Fabric con estructura anidada: properties.activities, etc.
        nested = data.get("properties")
        if isinstance(nested, dict):
            for key, subtype in _FABRIC_KEY_MAP.items():
                if key in nested:
                    log.debug(
                        "[subtype_detector] '%s' → subtipo '%s' (clave Fabric anidada: properties.%s)",
                        file_path, subtype, key
                    )
                    return subtype

        # ARM template con recurso pipeline Fabric
        resources = data.get("resources")
        if isinstance(resources, list):
            for resource in resources:
                if isinstance(resource, dict) and str(resource.get("type", "")).lower() == "pipelines":
                    return "fabric_pipeline"

        return "generic"

    # ------------------------------------------------------------------
    # XML / DrawIO
    # ------------------------------------------------------------------

    def _detect_xml_subtype(self, file_path: str) -> str:
        """
        Detecta subtipos XML:
        - drawio (root tag mxGraphModel o mxfile)
        - generic (por defecto)
        """
        # Leer los primeros 512 bytes para encontrar el tag raíz sin parsear
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                head = f.read(512)
        except OSError:
            return "generic"

        # Buscar el primer tag no-declaración
        m = re.search(r"<([a-zA-Z][a-zA-Z0-9_:.-]*)", head)
        if m:
            raw_tag = m.group(1)
            # Quitar namespace si existe
            tag = raw_tag.split(":")[-1] if ":" in raw_tag else raw_tag
            # Quitar namespace con prefijo {}
            tag = re.sub(r"^\{[^}]+\}", "", tag)

            if tag in _XML_ROOT_TAG_MAP:
                subtype = _XML_ROOT_TAG_MAP[tag]
                log.debug(
                    "[subtype_detector] '%s' → subtipo '%s' (root tag: %s)",
                    file_path, subtype, tag
                )
                return subtype

        # Verificación con lxml si está disponible (más robusta)
        try:
            from lxml import etree
            tree = etree.parse(file_path, etree.XMLParser(recover=True))
            root_tag = re.sub(r"\{[^}]+\}", "", tree.getroot().tag)
            if root_tag in _XML_ROOT_TAG_MAP:
                return _XML_ROOT_TAG_MAP[root_tag]
        except Exception:
            pass

        return "generic"
