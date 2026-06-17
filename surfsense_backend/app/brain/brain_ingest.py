import re
import datetime
import hashlib
import logging
import yaml
from qdrant_client.models import PointStruct
from app.brain.collections import BRAIN

log = logging.getLogger(__name__)


class BrainIngestor:
    def __init__(self, qdrant_client, embed_model, collection: str = None):
        self.qdrant_client = qdrant_client
        self.embed_model = embed_model
        # Si no se pasa colección explícita, usa BRAIN de collections.py
        self.collection = collection if collection is not None else BRAIN

    def chunk_by_sections(self, md_content: str):
        # Divide el .md por secciones que empiezan con '#' (H1, H2 o cualquier nivel)
        sections = []
        current_section = None
        current_text = []
        for line in md_content.splitlines():
            if line.startswith("#"):
                if current_section:
                    sections.append({
                        'section': current_section,
                        'text': "\n".join(current_text).strip()
                    })
                current_section = line.strip()
                current_text = []
            else:
                current_text.append(line)
        if current_section:
            sections.append({
                'section': current_section,
                'text': "\n".join(current_text).strip()
            })
        return sections

    def extract_tags_from_frontmatter(self, md_content: str):
        # Extrae tags del frontmatter YAML
        match = re.search(r'tags:\s*([\s\S]*?)\n(\w|---)', md_content)
        if match:
            tags_block = match.group(1)
            tags = re.findall(r'-\s*(\w+)', tags_block)
            return tags
        return []

    def _parse_frontmatter(self, md_content: str) -> dict:
        """Parsea el bloque YAML frontmatter entre delimitadores ---."""
        # Normalizar: eliminar BOM y homogenizar saltos de línea
        content = md_content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}

    def ingest_md(self, source: str, md_content: str, metadata: dict) -> int:
        """Vectoriza el .md por secciones ## en la colección brain. Devuelve chunks creados."""
        sections = self.chunk_by_sections(md_content)
        log.debug("[brain_ingest] source='%s' → %d secciones encontradas", source, len(sections))
        tags = self.extract_tags_from_frontmatter(md_content)
        fm = self._parse_frontmatter(md_content)
        log.debug("[brain_ingest] frontmatter keys=%s, tags=%s", list(fm.keys()), tags)
        ingested_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        vectors = []
        payloads = []
        for section in sections:
            text = section['text']
            if not text.strip():
                log.debug("[brain_ingest] Sección vacía omitida: '%s'", section['section'])
                continue
            vector = self.embed_model.embed(text)
            payload = {
                'text':           text,
                'source':         source,
                'section':        section['section'],
                'level':          1,
                'tags':           tags,
                'kb_id':          fm.get('id', ''),
                'type':           fm.get('type', ''),
                'domain':         fm.get('domain', ''),
                'subdomain':      fm.get('subdomain', ''),
                'importance':     fm.get('importance', 'medium'),
                'confidence':     float(fm.get('confidence', 0.5)),
                'refresh_policy': fm.get('refresh_policy', 'never'),
                'projects':       fm.get('projects') or [],
                'created_at':     str(fm.get('created_at', ingested_at)),
                'updated_at':     str(fm.get('updated_at', ingested_at)),
                'ingested_at':    ingested_at,
                **(metadata or {})
            }
            vectors.append(vector)
            payloads.append(payload)
        if vectors:
            points = [
                PointStruct(
                    id=int(hashlib.md5(f"{source}::{i}".encode()).hexdigest(), 16) % (10**15),
                    vector=vec,
                    payload=pay,
                )
                for i, (vec, pay) in enumerate(zip(vectors, payloads))
            ]
            log.debug("[brain_ingest] Haciendo upsert de %d puntos en colección '%s'", len(points), self.collection)
            self.qdrant_client.upsert(
                collection_name=self.collection,
                points=points,
            )
            log.info("[brain_ingest] Upsert completado: source='%s', collection='%s', chunks=%d", source, self.collection, len(points))
        else:
            log.warning("[brain_ingest] No se generaron chunks para source='%s' (secciones=%d, todas vacías o sin texto)", source, len(sections))
        return len(vectors)
