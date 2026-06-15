"""
graph.py
--------
BrainGraph — construye el grafo de relaciones entre documentos del brain.

El grafo se genera a partir de:
  - Campo 'related' del frontmatter (relaciones explícitas, peso 3)
  - Solapamiento de tags (relaciones semánticas, peso = nº tags compartidos)

El JSON resultante es compatible con D3.js y vis.js (nodos + edges).
Se persiste en /data/brain/_graph.json.
"""
import os
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

BRAIN_DIR = os.getenv("BRAIN_DIR", "/data/brain")

# Colores por dominio para visualización frontend
DOMAIN_COLORS = {
    "engineering": "#6d28d9",
    "business":    "#0284c7",
    "functional":  "#059669",
    "legal":       "#dc2626",
    "data":        "#d97706",
    "other":       "#64748b",
}

TYPE_ICONS = {
    "technical_doc": "📄",
    "contract":      "📋",
    "web":           "🌐",
    "note":          "📝",
    "repo":          "💻",
    "manual":        "📚",
    "report":        "📊",
}


class BrainGraph:
    """
    Construye y serializa el grafo de relaciones entre documentos del brain.
    """

    def build(self, docs: list[dict]) -> dict:
        """
        Construye el grafo a partir de los metadatos de los documentos.

        Args:
            docs: lista de dicts tal como la devuelve BrainWriter.list_all()

        Returns:
            {
                "nodes": [{"id", "label", "type", "domain", "color", "tags", ...}],
                "edges": [{"source", "target", "type", "weight", "label"}],
                "meta":  {"total_docs", "total_edges"},
            }
        """
        nodes = self._build_nodes(docs)
        edges = self._build_edges(docs)

        # Deduplicar edges: mismo par de nodos y mismo tipo → solo uno
        seen: set = set()
        dedup_edges = []
        for edge in edges:
            key = tuple(sorted([edge["source"], edge["target"]])) + (edge["type"],)
            if key not in seen:
                seen.add(key)
                dedup_edges.append(edge)

        log.debug(
            "[graph] build completado: %d nodos, %d edges (%d antes de dedup).",
            len(nodes), len(dedup_edges), len(edges),
        )

        return {
            "nodes": nodes,
            "edges": dedup_edges,
            "meta": {
                "total_docs":  len(nodes),
                "total_edges": len(dedup_edges),
            },
        }

    # ------------------------------------------------------------------
    # Construcción de nodos
    # ------------------------------------------------------------------

    def _build_nodes(self, docs: list[dict]) -> list[dict]:
        nodes = []
        for doc in docs:
            filename   = doc.get("filename", "")
            title      = doc.get("title") or filename.replace(".md", "").replace("-", " ").title()
            domain     = doc.get("domain", "other")
            doc_type   = doc.get("type", "technical_doc")
            tags       = doc.get("tags") or []
            importance = doc.get("importance", "medium")

            # Tag de serie (detectado desde el nombre de fichero)
            serie     = self._extract_series(filename)
            raw_tags  = tags if isinstance(tags, list) else []
            node_tags = raw_tags + ([f"serie:{serie}"] if serie and f"serie:{serie}" not in raw_tags else [])

            # Extraer resumen del cuerpo del .md (primeras 2 frases del texto)
            summary = ""
            doc_path = doc.get("path", "")
            if doc_path and os.path.exists(doc_path):
                try:
                    raw = Path(doc_path).read_text(encoding="utf-8").lstrip("\ufeff")
                    # Saltar frontmatter
                    import re as _re
                    body_match = _re.search(r"^---\n.*?\n---\s*\n(.*)", raw, _re.DOTALL)
                    body = body_match.group(1) if body_match else raw
                    # Saltar comentarios HTML y cabeceras
                    body = _re.sub(r"<!--.*?-->", "", body, flags=_re.DOTALL)
                    body = _re.sub(r"^#{1,6}[^\n]*\n?", "", body, flags=_re.MULTILINE)
                    body = body.strip()
                    # Extraer primeras palabras de texto limpio
                    plain = _re.sub(r"[*_`>\[\]#]", "", body).strip()
                    plain = " ".join(plain.split())
                    summary = plain[:240] + ("…" if len(plain) > 240 else "")
                except Exception:
                    pass

            nodes.append({
                "id":             filename,
                "label":          title,
                "type":           doc_type,
                "icon":           TYPE_ICONS.get(doc_type, "📄"),
                "domain":         domain,
                "color":          DOMAIN_COLORS.get(domain, DOMAIN_COLORS["other"]),
                "tags":           node_tags,
                "serie":          serie,
                "subdomain":      doc.get("subdomain", ""),
                "importance":     importance,
                "source_origin":  doc.get("source_origin", ""),
                "refresh_policy": doc.get("refresh_policy", "never"),
                "created_at":     str(doc.get("created_at", "")),
                "summary":        summary,
                # node size hint: high → 1.4, medium → 1.0, low → 0.7
                "size_factor":    {"high": 1.4, "medium": 1.0, "low": 0.7}.get(importance, 1.0),
            })
        return nodes

    # ------------------------------------------------------------------
    # Construcción de edges
    # ------------------------------------------------------------------

    def _build_edges(self, docs: list[dict]) -> list[dict]:
        edges = []
        filename_set = {doc.get("filename", "") for doc in docs}

        # 1. Relaciones explícitas desde 'related' en el frontmatter
        for doc in docs:
            src_id  = doc.get("filename", "")
            related = doc.get("related") or []
            if isinstance(related, str):
                related = [related]
            for ref in related:
                target_id = self._resolve_id(str(ref), filename_set)
                if target_id and target_id != src_id:
                    edges.append({
                        "source": src_id,
                        "target": target_id,
                        "type":   "related",
                        "weight": 3,
                        "label":  "relacionado",
                    })

        # 2. Solapamiento de tags (solo si comparten ≥ 1 tag)
        for i, doc1 in enumerate(docs):
            for j, doc2 in enumerate(docs):
                if i >= j:
                    continue
                tags1 = set(doc1.get("tags") or [])
                tags2 = set(doc2.get("tags") or [])
                shared = tags1 & tags2
                if shared:
                    edges.append({
                        "source":      doc1.get("filename", ""),
                        "target":      doc2.get("filename", ""),
                        "type":        "tag_overlap",
                        "weight":      len(shared),
                        "label":       ", ".join(sorted(shared)),
                        "shared_tags": sorted(shared),
                    })

        # 3. Conexiones por serie (documentos con mismo prefijo de serie en el nombre)
        series_groups: dict = {}
        for doc in docs:
            s = self._extract_series(doc.get("filename", ""))
            if s:
                series_groups.setdefault(s, []).append(doc)

        for serie, group in series_groups.items():
            for i, doc1 in enumerate(group):
                for j, doc2 in enumerate(group):
                    if i >= j:
                        continue
                    edges.append({
                        "source":      doc1.get("filename", ""),
                        "target":      doc2.get("filename", ""),
                        "type":        "serie",
                        "weight":      2,
                        "label":       f"serie: {serie}",
                        "shared_tags": [f"serie:{serie}"],
                    })

        return edges

    # ------------------------------------------------------------------
    # Detección de serie desde nombre de fichero
    # ------------------------------------------------------------------

    def _extract_series(self, filename: str) -> str:
        """
        Detecta si el fichero pertenece a una serie (curso, módulos, capítulos...)
        y devuelve el prefijo común de la serie.

        Ejemplos:
          'curso-arquitectura-dato-ia-modulo-01-teoria.md' → 'curso-arquitectura-dato-ia'
          'spark-fundamentals-chapter-03-joins.md'          → 'spark-fundamentals'
        """
        import re as _re
        slug = filename.replace(".md", "").replace("_", "-").lower()
        m = _re.match(
            r'^(.*?)[-_](?:modulo|module|capitulo|chapter|leccion|lesson|'
            r'parte|part|unidad|unit|session|sesion|mod|cap|lec|step|tema|semana|week)s?[-_]?\d+',
            slug, _re.IGNORECASE
        )
        if m:
            return m.group(1)
        return ""

    # ------------------------------------------------------------------
    # Resolución de referencias
    # ------------------------------------------------------------------

    def _resolve_id(self, ref: str, filename_set: set) -> str:
        """
        Intenta resolver una referencia (filename, slug, [[wikilink]])
        al id de nodo correspondiente.
        """
        ref = ref.strip()

        # [[wikilink]] → slug
        if ref.startswith("[[") and ref.endswith("]]"):
            ref = ref[2:-2].strip()

        if ref in filename_set:
            return ref
        if ref + ".md" in filename_set:
            return ref + ".md"

        return ""

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def save(self, graph_data: dict, brain_dir: str = None) -> str:
        """Persiste el grafo en _graph.json dentro del brain_dir."""
        bd   = brain_dir or BRAIN_DIR
        path = os.path.join(bd, "_graph.json")
        try:
            os.makedirs(bd, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(graph_data, f, ensure_ascii=False, indent=2)
            log.info(
                "Grafo guardado en '%s' (%d nodos, %d edges).",
                path,
                len(graph_data.get("nodes", [])),
                len(graph_data.get("edges", [])),
            )
        except Exception as exc:
            log.warning("No se pudo guardar _graph.json: %s", exc)
        return path
