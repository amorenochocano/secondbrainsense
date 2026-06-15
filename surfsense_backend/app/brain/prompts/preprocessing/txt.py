"""
brain/prompts/preprocessing/txt.py
-----------------------------------
Preprocesado de texto plano (.txt).

v2 — chunked-friendly:
  Detecta subtipo y aplica estructura ## / ### apropiada.
  LOG    → ## por nivel de error (Errores, Trazas, Advertencias, Info)
  SPEC   → ### por requisito numerado (REQ-001, RF-1.2...)
  NOTA   → ## por párrafo temático largo
  DUMP   → limpieza mínima
"""

from __future__ import annotations
import re as _re


_LOG_TS    = _re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_LOG_LEVEL = _re.compile(r"\b(ERROR|WARN|INFO|DEBUG|FATAL|TRACE)\b")
_TRACEBACK = _re.compile(r"Traceback|Exception|\bat\s+[\w.]+\(")
_REQ_ID    = _re.compile(r"^\s*(?:REQ|RF|RNF|HU|US)[-_]?\d+", _re.MULTILINE | _re.IGNORECASE)
_REQ_NUM   = _re.compile(r"^\s*\d+\.\d+(?:\.\d+)?\s+\w", _re.MULTILINE)
_MUST      = _re.compile(r"^\s*(?:Debe|Deberá|Must|Shall)\s+", _re.MULTILINE | _re.IGNORECASE)


def _detect_subtype(text: str) -> str:
    sample = text[:3000]
    log_hits = sum(1 for p in [_LOG_TS, _LOG_LEVEL, _TRACEBACK] if p.search(sample))
    if log_hits >= 2:
        return "log"
    # Spec: al menos 2 requisitos numerados OR combinación de patrones
    req_count = len(_REQ_ID.findall(sample)) + len(_REQ_NUM.findall(sample))
    spec_hits = sum(1 for p in [_REQ_ID, _REQ_NUM, _MUST] if p.search(sample))
    if req_count >= 2 or spec_hits >= 2:
        return "spec"
    paragraphs = [p for p in sample.split("\n\n") if len(p.strip()) > 50]
    if len(paragraphs) >= 2:
        return "nota"
    return "dump"


def _preprocess_log(text: str, max_chars: int) -> str:
    lines = text.split("\n")
    errors, warns, infos, traces = [], [], [], []
    for line in lines:
        if "ERROR" in line or "FATAL" in line:
            errors.append(line)
        elif "WARN" in line:
            warns.append(line)
        elif "Traceback" in line or "Exception" in line:
            traces.append(line)
        elif "INFO" in line:
            infos.append(line)
    parts = []
    if errors: parts.append("## Errores críticos\n" + "\n".join(errors[:30]))
    if traces:  parts.append("## Trazas de excepción\n" + "\n".join(traces[:15]))
    if warns:   parts.append("## Advertencias\n" + "\n".join(warns[:20]))
    if infos:   parts.append("## Eventos informativos\n" + "\n".join(infos[:20]))
    result = "\n\n".join(parts) if parts else text
    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result


def _preprocess_spec(text: str, max_chars: int) -> str:
    lines = text.split("\n")
    sections: list[str] = []
    current: list[str] = []
    current_id: str | None = None
    _REQ_MATCH = _re.compile(
        r"^((?:REQ|RF|RNF|HU|US)[-_]?\d+|\d+\.\d+(?:\.\d+)?)\s+(.*)",
        _re.IGNORECASE,
    )
    for line in lines:
        m = _REQ_MATCH.match(line.strip())
        if m:
            if current and current_id:
                sections.append(f"### {current_id}\n" + "\n".join(current))
            current_id = m.group(1)
            current = [m.group(2)] if m.group(2).strip() else []
        else:
            if line.strip():
                current.append(line)
    if current and current_id:
        sections.append(f"### {current_id}\n" + "\n".join(current))
    result = "\n\n".join(sections) if sections else text
    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result


def _preprocess_nota(text: str, max_chars: int) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    parts: list[str] = []
    idx = 1
    for para in paragraphs:
        if len(para) > 100:
            first = para.split(".")[0].strip()[:60]
            parts.append(f"## Sección {idx}: {first}\n{para}")
            idx += 1
        else:
            parts.append(para)
    result = "\n\n".join(parts)
    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result


def preprocess(full_text: str, max_chars: int) -> str:
    subtype = _detect_subtype(full_text)
    if subtype == "log":  return _preprocess_log(full_text, max_chars)
    if subtype == "spec": return _preprocess_spec(full_text, max_chars)
    if subtype == "nota": return _preprocess_nota(full_text, max_chars)
    result = _re.sub(r"\n{3,}", "\n\n", full_text).strip()
    return result[:max_chars] + "\n\n[... truncado ...]" if len(result) > max_chars else result
