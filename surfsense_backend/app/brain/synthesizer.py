"""
synthesizer.py  (v7 — Planner-aware + Chunked completo)
--------------------------------------------------------
Motor de síntesis híbrido con planificador v5.

CAMBIOS v7 respecto a v6:
  - Implementación completa del flujo CHUNKED en el synthesizer:
      * Call overview → todas las secciones EXCEPTO Core Knowledge
      * Calls B..N → cada chunk produce subsecciones ### de Core Knowledge
      * Merge: sustituye [procesando-chunked] por la concatenación de CK chunks
  - Logs alineados al estilo del resto del sistema (sin prefijos [v5])

CAMBIOS v6 respecto a v3:
  - Integra get_synthesis_calls() del paquete brain.prompts
  - El planner decide automáticamente N llamadas al LLM según:
      * perfil del modelo (small/medium/large/claude)
      * tamaño del documento preprocesado
      * número de entities a documentar
      * naturaleza del tipo (homogéneo vs heterogéneo)
  - Soporta el placeholder {call1_result} para encadenar llamadas
  - Mantiene model_profiles (v3) para detección de tier por modelo

FLAG DE CONTROL:
  SYNTHESIS_V5_ENABLED=true  (defecto) → planner v5 activo
  SYNTHESIS_V5_ENABLED=false           → comportamiento idéntico a v3

PLANES POSIBLES:
  single-call : 1 llamada con todas las secciones (small, large, claude)
  2-calls     : Summary+CK → resto (medium con docs heterogéneos medianos)
  chunked     : 1 overview + N chunks de CK (cualquier perfil con docs grandes)

Contratos:
  synthesize(source, file_type, full_text, blocks=[], ...) → dict con .md final
"""
import os
import re
import logging
import datetime
import json

from app.brain.prompts import (
    get_focused_synthesis_prompt,
    get_overview_synthesis_prompt,
    get_chunk_synthesis_prompt,
    split_doc_text_for_chunked,
    _preprocess_text,
    _MAX_CHARS,
    _DEFAULT_MAX_CHARS,
    build_doc_focused_text,
    # v5 — planner-based multi-call
    get_synthesis_calls,
    explain_plan,
    build_chunk_call,
    get_spec,
)
from app.brain.passport_builder import build_partial_passport
from app.brain.vocabulary import normalize_tags
from app.brain.llm_client import llm_client, DEFAULT_PROVIDER, SYNTHESIS_MODEL as _DEFAULT_SYNTHESIS_MODEL

log = logging.getLogger(__name__)

SYNTHESIS_ENABLED = os.getenv("SYNTHESIS_ENABLED", "true").lower() == "true"
SYNTHESIS_HYBRID  = os.getenv("SYNTHESIS_HYBRID",  "true").lower() == "true"
SYNTHESIS_V5_ENABLED = os.getenv("SYNTHESIS_V5_ENABLED", "true").lower() == "true"

# Fallback para chunk trigger cuando model_profiles no está disponible
_LARGE_CONTEXT_PROVIDERS = {"claude", "openai", "azure_openai"}


def _slugify(text: str) -> str:
    name = os.path.splitext(os.path.basename(text))[0] if not text.startswith("http") else text
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] if slug else "unnamed"


def _get_model_profile(model_name: str, provider: str):
    """
    Carga el ModelProfile para el modelo activo.
    Retorna None con gracia si model_profiles.py no está instalado —
    en ese caso synthesizer se comporta exactamente como v2.5.
    """
    try:
        from app.brain.model_profiles import get_profile
        if provider in _LARGE_CONTEXT_PROVIDERS:
            return get_profile("claude-sonnet")   # siempre perfil claude para APIs
        return get_profile(model_name)
    except ImportError:
        return None


def _inject_search_space_id(md_content: str, search_space_id: str) -> str:
    """
    Inyecta o actualiza search_space_id en el bloque YAML frontmatter.
    Si ya existe el campo, lo reemplaza. Si no, lo inserta antes del cierre ---.
    """
    if not search_space_id or not md_content:
        return md_content

    if re.search(r"^search_space_id:", md_content, re.MULTILINE):
        return re.sub(
            r"^(search_space_id:).*$",
            f'search_space_id: "{search_space_id}"',
            md_content,
            flags=re.MULTILINE,
        )

    match = re.search(r"^---\n(.*?)\n---", md_content, re.DOTALL)
    if match:
        insert_pos = match.end(1)
        return (
            md_content[:insert_pos]
            + f'\nsearch_space_id: "{search_space_id}"'
            + md_content[insert_pos:]
        )

    return md_content


