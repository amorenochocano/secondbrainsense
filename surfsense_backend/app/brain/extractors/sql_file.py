"""
sql_file.py
-----------
Extractor para ficheros .sql.

v2 — Mejoras respecto a v1:
  - BEGIN/END AWARENESS: el v1 troceaba un CREATE PROCEDURE en 5+ pedazos
    porque cortaba por cada ';' interno sin saber que estaba dentro de un
    BEGIN...END. v2 mantiene profundidad: ';' solo termina statement si
    nesting == 0. GO sigue terminando batch siempre.
  - NOMBRES CON CORCHETES T-SQL: el v1 devolvía obj=None para
    'CREATE PROCEDURE [main].[sp_load_X]' porque el regex no leía dentro
    de los corchetes. v2 los limpia antes de extraer el nombre.
  - DETECCIÓN DE ETAPA SQL: prioridad al keyword después de comentarios
    iniciales (un statement que empieza con '-- comentario\\nCREATE' ahora
    devuelve type='CREATE', no 'OTHER').
"""
import re
import logging
from pathlib import Path
from .base import BaseExtractor

log = logging.getLogger(__name__)

# Tokens que ABREN profundidad
_BEGIN_RE = re.compile(r"\b(BEGIN|CASE)\b", re.IGNORECASE)
# Tokens que CIERRAN profundidad
_END_RE = re.compile(r"\b(END)\b(?!\s*\w)", re.IGNORECASE)
# T-SQL: 'GO' como batch separator (solo en líneas propias)
_GO_LINE_RE = re.compile(r"^\s*GO\s*(?:;\s*)?$", re.IGNORECASE)


