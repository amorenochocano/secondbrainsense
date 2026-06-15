"""
rag_lib
-------
Librería de preprocesado RAG en capas reutilizable.

Cómo está organizado el preprocesado (dónde y cómo):

  0) detector (detección base)
     Dónde:
       - rag_lib/detector/format_detector.py
       - rag_lib/detector/subtype_detector.py
       - rag_lib/detector/processor_registry.py
       - rag_lib/config/registry_config.yaml
     Cómo:
       - Detecta formato base por magic bytes (pdf, docx, json, xml, etc.).
       - Detecta subtipo dentro del formato (drawio, fabric_pipeline, arm_template, ...).
       - Resuelve la cadena de procesadores por formato/subtipo mediante YAML.

  1) layer1_universal (limpieza común)
     Dónde:
       - rag_lib/layer1_universal/universal_cleaner.py
     Cómo:
       - Aplica limpieza transversal a bloques extraídos (normalización de texto,
         unicode, calidad, saneado básico y metadatos de calidad).

  2) layer2_format (extracción por formato)
     Dónde (estado actual):
       - api/extractors/factory.py
       - api/extractors/*.py
     Cómo:
       - Selecciona extractor según formato base y ejecuta extract().
       - Ejemplos: PDFExtractor, DocxExtractor, XmlExtractor, JsonFabricExtractor.

  3) layer3_subtype (especialización por subtipo)
     Dónde (estado actual):
       - Detección: rag_lib/detector/subtype_detector.py
       - Enrutado: rag_lib/orchestrator.py y rag_lib/config/registry_config.yaml
       - Parte de la ejecución reutiliza extractores especializados en api/extractors.
     Cómo:
       - Se aplica dentro de layer2_format: primero formato base, luego subtipo.
       - Especializa el tratamiento para variantes del mismo formato.

  4) layer4_assembler (ensamblado + chunking)
     Dónde:
       - rag_lib/layer4_assembler/chunk_assembler.py
     Cómo:
       - Convierte bloques limpios en CleanChunk listos para vectorización/ingesta.

  Orquestación end-to-end:
     - rag_lib/orchestrator.py (RAGOrchestrator) ejecuta el flujo:
       detector -> extractor (layer2) -> limpieza (layer1) -> assembler (layer4).
"""

from .detector import FormatDetector, SubtypeDetector, ProcessorRegistry
from .layer1_universal import UniversalCleaner
from .layer4_assembler import ChunkAssembler, CleanChunk
from .orchestrator import RAGOrchestrator

__all__ = [
    # Capa 0 — detección
    "FormatDetector",
    "SubtypeDetector",
    "ProcessorRegistry",
    # Capa 1 — limpieza universal
    "UniversalCleaner",
    # Capa 4 — ensamblado
    "ChunkAssembler",
    "CleanChunk",
    # Orquestador
    "RAGOrchestrator",
]