def _ensure_frontmatter(md_content: str, source: str, file_type: str, metadata: dict) -> str:
    """
    Garantiza que el .md tenga un bloque YAML frontmatter válido.
    Sin cambios respecto a v2.5.
    """
    md_content = md_content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    stripped = md_content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        stripped = inner.strip()
        md_content = stripped
    if stripped.startswith("---"):
        second = stripped.find("\n---", 3)
        if second != -1:
            return md_content
        body = stripped[stripped.find("\n") + 1:]
        log.warning("Frontmatter del LLM sin cierre '---'; se regenera para '%s'.", source)
    else:
        body = md_content

    slug = _slugify(source)
    kb_id = f"kb_{slug}"
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    _GENERIC_SYNTH_TITLES = {
        "summary", "resumen", "overview", "index", "índice",
        "introduction", "introducción", "contents", "contenido",
        "readme", "untitled", "sin título", "página", "page",
        "descripción funcional", "descripcion funcional",
        "descripción", "descripcion", "functional description",
        "documentación", "documentacion", "documentation",
        "notas", "notes", "draft", "borrador",
    }

    filename_title = slug.replace("-", " ").title()
    raw_title = filename_title

    if source.startswith("http"):
        title_match = re.search(r"^#{1,2}\s+(.+)$", body, re.MULTILINE)
        if title_match:
            candidate = re.sub(r"[^\w\s\-(),.:;/&]", "", title_match.group(1)).strip()
            if candidate and candidate.lower() not in _GENERIC_SYNTH_TITLES:
                raw_title = candidate
        else:
            from urllib.parse import urlparse as _urlparse
            _path = _urlparse(source).path.rstrip("/")
            _seg  = _path.split("/")[-1] if _path else ""
            raw_title = _seg.replace("-", " ").title() if _seg else filename_title
    else:
        title_match = re.search(r"^#{1,2}\s+(.+)$", body, re.MULTILINE)
        if title_match:
            candidate = re.sub(r"[^\w\s\-(),.:;/&]", "", title_match.group(1)).strip()
            candidate_lower = candidate.lower()
            is_generic = (
                not candidate or len(candidate) < 6
                or candidate_lower in _GENERIC_SYNTH_TITLES
                or any(candidate_lower.startswith(kw) for kw in _GENERIC_SYNTH_TITLES)
            )
            if not is_generic:
                slug_norm = slug.replace("-", "").lower()
                h1_norm   = re.sub(r"[^\w]", "", candidate_lower)
                if slug_norm in h1_norm or len(candidate) > len(filename_title) + 10:
                    raw_title = candidate

    title = re.sub(r"[^\w\s\-(),.:;/&]", "", raw_title).strip() or slug
    ext_type_map = {
        "pdf": "technical_doc", "docx": "technical_doc", "pptx": "manual",
        "xlsx": "technical_doc", "ipynb": "repo", "py": "repo",
        "md": "note", "web": "web",
    }
    doc_type = ext_type_map.get(file_type.lower().lstrip("."), "technical_doc")

    frontmatter = (
        "---\n"
        f"id: {kb_id}\n"
        f"title: \"{title}\"\n"
        f"type: {doc_type}\n"
        f"domain: {metadata.get('domain', 'other')}\n"
        f"subdomain: \"{metadata.get('subdomain', '')}\"\n"
        "source:\n"
        "  type: file\n"
        f"  origin: {source}\n"
        f"  format: {file_type}\n"
        f"created_at: {now}\n"
        f"updated_at: {now}\n"
        "importance: medium\n"
        "confidence: 0.8\n"
        "refresh_policy: on_demand\n"
        "raw_ingest: false\n"
        "embedding_scope:\n"
        "  - brain\n"
        "  - knowledge\n"
        "tags: []\n"
        "entities: []\n"
        "related: []\n"
        "---\n"
    )
    log.info("Frontmatter generado programáticamente para '%s' (kb_id=%s).", source, kb_id)
    return frontmatter + "\n" + body


