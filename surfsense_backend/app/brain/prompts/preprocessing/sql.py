"""
brain/prompts/preprocessing/sql.py
------------------------------------
Preprocesado universal de ficheros SQL para síntesis del pasaporte semántico.

v5 — Diseñado para el lenguaje SQL completo, no para patrones concretos.

FILOSOFÍA
---------
Un fichero SQL puede ser cualquiera de estas cosas:

  A) SCRIPT DE OBJETOS DDL: crea/altera tablas, vistas, índices, schemas.
     Cada objeto es una unidad semántica → ### por objeto.

  B) PROCEDIMIENTO / FUNCIÓN / TRIGGER con lógica interna.
     El objeto entero es la unidad. Dentro puede tener secciones lógicas
     que los desarrolladores señalan con comentarios separadores → ### por sección.

  C) SCRIPT DE MÚLTIPLES CTEs (analytics, reporting, ETL).
     Cada CTE es una unidad → ### por CTE.

  D) SCRIPT AD-HOC (SELECT complejo sin CTEs, DML directo).
     Sin objetos formales → secciones por bloques de comentarios o lógica.

  E) MIXTO: DDL + DML + procedimientos en el mismo fichero.
     Cada objeto/bloque es una sección.

DETECCIÓN DE OBJETOS SQL (dialectos cubiertos)
-----------------------------------------------
DDL:
  TABLE               CREATE [OR REPLACE|OR ALTER|IF NOT EXISTS|TEMP|EXTERNAL|
                              TRANSIENT|GLOBAL TEMP] TABLE
  ALTER TABLE         ALTER TABLE ... ADD/DROP/MODIFY/RENAME/ALTER COLUMN
  VIEW                CREATE [OR REPLACE|MATERIALIZED] VIEW
  ALTER VIEW
  PROCEDURE / PROC    CREATE [OR REPLACE|OR ALTER] PROCEDURE|PROC
                      (T-SQL, PL/SQL, MySQL, PostgreSQL, Snowflake)
  FUNCTION            CREATE [OR REPLACE|OR ALTER] FUNCTION
  TRIGGER             CREATE [OR REPLACE|OR ALTER] TRIGGER
  INDEX               CREATE [UNIQUE|CLUSTERED|NONCLUSTERED|BITMAP|FULLTEXT|SPATIAL] INDEX
  SCHEMA              CREATE SCHEMA
  DATABASE            CREATE DATABASE
  SEQUENCE            CREATE SEQUENCE
  TYPE / DOMAIN       CREATE TYPE | CREATE DOMAIN
  PACKAGE             CREATE PACKAGE | PACKAGE BODY (Oracle)
  CURSOR              DECLARE nombre CURSOR
  TASK / STREAM / PIPE (Snowflake)

DML standalone (top-level, fuera de procedimiento):
  INSERT INTO / INSERT OVERWRITE
  UPDATE ... SET
  DELETE FROM / DELETE
  MERGE INTO / MERGE
  TRUNCATE TABLE
  CALL / EXEC / EXECUTE

CTEs:
  WITH nombre AS (   — uno o múltiples CTEs encadenados

Seguridad:
  GRANT / REVOKE
  CREATE USER / ROLE / LOGIN

DETECCIÓN DE SECCIONES INTERNAS (dentro de procedimientos)
-----------------------------------------------------------
Todos los patrones reales encontrados en producción:

  Separadores visuales + etiqueta:
    -- ============ NOMBRE ============
    -- ------------ NOMBRE ------------
    -- *** NOMBRE ***
    -- >> NOMBRE
    -- # NOMBRE
    -- [NOMBRE]
    /* ============ NOMBRE ============ */

  Numerados explícitos:
    -- 0️⃣ NOMBRE    (keycap emoji dígito)
    -- 1. NOMBRE
    -- (1) NOMBRE
    -- #1 NOMBRE
    -- PASO 1: NOMBRE
    -- STEP 1: NOMBRE
    -- FASE 1: NOMBRE
    -- ETAPA 1: NOMBRE
    -- TABLA 1: NOMBRE    ← patrón real de sp_load_sm_tables
    -- BLOQUE 1: NOMBRE
    -- SECCIÓN 1: NOMBRE
    -- PARTE 1: NOMBRE
    -- STAGE 1: NOMBRE
    -- PHASE 1: NOMBRE

  Palabras clave de sección sin número:
    -- BEGIN TRANSACTION / COMMIT / ROLLBACK  (solo si son secciones lógicas)

DOCUMENTACIÓN DE CABECERA
--------------------------
Extrae y estructura:
  - Descripción del objeto/script
  - Parámetros (@param, IN/OUT, p_nombre)
  - Tipos soportados (mapa PySpark→SQL, SQL→otro)
  - Ejemplos de uso (EXEC ..., CALL ..., SELECT ...)
  - Metadata (autor, fecha, versión, plataforma)

SALIDA TÍPICA
-------------
Script DDL puro:
  ## DOCUMENTACION
  [descripción + metadata]

  ### TABLE `schema.nombre`
  [CREATE TABLE + columnas + constraints]

  ### INDEX `idx_x` ON `schema.tabla`
  [CREATE INDEX]

Procedimiento con fases:
  ## DOCUMENTACION
  [descripción + parámetros + tipos + ejemplos]

  ### PROCEDURE `schema.sp_nombre`
  [firma + DECLARE]

  ### Sección 1: CARGAR CONFIGURACIÓN
  [código de la sección]

  ### Sección 2: VALIDAR CONFIGURACIÓN
  [código]

Procedimiento con tablas internas (sp_load_sm_tables pattern):
  ### Sección 1: admin_project_users
  [TRUNCATE + INSERT + lógica]

  ### Sección 2: admin_project_user_companies
  [TRUNCATE + INSERT]

Script CTE:
  ### CTE `staging_demanda`
  [WITH ... AS (...)]

  ### CTE `agregado_diario`
  [, cte2 AS (...)]

Script ad-hoc:
  ### Consulta 1: [primera frase del bloque]
  [SELECT/JOIN/WHERE/GROUP BY]
"""

