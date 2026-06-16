"""
brain/chunking.py
-----------------
Migrado desde Second Brain (app/chunking.py).

Wrapper de chunking para el pipeline Brain. Divide el texto extraído
de un documento en fragmentos (chunks) antes de vectorizarlos y almacenarlos.

En F3 se implementará split_doc_text_for_chunked() que añadirá un chunker
semántico que respeta los cortes ##/### del preprocesado Brain.

Punto de entrada principal:
  get_chunks(text, strategy=None, content_type="text") -> List[str]

Estrategias disponibles (CHUNKING_STRATEGY env var):
  fixed     — corta cada CHUNK_SIZE caracteres con solapamiento CHUNK_OVERLAP
  paragraph — respeta párrafos naturales (recomendado, default)
  semantic  — detecta cambios semánticos entre frases (requiere sentence-transformers)

Variables de entorno:
  CHUNKING_STRATEGY  : estrategia activa. Default: paragraph.
  CHUNK_SIZE         : tamaño máximo de chunk en caracteres. Default: 600.
  CHUNK_OVERLAP      : solapamiento en estrategia fixed. Default: 100.
  MAX_CHUNK_CHARS    : límite de seguridad antes del modelo de embedding. Default: 4000.
"""

import os
import re
import logging
from typing import List

_log = logging.getLogger(__name__)

CHUNK_SIZE        = int(os.getenv("CHUNK_SIZE", 600))
CHUNK_OVERLAP     = int(os.getenv("CHUNK_OVERLAP", 100))
CHUNKING_STRATEGY = os.getenv("CHUNKING_STRATEGY", "paragraph")

# Tipos de contenido atómicos: el bloque completo es siempre un único chunk.
# Nunca deben dividirse porque su significado depende de la integridad del bloque.
ATOMIC_CONTENT_TYPES = {"table", "callout", "footnote", "figure_caption", "glossary_entry", "code"}

# Límite de seguridad: ningún chunk puede superar este tamaño antes de ser
# enviado al modelo de embedding.
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", 4000))


# ---------------------------------------------------------------------------
# Estrategia FIXED
# ---------------------------------------------------------------------------

