"""
ejemplo.py — fixture para tests del pipeline Brain F1.
Fichero Python real basado en chunking.py del proyecto Second Brain.
Contiene funciones documentadas con docstrings, imports y lógica real.
"""

import os
import re
import logging
from typing import List

_log = logging.getLogger(__name__)

CHUNK_SIZE        = int(os.getenv("CHUNK_SIZE", 600))
CHUNK_OVERLAP     = int(os.getenv("CHUNK_OVERLAP", 100))
CHUNKING_STRATEGY = os.getenv("CHUNKING_STRATEGY", "paragraph")

ATOMIC_CONTENT_TYPES = {"table", "callout", "footnote", "code"}
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", 4000))


def chunk_fixed(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto cada 'size' caracteres con 'overlap' de solapamiento.

    Args:
        text:    Texto a dividir.
        size:    Tamaño máximo de cada chunk en caracteres.
        overlap: Número de caracteres de solapamiento entre chunks consecutivos.

    Returns:
        Lista de strings, cada uno un chunk del texto original.
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


def chunk_paragraph(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Divide el texto por párrafos naturales (doble salto de línea).
    Párrafos que superan 'size' se subdividen con chunk_fixed.
    Párrafos cortos consecutivos se agrupan hasta alcanzar 'size'.

    Args:
        text:    Texto a dividir.
        size:    Tamaño máximo de chunk en caracteres.
        overlap: Solapamiento para subdivisión de párrafos largos.

    Returns:
        Lista de chunks respetando límites de párrafo.
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


def get_chunks(text: str, strategy: str = None, content_type: str = "text") -> List[str]:
    """
    Punto de entrada principal del módulo de chunking.
    Selecciona automáticamente la estrategia según CHUNKING_STRATEGY
    o el parámetro 'strategy' si se proporciona.

    Args:
        text:         Texto a dividir (ya preprocesado con marcas ##/###).
        strategy:     Sobreescribe CHUNKING_STRATEGY si se proporciona.
                      Valores válidos: 'fixed', 'paragraph'.
        content_type: Tipo de contenido. Los tipos atómicos (table, code,
                      callout) se devuelven como un único chunk sin cortes.

    Returns:
        Lista de strings, cada uno un chunk del texto original.
    """
    if not text or not text.strip():
        _log.warning("[chunking] get_chunks llamado con texto vacío")
        return []
    if content_type in ATOMIC_CONTENT_TYPES:
        return [text.strip()]
    active = (strategy or CHUNKING_STRATEGY).lower()
    if active == "fixed":
        chunks = chunk_fixed(text)
    else:
        chunks = chunk_paragraph(text)
    _log.debug("[chunking] strategy='%s' texto=%d chars → %d chunks",
               active, len(text), len(chunks))
    return chunks