class SqlExtractor(BaseExtractor):
    """
    Extrae statements SQL individuales de un fichero .sql.
    Cada statement es un bloque content_type: code / language: sql.
    """

    def extract(self, source: str) -> list[dict]:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        module_name = Path(source).stem
        dialect = self._detect_dialect(content)
        statements = self._split_with_comments(content)

        if not statements:
            return [{
                "content":      content,
                "content_type": "code",
                "language":     "sql",
                "page":         1,
                "module":       module_name,
                "function":     None,
                "metadata":     {"dialect": dialect, "parse_strategy": "raw"},
            }]

        blocks = []
        for idx, unit in enumerate(statements):
            stmt = unit["statement"].strip()
            comment = unit.get("preceding_comment", "").strip()
            if not stmt:
                continue
            chunk_text = self._build_chunk_text(stmt, comment)
            name = self._infer_name(stmt)
            parsed = self._parse_with_sqlglot(stmt, dialect=dialect)
            parse_strategy = parsed.pop("parse_strategy", "regex")
            blocks.append({
                "content":      chunk_text,
                "content_type": "code",
                "language":     "sql",
                "page":         unit.get("start_line", idx + 1),
                "module":       module_name,
                "function":     parsed.get("object_name") or name,
                "metadata":     {
                    "statement_index": idx + 1,
                    "total_statements": len(statements),
                    "statement_type":  self._statement_type(stmt),
                    "dialect": dialect,
                    "has_comment": bool(comment),
                    "comment_text": comment,
                    "object_name": parsed.get("object_name") or name,
                    "object_type": parsed.get("object_type"),
                    "tables_read": parsed.get("tables_read", []),
                    "tables_written": parsed.get("tables_written", []),
                    "columns": parsed.get("columns", []),
                    "parse_strategy": parse_strategy,
                },
            })

        log.debug("[sql] '%s' → %d statements", source, len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # División con conciencia de BEGIN/END
    # ------------------------------------------------------------------

    def _split_with_comments(self, sql: str) -> list[dict]:
        """
        Divide SQL preservando bloques BEGIN/END como UNA SOLA unidad.

        Reglas:
          - Línea de comentario (--): se acumula como comment_buffer del próximo stmt
          - BEGIN / CASE: incrementa nesting
          - END (sin sufijo): decrementa nesting
          - GO en línea propia: termina batch SIEMPRE
          - ';' al final de línea: termina statement SOLO si nesting == 0

        Esto evita partir CREATE PROCEDURE ... AS BEGIN ... ; ... END
        en múltiples bloques sueltos.
        """
        units: list[dict] = []
        lines = sql.splitlines()
        comment_buffer: list[str] = []
        code_buffer: list[str] = []
        nesting = 0
        stmt_start_line = 1

        def flush():
            nonlocal stmt_start_line, comment_buffer, code_buffer
            stmt = "\n".join(code_buffer).strip()
            stmt = re.sub(r"^\s*GO\s*$", "", stmt,
                          flags=re.IGNORECASE | re.MULTILINE).strip()
            if stmt and stmt != ";":
                units.append({
                    "statement": stmt,
                    "preceding_comment": " ".join(comment_buffer).strip(),
                    "start_line": stmt_start_line,
                })
            comment_buffer = []
            code_buffer = []

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Línea vacía: la conservamos dentro del statement si ya hay código
            if not stripped:
                if code_buffer:
                    code_buffer.append(line)
                continue

            # Comentario de línea ANTES de cualquier código del statement actual
            if stripped.startswith("--") and not code_buffer:
                comment_text = stripped[2:].strip()
                # Filtrar separadores decorativos (----, ====, ****)
                if comment_text and not re.match(r"^[=\-\*]{3,}$", comment_text):
                    comment_buffer.append(comment_text)
                continue

            # Inicio del statement (primera línea de código)
            if not code_buffer:
                stmt_start_line = idx

            code_buffer.append(line)

            # Análisis de nesting: quitar comentarios para no contar palabras
            # dentro de '-- coment BEGIN ...'
            line_no_comment = re.sub(r"--.*$", "", line)

            # Actualizar profundidad (contar todas las ocurrencias)
            for _ in _BEGIN_RE.findall(line_no_comment):
                nesting += 1
            for _ in _END_RE.findall(line_no_comment):
                nesting = max(0, nesting - 1)

            # Terminadores
            is_go = bool(_GO_LINE_RE.match(line))
            ends_with_semi_at_root = (
                nesting == 0
                and line_no_comment.rstrip().endswith(";")
            )

            if is_go or ends_with_semi_at_root:
                flush()
                nesting = 0   # safety reset por si quedó descuadrado

        # Volcar resto pendiente
        if code_buffer:
            flush()

        return units

    # ------------------------------------------------------------------
    # Clasificación de statement
    # ------------------------------------------------------------------

    def _statement_type(self, stmt: str) -> str:
        # Saltar comentarios iniciales y espacio antes de detectar keyword
        cleaned = re.sub(r"^(?:--[^\n]*\n|/\*.*?\*/|\s)+", "", stmt, flags=re.DOTALL)
        first_word = cleaned.split()[0].upper() if cleaned else ""
        return first_word if first_word in {
            "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE",
            "DROP", "ALTER", "WITH", "MERGE", "EXEC", "EXECUTE",
            "CALL", "DECLARE", "SET", "IF", "BEGIN", "TRUNCATE",
        } else "OTHER"

    # ------------------------------------------------------------------
    # Inferencia de nombre de objeto (mejorada — limpia corchetes T-SQL)
    # ------------------------------------------------------------------

    def _infer_name(self, stmt: str) -> str | None:
        """
        Extrae el nombre del objeto SQL definido o invocado.

        Cubre:
          - DDL: CREATE/ALTER [OR REPLACE] PROCEDURE|FUNCTION|VIEW|TABLE|TRIGGER|INDEX
          - DML: EXEC/EXECUTE
          - Soporta corchetes T-SQL: [schema].[name] → schema.name
        """
        # Eliminar corchetes T-SQL antes de buscar (preservando puntos)
        stmt_clean = re.sub(r"\[(\w+)\]", r"\1", stmt)

        # DDL
        match = re.search(
            r"(?:CREATE|ALTER)\s+"
            r"(?:OR\s+REPLACE\s+)?"
            r"(?:UNIQUE\s+|NONCLUSTERED\s+|CLUSTERED\s+)?"
            r"(?:PROCEDURE|PROC|FUNCTION|VIEW|TABLE|TRIGGER|INDEX)\s+"
            r"([\w]+(?:\.[\w]+)?)",
            stmt_clean,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)

        # EXEC / EXECUTE
        match = re.search(
            r"(?:EXEC|EXECUTE)\s+([\w]+(?:\.[\w]+)?)",
            stmt_clean,
            re.IGNORECASE,
        )
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Construcción de chunk text
    # ------------------------------------------------------------------

    @staticmethod
    def _build_chunk_text(stmt: str, comment: str) -> str:
        if not comment:
            return stmt
        return f"-- {comment}\n{stmt}"

    # ------------------------------------------------------------------
    # Detección de dialecto
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_dialect(sql: str) -> str:
        hints = {
            "tsql": [r"\bGO\b", r"@\w+", r"\bEXEC\b", r"\bSP_\w+",
                     r"\bSET\s+NOCOUNT\b", r"\[\w+\]\.\[\w+\]"],
            "postgres": [r"\$\$", r"\bRETURNING\b", r"::[\w]+", r"\bSERIAL\b"],
            "mysql": [r"\bAUTO_INCREMENT\b", r"\bENGINE=", r"\bLIMIT\s+\d+"],
            "bigquery": [r"`[\w.]+`", r"\bSTRUCT<", r"\bARRAY<"],
            "sqlite": [r"\bAUTOINCREMENT\b", r"\bWITHOUT\s+ROWID\b"],
        }
        scores = {k: 0 for k in hints}
        for dialect, patterns in hints.items():
            for pattern in patterns:
                if re.search(pattern, sql, re.IGNORECASE):
                    scores[dialect] += 1
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "tsql"

    # ------------------------------------------------------------------
    # Análisis con sqlglot
    # ------------------------------------------------------------------

    def _parse_with_sqlglot(self, stmt: str, dialect: str) -> dict:
        meta = {
            "object_name": None,
            "object_type": None,
            "tables_read": [],
            "tables_written": [],
            "columns": [],
            "parse_strategy": "regex",
        }
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return meta

        try:
            tree = sqlglot.parse_one(stmt, dialect=dialect)
        except Exception:
            return meta

        if tree is None:
            return meta

        meta["parse_strategy"] = "sqlglot"

        table_nodes = list(tree.find_all(exp.Table))
        table_names = [t.name for t in table_nodes if getattr(t, "name", None)]

        if isinstance(tree, exp.Select):
            meta["tables_read"] = sorted(set(table_names))
        elif isinstance(tree, exp.Insert):
            target = tree.find(exp.Table)
            if target and getattr(target, "name", None):
                meta["tables_written"] = [target.name]
            meta["tables_read"] = sorted(set(t for t in table_names
                                              if t not in meta["tables_written"]))
        elif isinstance(tree, (exp.Update, exp.Delete)):
            target = tree.find(exp.Table)
            if target and getattr(target, "name", None):
                meta["tables_written"] = [target.name]
            meta["tables_read"] = sorted(set(table_names))

        if isinstance(tree, exp.Create):
            target = tree.find(exp.Table)
            if target and getattr(target, "name", None):
                meta["object_name"] = target.name
                meta["object_type"] = "table"
        elif isinstance(tree, exp.AlterTable):
            target = tree.find(exp.Table)
            if target and getattr(target, "name", None):
                meta["object_name"] = target.name
                meta["object_type"] = "table"

        cols = [c.name for c in tree.find_all(exp.Column) if getattr(c, "name", None)]
        meta["columns"] = sorted(set(cols))[:100]
        return meta
