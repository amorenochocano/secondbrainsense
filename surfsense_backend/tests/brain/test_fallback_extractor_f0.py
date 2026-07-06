"""
tests/brain/test_fallback_extractor_f0.py
------------------------------------------
Tests Fase 0 — FallbackExtractor y ExtractorFactory.is_known().

Cobertura:
  - FallbackExtractor con extensión desconocida (.vsdx, .msg, .unknown)
  - FallbackExtractor con fichero binario puro (sin extensión legible)
  - FallbackExtractor con fichero de texto con extensión desconocida
  - ExtractorFactory.get() devuelve FallbackExtractor para ext desconocida
  - ExtractorFactory.is_known() distingue extensiones conocidas vs desconocidas
  - Garantía: ningún fichero lanza excepción en el pipeline
"""
import os
import struct
import tempfile
from pathlib import Path

import pytest

from app.brain.extractors.factory import ExtractorFactory
from app.brain.extractors.fallback import FallbackExtractor


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_tmp(suffix: str, content: bytes) -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.write(fd, content)
    os.close(fd)
    return Path(path)


def _write_tmp_text(suffix: str, text: str) -> Path:
    return _write_tmp(suffix, text.encode("utf-8"))


# ── Tests FallbackExtractor ────────────────────────────────────────────────────

@pytest.mark.unit
class TestFallbackExtractor:

    def test_vsdx_binario(self):
        """Fichero .vsdx (ZIP interno) → binario puro → bloque metadata-only."""
        # ZIP magic bytes sin contenido real
        zip_magic = struct.pack("<I", 0x04034B50) + b"\x00" * 200
        path = _write_tmp(".vsdx", zip_magic)
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(path))
            assert len(blocks) >= 1
            assert all(b["content"] for b in blocks)
            # Debe indicar binario en metadata
            meta = blocks[0].get("metadata", {})
            assert meta.get("fallback_mode") == "binary"
            assert meta.get("extension") == ".vsdx"
        finally:
            path.unlink(missing_ok=True)

    def test_msg_binario(self):
        """Fichero .msg (Outlook, CFBF) → binario → metadata block."""
        # CFBF magic
        cfbf_magic = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 200
        path = _write_tmp(".msg", cfbf_magic)
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(path))
            assert len(blocks) >= 1
            meta = blocks[0].get("metadata", {})
            assert meta.get("fallback_mode") == "binary"
            assert ".msg" in blocks[0]["content"]
        finally:
            path.unlink(missing_ok=True)

    def test_unknown_extension_texto(self):
        """Fichero .unknown con contenido texto → extrae como texto."""
        texto = "CAPÍTULO 1\nIntroducción al sistema.\n\nCAPÍTULO 2\nDesarrollo.\n" * 5
        path = _write_tmp_text(".unknown", texto)
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(path))
            assert len(blocks) >= 1
            meta = blocks[0].get("metadata", {})
            assert meta.get("fallback_mode") == "text"
            assert meta.get("extension") == ".unknown"
            # Debe haber detectado secciones
            assert meta.get("section_count", 0) > 0
        finally:
            path.unlink(missing_ok=True)

    def test_binario_sin_extension(self):
        """Fichero binario sin extensión → metadata block."""
        binary_data = bytes(range(256)) * 10  # secuencia de bytes no-texto
        path = _write_tmp("", binary_data)
        # Renombrar para quitar extensión (mkstemp puede añadir sufijo vacío)
        no_ext_path = path.with_name(path.stem)
        path.rename(no_ext_path)
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(no_ext_path))
            assert len(blocks) >= 1
            assert all(isinstance(b["content"], str) for b in blocks)
        finally:
            no_ext_path.unlink(missing_ok=True)

    def test_fichero_vacio(self):
        """Fichero vacío → bloque vacío válido, sin excepción."""
        path = _write_tmp(".xyz", b"")
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(path))
            assert len(blocks) == 1
            assert isinstance(blocks[0]["content"], str)
        finally:
            path.unlink(missing_ok=True)

    def test_texto_sin_secciones(self):
        """Texto plano sin headings → bloque summary + bloque body."""
        texto = "Este es un texto sin secciones.\n" * 20
        path = _write_tmp_text(".dat", texto)
        try:
            extractor = FallbackExtractor()
            blocks = extractor.extract(str(path))
            assert len(blocks) >= 1
            assert blocks[0]["metadata"]["fallback_mode"] == "text"
        finally:
            path.unlink(missing_ok=True)


# ── Tests ExtractorFactory ─────────────────────────────────────────────────────

@pytest.mark.unit
class TestExtractorFactoryFallback:

    def test_get_devuelve_fallback_para_ext_desconocida(self):
        extractor = ExtractorFactory.get("documento.vsdx")
        assert isinstance(extractor, FallbackExtractor)

    def test_get_devuelve_fallback_sin_extension(self):
        extractor = ExtractorFactory.get("fichero_sin_ext")
        assert isinstance(extractor, FallbackExtractor)

    def test_get_no_lanza_excepcion_para_ext_desconocida(self):
        """Garantía principal de Fase 0: ningún formato lanza ValueError."""
        for ext in [".vsdx", ".msg", ".dwg", ".unknown", ".xyz123"]:
            extractor = ExtractorFactory.get(f"test{ext}")
            assert extractor is not None

    def test_is_known_true_para_extensiones_registradas(self):
        for ext in [".pdf", ".docx", ".xlsx", ".py", ".md", ".confluence"]:
            assert ExtractorFactory.is_known(ext) is True, f"{ext} debería ser conocida"

    def test_is_known_false_para_extensiones_desconocidas(self):
        for ext in [".vsdx", ".msg", ".dwg", ".unknown", ""]:
            assert ExtractorFactory.is_known(ext) is False, f"{ext} no debería ser conocida"

    def test_extract_completo_sin_excepcion(self):
        """extract() (pipeline completo) no lanza excepción para ext desconocida."""
        texto = "Contenido de prueba para fichero desconocido.\n" * 5
        path = _write_tmp_text(".vsdx_test_fake", texto)
        try:
            blocks = ExtractorFactory.extract(str(path))
            assert isinstance(blocks, list)
            assert len(blocks) >= 1
            assert all("content" in b for b in blocks)
        finally:
            path.unlink(missing_ok=True)
