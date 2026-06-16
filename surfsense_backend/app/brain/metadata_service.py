"""
brain/metadata_service.py
--------------------------
BrainMetadataService — reemplaza masters.py + vocabulary.py.

Lee dominios, subdomains, doc_types, entity_hints y vocabulary de PostgreSQL.
Caché en memoria por search_space_id: recarga automática cada CACHE_TTL_SECONDS.

Resolución multi-tenant:
  - Busca primero registros con search_space_id == space_id (específico del space).
  - Fallback a registros con search_space_id IS NULL (globales, seed por defecto).

Punto de entrada:
  meta_svc = BrainMetadataService(session_factory)
  await meta_svc.load(search_space_id=0)

  classification = meta_svc.classify_document(
      tags=["pyspark","delta"],
      keyphrases=["pipeline bronze"],
      title="pipeline_tte_etl.py",
      file_type=".py",
      search_space_id=0,
  )
  # → {"domain": "data-engineering", "doc_type": "pipeline"}

  normalized = meta_svc.normalize_tags(["spark","datawarehouse"], search_space_id=0)
  # → ["pyspark", "data-warehouse"]

NOTA: El documento original usa search_space_id: str = "". En SecondBrainSense
search_space_id es int (igual que el resto de SurfSense). Se usa int = 0 como
equivalente del string vacío del documento (espacio global/por defecto).
"""
import asyncio
import logging
import re
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = int(60 * 30)  # 30 min — reconfigurable vía env var
_MIN_SCORE_DOMAIN  = 2
_MIN_SCORE_DOCTYPE = 1.5


class _SpaceCache:
    """Caché en memoria para un search_space_id específico."""
    def __init__(self):
        self.domains:       list[dict] = []
        self.doc_types:     list[dict] = []
        self.vocab_alias:   dict[str, str] = {}   # alias → canonical
        self.vocab_set:     set[str] = set()       # todas las canónicas
        self.entity_hints:  list[dict] = []
        self.loaded_at:     float = 0.0

    def is_stale(self) -> bool:
        return (time.monotonic() - self.loaded_at) > CACHE_TTL_SECONDS


