import os
import logging
import re
from .base import BaseExtractor
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# HTTP_VERIFY_SSL=false para entornos corporativos con proxy SSL interceptor.
_SSL_VERIFY = os.getenv("HTTP_VERIFY_SSL", "true").strip().lower() != "false"

# Umbral mínimo de texto útil extraido por requests para considerar que
# vale la pena continuar sin Crawl4AI.
# Si el HTML descargado produce menos de este número de chars de texto limpio,
# intentamos con Crawl4AI (página con mucho JS o bloqueo parcial).
_MIN_USEFUL_CHARS = int(os.getenv("WEB_MIN_USEFUL_CHARS", "200"))

# Si CRAWL4AI_ENABLED=false se desactiva el fallback (por defecto activo si
# crawl4ai está instalado).
_CRAWL4AI_ENABLED = os.getenv("CRAWL4AI_ENABLED", "true").strip().lower() != "false"

# ---------------------------------------------------------------------------
# Helpers de parsing HTML (usados por el path requests+BeautifulSoup)
# ---------------------------------------------------------------------------

# Patrones de ruido UI: copyright, numeración, navegación, breadcrumbs
_UI_NOISE_RE = re.compile(
    r"^("
    r"\d+\s*$|"
    r"[©®™]+.*$|"
    r"(inicio|home|menú|menu|"
    r"buscar|search|login|"
    r"cerrar|close|ver más|"
    r"read more|compartir|share)"
    r")\s*$",
    re.IGNORECASE,
)

# Elementos de bloque: cuando un tag tiene hijos de este tipo, NO es nodo hoja.
# Sin h1-h6 y p aquí, un <section><h2><p></p></section> se consideraría hoja
# y duplicaría el texto que ya emiten h2 y p individualmente.
_BLOCK_TAGS = frozenset({
    "div", "section", "article", "ul", "ol", "table", "tr",
    "blockquote", "header", "footer", "aside", "nav",
    "figure", "form", "main", "tbody", "thead", "dl",
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "dt", "dd", "td", "th",
})


def _is_ui_noise(text: str) -> bool:
    if not text or len(text) < 3:
        return True
    if re.match(_UI_NOISE_RE, text):
        return True
    if re.match(r"^[\w\s]+(>|/)\s*[\w\s]+(>|/)", text):
        return True
    return False


def _is_leaf_container(tag) -> bool:
    """True si el tag no tiene hijos block-level (es un nodo hoja semántico)."""
    if not hasattr(tag, "children"):
        return False
    for child in tag.children:
        if hasattr(child, "name") and child.name in _BLOCK_TAGS:
            return False
    return True


