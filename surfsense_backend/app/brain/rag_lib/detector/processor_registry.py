"""
processor_registry.py: Registro de Procesadores (Capa 0)
========================================================

RESPONSABILIDAD
---------------
Centralizar el mapeo de (formato, subtipo) → procesadores especializados.
Carga configuración YAML y resuelve nombres textuales a clases Python.
Permite agregar nuevos procesadores SIN cambiar código Python.

ARQUITECTURA
------------
  registry_config.yaml (YAML declarativo)
    ↓ (mapeo: formato → subtipo → [nombres de clases])
  ProcessorRegistry (Python)
    ↓ (resuelve nombres a callables vía _class_map)
  Procesadores registrados (PDFExtractor, DocxExtractor, ChunkAssembler, etc.)

STRUCTURA DEL YAML
------------------
  formato:
    subtipo:
      - NombreClase1
      - NombreClase2
      - NombreClase3
  
  ejemplo:
    pdf:
      generic: [UniversalCleaner, PDFExtractor, ChunkAssembler]
    json:
      fabric_pipeline: [UniversalCleaner, JsonFabricExtractor, ChunkAssembler]
      arm_template: [UniversalCleaner, JsonFabricExtractor, ChunkAssembler]
      generic: [UniversalCleaner, JsonExtractor, ChunkAssembler]
    xml:
      drawio: [UniversalCleaner, DrawioExtractor, ChunkAssembler]
      generic: [UniversalCleaner, XmlExtractor, ChunkAssembler]

FLUJO DE PROCESAMIENTO
----------------------
  1. FormatDetector.detect(file) → "pdf"
  2. SubtypeDetector.detect(file, "pdf") → "generic"
  3. registry.get_processors("pdf", "generic") → [UniversalCleaner, PDFExtractor, ChunkAssembler]
  4. Ejecutar cada procesador en orden (pipeline stages)

CAMBIO FÁCIL SIN CÓDIGO
------------------------
  Para agregar nuevo subtipo o cambiar orden de procesadores:
  ✓ Solo editar registry_config.yaml
  ✗ NO tocar código Python
  
  Para agregar nueva clase procesadora:
  1. Crear la clase en api/extractors/ o rag_lib/layerX/
  2. Registrar con registry.register("MiClase", MiClase)
  3. Agregar a registry_config.yaml en la pipeline deseada

VERSION: 1.1
AUTOR: Second Brain Core
DEPENDENCIAS: yaml, logging (stdlib), pathlib (stdlib)
CONFIGURACIÓN: config/registry_config.yaml (relativa a rag_lib/)
"""
import logging
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Ruta por defecto al YAML (relativa a este fichero)
_DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent / "config" / "registry_config.yaml"


