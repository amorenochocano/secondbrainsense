"""
json_fabric.py
--------------
Extractor para ficheros JSON de Microsoft Fabric:
dataflows, pipelines, lakehouse schemas, reports, y JSON genérico.

Produce N+1 bloques por fichero:
  - content_type: text  → 1 bloque con descripción semántica (va a knowledge)
  - content_type: code  → N bloques, uno por unidad lógica (actividad, recurso,
                          tabla, partición, etc.) en vez del JSON entero.
                          Esto garantiza que cada chunk del RAG tenga significado
                          propio y no supere el contexto del modelo de embedding.
"""
import json
import logging
from pathlib import Path
from .base import BaseExtractor

log = logging.getLogger(__name__)


class JsonFabricExtractor(BaseExtractor):
    """
    Detecta el tipo de artefacto Fabric y genera una descripción semántica.
    Siempre incluye también el JSON literal como bloque de código.
    """

    # Clave en el JSON → tipo de artefacto Fabric
    FABRIC_TYPES = {
        "activities":     "pipeline",
        "typeProperties": "dataflow",
        "tables":         "lakehouse_schema",
        "sections":       "report",
        "partitions":     "dataset",
    }

    # Patrones de $schema → tipo de plantilla ARM/Azure
    ARM_SCHEMA_PATTERNS = {
        "deploymenttemplate":      "arm_template",
        "deploymentparameters":    "arm_parameters",
        "managementinfrastructure": "arm_template",
        "schema.management.azure": "arm_template",
    }

    _ACTIVITY_NOISE_KEYS = {
        "policy",
        "logicAppsConnectionPayload",
        "lastModifiedByObjectId",
        "lastPublishTime",
    }

    def extract(self, source: str) -> list[dict]:
        with open(source, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                log.warning("[json_fabric] JSON inválido en '%s'. Devolviendo bloque de error.", source)
                # Fichero JSON inválido → un único bloque de texto con el error
                raw = f.read() if hasattr(f, "read") else ""
                return [{
                    "content":      f"Fichero JSON inválido o malformado: {Path(source).name}",
                    "content_type": "text",
                    "page":         1,
                    "metadata":     {"format": "json", "parse_error": True},
                }]

        fabric_type = self._detect_type(data)
        description = self._build_description(data, fabric_type, Path(source).name)
        code_blocks = self._build_code_blocks(data, fabric_type, Path(source).name)

        result = [
            {
                "content":      description,
                "content_type": "text",
                "page":         1,
                "metadata":     {"format": "json", "fabric_type": fabric_type},
            },
            *code_blocks,
        ]
        log.debug("[json_fabric] '%s' tipo='%s' → %d bloques", source, fabric_type, len(result))
        return result

    def _detect_type(self, data: dict) -> str:
        if not isinstance(data, dict):
            return "generic_json"
        # Detectar ARM templates por $schema
        schema_url = str(data.get("$schema", "")).lower()
        if schema_url:
            for pattern, arm_type in self.ARM_SCHEMA_PATTERNS.items():
                if pattern in schema_url:
                    # Caso especial: ARM que encapsula pipelines Fabric
                    if arm_type == "arm_template":
                        resources = data.get("resources", [])
                        if isinstance(resources, list):
                            for resource in resources:
                                if isinstance(resource, dict) and str(resource.get("type", "")).lower() == "pipelines":
                                    return "fabric_pipeline_arm"
                    return arm_type
        # Detectar artefactos Fabric nativos por clave
        for key, fabric_type in self.FABRIC_TYPES.items():
            if key in data:
                return fabric_type
        # Segundo nivel habitual en export de pipeline: properties.activities
        nested = data.get("properties")
        if isinstance(nested, dict):
            for key, fabric_type in self.FABRIC_TYPES.items():
                if key in nested:
                    return fabric_type
        return "generic_json"

    def _build_description(self, data: dict, fabric_type: str, filename: str) -> str:
        if not isinstance(data, dict):
            return f"Fichero JSON: {filename}"

        if fabric_type == "pipeline":
            activities = self._get_pipeline_activities(data)
            names = [a.get("name", "?") for a in activities if isinstance(a, dict)]
            return (
                f"Pipeline de Microsoft Fabric '{data.get('name', filename)}' "
                f"con {len(activities)} actividades: {', '.join(names) or 'ninguna'}."
            )

        if fabric_type == "fabric_pipeline_arm":
            pipeline = self._get_arm_pipeline_resource(data)
            props = pipeline.get("properties", {}) if isinstance(pipeline, dict) else {}
            activities = props.get("activities", []) if isinstance(props, dict) else []
            pipeline_name = pipeline.get("name", filename) if isinstance(pipeline, dict) else filename
            types = sorted({a.get("type", "?") for a in activities if isinstance(a, dict)})
            return (
                f"Pipeline Fabric en plantilla ARM '{pipeline_name}' con {len(activities)} actividades. "
                f"Tipos: {', '.join(types[:10]) or 'ninguno'}."
            )

        if fabric_type == "dataflow":
            return (
                f"Dataflow de Microsoft Fabric '{data.get('name', filename)}'. "
                f"Propiedades principales: {', '.join(list(data.keys())[:8])}."
            )

        if fabric_type == "lakehouse_schema":
            tables = data.get("tables", [])
            names = [t.get("name", "?") for t in tables if isinstance(t, dict)]
            return (
                f"Esquema de Lakehouse con {len(tables)} tablas: "
                f"{', '.join(names) or 'ninguna'}."
            )

        if fabric_type == "dataset":
            partitions = data.get("partitions", [])
            return (
                f"Dataset de Microsoft Fabric '{data.get('name', filename)}' "
                f"con {len(partitions)} particiones."
            )

        if fabric_type == "report":
            sections = data.get("sections", [])
            return (
                f"Informe de Power BI '{data.get('name', filename)}' "
                f"con {len(sections)} secciones/páginas."
            )

        if fabric_type == "arm_template":
            resources = data.get("resources", [])
            resource_types = list({r.get("type", "?").split("/")[-1] for r in resources if isinstance(r, dict)})
            content_version = data.get("contentVersion", "?")
            params = list(data.get("parameters", {}).keys())[:5]
            return (
                f"Plantilla ARM Azure v{content_version} con {len(resources)} recursos: "
                f"{', '.join(resource_types[:8]) or 'ninguno'}. "
                f"Parámetros: {', '.join(params) or 'ninguno'}."
            )

        if fabric_type == "arm_parameters":
            params = list(data.get("parameters", {}).keys())
            return (
                f"Fichero de parámetros ARM con {len(params)} parámetros: "
                f"{', '.join(params[:10]) or 'ninguno'}."
            )

        # Genérico
        top_keys = list(data.keys())[:10]
        return f"Fichero JSON '{filename}' con claves: {top_keys}."

    def _build_code_blocks(self, data: dict, fabric_type: str, filename: str) -> list[dict]:
        """
        Genera N bloques code, uno por unidad lógica del JSON.
        Cada unidad (actividad, recurso, tabla, partición...) es un chunk
        independiente con significado propio para el RAG.
        """
        stem = Path(filename).stem

        def make_block(item: dict, name: str = None, idx: int = 0) -> dict:
            return {
                "content":      json.dumps(item, indent=2, ensure_ascii=False),
                "content_type": "code",
                "language":     "json",
                "page":         idx + 1,
                "module":       stem,
                "function":     name,
                "metadata":     {"format": "json", "fabric_type": fabric_type},
            }

        # Pipeline Fabric: 1 bloque por actividad
        if fabric_type == "pipeline":
            activities = self._get_pipeline_activities(data)
            if activities:
                return [
                    make_block(self._sanitize_activity(a), name=a.get("name", f"activity_{i}"), idx=i)
                    for i, a in enumerate(activities) if isinstance(a, dict)
                ]

        # ARM con pipeline Fabric: 1 bloque por actividad del recurso pipeline
        if fabric_type == "fabric_pipeline_arm":
            pipeline = self._get_arm_pipeline_resource(data)
            props = pipeline.get("properties", {}) if isinstance(pipeline, dict) else {}
            activities = props.get("activities", []) if isinstance(props, dict) else []
            if activities:
                return [
                    make_block(self._sanitize_activity(a), name=a.get("name", f"activity_{i}"), idx=i)
                    for i, a in enumerate(activities) if isinstance(a, dict)
                ]
            if isinstance(pipeline, dict):
                return [make_block(pipeline, name=pipeline.get("name", "pipeline"), idx=0)]

        # ARM Template: 1 bloque por recurso
        if fabric_type == "arm_template":
            resources = data.get("resources", [])
            if resources:
                return [
                    make_block(
                        r,
                        name=r.get("name", r.get("type", f"resource_{i}")),
                        idx=i,
                    )
                    for i, r in enumerate(resources) if isinstance(r, dict)
                ]

        # ARM Parameters: 1 bloque por parámetro
        if fabric_type == "arm_parameters":
            params = data.get("parameters", {})
            if params:
                return [
                    make_block({k: v}, name=k, idx=i)
                    for i, (k, v) in enumerate(params.items())
                ]

        # Lakehouse Schema: 1 bloque por tabla
        if fabric_type == "lakehouse_schema":
            tables = data.get("tables", [])
            if tables:
                return [
                    make_block(t, name=t.get("name", f"table_{i}"), idx=i)
                    for i, t in enumerate(tables) if isinstance(t, dict)
                ]

        # Dataset: 1 bloque por partición
        if fabric_type == "dataset":
            partitions = data.get("partitions", [])
            if partitions:
                return [
                    make_block(p, name=p.get("name", f"partition_{i}"), idx=i)
                    for i, p in enumerate(partitions) if isinstance(p, dict)
                ]

        # Report Power BI: 1 bloque por sección/página
        if fabric_type == "report":
            sections = data.get("sections", [])
            if sections:
                return [
                    make_block(
                        s,
                        name=s.get("displayName", s.get("name", f"section_{i}")),
                        idx=i,
                    )
                    for i, s in enumerate(sections) if isinstance(s, dict)
                ]

        # Genérico: buscar el primer array de nivel superior con más de 1 elemento
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 1 and all(isinstance(v, dict) for v in value):
                    return [
                        make_block(item, name=f"{key}[{i}]", idx=i)
                        for i, item in enumerate(value)
                    ]

        # Fallback: JSON completo como un único bloque
        return [make_block(data if isinstance(data, dict) else {"value": data}, idx=0)]

    @staticmethod
    def _get_arm_pipeline_resource(data: dict) -> dict:
        resources = data.get("resources", [])
        if not isinstance(resources, list):
            return {}
        for resource in resources:
            if isinstance(resource, dict) and str(resource.get("type", "")).lower() == "pipelines":
                return resource
        return {}

    @staticmethod
    def _get_pipeline_activities(data: dict) -> list:
        activities = data.get("activities", []) if isinstance(data, dict) else []
        if isinstance(activities, list) and activities:
            return activities
        nested = data.get("properties") if isinstance(data, dict) else None
        if isinstance(nested, dict):
            activities = nested.get("activities", [])
            if isinstance(activities, list):
                return activities
        return []

    def _sanitize_activity(self, activity: dict) -> dict:
        if not isinstance(activity, dict):
            return {"value": activity}
        cleaned = {}
        for key, value in activity.items():
            if key in self._ACTIVITY_NOISE_KEYS:
                continue
            cleaned[key] = value
        return cleaned