from __future__ import annotations
import re as _re

# ===========================================================================
# PATRONES DDL/DML — multi-dialecto
# ===========================================================================

# TABLE ─────────────────────────────────────────────────────────────────────
_TABLE = _re.compile(
    r"CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?"
    r"(?:GLOBAL\s+)?(?:TEMPORARY|TEMP|EXTERNAL|TRANSIENT|ICEBERG\s+)?"
    r"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_#.\[\]`\"]+)",
    _re.IGNORECASE,
)
_ALTER_TBL = _re.compile(
    r"ALTER\s+TABLE\s+([A-Za-z_#.\[\]`\"]+)", _re.IGNORECASE
)

# VIEW ──────────────────────────────────────────────────────────────────────
_VIEW = _re.compile(
    r"CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?"
    r"(?:MATERIALIZED\s+|SECURE\s+|RECURSIVE\s+)?"
    r"VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)
_ALTER_VIEW = _re.compile(
    r"ALTER\s+(?:MATERIALIZED\s+)?VIEW\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE
)

# PROCEDURE / FUNCTION / TRIGGER ────────────────────────────────────────────
_PROC = _re.compile(
    r"CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?(?:PROCEDURE|PROC)\s+([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)
_FUNC = _re.compile(
    r"CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?"
    r"(?:DEFINER\s*=\s*\S+\s+)?FUNCTION\s+([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)
_TRIGGER = _re.compile(
    r"CREATE\s+(?:OR\s+(?:REPLACE|ALTER)\s+)?TRIGGER\s+([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)

# INDEX ─────────────────────────────────────────────────────────────────────
_INDEX = _re.compile(
    r"CREATE\s+(?:UNIQUE\s+|CLUSTERED\s+|NONCLUSTERED\s+|BITMAP\s+|"
    r"FULLTEXT\s+|SPATIAL\s+|GIN\s+|GiST\s+|BRIN\s+)?"
    r"INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_.\[\]`\"]+)\s+ON\s+([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)

# SCHEMA / DATABASE / SEQUENCE / TYPE / DOMAIN ──────────────────────────────
_SCHEMA  = _re.compile(r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_DB      = _re.compile(r"CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_SEQ     = _re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_TYPE    = _re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?TYPE\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_DOMAIN  = _re.compile(r"CREATE\s+DOMAIN\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)

# PACKAGE (Oracle) ──────────────────────────────────────────────────────────
_PACKAGE = _re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+(?:BODY\s+)?([A-Za-z_.\[\]`\"]+)",
    _re.IGNORECASE,
)

# CURSOR ────────────────────────────────────────────────────────────────────
_CURSOR = _re.compile(
    r"DECLARE\s+([A-Za-z_]+)\s+CURSOR", _re.IGNORECASE
)

# Snowflake-specific ────────────────────────────────────────────────────────
_TASK   = _re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?TASK\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_STREAM = _re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?STREAM\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_PIPE   = _re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?PIPE\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)

# CTEs ──────────────────────────────────────────────────────────────────────
# Detecta primer CTE: WITH nombre AS (
_CTE_FIRST = _re.compile(r"^\s*WITH\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", _re.IGNORECASE)
# CTE adicional: , nombre AS (  — al inicio de línea con coma
_CTE_NEXT  = _re.compile(r"^\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(", _re.IGNORECASE)

# DML standalone ────────────────────────────────────────────────────────────
_INSERT   = _re.compile(r"INSERT\s+(?:INTO\s+|OVERWRITE\s+(?:TABLE\s+)?)?([A-Za-z_#.\[\]`\"]+)", _re.IGNORECASE)
_UPDATE   = _re.compile(r"UPDATE\s+([A-Za-z_#.\[\]`\"]+)\s+SET\b", _re.IGNORECASE)
_DELETE   = _re.compile(r"DELETE\s+(?:FROM\s+)?([A-Za-z_#.\[\]`\"]+)", _re.IGNORECASE)
_MERGE    = _re.compile(r"MERGE\s+(?:INTO\s+)?([A-Za-z_#.\[\]`\"]+)", _re.IGNORECASE)
_TRUNCATE = _re.compile(r"TRUNCATE\s+(?:TABLE\s+)?([A-Za-z_#.\[\]`\"]+)", _re.IGNORECASE)
_CALL     = _re.compile(r"(?:CALL|EXEC(?:UTE)?)\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)

# Seguridad ─────────────────────────────────────────────────────────────────
_GRANT    = _re.compile(r"GRANT\s+(.+?)\s+ON\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_REVOKE   = _re.compile(r"REVOKE\s+(.+?)\s+ON\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)
_USER_DDL = _re.compile(r"CREATE\s+(?:USER|ROLE|LOGIN)\s+([A-Za-z_.\[\]`\"]+)", _re.IGNORECASE)

# Control de flujo ──────────────────────────────────────────────────────────
_BEGIN_KW = _re.compile(r"^\s*BEGIN\b(?!\s+TRANSACTION\b)", _re.IGNORECASE)
_END_KW   = _re.compile(r"^\s*END\b", _re.IGNORECASE)

# Separadores visuales (líneas de ===, ---, ***) — señalan inicio de sección
_SEP_LINE = _re.compile(r"^--\s*[=\-*#~]{10,}\s*$")
_SEP_BLOCK_START = _re.compile(r"^/\*\s*[=\-*#~]{5,}")
_SEP_BLOCK_END   = _re.compile(r"[=\-*#~]{5,}\s*\*/$")

# ===========================================================================
# DETECCIÓN DE SECCIONES INTERNAS DENTRO DE PROCEDIMIENTOS
# ===========================================================================
# Cubre todos los patrones reales de producción:
#
#   Emoji keycap:    -- 0️⃣ NOMBRE   -- 1️⃣ NOMBRE   -- 🎯 NOMBRE
#   Numerado simple: -- 1. NOMBRE   -- (1) NOMBRE   -- #1 NOMBRE
#   Palabras clave:  -- PASO 1: N   -- STEP 1: N    -- FASE 1: N
#                    -- TABLA 1: N  -- BLOQUE 1: N  -- SECCIÓN 1: N
#                    -- PARTE 1: N  -- STAGE 1: N   -- PHASE 1: N
#                    -- ETAPA 1: N  -- MÓDULO 1: N  -- GRUPO 1: N
#   Separador+texto: -- ===== NOMBRE =====   -- ----- NOMBRE -----
#                    -- *** NOMBRE ***        -- >> NOMBRE
#                    -- [NOMBRE]              -- # NOMBRE (si hay texto)
#
# NO detecta líneas de características (✅ ✓ ❌ ⚠️) que son bullets de docs

_PHASE_EMOJI = _re.compile(
    r"^--\s*(?:"
    r"\d+\s*\ufe0f\u20e3"           # 0️⃣ 1️⃣ ... 9️⃣
    r"|\U0001F3AF"                   # 🎯
    r"|\u2728"                       # ✨
    r"|\U0001F4CC"                   # 📌
    r"|\U0001F4DD"                   # 📝
    r")",
    _re.IGNORECASE | _re.UNICODE,
)

_PHASE_NUMBERED = _re.compile(
    r"^--\s*(?:"
    r"\d+\.\s+"                                     # 1. NOMBRE
    r"|\(\d+\)\s+"                                   # (1) NOMBRE
    r"|#\d+\s+"                                      # #1 NOMBRE
    r")",
    _re.IGNORECASE,
)

_PHASE_KEYWORD = _re.compile(
    r"^--\s*(?:"
    r"(?:paso|step|fase|etapa|tabla|bloque|secci[oó]n|parte|stage|phase|"
    r"m[oó]dulo|grupo|proceso|operaci[oó]n|tarea|task)"
    r"\s+\d+\s*[:\-]?\s+\S"         # palabra + número + separador + texto
    r")",
    _re.IGNORECASE | _re.UNICODE,
)

_PHASE_SEPARATOR_NAMED = _re.compile(
    r"^--\s*(?:"
    r"[=\-*#~]{3,}\s*\S.+\S\s*[=\-*#~]{3,}"   # ===NOMBRE=== o === NOMBRE ===
    r"|[=\-*#~]{3,}\s+\S[^=\-*#~]+"             # === NOMBRE (sin cierre)
    r"|>>\s*\S"                                   # >> NOMBRE
    r"|\*{2,}\s*\S.+\S\s*\*{2,}"                # *** NOMBRE ***
    r"|\[\s*\S[^\]]+\S\s*\]"                     # [NOMBRE]
    r")",
    _re.IGNORECASE,
)

def _is_phase_comment(line: str) -> bool:
    """
    Determina si una línea de comentario señala el inicio de una sección
    interna dentro de un procedimiento/función.

    Retorna True solo si la línea parece una etiqueta de sección,
    no una línea de característica (✅, ❌, ⚠) ni código comentado.
    """
    s = line.strip()
    if not s.startswith("--"):
        return False

    # Excluir líneas de características (checkmark, cross, bullets)
    # que aparecen en la documentación de cabecera
    content = s[2:].strip()
    if not content:
        return False
    if content[0] in ("✅", "❌", "⚠", "✓", "✗", "•", "·", "-", "*"):
        return False

    return bool(
        _PHASE_EMOJI.match(s)
        or _PHASE_NUMBERED.match(s)
        or _PHASE_KEYWORD.match(s)
        or _PHASE_SEPARATOR_NAMED.match(s)
    )


def _extract_phase_name(line: str) -> str:
    """
    Extrae el nombre legible de una línea de sección.
    Elimina los prefijos decorativos (---, ===, emoji, números).
    """
    s = line.strip()
    # Quitar --
    if s.startswith("--"):
        s = s[2:].strip()
    # Quitar emoji keycap
    s = _re.sub(r"\d+\s*\ufe0f\u20e3\s*", "", s, flags=_re.UNICODE)
    s = _re.sub(r"[\U0001F3AF\u2728\U0001F4CC\U0001F4DD]\s*", "", s, flags=_re.UNICODE)
    # Quitar separadores de borde: === NOMBRE === → NOMBRE
    s = _re.sub(r"^[=\-*#~>]+\s*", "", s)
    s = _re.sub(r"\s*[=\-*#~>]+$", "", s)
    # Quitar prefijos numerados: "1. " "(1) " "#1 "
    s = _re.sub(r"^\d+\.\s+|^\(\d+\)\s+|^#\d+\s+", "", s)
    # Quitar prefijos de palabra clave: "TABLA 1: " "PASO 2 - "
    s = _re.sub(
        r"^(?:paso|step|fase|etapa|tabla|bloque|secci[oó]n|parte|stage|phase|"
        r"m[oó]dulo|grupo|proceso|operaci[oó]n|tarea|task)\s+\d+\s*[:\-]?\s*",
        "", s, flags=_re.IGNORECASE | _re.UNICODE,
    )
    # Quitar [brackets]
    s = _re.sub(r"^\[|\]$", "", s)
    return s.strip()[:80] or "Sección"


# ===========================================================================
# REGISTRO DE PATRONES DDL/DML
# ===========================================================================

# (tipo_display, patron, top_level_only)
# top_level_only=True → no detectar si estamos dentro de PROC/FUNC/TRIGGER
#   Razón: CREATE TABLE dinámico vía sp_executesql dentro de un SP
#          no debe generar una sección de top-level falsa
_DDL_PATTERNS: list[tuple[str, _re.Pattern, bool]] = [
    # Objetos que pueden estar en cualquier nivel
    ("PROCEDURE",   _PROC,       False),
    ("FUNCTION",    _FUNC,       False),
    ("TRIGGER",     _TRIGGER,    False),
    ("PACKAGE",     _PACKAGE,    False),
    ("CTE",         _CTE_FIRST,  False),
    ("CTE",         _CTE_NEXT,   False),
    # Solo top-level (no dentro de proc)
    ("TABLE",       _TABLE,      True),
    ("ALTER TABLE", _ALTER_TBL,  True),
    ("VIEW",        _VIEW,       True),
    ("ALTER VIEW",  _ALTER_VIEW, True),
    ("INDEX",       _INDEX,      True),
    ("SCHEMA",      _SCHEMA,     True),
    ("DATABASE",    _DB,         True),
    ("SEQUENCE",    _SEQ,        True),
    ("TYPE",        _TYPE,       True),
    ("DOMAIN",      _DOMAIN,     True),
    ("CURSOR",      _CURSOR,     True),
    ("TASK",        _TASK,       True),
    ("STREAM",      _STREAM,     True),
    ("PIPE",        _PIPE,       True),
    ("INSERT",      _INSERT,     True),
    ("UPDATE",      _UPDATE,     True),
    ("DELETE",      _DELETE,     True),
    ("MERGE",       _MERGE,      True),
    ("TRUNCATE",    _TRUNCATE,   True),
    ("CALL",        _CALL,       True),
    ("GRANT",       _GRANT,      True),
    ("REVOKE",      _REVOKE,     True),
    ("USER/ROLE",   _USER_DDL,   True),
]

_PROC_KINDS = {"PROCEDURE", "FUNCTION", "TRIGGER", "PACKAGE"}


# ===========================================================================
# HELPERS
# ===========================================================================

def _clean_id(raw: str) -> str:
    """Elimina brackets y quotes de identificadores SQL."""
    return _re.sub(r'[\[\]`"]', "", raw).strip()


def _extract_doc_header(lines: list[str]) -> tuple[str, int]:
    """
    Extrae el bloque de comentarios de cabecera (antes del primer DDL real).

    Estructura el texto para preservar:
      - Descripción general
      - Parámetros (@param, IN/OUT)
      - Tipos de datos documentados
      - Ejemplos de uso (EXEC, CALL, SELECT)
      - Metadata (autor, fecha, versión)

    Devuelve (doc_text, índice_primera_línea_de_código).
    """
    doc_lines: list[str] = []
    in_block_comment = False
    first_code = 0

    for i, line in enumerate(lines):
        s = line.strip()

        # Bloque /* ... */
        if "/*" in s and not s.startswith("--"):
            in_block_comment = True
        if in_block_comment:
            doc_lines.append(line)
            if "*/" in s:
                in_block_comment = False
            continue

        # Línea de comentario o vacía → parte de cabecera
        if s.startswith("--") or not s:
            doc_lines.append(line)
            continue

        # Primera línea de código real (DDL, DML, DECLARE...)
        first_code = i
        break
    else:
        first_code = len(lines)

    # Limpiar y estructurar el texto de documentación
    cleaned: list[str] = []
    for l in doc_lines:
        s = l.strip()
        if s.startswith("--"):
            c = s[2:].strip()
            if not c:
                cleaned.append("")
                continue
            # Separadores decorativos puros → salto de línea
            if _re.match(r"^[=\-*#~]{8,}$", c):
                if cleaned and cleaned[-1] != "":
                    cleaned.append("")
                continue
            cleaned.append(c)
        elif s in ("/*", "*/", "/**"):
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
        elif s:
            c = _re.sub(r"^\*+\s*", "", s).strip()
            if c:
                cleaned.append(c)

    # Eliminar líneas vacías consecutivas
    result: list[str] = []
    prev_empty = False
    for l in cleaned:
        if not l:
            if not prev_empty:
                result.append("")
            prev_empty = True
        else:
            result.append(l)
            prev_empty = False

    return "\n".join(result).strip(), first_code


def _detect_artefact(
    stripped: str, inside_proc: bool
) -> tuple[str | None, str | None]:
    """
    Detecta el tipo de artefacto SQL en una línea.
    Si inside_proc=True ignora los DDL top-level-only.
    """
    for kind, pat, top_only in _DDL_PATTERNS:
        if top_only and inside_proc:
            continue
        m = pat.match(stripped)
        if not m:
            continue

        if kind == "INDEX":
            name = f"`{_clean_id(m.group(1))}` ON `{_clean_id(m.group(2))}`"
        elif kind in ("GRANT", "REVOKE"):
            perms = m.group(1).strip()[:40]
            name = f"on `{_clean_id(m.group(2))}` ({perms})"
        elif m.lastindex and m.lastindex >= 1:
            name = f"`{_clean_id(m.group(1))}`"
        else:
            name = ""
        return kind, name

    return None, None


# ===========================================================================
# PREPROCESADOR PRINCIPAL
# ===========================================================================

def preprocess(full_text: str, max_chars: int) -> str:
    """
    Preprocesa cualquier fichero SQL generando la estructura semántica.

    Algoritmo:
      1. Extrae cabecera de documentación → ## DOCUMENTACION
      2. Línea a línea detecta artefactos SQL → ### TIPO `nombre`
      3. Dentro de PROC/FUNC/TRIGGER: detecta secciones internas
         usando todos los patrones de comentario de sección reales
      4. CTEs múltiples: cada CTE es una sección ###
      5. Fallback para scripts sin artefactos: agrupa por bloques de comentarios
    """
    lines = full_text.split("\n")
    doc_header, code_start = _extract_doc_header(lines)
    code_lines = lines[code_start:]

    sections: list[str] = []
    current_kind: str | None  = None
    current_name: str | None  = None
    current_lines: list[str]  = []
    pending_comments: list[str] = []
    found_any    = False
    inside_proc  = False
    proc_depth   = 0
    section_counter = 0       # contador de secciones internas del proc actual
    in_block_comment = False

    def flush(nk: str | None = None, nn: str | None = None) -> None:
        nonlocal current_kind, current_name, current_lines, pending_comments
        content = [l for l in current_lines if l.strip()]
        if current_kind and content:
            label = f"### {current_kind} {current_name}".strip()
            sections.append(label + "\n" + "\n".join(current_lines))
        elif content and not current_kind and (sections or found_any):
            # Bloque de código sin artefacto identificado
            sections.append(f"### Bloque {len(sections)+1}\n" + "\n".join(current_lines))
        current_kind, current_name = nk, nn
        current_lines = list(pending_comments)
        pending_comments.clear()

    for raw_line in code_lines:
        s = raw_line.strip()

        # ── Bloques de comentario /* ... */ ────────────────────────────────
        if not in_block_comment and "/*" in s and not s.startswith("--"):
            in_block_comment = True
        if in_block_comment:
            pending_comments.append(raw_line)
            if "*/" in s:
                in_block_comment = False
            continue

        # ── Separadores visuales puros (=====, -----) ─────────────────────
        if _SEP_LINE.match(s):
            pending_comments.append(raw_line)
            continue

        # ── Líneas de comentario -- ────────────────────────────────────────
        if s.startswith("--"):
            if inside_proc and proc_depth > 0 and _is_phase_comment(raw_line):
                # Inicio de sección interna dentro del procedimiento
                flush()
                section_counter += 1
                phase_name = _extract_phase_name(raw_line)
                current_kind = f"Sección {section_counter}:"
                current_name = phase_name
                found_any = True
                current_lines = [raw_line]
            else:
                pending_comments.append(raw_line)
            continue

        # ── Línea vacía ───────────────────────────────────────────────────
        if not s:
            pending_comments.append(raw_line)
            continue

        # ── Rastrear profundidad BEGIN/END dentro de proc ─────────────────
        if inside_proc:
            if _BEGIN_KW.match(s):
                proc_depth += 1
            elif _END_KW.match(s):
                proc_depth = max(0, proc_depth - 1)
                if proc_depth == 0:
                    inside_proc = False
                    section_counter = 0

        # ── Detectar artefacto SQL ────────────────────────────────────────
        kind, name = _detect_artefact(s, inside_proc)
        if kind:
            found_any = True
            flush(kind, name)
            if kind in _PROC_KINDS:
                inside_proc = True
                proc_depth  = 0
                section_counter = 0
            current_lines.append(raw_line)
            continue

        # ── Código regular ────────────────────────────────────────────────
        if pending_comments:
            current_lines.extend(pending_comments)
            pending_comments.clear()
        current_lines.append(raw_line)

    flush()

    # ── Sin artefactos detectados: fallback por bloques de comentarios ────
    if not sections:
        return _fallback_by_blocks(full_text, max_chars)

    parts: list[str] = []
    if doc_header.strip():
        parts.append("## DOCUMENTACION\n" + doc_header)
    parts.extend(sections)

    result = "\n\n".join(parts)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result


# ===========================================================================
# FALLBACK: scripts ad-hoc sin artefactos formales
# ===========================================================================

def _fallback_by_blocks(full_text: str, max_chars: int) -> str:
    """
    Para scripts SQL ad-hoc sin DDL/DML formal:
    SELECT complejos, scripts de análisis, consultas one-off.

    Estrategia:
      1. Si hay bloques de comentarios con descripción → secciones por bloque
      2. Si hay CTEs inline → una sección por CTE
      3. Si no hay nada → el texto tal cual

    Genera ### Consulta N con la primera frase descriptiva como título.
    """
    lines = full_text.split("\n")
    sections: list[str] = []
    current: list[str] = []
    section_n = 1

    def _first_meaningful_comment(block: list[str]) -> str:
        for l in block:
            s = l.strip()
            if s.startswith("--"):
                c = s[2:].strip()
                if c and not _re.match(r"^[=\-*#~]{8,}$", c) and len(c) > 5:
                    return c[:60]
        # Si no hay comentario, tomar primera línea de código
        for l in block:
            s = l.strip()
            if s and not s.startswith("--"):
                return s[:60]
        return f"Consulta {section_n}"

    for line in lines:
        s = line.strip()
        # Un bloque de separadores visuales señala inicio de nueva sección
        if _SEP_LINE.match(s) and current:
            code_in_block = any(
                l.strip() and not l.strip().startswith("--")
                for l in current
            )
            if code_in_block:
                title = _first_meaningful_comment(current)
                sections.append(f"### Consulta {section_n}: {title}\n" + "\n".join(current))
                section_n += 1
                current = [line]
            else:
                current.append(line)
        else:
            current.append(line)

    if current:
        code_in_block = any(
            l.strip() and not l.strip().startswith("--")
            for l in current
        )
        if code_in_block:
            title = _first_meaningful_comment(current)
            sections.append(f"### Consulta {section_n}: {title}\n" + "\n".join(current))

    if not sections:
        return full_text[:max_chars]

    result = "\n\n".join(sections)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result
