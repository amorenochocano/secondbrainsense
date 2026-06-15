"""
xml_ext.py
----------
Extractor para ficheros XML (.xml) que NO son draw.io.

v2 — Mejora respecto a v1:
  - SALIDA LEGIBLE: v1 producía texto con indentación XML cruda
    (`<config> <db host="localhost">`), difícil de consumir para un LLM.
    v2 genera salida estructurada estilo YAML/markdown más legible:
      'config:
         db:
           host: localhost'
  - Detecta XML que contiene principalmente datos (text-heavy) vs estructural
    (attributes-heavy) y formatea acorde.
  - Mantiene comportamiento legacy: 1 bloque por hijo directo del root.
"""
import logging
from .base import BaseExtractor

log = logging.getLogger(__name__)

try:
    from lxml import etree as ET
    _LXML = True
except ImportError:
    import xml.etree.ElementTree as ET  # fallback stdlib
    _LXML = False


class XmlExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        try:
            if _LXML:
                tree = ET.parse(source, ET.XMLParser(recover=True, encoding=None))
            else:
                tree = ET.parse(source)
            root = tree.getroot()
        except Exception:
            log.warning("[xml] '%s' → XML inválido, usando texto en bruto", source)
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return [{"page": 1, "text": content, "content": content, "content_type": "text"}]

        children = list(root)
        if not children:
            # Root sin hijos: un único bloque
            content = self._format_element(root, depth=0)
            log.debug("[xml] '%s' → 1 bloque (root sin hijos)", source)
            return [{
                "page": 1,
                "text": content,
                "content": content,
                "content_type": "text",
                "metadata": {"format": "xml", "root": self._localname(root.tag)},
            }]

        blocks = []
        root_name = self._localname(root.tag)
        for page_num, child in enumerate(children, 1):
            content = self._format_element(child, depth=0)
            if not content.strip():
                continue
            child_name = self._localname(child.tag)
            blocks.append({
                "page": page_num,
                "text": content,
                "content": content,
                "content_type": "text",
                "metadata": {
                    "format": "xml",
                    "root": root_name,
                    "element": child_name,
                    "section": child_name,
                },
            })

        if not blocks:
            log.warning("[xml] '%s' → sin texto extraído", source)
            return [{"page": 1, "text": "", "content": "", "content_type": "text"}]
        log.debug("[xml] '%s' → %d bloques (uno por hijo de root)", source, len(blocks))
        return blocks

    # ------------------------------------------------------------------

    @staticmethod
    def _localname(tag) -> str:
        if isinstance(tag, str) and "}" in tag:
            return tag.split("}")[-1]
        return str(tag) if isinstance(tag, str) else ""

    def _format_element(self, element, depth: int = 0, max_depth: int = 8) -> str:
        """
        Formatea un elemento XML como texto estructurado tipo YAML.

        Ejemplo de salida:
          config:
            db:
              host: localhost
              port: 5432
            cache:
              ttl: 3600
        """
        if depth > max_depth:
            return ""

        tag = self._localname(element.tag)
        if not tag:
            return ""

        indent = "  " * depth
        lines: list[str] = []

        # Texto directo del elemento (saltar si solo whitespace)
        text = (element.text or "").strip()

        # Atributos relevantes (filtrar muy largos como base64)
        attrs = []
        if element.attrib:
            for k, v in element.attrib.items():
                k_local = self._localname(k)
                vs = str(v)
                if len(vs) < 200:
                    attrs.append((k_local, vs))

        children = list(element)

        # Caso 1: elemento "hoja" con solo texto
        if text and not children and not attrs:
            lines.append(f"{indent}{tag}: {text}")
            return "\n".join(lines)

        # Caso 2: solo atributos sin hijos ni texto
        if attrs and not children and not text:
            attr_str = ", ".join(f"{k}={v}" for k, v in attrs)
            lines.append(f"{indent}{tag}: ({attr_str})")
            return "\n".join(lines)

        # Caso 3: estructura compleja (hijos y/o atributos y/o texto)
        header = f"{indent}{tag}:"
        if attrs:
            attr_str = ", ".join(f"{k}={v}" for k, v in attrs[:4])
            header += f"  [{attr_str}]"
        lines.append(header)

        if text:
            lines.append(f"{indent}  # {text[:200]}")

        # Procesar hijos
        for i, child in enumerate(children):
            if i >= 30:
                lines.append(f"{indent}  ... ({len(children) - 30} más)")
                break
            child_str = self._format_element(child, depth + 1, max_depth)
            if child_str:
                lines.append(child_str)

        # Tail (texto que sigue al cierre, pertenece al padre)
        tail = (element.tail or "").strip()
        if tail and depth > 0:
            lines.append(f"{indent}{tail}")

        return "\n".join(lines)