class WebExtractor(BaseExtractor):
    """
    Extractor de páginas web con dos estrategias:

    Estrategia 1 — requests + BeautifulSoup (rápida, sin dependencias extra):
      Descarga el HTML estático y lo parsea con BeautifulSoup.
      Funciona para la mayoría de páginas con contenido server-rendered.
      Limitación: no ejecuta JavaScript, no supera bloqueos activos (403, etc.).

    Estrategia 2 — Crawl4AI (fallback automático):
      Se activa cuando requests falla (403, timeout, conexión rechazada)
      o cuando el texto extraido es menor a WEB_MIN_USEFUL_CHARS (página SPA
      o con contenido generado por JS).
      Crawl4AI usa Playwright headless, ejecuta JavaScript, y devuelve
      Markdown directamente limpio y estructurado.
      Requiere: pip install crawl4ai && crawl4ai-setup
      Control: CRAWL4AI_ENABLED=false para desactivar el fallback.

    Variables de entorno:
      HTTP_VERIFY_SSL       : "false" para redes corporativas con proxy SSL
      WEB_MIN_USEFUL_CHARS  : umbral para activar Crawl4AI (default: 200)
      CRAWL4AI_ENABLED      : "false" para desactivar Crawl4AI (default: true)
    """

    # ------------------------------------------------------------------
    # Descarga de HTML: requests con fallback a Crawl4AI
    # ------------------------------------------------------------------

    def download_html(self, url: str) -> str:
        """Descarga el HTML de una URL. Sin fallback (sólo requests)."""
        resp = requests.get(
            url,
            verify=_SSL_VERIFY,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SecondBrainBot/1.0)"},
        )
        resp.raise_for_status()
        return resp.text

    def _download_with_fallback(self, url: str) -> tuple[str, str]:
        """
        Descarga una URL intentando primero requests y luego Crawl4AI.

        Retorna (html_o_markdown: str, modo: str) donde modo es
        "requests" o "crawl4ai" según quién tuvo éxito.

        Crawl4AI se activa cuando:
          - requests lanza una excepción (403, timeout, SSL, etc.)
          - El texto extraído del HTML es menor a _MIN_USEFUL_CHARS
        """
        html_content = None
        requests_error = None

        # ── Intento 1: requests ─────────────────────────────────────────────
        try:
            resp = requests.get(
                url,
                verify=_SSL_VERIFY,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SecondBrainBot/1.0)"},
            )
            resp.raise_for_status()
            html_content = resp.text

            # Verificar que hay contenido útil (no es una página SPA vacía)
            text_preview = re.sub(r"<[^>]+>", " ", html_content)
            text_preview = re.sub(r"\s+", " ", text_preview).strip()
            if len(text_preview) >= _MIN_USEFUL_CHARS:
                log.debug("[web] requests OK: %d chars de texto en '%s'", len(text_preview), url)
                return html_content, "requests"
            else:
                log.info(
                    "[web] requests devolvió poca texto (%d chars < %d). "
                    "Activando Crawl4AI para '%s'",
                    len(text_preview), _MIN_USEFUL_CHARS, url,
                )
                requests_error = f"texto insuficiente ({len(text_preview)} chars)"

        except Exception as exc:
            requests_error = str(exc)
            log.info("[web] requests falló para '%s': %s. Probando Crawl4AI...", url, exc)

        # ── Intento 2: Crawl4AI (fallback) ─────────────────────────────────
        if not _CRAWL4AI_ENABLED:
            log.warning("[web] Crawl4AI desactivado (CRAWL4AI_ENABLED=false). "
                        "No hay más alternativas para '%s'.", url)
            # Devolver el HTML estático aunque sea pobre
            return html_content or "", "requests"

        try:
            import asyncio
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

            browser_cfg = BrowserConfig(
                headless=True,
                verbose=False,
                ignore_https_errors=not _SSL_VERIFY,
            )
            run_cfg = CrawlerRunConfig(
                page_timeout=30000,     # 30 s
                wait_for="domcontentloaded",
            )

            async def _crawl():
                async with AsyncWebCrawler(config=browser_cfg) as crawler:
                    result = await crawler.arun(url=url, config=run_cfg)
                    return result

            # Ejecutar en el event loop existente o crear uno nuevo
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Entorno async (FastAPI): usar asyncio.run_coroutine_threadsafe
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, _crawl())
                        crawl_result = future.result(timeout=45)
                else:
                    crawl_result = loop.run_until_complete(_crawl())
            except RuntimeError:
                crawl_result = asyncio.run(_crawl())

            if crawl_result and crawl_result.success:
                markdown = crawl_result.markdown or ""
                if len(markdown.strip()) >= _MIN_USEFUL_CHARS:
                    log.info("[web] Crawl4AI OK: %d chars Markdown para '%s'", len(markdown), url)
                    return markdown, "crawl4ai"
                else:
                    log.warning("[web] Crawl4AI devolvió poco contenido (%d chars) para '%s'",
                                len(markdown), url)
            else:
                err = getattr(crawl_result, "error_message", "unknown") if crawl_result else "sin resultado"
                log.warning("[web] Crawl4AI falló para '%s': %s", url, err)

        except ImportError:
            log.warning(
                "[web] crawl4ai no está instalado. "
                "Instalar con: pip install crawl4ai && crawl4ai-setup"
            )
        except Exception as exc:
            log.warning("[web] Crawl4AI error para '%s': %s", url, exc)

        # ── Fallback final: devolver el HTML de requests aunque sea pobre ───
        log.warning("[web] Usando HTML estático (posiblemente incompleto) para '%s'", url)
        return html_content or "", "requests"

    # ------------------------------------------------------------------
    # Extractor principal
    # ------------------------------------------------------------------

    def extract(self, source: str) -> list[dict]:
        with open(source, "r", encoding="utf-8") as f:
            content = f.read()
        # Detectar si el fichero es Markdown de Crawl4AI o HTML normal
        # (cuando se ingesta desde URL, source puede ser HTML guardado en disco)
        if content.lstrip().startswith("<"):
            return self._extract_from_html(content)
        else:
            # Ya es Markdown (Crawl4AI) — convertir directamente a bloques
            return self._extract_from_markdown(content)

    def extract_url(self, url: str) -> list[dict]:
        """
        Extrae bloques directamente desde una URL (sin fichero intermedio).
        Llamado desde el ingest router cuando la fuente es una URL directa.
        """
        content, mode = self._download_with_fallback(url)
        if not content:
            log.error("[web] Sin contenido para '%s'", url)
            return []

        log.info("[web] Modo de extraccion: %s para '%s'", mode, url)

        if mode == "crawl4ai":
            # Crawl4AI devuelve Markdown limpio directamente
            return self._extract_from_markdown(content, source_url=url)
        else:
            return self._extract_from_html(content, source_url=url)

    # ------------------------------------------------------------------
    # Extracción desde HTML (requests path)
    # ------------------------------------------------------------------

    def _extract_from_html(self, html: str, source_url: str = "") -> list[dict]:
        """Pipeline original: BeautifulSoup + nodos hoja + deduplicación."""
        soup = BeautifulSoup(html, "html.parser")
        for s in soup(["script", "style", "nav", "header", "footer", "aside", "form", "button"]):
            s.decompose()
        main_container = (
            soup.find("main")
            or soup.find("article")
            or soup.find(attrs={"role": "main"})
        )
        blocks = self._extract_blocks(soup, main_container or soup)
        log.debug("[web] HTML '%s' → %d bloques", source_url or "local", len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # Extracción desde Markdown (Crawl4AI path)
    # ------------------------------------------------------------------

    def _extract_from_markdown(self, markdown: str, source_url: str = "") -> list[dict]:
        """
        Convierte Markdown de Crawl4AI en bloques tipados.

        Crawl4AI ya hizo el trabajo de:
          - Ejecutar JavaScript y obtener el DOM completo
          - Eliminar nav/footer/boilerplate
          - Convertir headings HTML a # Markdown
          - Extraer bloques de código como ```lang

        Aquí separamos: bloques de código → content_type="code",
        texto con ## headers → content_type="text" estructurado.
        """
        blocks = []
        page = 1

        # Extraer bloques de código ```lang ... ```
        code_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        code_blocks_found = []
        for m in code_pattern.finditer(markdown):
            lang = m.group(1) or "unknown"
            code = m.group(2).strip()
            if code and len(code) > 20:
                code_blocks_found.append((lang, code, m.start(), m.end()))
                blocks.append(self._make_block(
                    content=code,
                    content_type="code",
                    page=page,
                    language=lang,
                    metadata={"format": "html", "language": lang, "via": "crawl4ai"},
                ))
                page += 1

        # Eliminar código del markdown para procesar solo el texto
        text_md = code_pattern.sub("", markdown).strip()

        # Limpiar líneas de ruido: separadores ---, ***, metadata YAML
        lines = []
        for line in text_md.split("\n"):
            s = line.strip()
            if not s:
                continue
            if re.match(r"^[-*_]{3,}$", s):   # separadores horizontales
                continue
            if re.match(r"^\|[-| :]+\|$", s): # líneas de tabla markdown
                continue
            lines.append(line)

        # El texto ya viene con ## headers de Crawl4AI: pasarlo casi directo
        # al bloque principal (el preprocesador html.py lo estructura)
        clean_text = "\n".join(lines).strip()
        if clean_text and len(clean_text) > 50:
            blocks.append(self._make_block(
                content=clean_text,
                content_type="text",
                page=page,
                metadata={"format": "html", "section": "main", "via": "crawl4ai"},
            ))

        log.debug("[web] Markdown Crawl4AI '%s' → %d bloques (%d code)",
                  source_url or "local", len(blocks), len(code_blocks_found))
        return blocks

    def _extract_blocks(self, full_soup, content_soup) -> list[dict]:
        """
        Extrae bloques tipados del HTML con deduplicación agresiva y detección
        de estructura semántica.

        Estrategia:
          1. Metadatos del head (<title>, <meta>) → bloque 'metadata'.
          2. <pre>/<code> standalone → content_type: 'code'.
          3. Headings (h1-h4) marcan secciones; el resto se extrae solo de
             NODOS HOJA (elementos sin hijos block-level) para evitar duplicación.
          4. Si se detectan secciones numeradas en el texto (// 00, 01\\nTítulo),
             se reestructura el bloque principal con jerarquía explícita.
          5. Fallback al texto plano deduplicado si no hay estructura.
        """
        blocks = []

        # ── Metadatos del <head> ───────────────────────────────────────
        meta_lines = []
        title_el = full_soup.find("title")
        if title_el and title_el.string:
            meta_lines.append(f"title: {title_el.string.strip()}")

        meta_seen = set()
        for m in full_soup.find_all("meta"):
            name = (m.get("name") or "").lower()
            prop = (m.get("property") or "").lower()
            content = (m.get("content") or "").strip()
            key = name or prop
            if not content or not key:
                continue
            if key in ("viewport", "charset", "robots", "theme-color"):
                continue
            if key in meta_seen:
                continue
            meta_seen.add(key)
            label = "og:title" if prop == "og:title" else \
                    "og:description" if prop == "og:description" else \
                    "description" if name == "description" else key
            meta_lines.append(f"{label}: {content[:300]}")

        if meta_lines:
            meta_text = "\n".join(meta_lines)
            blocks.append({
                "content": meta_text,
                "content_type": "text",
                "page": 1,
                "format": "html",
                "text": meta_text,
                "metadata": {"format": "html", "section": "metadata"},
            })

        # ── Bloques de código (<pre>) ──────────────────────────────────
        # Se procesan antes que el texto para evitar incluirlos como texto
        # cuando luego eliminemos los tags del DOM.
        for pre in content_soup.find_all("pre"):
            code_tag = pre.find("code")
            language = _detect_language(code_tag) if code_tag else "unknown"
            code_text = pre.get_text(separator="\n", strip=False).strip()
            if code_text and len(code_text) > 20:
                blocks.append({
                    "content": code_text,
                    "content_type": "code",
                    "language": language,
                    "page": 1,
                    "format": "html",
                    "text": code_text,
                    "metadata": {"format": "html", "language": language},
                })
            pre.decompose()  # eliminar para que no aparezca en el texto principal

        # ── Texto principal: solo nodos hoja ──────────────────────────
        seen_fps: set = set()
        raw_lines: list[str] = []

        # Recorrer en orden documental
        for tag in content_soup.find_all(True):
            if tag.name in ("script", "style", "head"):
                continue
            if not _is_leaf_container(tag):
                continue

            text = tag.get_text(separator=" ", strip=True)
            text = " ".join(text.split())  # normalizar espacios internos
            if len(text) < 20:
                continue
            if _is_ui_noise(text):
                continue

            # Deduplicar por fingerprint
            fp = text[:80].lower()
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            raw_lines.append(text)

        if not raw_lines:
            # Fallback: texto plano completo
            text = full_soup.get_text(separator="\n")
            text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
            if text:
                blocks.append({
                    "content": text,
                    "content_type": "text",
                    "page": 1,
                    "format": "html",
                    "text": text,
                })
            return blocks

        # ── Estructurar si hay secciones numeradas, sino texto plano ───
        structured = self._build_structured_text(raw_lines)
        content = structured if structured else "\n".join(raw_lines)

        blocks.append({
            "content": content,
            "content_type": "text",
            "page": 1,
            "format": "html",
            "text": content,
            "metadata": {"format": "html", "section": "main"},
        })

        return blocks

    def _build_structured_text(self, lines: list) -> str:
        """
        Detecta estructura de secciones en el texto extraído y reconstruye
        jerarquía con ## headers compatibles con el prompt LLM.

        Patrones detectados (en orden de prioridad):
          P1: "// 00 — Título" / "## 00 — Título" / "00 — Título"
              (cursos, specs técnicas, documentos estilo dev)
          P2: "00\\nTítulo" en líneas separadas
              (cursos exportados con números aislados)

        Si detecta < 3 secciones, devuelve "" para usar texto plano.
        """
        full_text = "\n".join(lines)

        # P1: separador comentario o markdown header con número
        P1 = re.compile(
            r"^(?://\s*|#+\s*)?(0[0-9]|1[0-9]|2[0-9])\s*[—–-]\s*(.+)",
            re.MULTILINE,
        )
        matches_p1 = [(m.group(1), m.group(2).strip(), m.start(), m.end())
                      for m in P1.finditer(full_text)]

        if len(matches_p1) >= 3:
            return self._structure_from_p1(matches_p1, full_text, lines)

        # P2: número aislado + título en línea siguiente
        P2 = re.compile(
            r"(?m)^(0[1-9]|10)\s*\n(.+?)(?=\n(?:0[1-9]|10)\s*\n|\Z)",
            re.DOTALL,
        )
        matches_p2 = list(P2.finditer(full_text))

        if len(matches_p2) >= 3:
            return self._structure_from_p2(matches_p2, full_text, lines)

        return ""

    def _structure_from_p1(self, matches, full_text, lines):
        """Estructura desde patrón '// 00 — Título'."""
        out = []

        # Cabecera (antes del primer match)
        header_raw = full_text[:matches[0][2]]
        header_lines = list(dict.fromkeys(
            l.strip() for l in header_raw.split("\n")
            if l.strip() and len(l.strip()) > 20
        ))
        if header_lines:
            out.append("## Documento")
            out.append("\n".join(header_lines[:6]))

        # Índice
        out.append(f"\n## Índice ({len(matches)} secciones)")
        out.append("\n".join(f"- Sección {num}: {title}"
                              for num, title, _, __ in matches))

        # Cuerpo de cada sección
        for i, (num, title, start, end) in enumerate(matches):
            next_start = matches[i+1][2] if i+1 < len(matches) else len(full_text)
            body_raw = full_text[end:next_start]
            body_lines = list(dict.fromkeys(
                l.strip() for l in body_raw.split("\n")
                if l.strip() and len(l.strip()) > 20
            ))
            out.append(f"\n## Sección {num}: {title}")
            out.append("\n".join(body_lines[:10]))

        return self._append_stack(out, lines)

    def _structure_from_p2(self, matches, full_text, lines):
        """Estructura desde patrón '01\\nTítulo'."""
        out = []

        header_raw = full_text[:matches[0].start()]
        header_lines = list(dict.fromkeys(
            l.strip() for l in header_raw.split("\n")
            if l.strip() and len(l.strip()) > 20
        ))
        if header_lines:
            out.append("## Documento")
            out.append("\n".join(header_lines[:6]))

        section_titles = [(m.group(1), m.group(2).strip()) for m in matches]
        out.append(f"\n## Índice ({len(section_titles)} módulos)")
        out.append("\n".join(f"- Módulo {n}: {t}" for n, t in section_titles))

        for m in matches:
            num = m.group(1)
            title = m.group(2).strip()
            body_start = m.start() + len(num) + 1 + len(m.group(2)) + 1
            body_raw = full_text[body_start:m.end()]
            body_lines = list(dict.fromkeys(
                l.strip() for l in body_raw.split("\n")
                if l.strip() and len(l.strip()) > 20
            ))
            out.append(f"\n## Módulo {num}: {title}")
            out.append("\n".join(body_lines[:10]))

        return self._append_stack(out, lines)

    def _append_stack(self, output: list, lines: list) -> str:
        """Añade sección de stack tecnológico si existe."""
        STACK_KW = ("STACK", "TECNOLÓGICO", "TECNOLOGICO", "HERRAMIENTA",
                    "REFERENCIA", "TECNOLOGÍA", "TECH STACK")
        stack_idx = next(
            (i for i, l in enumerate(lines)
             if any(kw in l.upper() for kw in STACK_KW)),
            None,
        )
        if stack_idx is not None:
            stack_lines = list(dict.fromkeys(
                l for l in lines[stack_idx: stack_idx + 30] if len(l) > 10
            ))
            output.append("\n## Stack tecnológico")
            output.append("\n".join(stack_lines[:25]))

        return "\n".join(output)


def _detect_language(code_tag) -> str:
    """Infiere el lenguaje de la clase CSS del tag <code> (ej: language-python → python)."""
    if code_tag is None:
        return "unknown"
    classes = code_tag.get("class") or []
    for cls in classes:
        if cls.startswith("language-"):
            return cls[len("language-"):]
        if cls.startswith("lang-"):
            return cls[len("lang-"):]
    return "unknown"