class ProcessorRegistry:
    """
    Registro central de procesadores para enrutamiento de documentos.
    
    Carga la configuración declarativa YAML (registry_config.yaml) que mapea
    (formato, subtipo) → lista de procesadores. Resuelve nombres textuales
    a clases Python reales para ejecutar pipelines.
    
    CARACTERÍSTICAS
    ===============
    ✓ Configuración declarativa (YAML) → Sin cambios de código para nuevos procesadores
    ✓ Fallback automático → Si subtipo no existe, intenta "generic"
    ✓ Validación — Warning si clase no está registrada
    ✓ Consultas — Listar formatos, subtipos, procesadores disponibles
    ✓ Thread-safe para pipelines paralelos
    ✓ Fail-safe — Omite clases no registradas con warning
    
    CICLO DE VIDA
    =============
    1. __init__(registry_path) → Carga YAML
    2. register(name, cls) → Registrar clases Python (bootstrap)
    3. get_processors(format, subtype) → Resolver nombres a callables
    4. Ejecutar pipeline (cada procesador en orden)
    
    GARANTÍAS
    =========
    • YAML siempre se carga (incluso si no existe)
    • get_processors() devuelve siempre lista (posiblemente vacía)
    • Clases no registradas se omiten con warning
    • Subtipos no encontrados fallback a "generic"
    
    USO
    ===
    >>> registry = ProcessorRegistry()
    >>> registry.register_many({
    ...     "UniversalCleaner": UniversalCleaner,
    ...     "PDFExtractor": PDFExtractor,
    ...     "ChunkAssembler": ChunkAssembler,
    ... })
    >>> processors = registry.get_processors("pdf", "generic")
    >>> # Ejecutar cada procesador
    >>> for processor_class in processors:
    ...     proc = processor_class()
    ...     result = proc.process(document)
    
    NOTAS
    =====
    • Los "procesadores" son instancias de clases con método process()
    • El orden en YAML importa — se ejecutan en secuencia
    • "generic" es el subtipo por defecto para formatos sin subtipos especiales
    • Durante migración: procesadores = extractores actuales de api/extractors/
    • Fase 5: migración completa a rag_lib/layerX_* procesadores
    """

    def __init__(self, registry_path: str | None = None):
        path = Path(registry_path) if registry_path else _DEFAULT_REGISTRY_PATH
        self._config: dict = self._load_yaml(path)
        # Mapa nombre_clase → callable — se rellena con register()
        self._class_map: dict[str, type] = {}

    # ------------------------------------------------------------------
    # Carga del YAML
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            log.debug("[registry] YAML cargado desde '%s'", path)
            return data or {}
        except FileNotFoundError:
            log.error("[registry] registry_config.yaml no encontrado en '%s'", path)
            return {}
        except yaml.YAMLError as e:
            log.error("[registry] Error parseando registry_config.yaml: %s", e)
            return {}

    # ------------------------------------------------------------------
    # Registro de clases
    # ------------------------------------------------------------------

    def register(self, name: str, cls: type) -> None:
        """
        Registra una clase Python bajo su nombre textual en el YAML.
        
        ARGUMENTOS
        ==========
        name : str
            Nombre de la clase tal como aparece en registry_config.yaml
            Ejemplo: "PDFExtractor", "UniversalCleaner", "ChunkAssembler"
        cls : type
            La clase Python real (callable)
            Ejemplo: PDFExtractor, UniversalCleaner, ChunkAssembler
        
        NOTA
        ====
        Llamado durante el bootstrap de la aplicación para mapear
        nombres YAML → clases Python reales.
        
        EJEMPLO
        =======
        >>> registry = ProcessorRegistry()
        >>> registry.register("PDFExtractor", PDFExtractor)
        >>> registry.register("UniversalCleaner", UniversalCleaner)
        """
        self._class_map[name] = cls
        log.debug("[registry] Clase registrada: %s → %s", name, cls)

    def register_many(self, mapping: dict[str, type]) -> None:
        """Registra múltiples clases de golpe."""
        for name, cls in mapping.items():
            self.register(name, cls)

    # ------------------------------------------------------------------
    # Consulta del registro
    # ------------------------------------------------------------------

    def get_processor_names(self, base_format: str, subtype: str = "generic") -> list[str]:
        """
        Obtiene los nombres textuales de procesadores para un (formato, subtipo).
        
        ARGUMENTOS
        ==========
        base_format : str
            Formato base del documento ("pdf", "json", "xml", etc.)
        subtype : str
            Subtipo dentro del formato ("fabric_pipeline", "drawio", "generic", etc.)
            Default: "generic"
        
        RETORNA
        =======
        list[str] : Nombres de procesadores tal como aparecen en el YAML
            Ejemplo: ["UniversalCleaner", "PDFExtractor", "ChunkAssembler"]
        
        COMPORTAMIENTO
        ===============
        • Si subtipo específico no existe → fallback a "generic"
        • Si formato no existe en YAML → devuelve lista vacía + warning
        • Devuelve siempre una lista (posiblemente vacía)
        
        EJEMPLO
        =======
        >>> registry = ProcessorRegistry()
        >>> names = registry.get_processor_names("pdf", "generic")
        >>> print(names)  # ["UniversalCleaner", "PDFExtractor", "ChunkAssembler"]
        
        >>> names = registry.get_processor_names("json", "fabric_pipeline")
        >>> print(names)  # ["UniversalCleaner", "JsonFabricExtractor", "ChunkAssembler"]
        """
        fmt_cfg = self._config.get(base_format, {})
        if not fmt_cfg:
            log.warning(
                "[registry] Formato '%s' no está en registry_config.yaml", base_format
            )
            return []

        processors = fmt_cfg.get(subtype)
        if processors is None and subtype != "generic":
            log.debug(
                "[registry] Subtipo '%s/%s' no encontrado, usando 'generic'",
                base_format, subtype
            )
            processors = fmt_cfg.get("generic", [])

        return processors or []

    def get_processors(self, base_format: str, subtype: str = "generic") -> list[type]:
        """
        Obtiene las clases Python resueltas para un (formato, subtipo).
        
        ARGUMENTOS
        ==========
        base_format : str
            Formato base del documento ("pdf", "json", "xml", etc.)
        subtype : str
            Subtipo dentro del formato ("fabric_pipeline", "drawio", "generic", etc.)
            Default: "generic"
        
        RETORNA
        =======
        list[type] : Clases Python callable (procesadores)
            Ejemplo: [UniversalCleaner, PDFExtractor, ChunkAssembler]
        
        COMPORTAMIENTO
        ===============
        • Si clase no está registrada → la omite + warning + continúa
        • Si subtipo no existe → fallback a "generic"
        • Si formato no existe → devuelve lista vacía
        • Devuelve siempre una lista (posiblemente vacía)
        • Mantiene el orden del YAML
        
        EJEMPLO
        =======
        >>> registry = ProcessorRegistry()
        >>> registry.register_many({
        ...     "UniversalCleaner": UniversalCleaner,
        ...     "PDFExtractor": PDFExtractor,
        ...     "ChunkAssembler": ChunkAssembler,
        ... })
        >>> procs = registry.get_processors("pdf", "generic")
        >>> print(procs)  # [<class UniversalCleaner>, <class PDFExtractor>, <class ChunkAssembler>]
        
        >>> for proc_class in procs:
        ...     processor = proc_class()  # Instanciar
        ...     result = processor.process(document)  # Ejecutar
        """
        names = self.get_processor_names(base_format, subtype)
        result = []
        for name in names:
            cls = self._class_map.get(name)
            if cls is None:
                log.warning(
                    "[registry] Clase '%s' no registrada — omitida para %s/%s",
                    name, base_format, subtype
                )
            else:
                result.append(cls)
        return result

    def list_formats(self) -> list[str]:
        """
        Lista todos los formatos soportados según registry_config.yaml.
        
        RETORNA
        =======
        list[str] : Formatos disponibles
            Ejemplo: ["pdf", "docx", "json", "xml", "csv", "python", ...]
        
        EJEMPLO
        =======
        >>> registry = ProcessorRegistry()
        >>> formats = registry.list_formats()
        >>> print(formats)  # ["pdf", "docx", "xlsx", "json", "xml", ...]
        """
        return list(self._config.keys())

    def list_subtypes(self, base_format: str) -> list[str]:
        """
        Lista todos los subtipos soportados para un formato dado.
        
        ARGUMENTOS
        ==========
        base_format : str
            Formato base ("json", "xml", "pdf", etc.)
        
        RETORNA
        =======
        list[str] : Subtipos disponibles para el formato
            Ejemplo para "json": ["fabric_pipeline", "arm_template", "generic", ...]
            Ejemplo para "xml": ["drawio", "generic"]
        
        EJEMPLO
        =======
        >>> registry = ProcessorRegistry()
        >>> subtypes = registry.list_subtypes("json")
        >>> print(subtypes)  # ["fabric_pipeline", "fabric_dataflow", "arm_template", "generic"]
        """
        return list(self._config.get(base_format, {}).keys())
