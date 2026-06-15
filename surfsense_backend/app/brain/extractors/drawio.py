"""
drawio.py
---------
Extractor para ficheros Draw.io (.drawio).
Los ficheros .drawio son XML con nodos mxCell que contienen labels de componentes,
relaciones y metadatos de arquitecturas y diagramas.

Los diagramas pueden estar en dos formatos:
  - XML directo dentro de <diagram>
  - Base64 + zlib deflate comprimido (formato moderno de draw.io)
"""
import xml.etree.ElementTree as ET
import html
import re
import base64
import zlib
import logging
from urllib.parse import unquote
from .base import BaseExtractor

log = logging.getLogger(__name__)


class DrawioExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        """
        Parsea un fichero .drawio y extrae:
        - Nombres de las páginas/tabs del diagrama
        - Labels de todos los nodos (componentes)
        - Conexiones entre nodos (relaciones)
        """
        try:
            tree = ET.parse(source)
            root = tree.getroot()
        except ET.ParseError:
            log.warning("[drawio] '%s' → XML inválido, usando texto en bruto", source)
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                return [{"page": 1, "text": f.read()}]

        pages = []
        diagrams = root.findall(".//diagram")

        if not diagrams:
            # Formato antiguo: mxGraphModel directo
            diagrams = [root]

        for idx, diagram in enumerate(diagrams, 1):
            page_name = diagram.get("name", f"Página {idx}")
            nodes = []
            edges = []

            # Obtener el mxGraphModel — puede estar como XML hijo directo
            # o como contenido base64+deflate (formato moderno de draw.io)
            graph_model = diagram.find("mxGraphModel")
            if graph_model is None and diagram.text and diagram.text.strip():
                graph_model = self._decompress_diagram(diagram.text.strip())

            if graph_model is None:
                # Diagrama vacío o no parseable
                pages.append({
                    "content":      f"# Diagrama: {page_name}\n",
                    "content_type": "text",
                    "page":         idx,
                    "text":         f"# Diagrama: {page_name}\n",
                    "metadata": {
                        "format":      "drawio",
                        "page_name":   page_name,
                        "nodes_count": 0,
                        "edges_count": 0,
                    },
                })
                continue

            # Primera pasada: construir mapa id → label para resolver tripletas
            id_to_label: dict[str, str] = {}
            for cell in graph_model.iter("mxCell"):
                cid = cell.get("id", "")
                value = cell.get("value", "").strip()
                if cid and value:
                    id_to_label[cid] = self._clean_html(value)
            for obj in graph_model.iter("UserObject"):
                oid = obj.get("id", "")
                label = obj.get("label", "").strip()
                if oid and label:
                    id_to_label[oid] = self._clean_html(label)

            # Segunda pasada: nodos y edges con tripletas legibles
            for cell in graph_model.iter("mxCell"):
                value = cell.get("value", "").strip()
                if not value:
                    continue

                clean_value = self._clean_html(value)
                if not clean_value:
                    continue

                if cell.get("edge") == "1":
                    src_id = cell.get("source", "")
                    tgt_id = cell.get("target", "")
                    src_label = id_to_label.get(src_id, src_id) or src_id
                    tgt_label = id_to_label.get(tgt_id, tgt_id) or tgt_id
                    relation = clean_value if clean_value else "→"
                    if src_label and tgt_label:
                        edges.append(f"  {src_label} → [{relation}] → {tgt_label}")
                    else:
                        edges.append(f"  [{relation}] {src_id} → {tgt_id}")
                else:
                    style = cell.get("style", "")
                    node_type = self._infer_type(style)
                    # Extraer tooltip si existe
                    tooltip = cell.get("tooltip", "").strip()
                    if tooltip:
                        nodes.append(f"  [{node_type}] {clean_value} — {tooltip}")
                    else:
                        nodes.append(f"  [{node_type}] {clean_value}")

            # Buscar UserObject (nodos con metadatos)
            for obj in graph_model.iter("UserObject"):
                label = obj.get("label", "").strip()
                if label:
                    clean_label = self._clean_html(label)
                    if clean_label:
                        tooltip = obj.get("tooltip", "").strip()
                        if tooltip:
                            nodes.append(f"  [component] {clean_label} — {tooltip}")
                        else:
                            nodes.append(f"  [component] {clean_label}")

            # Construir texto de la página
            parts = [f"# Diagrama: {page_name}\n"]
            if nodes:
                parts.append("## Componentes:")
                parts.extend(nodes)
            if edges:
                parts.append("\n## Conexiones:")
                parts.extend(edges)

            text = "\n".join(parts)
            if text.strip():
                pages.append({
                    "content":      text,
                    "content_type": "text",
                    "page":         idx,
                    # Backward compat: campo 'text' para extractores antiguos
                    "text":         text,
                    "metadata": {
                        "format":      "drawio",
                        "page_name":   page_name,
                        "nodes_count": len(nodes),
                        "edges_count": len(edges),
                    },
                })

        if not pages:
            log.warning("[drawio] '%s' → diagrama vacío o sin contenido legible", source)
            placeholder = "(diagrama vacío)"
            return [{
                "content":      placeholder,
                "content_type": "text",
                "page":         1,
                "text":         placeholder,
                "metadata":     {"format": "drawio", "nodes_count": 0, "edges_count": 0},
            }]
        log.debug("[drawio] '%s' → %d páginas", source, len(pages))
        return pages

    def _decompress_diagram(self, encoded: str):
        """
        Descomprime el contenido de un <diagram> en formato moderno de draw.io:
        base64(zlib_deflate(urlencode(xml)))
        Retorna un ElementTree Element (mxGraphModel) o None si falla.
        """
        try:
            compressed = base64.b64decode(encoded)
            # draw.io usa deflate raw (wbits=-15)
            xml_urlencoded = zlib.decompress(compressed, -15).decode("utf-8")
            xml_str = unquote(xml_urlencoded)
            return ET.fromstring(xml_str)
        except Exception:
            return None

    def _clean_html(self, text: str) -> str:
        """Elimina tags HTML y decodifica entidades."""
        # Decodificar entidades HTML
        text = html.unescape(text)
        # Eliminar tags HTML
        text = re.sub(r"<[^>]+>", " ", text)
        # Limpiar espacios
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _infer_type(self, style: str) -> str:
        """Infiere el tipo de componente según el estilo del nodo."""
        style_lower = style.lower()
        if "shape=cylinder" in style_lower or "database" in style_lower:
            return "database"
        if "cloud" in style_lower:
            return "cloud"
        if "shape=process" in style_lower or "subprocess" in style_lower:
            return "process"
        if "shape=document" in style_lower:
            return "document"
        if "group" in style_lower or "swimlane" in style_lower:
            return "group"
        if "arrow" in style_lower or "edge" in style_lower:
            return "connector"
        return "component"