def chunk_fixed(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto cada 'size' caracteres con 'overlap' de solapamiento.
    Estrategia simple y predecible, puede cortar frases a mitad.
    """
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Estrategia PARAGRAPH (recomendada)
# ---------------------------------------------------------------------------

def chunk_paragraph(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto por párrafos naturales (doble salto de línea).
    - Párrafos que superan 'size' se subdividen con chunk_fixed.
    - Párrafos cortos se agrupan hasta alcanzar 'size'.
    Produce chunks coherentes que respetan la estructura del documento.
    """
    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    chunks: List[str] = []
    current_group = ""

    for para in paragraphs:
        if len(para) > size:
            if current_group:
                chunks.append(current_group.strip())
                current_group = ""
            chunks.extend(chunk_fixed(para, size=size, overlap=overlap))
        else:
            separator = "\n\n" if current_group else ""
            candidate = current_group + separator + para
            if len(candidate) <= size:
                current_group = candidate
            else:
                if current_group:
                    chunks.append(current_group.strip())
                current_group = para

    if current_group:
        chunks.append(current_group.strip())

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Singleton del modelo semántico
# ---------------------------------------------------------------------------

_semantic_model = None


def _get_semantic_model():
    """
    Devuelve el modelo SentenceTransformer, cargándolo una sola vez (singleton).
    Evita recargar ~80MB del modelo en cada llamada a chunk_semantic.
    Devuelve None si sentence-transformers no está disponible.
    """
    global _semantic_model
    if _semantic_model is not None:
        return _semantic_model
    try:
        from sentence_transformers import SentenceTransformer
        _semantic_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        _log.info("[chunking] Modelo semántico all-MiniLM-L6-v2 cargado (singleton)")
        return _semantic_model
    except Exception as exc:
        _log.warning(
            "[chunking] Modelo all-MiniLM-L6-v2 no disponible (%s). "
            "Estrategia semantic usará fallback a paragraph.", exc
        )
        return None


# ---------------------------------------------------------------------------
# Estrategia SEMANTIC
# ---------------------------------------------------------------------------

def chunk_semantic(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto detectando cambios semánticos entre frases.
    Usa similitud coseno entre embeddings de frases adyacentes para detectar
    puntos de corte donde el tema cambia significativamente.
    Fallback automático a chunk_paragraph si el modelo no está disponible.
    """
    import numpy as np

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) <= 1:
        return [text.strip()] if text.strip() else []

    model = _get_semantic_model()
    if model is None:
        return chunk_paragraph(text, size=size, overlap=overlap)

    embeddings = model.encode(sentences, batch_size=32, show_progress_bar=False)

    def cosine_sim(a, b):
        denom = (np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom != 0 else 1.0

    similarities = [
        cosine_sim(embeddings[i], embeddings[i + 1])
        for i in range(len(embeddings) - 1)
    ]

    mean_sim  = float(np.mean(similarities))
    std_sim   = float(np.std(similarities))
    threshold = mean_sim - 0.5 * std_sim

    chunks: List[str] = []
    current_chunk: List[str] = [sentences[0]]

    for i, sim in enumerate(similarities):
        next_sentence = sentences[i + 1]
        current_text  = " ".join(current_chunk)
        if sim < threshold and len(current_text) >= size // 2:
            chunks.append(current_text.strip())
            current_chunk = [next_sentence]
        else:
            if len(current_text) + len(next_sentence) > size:
                chunks.append(current_text.strip())
                current_chunk = [next_sentence]
            else:
                current_chunk.append(next_sentence)

    if current_chunk:
        chunks.append(" ".join(current_chunk).strip())

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Capa de seguridad — evita chunks oversized antes del modelo de embedding
# ---------------------------------------------------------------------------

def _enforce_max_chunk_size(chunks: List[str], max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """
    Garantiza que ningún chunk supere max_chars caracteres.
    Crítico para código Python/SQL donde la división por frases no produce cortes.
    Los chunks oversized se subdividen automáticamente con chunk_fixed.
    """
    result: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            _log.warning(
                "[chunking] CHUNK_OVERSIZE: %d chars supera MAX_CHUNK_CHARS=%d. "
                "Subdividiendo automáticamente.",
                len(chunk), max_chars
            )
            result.extend(chunk_fixed(chunk, size=max_chars, overlap=0))
    return result


# ---------------------------------------------------------------------------
# Punto de entrada principal
# ---------------------------------------------------------------------------

def get_chunks(text: str, strategy: str = None, content_type: str = "text") -> List[str]:
    """
    Divide el texto usando la estrategia configurada.

    Args:
        text:         Texto a dividir (idealmente ya preprocesado con marcas ##/###).
        strategy:     Sobreescribe CHUNKING_STRATEGY si se proporciona.
                      Valores válidos: "fixed", "paragraph", "semantic".
        content_type: Tipo de contenido del bloque.
                      Los tipos atómicos (table, code, callout, etc.) se devuelven
                      como un único chunk sin cortes.

    Returns:
        Lista de strings, cada uno un chunk del texto original.
    """
    if not text or not text.strip():
        _log.warning("[chunking] get_chunks llamado con texto vacío")
        return []

    # Tipos atómicos: un bloque = un chunk, nunca se corta
    if content_type in ATOMIC_CONTENT_TYPES:
        chunk = text.strip()
        if len(chunk) > MAX_CHUNK_CHARS:
            _log.warning(
                "[chunking] ATOMIC_OVERSIZE: tipo '%s' tiene %d chars (MAX=%d). "
                "Se mantiene completo por coherencia semántica.",
                content_type, len(chunk), MAX_CHUNK_CHARS,
            )
        return [chunk]

    active = (strategy or CHUNKING_STRATEGY).lower()

    if active == "fixed":
        chunks = chunk_fixed(text)
    elif active == "semantic":
        chunks = chunk_semantic(text)
    else:  # paragraph (default y recomendado)
        chunks = chunk_paragraph(text)

    # Capa de seguridad: garantizar que ningún chunk supere MAX_CHUNK_CHARS
    final_chunks = _enforce_max_chunk_size(chunks)

    _log.debug(
        "[chunking] strategy='%s' texto=%d chars → %d chunks",
        active, len(text), len(final_chunks),
    )
    return final_chunks
"""
chunking.py
-----------
Módulo de chunking: divide el texto extraído de un documento en fragmentos (chunks)
antes de vectorizarlos y almacenarlos en Qdrant.

¿Por qué es necesario el chunking?
    Los modelos de embedding tienen un límite de tokens de entrada (~512 tokens para
    nomic-embed-text). Además, vectorizar bloques de texto demasiado grandes mezcla
    múltiples conceptos en un solo vector, lo que perjudica la precisión de la búsqueda.
    Dividir el texto en chunks pequeños permite:
      1. Respetar el límite de tokens del modelo de embedding.
      2. Recuperar solo el fragmento exacto relevante para cada pregunta.
      3. Citar la fuente con mayor precisión (página y posición dentro del documento).

Estrategias disponibles — seleccionables con la variable de entorno CHUNKING_STRATEGY:

  fixed (comportamiento original, CHUNKING_STRATEGY=fixed)
  -------------------------------------------------------
  Corta el texto cada CHUNK_SIZE caracteres sin importar el contenido.
  Añade CHUNK_OVERLAP caracteres de solapamiento entre chunks consecutivos para
  evitar que una idea quede partida justo en el límite sin contexto.
  Ventaja: simple y predecible.
  Inconveniente: puede cortar una frase o párrafo a mitad, perdiendo coherencia.
  Usar cuando: se necesita compatibilidad exacta con el comportamiento de Fase 1/2.

  paragraph (recomendado, CHUNKING_STRATEGY=paragraph)
  -----------------------------------------------------
  Divide el texto por párrafos naturales (doble salto de línea \\n\\n).
  Si un párrafo supera CHUNK_SIZE caracteres, lo subdivide internamente con 'fixed'.
  Agrupa párrafos cortos consecutivos hasta alcanzar CHUNK_SIZE para no generar
  chunks de una sola frase que aporten poco contexto al LLM.
  Ventaja: los chunks respetan la estructura del documento (ideas completas).
  Inconveniente: el tamaño de los chunks es variable.
  Usar cuando: los documentos tienen estructura de párrafos (contratos, manuales, informes).

  semantic (experimental, CHUNKING_STRATEGY=semantic)
  ----------------------------------------------------
  Calcula el embedding de cada frase con sentence-transformers (modelo all-MiniLM-L6-v2)
  y mide la similitud coseno entre frases adyacentes. Corta el texto en los puntos donde
  la similitud cae por debajo de un umbral estadístico (media - 0.5 * desviación típica),
  es decir, donde hay un cambio temático real.
  Ventaja: chunks con máxima coherencia semántica interna.
  Inconveniente: más lento en ingesta (requiere inferencia local de embeddings por frase).
                 Si sentence-transformers no está instalado, hace fallback a 'paragraph'.
  Usar cuando: los documentos mezclan temas dentro del mismo párrafo y se necesita
               la mayor precisión posible en la división.

Variables de entorno relevantes:
  CHUNKING_STRATEGY  : estrategia activa (fixed | paragraph | semantic). Default: paragraph.
  CHUNK_SIZE         : tamaño máximo de chunk en caracteres. Default: 600.
  CHUNK_OVERLAP      : solapamiento entre chunks en estrategia 'fixed'. Default: 100.

Punto de entrada principal:
  get_chunks(text, strategy=None) -> List[str]
      Devuelve la lista de chunks según la estrategia configurada.
      El parámetro 'strategy' sobreescribe CHUNKING_STRATEGY si se proporciona.
"""

import os
import re
import logging
from typing import List

_log = logging.getLogger(__name__)

CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE", 600))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 100))
CHUNKING_STRATEGY = os.getenv("CHUNKING_STRATEGY", "paragraph")

# Tipos de contenido atómicos: el bloque completo es siempre un único chunk.
# Nunca deben dividirse porque su significado depende de la integridad del bloque.
ATOMIC_CONTENT_TYPES = {"table", "callout", "footnote", "figure_caption", "glossary_entry", "code"}
# Límite de seguridad: ningún chunk puede superar este tamaño antes de ser enviado
# al modelo de embedding. Configurable vía env var. Valor conservador: 4000 chars
# (~1000-1500 tokens), muy por debajo del límite de 8192 tokens de nomic-embed-text.
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", 4000))


# ---------------------------------------------------------------------------
# Estrategia FIXED (comportamiento original)
# ---------------------------------------------------------------------------

def chunk_fixed(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto cada 'size' caracteres con 'overlap' de solapamiento.
    Equivale al chunk_text original.
    """
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap
    return chunks


# ---------------------------------------------------------------------------
# Estrategia PARAGRAPH
# ---------------------------------------------------------------------------

def chunk_paragraph(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto por párrafos (doble salto de línea).
    - Si un párrafo supera 'size', lo subdivide con chunk_fixed.
    - Agrupa párrafos cortos consecutivos hasta alcanzar 'size'.
    """
    # Separar por doble (o más) saltos de línea
    raw_paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]

    chunks: List[str] = []
    current_group = ""

    for para in paragraphs:
        if len(para) > size:
            # Vaciar el grupo actual antes de procesar el párrafo largo
            if current_group:
                chunks.append(current_group.strip())
                current_group = ""
            # Subdividir el párrafo grande con estrategia fixed
            chunks.extend(chunk_fixed(para, size=size, overlap=overlap))
        else:
            # Intentar agrupar párrafos cortos
            separator = "\n\n" if current_group else ""
            candidate = current_group + separator + para
            if len(candidate) <= size:
                current_group = candidate
            else:
                if current_group:
                    chunks.append(current_group.strip())
                current_group = para

    if current_group:
        chunks.append(current_group.strip())

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Singleton del modelo semántico — se carga una vez y se reutiliza
# ---------------------------------------------------------------------------

_semantic_model = None  # instancia SentenceTransformer, cargada bajo demanda

def _get_semantic_model():
    """
    Devuelve el modelo SentenceTransformer, cargándolo una sola vez.
    Subsiguientes llamadas devuelven la instancia cacheada en memoria.
    Esto evita recargar ~80MB del modelo en cada chunk (el bug original
    que causaba 52× 'Load pretrained SentenceTransformer' en el log).
    """
    global _semantic_model
    if _semantic_model is not None:
        return _semantic_model
    try:
        from sentence_transformers import SentenceTransformer
        _semantic_model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        _log.info("[chunking] Modelo semántico all-MiniLM-L6-v2 cargado en memoria (singleton).")
        return _semantic_model
    except Exception as exc:
        _log.warning(
            "Modelo all-MiniLM-L6-v2 no disponible (%s). "
            "Estrategia semantic usará fallback a paragraph.", exc
        )
        return None


# ---------------------------------------------------------------------------
# Estrategia SEMANTIC
# ---------------------------------------------------------------------------

def chunk_semantic(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto detectando cambios semánticos entre frases.
    Usa el singleton _get_semantic_model() para evitar recargar el modelo
    en cada llamada (fix del bug de 52× recarga en log).
    """
    import numpy as np

    # Separar en frases por puntuación básica
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) <= 1:
        return [text.strip()] if text.strip() else []

    model = _get_semantic_model()
    if model is None:
        return chunk_paragraph(text, size=size, overlap=overlap)

    embeddings = model.encode(sentences, batch_size=32, show_progress_bar=False)

    # Calcular similitud coseno entre frases adyacentes
    def cosine_sim(a, b):
        denom = (np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return 1.0
        return float(np.dot(a, b) / denom)

    similarities = [
        cosine_sim(embeddings[i], embeddings[i + 1])
        for i in range(len(embeddings) - 1)
    ]

    # Determinar umbrales de corte: similitudes por debajo de la media − 0.5*std
    mean_sim = float(np.mean(similarities))
    std_sim  = float(np.std(similarities))
    threshold = mean_sim - 0.5 * std_sim

    # Construir chunks agrupando frases entre puntos de corte
    chunks: List[str] = []
    current_chunk: List[str] = [sentences[0]]

    for i, sim in enumerate(similarities):
        next_sentence = sentences[i + 1]
        current_text = " ".join(current_chunk)

        # Cortar si: similitud baja Y el chunk ya tiene tamaño razonable
        if sim < threshold and len(current_text) >= size // 2:
            chunks.append(current_text.strip())
            current_chunk = [next_sentence]
        else:
            # Si el chunk supera el tamaño máximo, forzar corte
            if len(current_text) + len(next_sentence) > size:
                chunks.append(current_text.strip())
                current_chunk = [next_sentence]
            else:
                current_chunk.append(next_sentence)

    if current_chunk:
        chunks.append(" ".join(current_chunk).strip())

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Capa de seguridad — trunca chunks oversized antes de llegar al modelo
# ---------------------------------------------------------------------------

def _enforce_max_chunk_size(chunks: List[str], max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """
    Garantiza que ningún chunk supere max_chars caracteres.
    Si un chunk es demasiado grande (ej: celda de código sin párrafos ni puntuación),
    se subdivide recursivamente con chunk_fixed.
    Esto es especialmente crítico con CHUNKING_STRATEGY=semantic sobre código Python/SQL,
    donde la división por frases (`.!?`) no produce cortes en el código.
    """
    result: List[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            _log.warning(
                "CHUNK_OVERSIZE: chunk de %d chars supera MAX_CHUNK_CHARS=%d. "
                "Subdividiendo automáticamente con split de seguridad.",
                len(chunk), max_chars
            )
            sub = chunk_fixed(chunk, size=max_chars, overlap=0)
            result.extend(sub)
    return result


# ---------------------------------------------------------------------------
# Función principal — selecciona la estrategia según env var
# ---------------------------------------------------------------------------

def get_chunks(text: str, strategy: str = None, content_type: str = "text") -> List[str]:
    """
    Divide el texto usando la estrategia configurada.

    Args:
        text:         Texto a dividir.
        strategy:     Sobreescribe CHUNKING_STRATEGY si se proporciona.
        content_type: Tipo de contenido del bloque (text, table, code, etc.).
                      Los tipos atómicos (ATOMIC_CONTENT_TYPES) devuelven el
                      bloque completo como un único chunk sin ningún corte.
    """
    if not text or not text.strip():
        return []

    # Tipos atómicos: un bloque = un chunk, nunca se corta
    if content_type in ATOMIC_CONTENT_TYPES:
        chunk = text.strip()
        if len(chunk) > MAX_CHUNK_CHARS:
            _log.warning(
                "ATOMIC_OVERSIZE: chunk atómico de tipo '%s' tiene %d chars "
                "(MAX=%d). Se mantiene completo por coherencia semántica.",
                content_type, len(chunk), MAX_CHUNK_CHARS,
            )
        return [chunk]

    active = (strategy or CHUNKING_STRATEGY).lower()
    if active == "fixed":
        chunks = chunk_fixed(text)
    elif active == "semantic":
        chunks = chunk_semantic(text)
    else:  # paragraph (default y recomendado)
        chunks = chunk_paragraph(text)
    # Capa 1 de seguridad: garantizar que ningún chunk supere MAX_CHUNK_CHARS
    # antes de llegar al modelo de embedding (crítico para código y celdas ipynb).
    final_chunks = _enforce_max_chunk_size(chunks)
    _log.debug(
        "[chunking] strategy='%s' texto=%d chars → %d chunks",
        active, len(text), len(final_chunks),
    )
    return final_chunks
