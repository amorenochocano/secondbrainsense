import logging
from .base import BaseExtractor
import re

log = logging.getLogger(__name__)

_CODE_FENCE = re.compile(r"^```")


class MarkdownExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()

        _frontmatter, body = self._split_frontmatter(raw)
        # Separar bloques de código antes de dividir secciones
        segments = self._split_code_blocks(body)

        blocks: list[dict] = []
        page_idx = 1

        for seg_content, seg_type in segments:
            seg_content = seg_content.strip()
            if not seg_content:
                continue

            if seg_type == "code":
                blocks.append({
                    "content": seg_content,
                    "content_type": "code",
                    "page": page_idx,
                    "text": seg_content,
                    "metadata": {"format": "markdown", "section": "code_block"},
                })
                page_idx += 1
            else:
                # Dividir la parte de texto en secciones por headings
                sections = self._split_sections(seg_content)
                for sec in sections:
                    content = sec.strip()
                    if not content:
                        continue
                    heading = self._extract_heading(content)
                    blocks.append({
                        "content": content,
                        "content_type": "text",
                        "page": page_idx,
                        "text": content,
                        "metadata": {
                            "format": "markdown",
                            "section": heading or f"section_{page_idx}",
                        },
                    })
                    page_idx += 1

        if blocks:
            log.debug("[markdown] '%s' → %d bloques", source, len(blocks))
            return blocks

        log.warning("[markdown] '%s' → sin secciones detectadas, usando fallback", source)
        fallback = body.strip() or raw.strip()
        return [{"page": 1, "text": fallback, "content": fallback, "content_type": "text"}]

    @staticmethod
    def _split_code_blocks(text: str) -> list[tuple[str, str]]:
        """
        Divide el texto en segmentos (contenido, tipo).
        tipo = 'code' para fenced code blocks, 'text' para el resto.
        """
        segments: list[tuple[str, str]] = []
        lines = text.splitlines(keepends=True)
        buffer: list[str] = []
        in_fence = False

        for line in lines:
            if _CODE_FENCE.match(line.rstrip()):
                if in_fence:
                    # Cerrar bloque de código
                    buffer.append(line)
                    segments.append(("".join(buffer), "code"))
                    buffer = []
                    in_fence = False
                else:
                    # Abrir bloque de código — volcar texto acumulado
                    if buffer:
                        segments.append(("".join(buffer), "text"))
                        buffer = []
                    buffer.append(line)
                    in_fence = True
            else:
                buffer.append(line)

        # Resto al cerrar sin fence de cierre
        if buffer:
            seg_type = "code" if in_fence else "text"
            segments.append(("".join(buffer), seg_type))

        return segments

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[str, str]:
        lines = text.splitlines()
        if len(lines) >= 3 and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    frontmatter = "\n".join(lines[: i + 1])
                    body = "\n".join(lines[i + 1 :])
                    return frontmatter, body
        return "", text

    @staticmethod
    def _split_sections(body: str) -> list[str]:
        lines = body.splitlines()
        if not lines:
            return [body]

        sections: list[str] = []
        current: list[str] = []
        heading_re = re.compile(r"^#{1,3}\s+")

        for line in lines:
            if heading_re.match(line) and current:
                sections.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)

        if current:
            sections.append("\n".join(current).strip())

        return [s for s in sections if s.strip()]

    @staticmethod
    def _extract_heading(text: str) -> str:
        for line in text.splitlines():
            m = re.match(r"^#{1,3}\s+(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        return ""