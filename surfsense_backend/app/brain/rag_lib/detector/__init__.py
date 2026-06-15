"""
rag_lib/detector: Capa 0 - Detección de Formato y Subtipo de Documentos
========================================================================

Módulo central para la identificación y clasificación de documentos en el sistema RAG.
Parte integral de UniversalCleaner, que utiliza estos componentes para enrutar documentos
al procesador adecuado en la Capa 3.

ARQUITECTURA DE CAPAS (RAG Pipeline)
------------------------------------
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Capa 4: Output        → Chunks entrelazados + metadatos             │
  │ Capa 3: Processing    → PDFExtractor, DocxExtractor, etc.           │
  │ Capa 2: Extraction    → FormatDetector → Contenido crudo            │
  │ Capa 1: Cleaning      → UniversalCleaner → Texto normalizado        │
  │ Capa 0: Detection     → FormatDetector + SubtypeDetector            │
  │ Input:                → Fichero binario                             │
  └─────────────────────────────────────────────────────────────────────┘

FLUJO DE DETECCIÓN
------------------
1. FormatDetector analiza magic bytes → formato base (pdf, docx, json, xml...)
2. SubtypeDetector identifica el subtipo dentro del formato
   - JSON: fabric_pipeline, fabric_dataflow, arm_template, etc.
   - XML: drawio, generic
   - Otros: generic (sin subtipo específico)
3. ProcessorRegistry mapea (formato, subtipo) → procesador de Capa 3

COMPONENTES EXPORTADOS
----------------------
  • FormatDetector     - Detecta formato base sin depender del nombre del fichero
  • SubtypeDetector    - Identifica subtipos específicos (Fabric, ARM, Drawio, etc.)
  • ProcessorRegistry  - Gestiona mapeo de formatos/subtipos a procesadores

EJEMPLO DE USO (en UniversalCleaner)
------------------------------------
  from rag_lib.detector import FormatDetector, SubtypeDetector, ProcessorRegistry
  
  format_det = FormatDetector()
  subtype_det = SubtypeDetector()
  registry = ProcessorRegistry()
  
  # Detectar qué es el archivo
  base_format = format_det.detect(file_path)  # "json"
  subtipo = subtype_det.detect(file_path, base_format)  # "fabric_pipeline"
  
  # Obtener procesador apropiado
  processor = registry.get_processor(base_format, subtipo)

FORMATOS SOPORTADOS
-------------------
  Binarios:    PDF, DOCX, XLSX, PPTX, DOC (OLE), XLS (OLE), DRAWIO
  Texto:       JSON, XML, CSV, TXT, MARKDOWN, PYTHON
  Web:         HTML
  Especiales:  JSONL (newline-delimited), Drawio (XML + esquema gráfico)

SUBTIPOS JSON DETECTADOS
------------------------
  • fabric_pipeline    → Pipelines de Azure Data Factory / Synapse
  • fabric_dataflow    → Dataflows de Azure
  • fabric_lakehouse   → Tablas y configuración de lakehouse
  • fabric_report      → Definiciones de reportes
  • fabric_dataset     → Configuraciones de datasets
  • arm_template       → Plantillas ARM de Azure
  • arm_parameters     → Ficheros de parámetros ARM
  • generic            → JSON estándar sin procesamiento especial

NOTAS TÉCNICAS
--------------
  • FormatDetector utiliza magic bytes primero, fallback a extensión
  • SubtypeDetector NO requiere inspección de contenido para rendimiento
  • ProcessorRegistry es thread-safe para uso en pipelines paralelos
  • Todos los componentes están optimizados para Low-Latency Processing

FLUJO EN UniversalCleaner
--------------------------
  UniversalCleaner
    ↓
  Capa 0: FormatDetector.detect()
    ↓
  Capa 0: SubtypeDetector.detect()
    ↓
  Capa 1: Cleaning (normalización de texto)
    ↓
  Capa 2: Extraction (formato específico)
    ↓
  Capa 3: Processing (según subtipo registrado)
    ↓
  Capa 4: Output (chunks + metadatos)

VERSION: 1.1
AUTOR: Second Brain Core
DEPENDENCIAS: procesadores especializados en rag_lib/layer1_universal y rag_lib/layer3_*
"""

from .format_detector import FormatDetector
from .subtype_detector import SubtypeDetector
from .processor_registry import ProcessorRegistry

__all__ = [
    "FormatDetector",
    "SubtypeDetector", 
    "ProcessorRegistry",
]