class BrainMetadataService:
    """
    Servicio singleton (por aplicación) para clasificación y vocabulario Brain.
    Instanciar una vez en el lifespan de FastAPI e inyectar vía dependencia.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._caches: dict[int, _SpaceCache] = {}

    # ── Carga / refresco ──────────────────────────────────────────────────

    async def load(self, search_space_id: int = 0) -> None:
        """Carga (o refresca) la caché para el search_space_id dado."""
        cache = self._caches.get(search_space_id) or _SpaceCache()
        if not cache.is_stale():
            return

        async with self._session_factory() as session:
            # Globales (NULL) + específicos del space → específico sobreescribe global
            domains   = await self._load_domains(session, search_space_id)
            doc_types = await self._load_doc_types(session, search_space_id)
            vocab     = await self._load_vocab(session, search_space_id)
            hints     = await self._load_hints(session, search_space_id)

        cache.domains       = domains
        cache.doc_types     = doc_types
        cache.vocab_alias   = vocab["alias"]
        cache.vocab_set     = vocab["canonical_set"]
        cache.entity_hints  = hints
        cache.loaded_at     = time.monotonic()

        self._caches[search_space_id] = cache
        log.info("[brain_meta] Caché cargada: space=%s domains=%d vocab=%d",
                 search_space_id or "global", len(domains), len(vocab["canonical_set"]))

    async def reload(self, search_space_id: int = 0) -> None:
        """Fuerza recarga ignorando TTL (útil tras edición desde admin UI)."""
        if search_space_id in self._caches:
            self._caches[search_space_id].loaded_at = 0.0
        await self.load(search_space_id)

    async def ensure_loaded(self, search_space_id: int = 0) -> _SpaceCache:
        """Carga si es necesario y devuelve la caché."""
        await self.load(search_space_id)
        return self._caches[search_space_id]

    # ── Clasificación de documento ───────────────────────────────────────────

    def classify_document(
        self,
        tags: list[str],
        keyphrases: list[str],
        title: str,
        file_type: str,
        search_space_id: int = 0,
    ) -> dict:
        """
        Clasifica un documento en domain + doc_type.
        Lógica equivalente a masters.classify_document() pero leyendo de PostgreSQL.
        Retorna: {"domain": str, "doc_type": str}
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        text_lower = " ".join([title] + tags + keyphrases).lower()

        # ── Dominio ─────────────────────────────────────────────────────────
        best_domain, best_domain_score = "other", 0.0
        for d in cache.domains:
            score = sum(1.0 for t in (d.get("signal_tags") or []) if t in tags)
            score += sum(0.5 for kw in (d.get("signal_kw") or []) if kw in text_lower)
            if score > best_domain_score:
                best_domain_score, best_domain = score, d["domain_key"]

        if best_domain_score < _MIN_SCORE_DOMAIN:
            best_domain = "other"

        # ── Tipo documental ───────────────────────────────────────────────
        best_type, best_type_score = "document", 0.0
        for dt in cache.doc_types:
            score = sum(1.0 for t in (dt.get("signal_tags") or []) if t in tags)
            score += sum(0.5 for kw in (dt.get("signal_kw") or []) if kw in text_lower)
            score += sum(2.0 for fmt in (dt.get("signal_formats") or []) if fmt == file_type)
            if score > best_type_score:
                best_type_score, best_type = score, dt["type_key"]

        if best_type_score < _MIN_SCORE_DOCTYPE:
            best_type = "document"

        return {"domain": best_domain, "doc_type": best_type}

    # ── Vocabulario ─────────────────────────────────────────────────────────

    def normalize_tags(
        self,
        raw_tags: list[str],
        cap: int = 10,
        search_space_id: int = 0,
    ) -> list[str]:
        """
        Normaliza tags: variante → canónica. Filtra las que no están en vocabulario.
        Equivalente a vocabulary.normalize_tags() pero leyendo de PostgreSQL.
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        seen, result = set(), []
        for tag in raw_tags:
            canonical = cache.vocab_alias.get(tag.lower(), tag.lower())
            if canonical in cache.vocab_set and canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
            if len(result) >= cap:
                break
        return result

    def match_text_to_vocab(
        self,
        text: str,
        cap: int = 20,
        search_space_id: int = 0,
    ) -> list[str]:
        """
        Detecta qué tags del vocabulario aparecen en el texto.
        Equivalente a vocabulary.match_text_to_vocab().
        """
        cache = self._caches.get(search_space_id) or _SpaceCache()
        text_lower = text.lower()
        found = []
        for canonical in cache.vocab_set:
            if canonical in text_lower:
                found.append(canonical)
            if len(found) >= cap:
                break
        return found

    def get_entity_hints(
        self,
        domain_key: Optional[str] = None,
        doc_type_key: Optional[str] = None,
        search_space_id: int = 0,
    ) -> list[dict]:
        """Devuelve los entity hints aplicables al dominio/tipo dado."""
        cache = self._caches.get(search_space_id) or _SpaceCache()
        return [
            h for h in cache.entity_hints
            if (h.get("domain_key") is None or h.get("domain_key") == domain_key)
            and (h.get("doc_type_key") is None or h.get("doc_type_key") == doc_type_key)
        ]

    # ── Loaders internos ───────────────────────────────────────────────────

    async def _load_domains(self, session: AsyncSession, space_id: int) -> list[dict]:
        from app.db import BrainDomain
        rows = await session.execute(
            select(BrainDomain).where(
                BrainDomain.is_active == True,
                (BrainDomain.search_space_id == None) |
                (BrainDomain.search_space_id == space_id)
            )
        )
        merged: dict[str, dict] = {}
        for d in rows.scalars().all():
            entry = {"domain_key": d.domain_key, "label": d.label,
                     "signal_tags": d.signal_tags or [], "signal_kw": d.signal_kw or []}
            if d.search_space_id is not None or d.domain_key not in merged:
                merged[d.domain_key] = entry
        return list(merged.values())

    async def _load_doc_types(self, session: AsyncSession, space_id: int) -> list[dict]:
        from app.db import BrainDocType
        rows = await session.execute(
            select(BrainDocType).where(
                BrainDocType.is_active == True,
                (BrainDocType.search_space_id == None) |
                (BrainDocType.search_space_id == space_id)
            )
        )
        merged: dict[str, dict] = {}
        for dt in rows.scalars().all():
            entry = {"type_key": dt.type_key, "label": dt.label,
                     "signal_tags": dt.signal_tags or [], "signal_kw": dt.signal_kw or [],
                     "signal_formats": dt.signal_formats or []}
            if dt.search_space_id is not None or dt.type_key not in merged:
                merged[dt.type_key] = entry
        return list(merged.values())

    async def _load_vocab(self, session: AsyncSession, space_id: int) -> dict:
        from app.db import BrainVocabulary
        rows = await session.execute(
            select(BrainVocabulary).where(
                BrainVocabulary.is_active == True,
                (BrainVocabulary.search_space_id == None) |
                (BrainVocabulary.search_space_id == space_id)
            )
        )
        alias_map: dict[str, str] = {}
        canonical_set: set[str] = set()
        seen: dict[str, bool] = {}
        for v in rows.scalars().all():
            canonical = v.canonical_tag.lower()
            if v.search_space_id is not None or canonical not in seen:
                seen[canonical] = True
                canonical_set.add(canonical)
                alias_map[canonical] = canonical
                for alias in (v.aliases or []):
                    alias_map[alias.lower()] = canonical
        return {"alias": alias_map, "canonical_set": canonical_set}

    async def _load_hints(self, session: AsyncSession, space_id: int) -> list[dict]:
        from app.db import BrainEntityHint
        rows = await session.execute(
            select(BrainEntityHint).where(
                BrainEntityHint.is_active == True,
                (BrainEntityHint.search_space_id == None) |
                (BrainEntityHint.search_space_id == space_id)
            )
        )
        return [
            {"hint_key": h.hint_key, "domain_key": h.domain_key,
             "doc_type_key": h.doc_type_key, "label": h.label,
             "patterns": h.patterns or [], "examples": h.examples or []}
            for h in rows.scalars().all()
        ]


# ---------------------------------------------------------------------------
# Singleton — inicializado en el lifespan (app.py)
# El documento propone guardarlo en app/brain/__init__.py.
# Por cohesión lo mantenemos en este módulo y lo exportamos desde __init__.py.
# ---------------------------------------------------------------------------

_brain_metadata_service: Optional["BrainMetadataService"] = None


def init_brain_metadata_service(session_factory: async_sessionmaker) -> "BrainMetadataService":
    """Inicializa el singleton. Llamar una vez en el lifespan de FastAPI."""
    global _brain_metadata_service
    _brain_metadata_service = BrainMetadataService(session_factory)
    log.info("[BrainMetadataService] Singleton inicializado")
    return _brain_metadata_service


def get_brain_metadata_service() -> "BrainMetadataService":
    """Devuelve el singleton. Usar como Depends(get_brain_metadata_service)."""
    if _brain_metadata_service is None:
        raise RuntimeError(
            "BrainMetadataService no inicializado. "
            "Llama a init_brain_metadata_service() en el lifespan de FastAPI."
        )
    return _brain_metadata_service