class DocumentSynthesizer:
    """
    Orquesta síntesis híbrida: pasaporte parcial programático + LLM solo para narrativa.
    """

    def __init__(self):
        pass

    def synthesize(
        self,
        source: str,
        file_type: str,
        full_text: str,
        blocks: list[dict] | None = None,
        metadata: dict | None = None,
        provider: str | None = None,
        model: str | None = None,
        current_md: str | None = None,
        ingest_metadata: dict | None = None,
        search_space_id: str = "",
    ) -> dict:
        """
        Genera pasaporte semántico (FLUJO HÍBRIDO v3.0).

        Args:
            source:          nombre fichero
            file_type:       extensión
            full_text:       texto completo extraído
            blocks:          bloques tipados si existen
            metadata:        metadatos del extractor
            provider:        "ollama" | "claude" | "hybrid" | None
            model:           modelo Ollama específico (sobreescribe SYNTHESIS_MODEL)
            current_md:      contenido actual del .md (modo mejora)
            ingest_metadata: metadatos del ingest router

        Returns:
            {"md_content": str, "tags": list, "entities": list, ...}
        """
        if not SYNTHESIS_ENABLED:
            log.info("SYNTHESIS_ENABLED=false, generando frontmatter mínimo.")
            result = self._minimal_passport(source, file_type, metadata or {})
            result["md_content"] = _inject_search_space_id(result["md_content"], search_space_id)
            return result

        if not full_text or not full_text.strip():
            log.warning("Texto vacío para síntesis de '%s'.", source)
            return {"md_content": "", "tags": [], "entities": [], "drill_down_triggers": []}

        metadata = metadata or {}
        blocks = blocks or []
        effective_provider = provider or DEFAULT_PROVIDER
        effective_model    = model or _DEFAULT_SYNTHESIS_MODEL

        # ─── Cargar perfil del modelo ─────────────────────────────────────────
        # Si model_profiles.py no está instalado, profile=None y el comportamiento
        # es idéntico a v2.5 (retrocompatible).
        profile = _get_model_profile(effective_model, effective_provider)
        prompt_tier = profile.prompt_tier if profile else "large"

        if profile:
            log.info(
                "ModelProfile activo: model=%s tier=%s ctx=%dK doc_budget=%dK improve=%s",
                effective_model, profile.prompt_tier,
                profile.context_tokens // 1000,
                profile.context_chars // 1000,
                "✓" if profile.supports_improve_mode else "✗",
            )

        # =====================================================================
        # PASO 1: Extracción programática (SIN LLM)
        # =====================================================================
        log.info("Paso 1/2 [PROGRAMMATIC]: Extrayendo frontmatter, entities, tags...")
        try:
            _im = ingest_metadata or {}
            passport_result = build_partial_passport(
                source=source,
                file_type=file_type,
                blocks=blocks,
                full_text=full_text,
                metadata=metadata,
                source_location=_im.get("ingest_path", ""),
                source_type=_im.get("ingest_origin", ""),
                source_repo=_im.get("source_repo", ""),
                source_repo_path=_im.get("source_repo_path", ""),
            )
            partial_passport = passport_result["passport_partial"]
            entities   = passport_result["entities"]
            tags_base  = passport_result["tags_base"]
            triggers   = passport_result["triggers"]
        except Exception as exc:
            log.error("Error en extracción programática: %s", exc)
            return self._minimal_passport(source, file_type, metadata)

        log.info(
            "Pasaporte parcial: %d entities, %d tags, %d triggers",
            len(entities), len(tags_base), len(triggers),
        )

        # =====================================================================
        # PASO 2: Pre-procesar texto ajustando max_chars al perfil del modelo
        # =====================================================================
        log.info(
            "Paso 2/2 [LLM]: provider='%s' model='%s' tier='%s'",
            effective_provider, effective_model, prompt_tier,
        )

        ft = file_type.lower().lstrip(".")

        # max_chars para el processed_text:
        #   - type_max: techo por tipo de fichero (_MAX_CHARS), refleja cuánto
        #     texto ÚTIL genera build_focused para ese tipo. Para docx/pdf es
        #     50K porque build_focused genera toda la jerarquía de headings.
        #   - Si hay perfil del modelo: usar el menor entre type_max y
        #     context_chars para no sobrepasar ninguno de los dos límites.
        #   - Sin perfil: usar type_max directamente.
        # NOTA: no usar min() ciego aquí — context_chars suele ser mucho mayor
        # que type_max para modelos medium/claude, y type_max es el límite real.
        type_max = _MAX_CHARS.get(ft, _DEFAULT_MAX_CHARS)
        if profile:
            # Solo limitar por context_chars si es menor que type_max
            # (modelos pequeños pueden tener context_chars < type_max)
            max_chars = min(profile.context_chars, type_max) if profile.context_chars < type_max else type_max
        else:
            max_chars = type_max

        log.info(
            "[preprocess] '%s' ft=%s type_max=%d context_chars=%s max_chars=%d",
            source, ft, type_max,
            profile.context_chars if profile else "N/A",
            max_chars,
        )

        _DOC_RICH_TYPES = {
            "ipynb", "py", "sql",
            "json", "xml",
            "csv", "xlsx",
            "pdf", "docx", "pptx", "html",
            "md", "txt",
            "drawio",
        }
        if ft in _DOC_RICH_TYPES and blocks:
            processed_text = build_doc_focused_text(blocks, full_text, ft, max_chars)
        else:
            processed_text = _preprocess_text(full_text, ft, max_chars)

        # ─── current_md: desactivar si el perfil no lo soporta ───────────────
        effective_current_md = current_md
        if profile and not profile.supports_improve_mode:
            if current_md:
                log.info(
                    "Modo mejora desactivado para '%s' (perfil=%s, contexto insuficiente).",
                    effective_model, profile.prompt_tier,
                )
            effective_current_md = None

        # =====================================================================
        # PASO 2b: Síntesis fragmentada para documentos grandes
        # chunk_trigger viene del perfil si está disponible,
        # sino del comportamiento v2.5 (variable global / override por provider).
        # =====================================================================
        if profile:
            chunk_trigger = profile.chunk_trigger
            chunk_size    = profile.chunk_size
        else:
            # Comportamiento v2.5
            from app.brain.prompts import SYNTHESIS_CHUNK_TRIGGER, SYNTHESIS_CHUNK_SIZE
            if effective_provider in _LARGE_CONTEXT_PROVIDERS:
                _env_key = f"SYNTHESIS_CHUNK_TRIGGER_{effective_provider.upper()}"
                chunk_trigger = int(os.getenv(_env_key, "99999"))
            else:
                chunk_trigger = SYNTHESIS_CHUNK_TRIGGER
            chunk_size = SYNTHESIS_CHUNK_SIZE

        if len(processed_text) > chunk_trigger and prompt_tier != "small":
            log.info(
                "Doc grande (%d chars > trigger=%d, provider=%s): síntesis fragmentada para '%s'",
                len(processed_text), chunk_trigger, effective_provider, source,
            )
            try:
                md_chunked = self._synthesize_chunked(
                    partial_passport=partial_passport,
                    source=source,
                    file_type=file_type,
                    doc_text=processed_text,
                    metadata=metadata,
                    effective_provider=effective_provider,
                    effective_model=effective_model,
                    prompt_tier=prompt_tier,
                    chunk_size=chunk_size,
                )
                md_final = self._sanitize_source_extract(md_chunked, processed_text)
                final_tags     = self._extract_tags_from_yaml(md_final)
                final_entities = self._extract_entities_from_yaml(md_final)
                final_tags = normalize_tags(final_tags, cap=12) or normalize_tags(tags_base, cap=12)
                log.info(
                    "Síntesis chunked OK para '%s': %d chars, %d tags, %d entities "
                    "(provider=%s, model=%s)",
                    source, len(md_final), len(final_tags), len(final_entities),
                    effective_provider, effective_model,
                )
                return {
                    "md_content": _inject_search_space_id(md_final, search_space_id),
                    "tags": final_tags if final_tags else tags_base,
                    "entities": final_entities if final_entities else entities,
                    "drill_down_triggers": triggers,
                    "llm_ok": True,
                }
            except Exception as exc:
                log.warning(
                    "Síntesis fragmentada falló para '%s' (%s). Fallback a single-call.",
                    source, exc,
                )

        # =====================================================================
        # PASO 2c: Síntesis con planificador (1 / 2 / chunked según plan)
        # Si SYNTHESIS_V5_ENABLED=false → cae al flujo single-call legacy abajo.
        # Si el planner da error → fallback al flujo single-call legacy.
        # =====================================================================
        if SYNTHESIS_V5_ENABLED:
            try:
                v5_calls, v5_plan = get_synthesis_calls(
                    partial_passport=partial_passport,
                    source=source,
                    file_type=file_type,
                    processed_text=processed_text,
                    metadata=metadata,
                    current_md=effective_current_md,
                    model_profile=prompt_tier,
                    context_chars=profile.context_chars,
                )
                log.info(
                    "Plan de síntesis para '%s' (tier=%s): %s",
                    source, prompt_tier, explain_plan(v5_plan),
                )

                # ─────────────────────────────────────────────────────────
                # Ejecutar la(s) call(s) overview del plan
                # ─────────────────────────────────────────────────────────
                v5_results: list[str] = []
                for i, (sys_p, usr_p) in enumerate(v5_calls):
                    call_spec = v5_plan.calls[i]

                    # Sustituir {call1_result} si esta call necesita el resultado previo
                    if call_spec.needs_prev_result and v5_results:
                        prev_combined = "\n\n".join(v5_results)
                        usr_p = usr_p.replace("{call1_result}", prev_combined)

                    log.info(
                        "Llamada %d/%d — secciones: %s",
                        i + 1, len(v5_calls), ", ".join(call_spec.sections),
                    )
                    result_text = llm_client.generate(
                        prompt=usr_p,
                        provider=effective_provider,
                        system=sys_p,
                        temperature=call_spec.temperature,
                        max_tokens=call_spec.max_tokens_out,
                        model=effective_model,
                    )

                    # Limpiar triple-backtick y frontmatter si el modelo los añadió
                    cleaned = result_text.strip()
                    if cleaned.startswith("```"):
                        _lines = cleaned.splitlines()
                        cleaned = "\n".join(
                            _lines[1:-1] if _lines[-1].strip() == "```" else _lines[1:]
                        ).strip()
                    if cleaned.startswith("---"):
                        _sep = cleaned.find("\n---", 3)
                        if _sep != -1:
                            cleaned = cleaned[_sep + 4:].strip()
                    v5_results.append(cleaned)

                # ─────────────────────────────────────────────────────────
                # Si el plan es CHUNKED: procesar Core Knowledge por fragmentos
                # ─────────────────────────────────────────────────────────
                if v5_plan.chunked:
                    spec = get_spec(file_type.lower().lstrip("."))
                    chunks = split_doc_text_for_chunked(
                        processed_text, chunk_size=v5_plan.chunk_size
                    )
                    log.info(
                        "Procesando Core Knowledge en %d fragmentos (chunk_size=%d)...",
                        len(chunks), v5_plan.chunk_size,
                    )

                    ck_parts: list[str] = []
                    for ci, chunk in enumerate(chunks):
                        ck_sys, ck_usr = build_chunk_call(
                            file_type=file_type,
                            spec=spec,
                            chunk=chunk,
                            chunk_index=ci + 1,
                            total_chunks=len(chunks),
                            profile=prompt_tier,
                        )
                        # max_tokens para chunks CK: proporcional al tamaño del chunk.
                        # ~1 token de output por cada 3 chars de input es una razón razonable.
                        # Mín 2000 para tener sustancia, máx 4096 para no saturar.
                        ck_max_tokens = max(2000, min(4096, len(chunk) // 3))
                        ck_result = llm_client.generate(
                            prompt=ck_usr,
                            provider=effective_provider,
                            system=ck_sys,
                            temperature=0.2,
                            max_tokens=ck_max_tokens,
                            model=effective_model,
                        )
                        # Limpiar y conservar solo subsecciones ###
                        ck_clean = ck_result.strip()
                        if ck_clean.startswith("```"):
                            _lines = ck_clean.splitlines()
                            ck_clean = "\n".join(
                                _lines[1:-1] if _lines[-1].strip() == "```" else _lines[1:]
                            ).strip()
                        log.info(
                            "Fragmento %d/%d procesado (%d chars)",
                            ci + 1, len(chunks), len(ck_clean),
                        )
                        if ck_clean:
                            ck_parts.append(ck_clean)

                    # Insertar el CK generado en lugar del marcador [procesando-chunked]
                    core_knowledge_body = "\n\n".join(ck_parts)
                    overview_text = v5_results[0] if v5_results else ""
                    if "[procesando-chunked]" in overview_text:
                        overview_text = overview_text.replace(
                            "[procesando-chunked]",
                            core_knowledge_body if core_knowledge_body
                                else "[No se pudo generar Core Knowledge]",
                        )
                        v5_results[0] = overview_text
                    else:
                        # Fallback: si el LLM no respetó el marcador, añadir CK al final
                        v5_results.append(
                            "# 🧩 Core Knowledge\n\n" + core_knowledge_body
                        )
                    log.info(
                        "Merge overview + Core Knowledge fragmentado completado",
                    )

                # ─────────────────────────────────────────────────────────
                # Mergear: frontmatter del partial_passport + cuerpo generado
                # ─────────────────────────────────────────────────────────
                fm_end = partial_passport.find("\n---\n", 3)
                frontmatter_block = partial_passport[:fm_end + 5] if fm_end != -1 else ""
                v5_md = frontmatter_block + "\n" + "\n\n".join(v5_results)
                v5_md = self._repair_unclosed_fences(v5_md)

                # Validar/sanear y devolver
                md_final = self._sanitize_source_extract(v5_md, processed_text)
                final_tags     = self._extract_tags_from_yaml(md_final)
                final_entities = self._extract_entities_from_yaml(md_final)
                final_tags = normalize_tags(final_tags, cap=12) or normalize_tags(tags_base, cap=12)

                log.info(
                    "Síntesis OK para '%s': %d chars, %d tags, %d entities "
                    "(tier=%s, provider=%s)",
                    source, len(md_final), len(final_tags), len(final_entities),
                    prompt_tier, effective_provider,
                )
                return {
                    "md_content": _inject_search_space_id(md_final, search_space_id),
                    "tags": final_tags if final_tags else tags_base,
                    "entities": final_entities if final_entities else entities,
                    "drill_down_triggers": triggers,
                    "llm_ok": True,
                }
            except Exception as v5_exc:
                log.warning(
                    "Plan de síntesis falló para '%s' (%s). Fallback a flujo legacy single-call.",
                    source, v5_exc,
                )
                # Fall-through al flujo single-call legacy

        # =====================================================================
        # PASO 2c LEGACY: Síntesis single-call (v3) — fallback o V5 OFF
        # =====================================================================
        system_prompt, user_msg = get_focused_synthesis_prompt(
            partial_passport=partial_passport,
            source=source,
            file_type=file_type,
            processed_text=processed_text,
            metadata=metadata,
            current_md=effective_current_md,
            model_profile=prompt_tier,
        )

        try:
            llm_result = llm_client.generate(
                prompt=user_msg,
                provider=effective_provider,
                system=system_prompt,
                temperature=0.3,
                max_tokens=4096,
                model=effective_model,
            )
        except Exception as exc:
            log.warning(
                "Error en síntesis LLM de '%s' (provider=%s, model=%s): %s. Usando parcial.",
                source, effective_provider, effective_model, exc,
            )
            md_fallback = self._fill_placeholders_from_text(
                partial_passport, processed_text, source, entities, tags_base
            )
            return {
                "md_content": _inject_search_space_id(md_fallback, search_space_id),
                "tags": normalize_tags(tags_base, cap=12),
                "entities": entities,
                "drill_down_triggers": triggers,
                "llm_failed": True,
                "error": str(exc),
            }

        # =====================================================================
        # PASO 3: Merge y validación final
        # =====================================================================
        log.info("Validando y mergeando resultado LLM...")
        md_final = self._merge_llm_into_partial(partial_passport, llm_result)
        md_final = self._sanitize_source_extract(md_final, processed_text)

        final_tags     = self._extract_tags_from_yaml(md_final)
        final_entities = self._extract_entities_from_yaml(md_final)
        final_tags = normalize_tags(final_tags, cap=12) or normalize_tags(tags_base, cap=12)

        log.info(
            "Síntesis OK para '%s': %d chars, %d tags, %d entities (provider=%s, tier=%s)",
            source, len(md_final), len(final_tags), len(final_entities),
            effective_provider, prompt_tier,
        )

        return {
            "md_content": _inject_search_space_id(md_final, search_space_id),
            "tags": final_tags if final_tags else tags_base,
            "entities": final_entities if final_entities else entities,
            "drill_down_triggers": triggers,
            "llm_ok": True,
        }

    # =========================================================================
    # Síntesis fragmentada (chunked)
    # =========================================================================

    def _synthesize_chunked(
        self,
        partial_passport: str,
        source: str,
        file_type: str,
        doc_text: str,
        metadata: dict,
        effective_provider: str,
        effective_model: str | None,
        prompt_tier: str = "large",
        chunk_size: int = 3000,
    ) -> str:
        """
        Síntesis fragmentada para documentos grandes.

        Call A (overview): todas las secciones excepto Core Knowledge.
        Calls B..N (chunks): subsecciones ### de Core Knowledge por fragmento.
        Compose: merge determinístico Python.
        """
        from app.brain.prompts import SYNTHESIS_OVERVIEW_EXCERPT

        # Excerpt para la call de overview
        # Para perfiles small/medium usamos un extracto más corto
        if prompt_tier == "small":
            overview_excerpt_size = 600
        elif prompt_tier == "medium":
            overview_excerpt_size = 1200
        else:
            overview_excerpt_size = SYNTHESIS_OVERVIEW_EXCERPT

        excerpt = doc_text[:overview_excerpt_size]
        if len(doc_text) > overview_excerpt_size:
            excerpt += f"\n\n[... documento completo ({len(doc_text)} chars) — solo el inicio ...]"

        # ── Call A: Overview ──────────────────────────────────────────────────
        log.info("[chunked] Call A: overview (extracto=%d chars, tier=%s)", len(excerpt), prompt_tier)
        system_ov, user_ov = get_overview_synthesis_prompt(
            partial_passport, source, file_type, excerpt, metadata,
            model_profile=prompt_tier,          # ← NUEVO v3.0
        )
        overview_result = llm_client.generate(
            prompt=user_ov,
            provider=effective_provider,
            system=system_ov,
            temperature=0.3,
            max_tokens=2048,
            model=effective_model,
        )

        # ── Calls B..N: Core Knowledge chunks ─────────────────────────────────
        chunks = split_doc_text_for_chunked(doc_text, chunk_size)
        log.info(
            "[chunked] %d chunks de Core Knowledge (tamaño≤%d chars, tier=%s)",
            len(chunks), chunk_size, prompt_tier,
        )

        ck_parts: list[str] = []
        for i, chunk in enumerate(chunks):
            log.info("[chunked] Call CK %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
            system_ck, user_ck = get_chunk_synthesis_prompt(
                file_type, chunk, i + 1, len(chunks),
                model_profile=prompt_tier,      # ← NUEVO v3.0
            )
            ck_max_tokens = max(2000, min(4096, len(chunk) // 3))
            ck_result = llm_client.generate(
                prompt=user_ck,
                provider=effective_provider,
                system=system_ck,
                temperature=0.2,
                max_tokens=ck_max_tokens,
                model=effective_model,
            )
            cleaned = ck_result.strip()
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                cleaned = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                ).strip()
            ck_parts.append(cleaned)

        # ── Compose ──────────────────────────────────────────────────────────
        core_knowledge_body = "\n\n".join(p for p in ck_parts if p)
        merged = self._merge_llm_into_partial(partial_passport, overview_result)
        merged = self._replace_core_knowledge(merged, core_knowledge_body)

        log.info(
            "[chunked] Completado para '%s': %d chars (%d calls: 1 overview + %d CK chunks)",
            source, len(merged), 1 + len(chunks), len(chunks),
        )
        return merged

    # =========================================================================
    # Helpers (idénticos a v2.5 — sin cambios)
    # =========================================================================

    def _replace_core_knowledge(self, md: str, core_body: str) -> str:
        section_re = re.compile(r"(?ms)^#\s+🧩\s+Core Knowledge\s*\n.*?(?=^#\s+|\Z)")
        replacement = f"# 🧩 Core Knowledge\n\n{core_body}\n\n"
        result, n_replaced = section_re.subn(replacement, md)
        if n_replaced == 0:
            log.warning("[chunked] Sección Core Knowledge no encontrada; añadiendo al final.")
            result = md.rstrip() + f"\n\n# 🧩 Core Knowledge\n\n{core_body}\n"
        return result

    def _merge_llm_into_partial(self, partial_passport: str, llm_result: str) -> str:
        cleaned = llm_result.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

        partial_sep_end = partial_passport.find("\n---\n", 3) + 5
        frontmatter_block = partial_passport[:partial_sep_end]

        body = cleaned
        if cleaned.startswith("---"):
            second_sep = cleaned.find("\n---", 3)
            if second_sep != -1:
                body = cleaned[second_sep + 4:].strip()

        if not body.strip():
            body = partial_passport[partial_sep_end:].strip()

        result = frontmatter_block + "\n" + body
        return self._repair_unclosed_fences(result)

    @staticmethod
    def _repair_unclosed_fences(md: str) -> str:
        lines = md.splitlines()
        open_fences = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                if stripped == "```":
                    if open_fences > 0:
                        open_fences -= 1
                else:
                    open_fences += 1
        if open_fences > 0:
            md = md.rstrip() + "\n" + ("```\n" * open_fences)
        return md

    def _extract_tags_from_yaml(self, md_content: str) -> list[str]:
        tags = []
        try:
            match = re.search(r"tags:\s*(\[.*?\])", md_content, re.DOTALL)
            if match:
                tags = json.loads(match.group(1))
            else:
                in_tags = False
                for line in md_content.split("\n"):
                    stripped = line.strip()
                    if stripped == "tags:":
                        in_tags = True; continue
                    if in_tags:
                        if stripped.startswith("- "):
                            tags.append(stripped[2:].strip())
                        elif not stripped or not stripped.startswith(" "):
                            break
        except Exception as exc:
            log.warning("Error extrayendo tags: %s", exc)
        return tags

    def _extract_entities_from_yaml(self, md_content: str) -> list[str]:
        entities = []
        try:
            match = re.search(r"entities:\s*(\[.*?\])", md_content, re.DOTALL)
            if match:
                entities = json.loads(match.group(1))
            else:
                in_entities = False
                for line in md_content.split("\n"):
                    stripped = line.strip()
                    if stripped == "entities:":
                        in_entities = True; continue
                    if in_entities:
                        if stripped.startswith("- "):
                            entities.append(stripped[2:].strip())
                        elif not stripped or not stripped.startswith(" "):
                            break
        except Exception as exc:
            log.warning("Error extrayendo entities: %s", exc)
        return entities

    def _sanitize_source_extract(self, md_content: str, processed_text: str) -> str:
        section_re = re.compile(
            r"(?ms)^#\s+📄\s+Source Extract\s*\n(?P<body>.*?)(?=^#\s+|\Z)"
        )
        match = section_re.search(md_content)
        fallback_body = self._build_source_extract_fallback(processed_text)

        if not match:
            return md_content.rstrip() + "\n\n# 📄 Source Extract\n" + fallback_body + "\n"

        body = (match.group("body") or "").strip()
        if self._is_invalid_source_extract(body):
            replacement = "# 📄 Source Extract\n" + fallback_body + "\n"
            return section_re.sub(replacement, md_content, count=1)

        return md_content

    @staticmethod
    def _is_invalid_source_extract(body: str) -> bool:
        if not body:
            return True
        if body.count("```") % 2 != 0:
            return True
        frontmatter_markers = re.compile(
            r"(?im)^(id:\s*kb_|title:\s|type:\s|source:\s*$|created_at:\s|updated_at:\s|tags:\s*\[|entities:\s*\[)"
        )
        if body.lstrip().startswith("---"):
            return True
        if frontmatter_markers.search(body):
            return True
        return False

    @staticmethod
    def _build_source_extract_fallback(
        processed_text: str, max_lines: int = 8, max_chars: int = 900
    ) -> str:
        lines = [ln.strip() for ln in processed_text.splitlines() if ln and ln.strip()]
        if not lines:
            return "Extracto no disponible."

        selected = []
        total = 0
        _INTERNAL_HEADERS = re.compile(
            r"^##\s+(Documentación|Firmas de funciones|Estructura|Campos documentados)\s*$"
        )
        for ln in lines:
            if re.match(r"^#+\s+[📌📄🧩🧠🔗⚙️⚠️]", ln):
                continue
            if _INTERNAL_HEADERS.match(ln):
                continue
            if total + len(ln) > max_chars:
                break
            selected.append(ln)
            total += len(ln)
            if len(selected) >= max_lines:
                break

        if not selected:
            return "Extracto no disponible."
        return "\n".join(f"> {ln}" for ln in selected)

    def _fill_placeholders_from_text(
        self, partial_passport: str, full_text: str, source: str,
        entities: list, tags: list,
    ) -> str:
        import textwrap
        clean = full_text.strip().replace("\n\n\n", "\n\n")
        summary_extract = textwrap.shorten(clean[:1200], width=600, placeholder="...")
        entities_str = ", ".join(entities[:10]) if entities else "—"
        tags_str = ", ".join(tags[:12]) if tags else "—"
        source_extract = clean[:800]

        replacements = {
            r"\[LLM:.*?Summary.*?\]":       f"Documento `{source}`. Entidades: {entities_str}. Tags: {tags_str}.\n\n{summary_extract}",
            r"\[LLM:.*?Core Knowledge.*?\]": f"Entidades detectadas:\n- {entities_str}\n\n{textwrap.shorten(clean[:800], width=400, placeholder='...')}",
            r"\[LLM:.*?Key Insights.*?\]":   "_Sección no disponible (timeout LLM)._",
            r"\[LLM:.*?Relationships.*?\]":  "_Sección no disponible (timeout LLM)._",
            r"\[LLM:.*?Practical Usage.*?\]":"_Sección no disponible (timeout LLM)._",
            r"\[LLM:.*?Pitfalls.*?\]":       "_Sección no disponible (timeout LLM)._",
            r"\[LLM:.*?Source Extract.*?\]": f"```\n{source_extract}\n```",
            r"\[LLM:[^\]]*\]":               "_No disponible (timeout LLM)._",
        }

        result = partial_passport
        for pattern, replacement in replacements.items():
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE | re.DOTALL)

        log.info("[fill_placeholders] Pasaporte fallback generado para '%s'.", source)
        return result

    def _minimal_passport(self, source: str, file_type: str, metadata: dict) -> dict:
        from app.brain.passport_builder import _slugify
        slug = _slugify(source)
        kb_id = f"kb_{slug}"
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter = (
            "---\n"
            f"id: {kb_id}\n"
            f"title: \"{slug.replace('-', ' ').title()}\"\n"
            "type: technical_doc\n"
            f"domain: {metadata.get('domain', 'other')}\n"
            "source:\n"
            "  type: file\n"
            f"  origin: {source}\n"
            f"  format: {file_type}\n"
            f"created_at: {now}\n"
            f"updated_at: {now}\n"
            "tags: []\n"
            "entities: []\n"
            "---\n"
            "# Síntesis deshabilitada\n"
        )
        return {"md_content": frontmatter, "tags": [], "entities": [], "drill_down_triggers": []}


# Singleton global
_synthesizer = DocumentSynthesizer()


def synthesize(
    source: str,
    file_type: str,
    full_text: str,
    blocks: list[dict] | None = None,
    metadata: dict | None = None,
    provider: str | None = None,
    model: str | None = None,
    current_md: str | None = None,
    ingest_metadata: dict | None = None,
    search_space_id: str = "",
) -> dict:
    """Función de conveniencia — wrapper del singleton DocumentSynthesizer."""
    return _synthesizer.synthesize(
        source=source,
        file_type=file_type,
        full_text=full_text,
        blocks=blocks,
        metadata=metadata,
        provider=provider,
        model=model,
        current_md=current_md,
        ingest_metadata=ingest_metadata,
        search_space_id=search_space_id,
    )
