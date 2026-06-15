"""
writer.py
---------
Gestiona la escritura, lectura y eliminación de ficheros .md (pasaportes semánticos)
en el directorio del brain. Cada documento ingestado genera un .md en /brain/.
"""
import os
import re
import json
import logging
import datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

BRAIN_DIR = os.getenv("BRAIN_DIR", "/data/brain")

# Emojis de dominio para index.md
_DOMAIN_EMOJI = {
    "engineering": "🔧",
    "business":    "💼",
    "functional":  "⚙️",
    "legal":       "⚖️",
    "data":        "📊",
    "other":       "📁",
}


def _slugify(text: str) -> str:
    """Convierte un nombre de fichero o URL en un slug válido para nombre de fichero."""
    # Quitar extensión si es un fichero
    # Detectar URL real (tiene ://). Los slugs generados desde URLs (ej: httpsollamacomlibrary.md)
    # empiezan con "http" pero NO contienen "://", por lo que se tratan como ficheros.
    name = text if "://" in text else os.path.splitext(os.path.basename(text))[0]
    # Limpiar caracteres no alfanuméricos
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    # Limitar longitud
    return slug[:80] if slug else "unnamed"


class BrainWriter:
    """
    Escribe y gestiona los ficheros .md del Second Brain en disco.
    """

    def __init__(self, brain_dir: str = None):
        self.brain_dir = brain_dir or BRAIN_DIR
        os.makedirs(self.brain_dir, exist_ok=True)

    def _get_path(self, source: str) -> str:
        """Genera la ruta completa del .md a partir del source."""
        slug = _slugify(source)
        return os.path.join(self.brain_dir, f"{slug}.md")

    def write(self, source: str, md_content: str, overwrite: bool = False) -> dict:
        """
        Escribe el .md en disco.

        Args:
            source: nombre del fichero original o URL
            md_content: contenido markdown generado
            overwrite: si True, sobreescribe; si False, devuelve warning

        Returns:
            dict con: path, status ('created' | 'exists_warning' | 'overwritten' | 'empty')
        """
        if not md_content or not md_content.strip():
            return {"path": "", "status": "empty"}

        path = self._get_path(source)

        if os.path.exists(path) and not overwrite:
            log.warning("El .md ya existe para '%s' en '%s'. No se sobreescribe.", source, path)
            return {"path": path, "status": "exists_warning"}

        status = "overwritten" if os.path.exists(path) else "created"

        with open(path, "w", encoding="utf-8") as f:
            f.write(md_content)

        log.info("Brain .md %s: '%s'", status, path)
        return {"path": path, "status": status}

    def read(self, source: str) -> str | None:
        """Lee el .md de un documento si existe. Retorna None si no existe."""
        path = self._get_path(source)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def list_all(self) -> list[dict]:
        """
        Lista todos los .md del brain con metadatos extraídos del frontmatter.
        Retorna lista de dicts con: filename, path, title, tags, source_origin.
        """
        results = []
        brain_path = Path(self.brain_dir)

        if not brain_path.exists():
            return results

        _EXCLUDED = {"index.md", "_graph.json"}

        for md_file in sorted(brain_path.glob("*.md")):
            if md_file.name in _EXCLUDED:
                continue
            meta = self._extract_frontmatter(md_file)
            results.append({
                "filename": md_file.name,
                "path": str(md_file),
                **meta,
            })

        return results

    def update(self, source: str, md_content: str) -> dict:
        """
        Actualiza el .md en disco (siempre sobreescribe).
        Actualiza el campo updated_at en el frontmatter si existe.

        Returns:
            dict con: path, status ('updated' | 'created' | 'empty')
        """
        if not md_content or not md_content.strip():
            return {"path": "", "status": "empty"}

        # Actualizar updated_at en el frontmatter
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        updated = re.sub(
            r"^(updated_at:\s*).*$",
            f"\\g<1>{now}",
            md_content,
            flags=re.MULTILINE,
        )

        return self.write(source, updated, overwrite=True)

    def delete(self, source: str) -> bool:
        """Elimina el .md de un documento. Retorna True si se eliminó, False si no existía."""
        path = self._get_path(source)
        log.info("[writer] delete CHECK path='%s'", path)
        if os.path.exists(path):
            os.remove(path)
            log.info("[writer] delete OK path='%s' → .md eliminado del disco", path)
            return True
        log.warning("[writer] delete SKIP path='%s' → fichero no encontrado en disco", path)
        return False

    def exists(self, source: str) -> bool:
        """Verifica si ya existe un .md para el source dado."""
        return os.path.exists(self._get_path(source))

    # ------------------------------------------------------------------
    # Generación de index.md (Obsidian-compatible)
    # ------------------------------------------------------------------

    def generate_index(self, docs: list[dict] = None) -> str:
        """
        Genera /data/brain/index.md con la tabla de contenidos del brain.
        El formato usa [[wikilinks]] compatibles con Obsidian.

        Args:
            docs: lista de metadatos (si None, llama a list_all())

        Returns:
            Ruta del index.md generado.
        """
        if docs is None:
            docs = self.list_all()

        # Filtrar el propio index.md y _graph.json
        docs = [d for d in docs if not d.get("filename", "").startswith(("index", "_"))]

        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        total   = len(docs)

        # Recopilar tags únicos
        all_tags: set = set()
        for d in docs:
            for t in (d.get("tags") or []):
                if t:
                    all_tags.add(str(t))

        lines = [
            "---",
            "auto_generated: true",
            f"updated_at: {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"total_docs: {total}",
            f"total_tags: {len(all_tags)}",
            "---",
            "",
            "# 🧠 Second Brain — Índice",
            "",
            f"*Actualizado: {now_str} | Documentos: {total} | Tags únicos: {len(all_tags)}*",
            "",
            "---",
            "",
        ]

        # Sección por dominio
        lines.append("## Por dominio")
        lines.append("")
        by_domain: dict = {}
        for d in docs:
            dom = d.get("domain") or "other"
            by_domain.setdefault(dom, []).append(d)

        for domain in sorted(by_domain.keys()):
            emoji = _DOMAIN_EMOJI.get(domain, "📁")
            lines.append(f"### {emoji} {domain.title()}")
            for d in sorted(by_domain[domain], key=lambda x: x.get("title", "")):
                slug   = d["filename"].replace(".md", "")
                title  = d.get("title") or slug
                tags   = d.get("tags") or []
                tag_str = " · ".join(f"#{t}" for t in tags[:4]) if tags else ""
                suffix = f" · {tag_str}" if tag_str else ""
                lines.append(f"- [[{slug}]] — {title}{suffix}")
            lines.append("")

        lines.append("---")
        lines.append("")

        # Sección por tags (los 20 más frecuentes)
        lines.append("## Por tags")
        lines.append("")
        tag_freq: dict = {}
        for d in docs:
            for t in (d.get("tags") or []):
                if t:
                    tag_freq[str(t)] = tag_freq.get(str(t), 0) + 1

        top_tags = sorted(tag_freq, key=lambda x: -tag_freq[x])[:20]
        for tag in top_tags:
            lines.append(f"### #{tag}")
            for d in docs:
                if tag in (d.get("tags") or []):
                    slug  = d["filename"].replace(".md", "")
                    title = d.get("title") or slug
                    lines.append(f"- [[{slug}]] — {title}")
            lines.append("")

        lines.append("---")
        lines.append("")

        # Sección por tipo
        lines.append("## Por tipo")
        lines.append("")
        by_type: dict = {}
        for d in docs:
            dt = d.get("type") or "technical_doc"
            by_type.setdefault(dt, []).append(d)
        for dtype in sorted(by_type.keys()):
            lines.append(f"### {dtype}")
            for d in sorted(by_type[dtype], key=lambda x: x.get("title", "")):
                slug  = d["filename"].replace(".md", "")
                title = d.get("title") or slug
                lines.append(f"- [[{slug}]] — {title}")
            lines.append("")

        # Sección por refresh_policy (destacar los que hay que actualizar)
        monthly_docs = [d for d in docs if d.get("refresh_policy") in ("monthly", "quarterly")]
        if monthly_docs:
            lines.append("---")
            lines.append("")
            lines.append("## Por refresh_policy (actualizar pronto)")
            lines.append("")
            for d in monthly_docs:
                slug   = d["filename"].replace(".md", "")
                title  = d.get("title") or slug
                policy = d.get("refresh_policy", "")
                lines.append(f"- [[{slug}]] — {title} `{policy}`")
            lines.append("")

        content = "\n".join(lines)
        index_path = os.path.join(self.brain_dir, "index.md")
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("index.md regenerado: %d documentos, %d tags.", total, len(all_tags))
        except Exception as exc:
            log.warning("Error generando index.md: %s", exc)
        return index_path

    def _extract_frontmatter(self, md_path: Path) -> dict:
        """
        Extrae todos los metadatos del frontmatter YAML del .md usando yaml.safe_load.
        Incluye: id, title, type, domain, subdomain, tags, importance, confidence,
                 refresh_policy, raw_ingest, embedding_scope, related, entities,
                 created_at, updated_at, source_origin.
        """
        meta = {
            "title":          "",
            "tags":           [],
            "source_origin":  "",
            "source_type":    "",
            "source_location": "",
            "source_repo":    "",
            "source_repo_path": "",
            "id":             "",
            "type":           "technical_doc",
            "domain":         "other",
            "subdomain":      "",
            "importance":     "medium",
            "confidence":     0.8,
            "refresh_policy": "never",
            "raw_ingest":     False,
            "embedding_scope": ["brain", "knowledge"],
            "related":        [],
            "entities":       [],
            "projects":       [],
            "created_at":     "",
            "updated_at":     "",
        }
        try:
            content = md_path.read_text(encoding="utf-8")
            # Eliminar BOM (UTF-8 con BOM escribe \ufeff al inicio)
            content = content.lstrip("\ufeff")
            # Saltar comentario HTML opcional al inicio (ej: <!-- filename: ... -->)
            stripped = re.sub(r"^<!--.*?-->\s*", "", content, flags=re.DOTALL)
            fm_match = re.match(r"^---\n(.*?)\n---", stripped, re.DOTALL)
            if not fm_match:
                return meta

            parsed = yaml.safe_load(fm_match.group(1)) or {}

            # Campos escalares simples
            for key in ("id", "type", "domain", "subdomain", "importance",
                        "confidence", "refresh_policy", "raw_ingest"):
                if key in parsed:
                    meta[key] = parsed[key]

            if "title" in parsed:
                meta["title"] = str(parsed["title"]).strip('"')

            # Campos de lista
            for key in ("tags", "embedding_scope", "related", "entities", "projects"):
                val = parsed.get(key)
                if isinstance(val, list):
                    meta[key] = [str(v) for v in val if v]
                elif isinstance(val, str) and val:
                    meta[key] = [val]

            # Timestamps
            for key in ("created_at", "updated_at"):
                if key in parsed:
                    meta[key] = str(parsed[key])

            # source.origin + campos de localización extendidos
            src = parsed.get("source")
            if isinstance(src, dict):
                meta["source_origin"]    = str(src.get("origin", ""))
                meta["source_type"]      = str(src.get("type", ""))
                meta["source_location"]  = str(src.get("location", ""))
                meta["source_repo"]      = str(src.get("repo", ""))
                meta["source_repo_path"] = str(src.get("repo_path", ""))
            elif isinstance(src, str):
                meta["source_origin"] = src

        except Exception as exc:
            log.warning("Error leyendo frontmatter de '%s': %s", md_path, exc)

        return meta
