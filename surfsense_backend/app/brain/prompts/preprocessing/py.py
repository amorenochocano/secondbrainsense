"""
brain/prompts/preprocessing/py.py
----------------------------------
Preprocesado de scripts Python (.py) para síntesis del pasaporte semántico.

v4 — Diseñado para el lenguaje Python completo, no para patrones concretos.

FILOSOFÍA
---------
Un fichero Python puede ser cualquiera de estas cosas:

  A) MÓDULO DE BIBLIOTECA: funciones públicas + clases + constantes.
     Cada función y clase es una unidad semántica → ### por cada una.

  B) SCRIPT UTILITARIO con separadores de sección visuales.
     # --------------- Estrategia FIXED ---------------
     # Función principal
     → ## por cada sección, ### por cada función dentro.

  C) CLASE ÚNICA (extractor, connector, router...).
     Métodos públicos Y privados importantes → ### por clase, #### por método.

  D) MÓDULO DE CONFIGURACIÓN/CONSTANTES.
     UPPER_CASE = valor / os.getenv(...) → ## CONSTANTES con sus valores.

  E) SCRIPT CLI con if __name__ == '__main__'.
     main() + punto de entrada CLI documentado.

  F) MIXTO: todo lo anterior combinado.

ESTRUCTURAS PYTHON DETECTADAS
------------------------------
Nivel módulo:
  - Docstring de módulo
  - __all__, __version__, __author__
  - IMPORTS agrupados: stdlib / terceros / locales
  - CONSTANTES UPPER_CASE con valor Constant, os.getenv, int(os.getenv...)
  - CONSTANTES de sección: SET = {...}, LIST = [...], DICT = {...}
  - Separadores visuales de sección con etiqueta:
      # -------- NOMBRE --------
      # ======== NOMBRE ========
      # Estrategia FIXED (comportamiento original)
      (línea con solo # + texto descriptivo entre separadores)
  - if __name__ == '__main__': → punto de entrada CLI

Clases:
  - class Nombre(Herencia) [@decoradores]
  - Docstring de clase
  - Fields de dataclass: nombre: tipo = default
  - Values de Enum: KEY = valor
  - Fields de TypedDict / NamedTuple
  - Métodos: públicos, _privados (si tienen docstring), dunders relevantes
  - @property, @staticmethod, @classmethod, @abstractmethod
  - async def

Funciones top-level:
  - Públicas: SIEMPRE
  - _Privadas: SI tienen docstring (son parte de la API interna)
  - __dunder__: si son __init__, __call__, __str__, __repr__
  - async def: con prefijo async
  - Firma completa con type hints → ast.unparse()

SEPARADORES DE SECCIÓN (patrones de producción)
-------------------------------------------------
Python usa # para separar bloques lógicos. Patrones detectados:

  Separadores visuales simples (ignorar, son decoración):
    # ---------------------------------------------------------------------------
    # ============================================================================

  Separadores con etiqueta (CONVERTIR en ## sección):
    # ----------- Estrategia FIXED (comportamiento original) -----------
    # ======== NOMBRE ========
    # Función principal — selecciona la estrategia según env var
    # Estrategia PARAGRAPH
    # Capa de seguridad — trunca chunks oversized

  Criterio: el comentario # tiene texto significativo (>5 chars, no solo guiones)
  y está en la primera columna (sin indentación), a nivel módulo.

CLASIFICACIÓN DE IMPORTS
-------------------------
  Stdlib: os, sys, re, ast, typing, datetime, pathlib, abc, logging, etc.
  Terceros: pyspark, pandas, numpy, fastapi, qdrant, sentence_transformers, etc.
  Locales: relative imports (.), app.*, brain.*, api.*, rag_lib.*, chunking, ingest_utils

SALIDA TÍPICA
-------------
Módulo de utilidades (chunking.py pattern):
  ## MODULO
  [docstring del módulo]

  ## IMPORTS
  [stdlib / terceros / locales]

  ## CONSTANTES DE MODULO
  CHUNK_SIZE = 600  # int
  CHUNK_OVERLAP = 100  # int
  ATOMIC_CONTENT_TYPES = {'table', 'code', ...}  # set

  ## Estrategia FIXED
  ### `chunk_fixed(text, size, overlap) -> List[str]`
  [docstring + código]

  ## Singleton del modelo semántico
  ### `_get_semantic_model()`
  [docstring — incluida porque tiene docstring relevante]

  ## Función principal
  ### `get_chunks(text, strategy, content_type) -> List[str]`
  [docstring + código]

Clase única (SqlExtractor pattern):
  ## MODULO
  [docstring]

  ## IMPORTS
  [imports]

  ### `SqlExtractor`(BaseExtractor)
  [docstring de clase]
  #### `extract(self, source) -> list[dict]`
  #### `_split_with_comments(self, sql) -> list[dict]`
  #### `_statement_type(self, stmt) -> str`
  #### `_detect_dialect(sql) -> str` [@staticmethod]

Dataclass:
  ### `FetchedItem`  [@dataclass]
  [docstring]
  Fields:
    item_id: str
    slug: str
    title: str
    text: str
    metadata: dict = field(default_factory=dict)

Enum:
  ### `Color`(Enum)
  Values:
    RED = 1
    GREEN = 2
    BLUE = 3

Script CLI (ingest.py pattern):
  ## CONSTANTES DE MODULO
  QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
  CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 600))

  ### `main()`
  [docstring + punto de entrada CLI]
  CLI: python ingest.py [--pdfs DIR] [--chunk-size N]
"""

from __future__ import annotations
import re as _re
import ast as _ast


# ===========================================================================
# STDLIB — lista completa para clasificación de imports
# ===========================================================================

_STDLIB_ROOTS: frozenset[str] = frozenset({
    "abc", "ast", "asyncio", "atexit", "base64", "binascii", "bisect",
    "builtins", "calendar", "cgi", "cmd", "code", "codecs", "codeop",
    "collections", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "csv", "ctypes", "dataclasses",
    "datetime", "dbm", "decimal", "difflib", "dis", "doctest", "email",
    "encodings", "enum", "errno", "faulthandler", "fcntl", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "importlib", "inspect",
    "io", "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
    "locale", "logging", "lzma", "mailbox", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib", "numbers",
    "operator", "optparse", "os", "pathlib", "pdb", "pickle", "pickletools",
    "pkgutil", "platform", "plistlib", "poplib", "posix", "posixpath",
    "pprint", "profile", "pstats", "pty", "pwd", "py_compile", "queue",
    "quopri", "random", "re", "readline", "reprlib", "resource", "rlcompleter",
    "runpy", "sched", "secrets", "select", "selectors", "shelve", "shlex",
    "shutil", "signal", "site", "smtplib", "socket", "socketserver",
    "spwd", "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
    "struct", "subprocess", "sunau", "symtable", "sys", "sysconfig",
    "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios",
    "test", "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc", "tty",
    "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp",
    "zipfile", "zipimport", "zlib", "zoneinfo",
})

# Prefijos de imports locales del proyecto
_LOCAL_PREFIXES: tuple[str, ...] = (
    "app.", "api.", "brain.", "rag_lib.", "ui.",
    "chunking", "ingest_utils", "ingest", "collections_",
)


# ===========================================================================
# DETECCIÓN DE SEPARADORES DE SECCIÓN (comentarios # a nivel módulo)
# ===========================================================================

# Línea de guiones/iguales pura (decoración, ignorar)
_SEP_PURE = _re.compile(r"^#\s*[-=*#~]{8,}\s*$")

# Separador con etiqueta — líneas que empiezan con # y tienen texto descriptivo.
# Patrones en producción:
#   # ----------- Estrategia FIXED (comportamiento original) -----------
#   # ======== Capa de seguridad ========
#   # Función principal — selecciona la estrategia
#   # Estrategia PARAGRAPH
_SEP_WITH_LABEL = _re.compile(
    r"^#\s*(?:"
    r"[-=*#~]{3,}\s+\S.+\S\s+[-=*#~]{3,}"   # --- Nombre ---  === Nombre ===
    r"|[-=*#~]{3,}\s+\S[^-=*#~]+"             # --- Nombre (sin cierre)
    r")",
    _re.IGNORECASE,
)

# Comentario # solo con texto largo como título de sección
# (primera columna, texto de más de 5 chars, no bullet ni código)
_SEP_TITLE = _re.compile(r"^#\s+[A-ZÁÉÍÓÚA-Z][A-Za-záéíóúñÁÉÍÓÚÑ\s\w,./()_-]{4,}$")


def _is_section_comment(line: str, prev_was_sep: bool) -> tuple[bool, str]:
    """
    Determina si una línea de comentario # es un separador de sección
    y extrae su nombre limpio.

    Retorna (es_seccion, nombre_limpio).
    prev_was_sep: True si la línea anterior era un separador puro (--- o ===)
    """
    s = line.strip()
    if not s.startswith("#"):
        return False, ""

    content = s[1:].strip()
    if not content or len(content) < 5:
        return False, ""

    # Separador decorativo puro
    if _SEP_PURE.match(s):
        return False, ""

    # Separador con etiqueta: --- NOMBRE ---  === NOMBRE ===
    if _SEP_WITH_LABEL.match(s):
        # Extraer el nombre limpio
        name = _re.sub(r"^[-=*#~\s]+|[-=*#~\s]+$", "", content).strip()
        return bool(name and len(name) > 3), name

    # Texto después de un separador puro → es el título de esa sección
    if prev_was_sep and _re.match(r"^[A-Za-záéíóúñÁÉÍÓÚÑ_]", content):
        # Excluir comentarios que son bullets o código inline
        if not content.startswith(("-", "*", "→", "·", "•", ">", ">>", "type:")):
            return True, content[:80]

    return False, ""


# ===========================================================================
# HELPERS DE EXTRACCIÓN DE TEXTO
# ===========================================================================

def _get_source_segment(lines: list[str], node: _ast.AST) -> str:
    """Extrae el segmento de código fuente de un nodo AST, incluyendo decoradores."""
    start = node.lineno - 1
    if hasattr(node, "decorator_list") and node.decorator_list:
        start = node.decorator_list[0].lineno - 1
    end = node.end_lineno
    return "\n".join(lines[start:end])


def _get_docstring(node: _ast.AST) -> str | None:
    """Extrae el docstring de una función, clase o módulo."""
    if (
        hasattr(node, "body")
        and node.body
        and isinstance(node.body[0], _ast.Expr)
        and isinstance(node.body[0].value, _ast.Constant)
        and isinstance(node.body[0].value.value, str)
    ):
        return node.body[0].value.value.strip()
    return None


def _format_arg(arg: _ast.arg) -> str:
    """Formatea un argumento con su type hint."""
    if arg.annotation:
        try:
            ann = _ast.unparse(arg.annotation)
            return f"{arg.arg}: {ann}"
        except Exception:
            pass
    return arg.arg


def _format_signature(node: _ast.FunctionDef | _ast.AsyncFunctionDef) -> str:
    """
    Formatea la firma completa preservando type hints, defaults, *args, **kwargs.
    Ejemplo: func(df: DataFrame, cols: List[str] = None, **kw) -> bool
    """
    args = node.args
    parts: list[str] = []

    n_defaults = len(args.defaults)
    n_args = len(args.args)
    for i, arg in enumerate(args.args):
        default_idx = i - (n_args - n_defaults)
        formatted = _format_arg(arg)
        if default_idx >= 0:
            try:
                formatted += f"={_ast.unparse(args.defaults[default_idx])}"
            except Exception:
                formatted += "=..."
        parts.append(formatted)

    if args.vararg:
        parts.append(f"*{_format_arg(args.vararg)}")
    elif args.kwonlyargs:
        parts.append("*")

    for i, arg in enumerate(args.kwonlyargs):
        formatted = _format_arg(arg)
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            try:
                formatted += f"={_ast.unparse(args.kw_defaults[i])}"
            except Exception:
                formatted += "=..."
        parts.append(formatted)

    if args.kwarg:
        parts.append(f"**{_format_arg(args.kwarg)}")

    sig = f"({', '.join(parts)})"
    if node.returns:
        try:
            sig += f" -> {_ast.unparse(node.returns)}"
        except Exception:
            pass
    return sig


def _format_decorators(node: _ast.AST) -> list[str]:
    """Devuelve lista de decoradores formateados."""
    if not hasattr(node, "decorator_list"):
        return []
    result = []
    for d in node.decorator_list:
        try:
            result.append(f"@{_ast.unparse(d)}")
        except Exception:
            result.append("@decorator")
    return result


def _is_private_with_docstring(
    node: _ast.FunctionDef | _ast.AsyncFunctionDef,
) -> bool:
    """
    True si una función _privada tiene un docstring suficientemente largo
    para ser parte de la API interna documentada.
    Umbral: docstring > 20 chars (descarta los de una línea tipo 'Internal use.')
    """
    doc = _get_docstring(node)
    return bool(doc and len(doc) > 20)


# ===========================================================================
# SECCIÓN: MÓDULO (docstring + metadata)
# ===========================================================================

def _build_module_section(tree: _ast.Module) -> str | None:
    """Extrae docstring del módulo, __all__, __version__, __author__."""
    parts: list[str] = []

    doc = _get_docstring(tree)
    if doc:
        parts.append(doc)

    for node in tree.body:
        if isinstance(node, _ast.Assign):
            for target in node.targets:
                if isinstance(target, _ast.Name) and target.id in (
                    "__all__", "__version__", "__author__", "__maintainer__",
                    "__email__", "__license__",
                ):
                    try:
                        parts.append(f"{target.id} = {_ast.unparse(node.value)}")
                    except Exception:
                        pass

    return ("## MODULO\n" + "\n\n".join(parts)) if parts else None


# ===========================================================================
# SECCIÓN: IMPORTS
# ===========================================================================

def _build_imports_section(tree: _ast.Module) -> str | None:
    """
    Clasifica todos los imports en Stdlib / Terceros / Locales.
    Locales: relative imports + prefijos del proyecto.
    """
    stdlib: list[str] = []
    third: list[str] = []
    local: list[str] = []

    for node in tree.body:
        if isinstance(node, _ast.Import):
            for alias in node.names:
                line = f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
                root = alias.name.split(".")[0]
                (stdlib if root in _STDLIB_ROOTS else third).append(line)

        elif isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(
                (f"{a.name} as {a.asname}" if a.asname else a.name)
                for a in node.names
            )
            line = f"from {'.' * node.level}{module} import {names}"
            if node.level > 0:
                local.append(line)
            elif module.split(".")[0] in _STDLIB_ROOTS:
                stdlib.append(line)
            elif any(module.startswith(p) for p in _LOCAL_PREFIXES):
                local.append(line)
            else:
                third.append(line)

    if not (stdlib or third or local):
        return None

    parts = []
    if stdlib:
        parts.append("# Stdlib\n" + "\n".join(stdlib))
    if third:
        parts.append("# Terceros\n" + "\n".join(third))
    if local:
        parts.append("# Locales\n" + "\n".join(local))

    return "## IMPORTS\n" + "\n\n".join(parts)


# ===========================================================================
# SECCIÓN: CONSTANTES DE MÓDULO
# ===========================================================================

def _build_constants_section(tree: _ast.Module) -> str | None:
    """
    Extrae variables UPPER_CASE de nivel módulo.

    Detecta:
      - NOMBRE = literal_value   (int, str, float, bool, None)
      - NOMBRE = {set}  [list]  {dict}
      - NOMBRE = os.getenv("VAR", default)
      - NOMBRE = int(os.getenv(...))  — constantes de config desde env
      - NOMBRE = frozenset({...})
    """
    constants: list[str] = []

    for node in tree.body:
        if not isinstance(node, _ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, _ast.Name):
                continue
            name = target.id
            # UPPER_CASE: al menos 2 chars, no dunder, todo mayúsculas+_+0-9
            if not _re.match(r"^[A-Z][A-Z0-9_]{1,}$", name):
                continue
            if name.startswith("__"):
                continue

            try:
                value_repr = _ast.unparse(node.value)[:100]
            except Exception:
                continue

            # Inferir tipo de la constante
            v = node.value
            if isinstance(v, _ast.Constant):
                tipo = type(v.value).__name__
            elif isinstance(v, _ast.Set):
                tipo = "set"
            elif isinstance(v, _ast.List):
                tipo = "list"
            elif isinstance(v, _ast.Dict):
                tipo = "dict"
            elif isinstance(v, _ast.Tuple):
                tipo = "tuple"
            elif isinstance(v, _ast.Call):
                # os.getenv, int(os.getenv(...)), frozenset(...)
                try:
                    func_name = _ast.unparse(v.func)
                    if "getenv" in func_name:
                        tipo = "env_var"
                    elif "frozenset" in func_name:
                        tipo = "frozenset"
                    else:
                        tipo = "callable"
                except Exception:
                    tipo = "callable"
            else:
                tipo = "expr"

            constants.append(f"  {name} = {value_repr}  # {tipo}")

    return ("## CONSTANTES DE MODULO\n" + "\n".join(constants)) if constants else None


# ===========================================================================
# SECCIÓN: CLASES
# ===========================================================================

def _is_dataclass_or_similar(node: _ast.ClassDef) -> bool:
    """True si la clase tiene @dataclass, hereda de NamedTuple, TypedDict o Enum."""
    decorators = [_ast.unparse(d) for d in node.decorator_list
                  if not isinstance(d, _ast.Attribute)]
    deco_names = " ".join(decorators).lower()
    if "dataclass" in deco_names:
        return True
    bases_str = " ".join(
        _ast.unparse(b) for b in node.bases
        if not isinstance(b, _ast.Attribute)
    )
    return any(t in bases_str for t in ("NamedTuple", "TypedDict", "Enum", "IntEnum", "Flag"))


def _build_class_fields(node: _ast.ClassDef) -> str:
    """
    Extrae los fields de dataclass/NamedTuple/TypedDict/Enum.

    Para dataclass / TypedDict / NamedTuple: ann_assign en el cuerpo de la clase.
    Para Enum: assign con valor.
    """
    fields: list[str] = []

    for item in node.body:
        # Enum: KEY = valor
        if isinstance(item, _ast.Assign):
            for target in item.targets:
                if isinstance(target, _ast.Name):
                    try:
                        val = _ast.unparse(item.value)
                        fields.append(f"  {target.id} = {val}")
                    except Exception:
                        fields.append(f"  {target.id}")

        # dataclass / TypedDict / NamedTuple: field: tipo = default
        elif isinstance(item, _ast.AnnAssign) and isinstance(item.target, _ast.Name):
            try:
                ann = _ast.unparse(item.annotation)
                if item.value:
                    val = _ast.unparse(item.value)
                    fields.append(f"  {item.target.id}: {ann} = {val}")
                else:
                    fields.append(f"  {item.target.id}: {ann}")
            except Exception:
                fields.append(f"  {item.target.id}")

    return "\n".join(fields)


def _build_class_section(node: _ast.ClassDef, lines: list[str]) -> str:
    """
    Genera el bloque ### para una clase con:
    - Decoradores (@dataclass, @abstractclass...)
    - Herencia
    - Docstring
    - Fields (si es dataclass/Enum/NamedTuple/TypedDict)
    - Métodos: públicos + _privados con docstring + dunders relevantes
    """
    decorators = _format_decorators(node)
    is_data = _is_dataclass_or_similar(node)

    bases: list[str] = []
    for base in node.bases:
        try:
            bases.append(_ast.unparse(base))
        except Exception:
            bases.append("?")

    header = f"### `{node.name}`"
    if bases:
        header += f"({', '.join(bases)})"
    if decorators:
        header += f" [{', '.join(decorators)}]"

    parts: list[str] = [header]

    doc = _get_docstring(node)
    if doc:
        parts.append(doc)

    # Fields para dataclass / Enum / TypedDict / NamedTuple
    if is_data:
        fields = _build_class_fields(node)
        if fields:
            parts.append("Fields:\n" + fields)

    # Métodos
    for item in node.body:
        if not isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue

        mname = item.name
        mdecs = _format_decorators(item)
        msig = _format_signature(item)
        mdoc = _get_docstring(item)
        is_async = isinstance(item, _ast.AsyncFunctionDef)

        # Dunders: solo los relevantes
        if mname.startswith("__") and mname.endswith("__"):
            _relevant_dunders = {
                "__init__", "__call__", "__str__", "__repr__",
                "__len__", "__iter__", "__next__", "__enter__",
                "__exit__", "__getitem__", "__setitem__", "__contains__",
                "__eq__", "__lt__", "__gt__", "__add__", "__mul__",
            }
            if mname not in _relevant_dunders:
                continue

        # _privados name-mangled (__name sin dunder final): excluir
        if mname.startswith("__") and not mname.endswith("__"):
            continue

        # _privados simples: incluir siempre en clase (son la implementación)
        prefix = "async " if is_async else ""
        method_header = f"#### `{prefix}{mname}{msig}`"
        if mdecs:
            method_header += f" [{', '.join(mdecs)}]"

        parts.append(method_header)
        if mdoc:
            parts.append("    " + mdoc.replace("\n", "\n    "))

    return "\n".join(parts)


# ===========================================================================
# SECCIÓN: FUNCIONES TOP-LEVEL
# ===========================================================================

def _build_function_section(
    node: _ast.FunctionDef | _ast.AsyncFunctionDef,
    lines: list[str],
    group_label: str | None = None,
) -> str:
    """
    Genera el bloque ### para una función top-level con:
    - Firma completa con type hints
    - Decoradores
    - Docstring
    - Código completo (para que el chunker tenga el código real)
    """
    name = node.name
    decorators = _format_decorators(node)
    sig = _format_signature(node)
    doc = _get_docstring(node)
    is_async = isinstance(node, _ast.AsyncFunctionDef)

    prefix = "async " if is_async else ""
    header = f"### `{prefix}{name}{sig}`"
    if decorators:
        header += f"\n{chr(10).join(decorators)}"

    parts: list[str] = [header]
    if doc:
        parts.append(doc)

    # Incluir código completo de la función
    code = _get_source_segment(lines, node)
    parts.append(code)

    return "\n".join(parts)


# ===========================================================================
# DETECCIÓN DE SECCIONES DE MÓDULO POR COMENTARIOS #
# ===========================================================================

def _extract_module_sections(
    tree: _ast.Module,
    lines: list[str],
) -> list[tuple[str | None, list[str]]]:
    """
    Divide el código del módulo en grupos semánticos basándose en los
    comentarios separadores # que los desarrolladores usan como títulos.

    Retorna lista de (nombre_sección | None, líneas_del_grupo).

    Ejemplo de chunking.py:
      None → [imports, constantes]
      "Estrategia FIXED" → [chunk_fixed]
      "Estrategia PARAGRAPH" → [chunk_paragraph]
      "Singleton del modelo semántico" → [_semantic_model, _get_semantic_model]
      "Estrategia SEMANTIC" → [chunk_semantic]
      "Capa de seguridad" → [_enforce_max_chunk_size]
      "Función principal" → [get_chunks]
    """
    # Obtener el rango de líneas de código (post-cabecera)
    # Mapear cada nodo AST a su línea de inicio (con decoradores)
    node_starts: dict[int, _ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            start = node.lineno - 1
            if hasattr(node, "decorator_list") and node.decorator_list:
                start = node.decorator_list[0].lineno - 1
            node_starts[start] = node

    groups: list[tuple[str | None, list[str]]] = []
    current_label: str | None = None
    current_lines: list[str] = []
    prev_was_sep = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Separador puro: marcar y acumular en pending
        if _SEP_PURE.match(stripped) if stripped else False:
            prev_was_sep = True
            current_lines.append(line)
            continue

        # Verificar si es separador con etiqueta
        is_sec, label = _is_section_comment(line, prev_was_sep)
        if is_sec and not line.startswith((" ", "\t")):
            # Nueva sección: guardar la actual y empezar nueva
            if current_lines and any(l.strip() and not l.strip().startswith("#")
                                     for l in current_lines):
                groups.append((current_label, current_lines))
            current_label = label
            current_lines = []
            prev_was_sep = False
            continue

        prev_was_sep = bool(stripped and _SEP_PURE.match(stripped)) if stripped else False
        current_lines.append(line)

    if current_lines:
        groups.append((current_label, current_lines))

    return groups


# ===========================================================================
# PREPROCESADOR PRINCIPAL
# ===========================================================================

def preprocess(full_text: str, max_chars: int) -> str:
    """
    Preprocesa cualquier fichero Python generando secciones estructuradas.

    Usa AST como fuente de verdad para:
      - Detectar módulo, imports, constantes, clases, funciones
      - Preservar type hints y firmas completas
      - Clasificar imports correctamente

    Si el AST falla (sintaxis inválida, notebook suelto), usa regex como fallback.
    """
    try:
        tree = _ast.parse(full_text)
        return _preprocess_ast(full_text, tree, max_chars)
    except SyntaxError:
        return _preprocess_regex(full_text, max_chars)


def _preprocess_ast(full_text: str, tree: _ast.Module, max_chars: int) -> str:
    """Preprocesado completo usando AST con detección de secciones por comentarios."""
    lines = full_text.split("\n")
    sections: list[str] = []

    # 1. Módulo: docstring + metadata
    mod_sec = _build_module_section(tree)
    if mod_sec:
        sections.append(mod_sec)

    # 2. Imports
    imp_sec = _build_imports_section(tree)
    if imp_sec:
        sections.append(imp_sec)

    # 3. Constantes de módulo
    const_sec = _build_constants_section(tree)
    if const_sec:
        sections.append(const_sec)

    # 4. Clases y funciones — con agrupación por secciones de comentario
    #
    # Detectamos si el módulo usa separadores # para agrupar funciones.
    # Si los usa: generamos ## por cada grupo + ### por cada función.
    # Si no los usa: generamos ### directamente por cada función/clase.

    # Construir mapa línea → nodo AST de nivel módulo
    node_line_map: dict[int, _ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            start = node.lineno - 1
            if hasattr(node, "decorator_list") and node.decorator_list:
                start = node.decorator_list[0].lineno - 1
            node_line_map[start] = node

    # Extraer grupos por separadores
    groups = _extract_module_sections(tree, lines)
    has_groups = any(label is not None for label, _ in groups)

    if has_groups:
        # Módulo con secciones lógicas (chunking.py, sql_file.py pattern)
        for group_label, group_lines in groups:
            group_funcs: list[str] = []

            # Encontrar nodos AST en este grupo
            for line_i, gline in enumerate(lines):
                # Calcular índice global
                try:
                    global_i = lines.index(gline, 0)  # impreciso con líneas repetidas
                except ValueError:
                    continue

            # Alternativa: recorrer nodos y ver cuáles caen en las líneas de este grupo
            group_start = _find_group_start(lines, group_lines)
            group_end = group_start + len(group_lines)

            for node_start, node in node_line_map.items():
                if group_start <= node_start < group_end:
                    if isinstance(node, _ast.ClassDef):
                        group_funcs.append(_build_class_section(node, lines))
                    elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        name = node.name
                        # Incluir: públicas + _privadas con docstring
                        if name.startswith("__") and name.endswith("__"):
                            if name not in ("__init__", "__call__", "__main__"):
                                continue
                        elif name.startswith("_") and not _is_private_with_docstring(node):
                            continue
                        group_funcs.append(_build_function_section(node, lines))

            if group_funcs:
                if group_label:
                    sections.append(f"## {group_label}\n" + "\n\n".join(group_funcs))
                else:
                    sections.extend(group_funcs)

    else:
        # Módulo sin secciones → ### directamente por clase/función
        for node in tree.body:
            if isinstance(node, _ast.ClassDef):
                sections.append(_build_class_section(node, lines))

            elif isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("__") and name.endswith("__"):
                    if name not in ("__init__", "__call__"):
                        continue
                elif name.startswith("_"):
                    if not _is_private_with_docstring(node):
                        continue
                sections.append(_build_function_section(node, lines))

    # Detectar if __name__ == '__main__'
    main_block = _extract_main_block(tree, lines)
    if main_block:
        sections.append(main_block)

    if not sections:
        return _preprocess_regex(full_text, max_chars)

    result = "\n\n".join(sections)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result


def _find_group_start(all_lines: list[str], group_lines: list[str]) -> int:
    """
    Encuentra el índice de inicio de un grupo de líneas en la lista completa.
    Usa la primera línea no vacía del grupo como ancla.
    """
    for gl in group_lines:
        if gl.strip():
            try:
                return all_lines.index(gl)
            except ValueError:
                pass
    return 0


def _extract_main_block(tree: _ast.Module, lines: list[str]) -> str | None:
    """
    Detecta el bloque if __name__ == '__main__': y lo documenta como
    punto de entrada CLI.
    """
    for node in tree.body:
        if not isinstance(node, _ast.If):
            continue
        # Detectar: if __name__ == '__main__':
        test = node.test
        if isinstance(test, _ast.Compare):
            left = test.left
            ops = test.ops
            comparators = test.comparators
            if (isinstance(left, _ast.Name) and left.id == "__name__"
                    and len(ops) == 1 and isinstance(ops[0], _ast.Eq)
                    and len(comparators) == 1
                    and isinstance(comparators[0], _ast.Constant)
                    and comparators[0].value == "__main__"):
                code = "\n".join(lines[node.lineno - 1: node.end_lineno])
                return f"## PUNTO DE ENTRADA CLI\n```python\n{code}\n```"
    return None


# ===========================================================================
# FALLBACK: preprocesado por regex (código con errores de sintaxis)
# ===========================================================================

_DEF_RE   = _re.compile(r"^(?:async\s+)?def\s+([A-Za-z][A-Za-z0-9_]*)\s*\(", _re.MULTILINE)
_CLASS_RE = _re.compile(r"^class\s+([A-Za-z][A-Za-z0-9_]*)(\([^)]*\))?", _re.MULTILINE)


def _preprocess_regex(full_text: str, max_chars: int) -> str:
    """
    Fallback para código con errores de sintaxis o scripts parciales.

    Genera ### por cada función/clase detectada a nivel 0 de indentación.
    Incluye:
      - Funciones públicas y _privadas con docstring
      - async def
      - Clases con herencia
      - Separadores # como ## grupos
    """
    lines = full_text.split("\n")
    output: list[str] = []
    pending_decorators: list[str] = []
    in_docstring = False
    doc_quote: str | None = None
    prev_was_sep = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Rastrear docstrings (no generar headers dentro)
        for q in ('"""', "'''"):
            if not in_docstring and stripped.startswith(q):
                in_docstring = True
                doc_quote = q
                if stripped.count(q) >= 2 and len(stripped) > 3:
                    in_docstring = False
                    doc_quote = None
                break
            elif in_docstring and doc_quote == q and q in stripped:
                in_docstring = False
                doc_quote = None
                break

        if in_docstring:
            output.append(line)
            i += 1
            prev_was_sep = False
            continue

        # Separadores # visuales (a nivel módulo, sin indentación)
        if not line.startswith((" ", "\t")) and stripped.startswith("#"):
            if _SEP_PURE.match(stripped):
                prev_was_sep = True
                output.append(line)
                i += 1
                continue
            is_sec, label = _is_section_comment(line, prev_was_sep)
            if is_sec:
                output.append(f"\n## {label}")
                prev_was_sep = False
                i += 1
                continue
            prev_was_sep = False

        # Decoradores a nivel 0
        if not line.startswith((" ", "\t")) and stripped.startswith("@"):
            pending_decorators.append(line)
            i += 1
            prev_was_sep = False
            continue

        # def / async def / class a nivel 0
        m_def   = _re.match(r"^(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
        m_class = _re.match(r"^class\s+([A-Za-z][A-Za-z0-9_]*)(\([^)]*\))?", stripped)

        if (m_def or m_class) and not line.startswith((" ", "\t")):
            name = (m_def or m_class).group(1)

            # Incluir: pública + _privada (en fallback incluimos todas
            # porque no podemos inspeccionar el docstring fácilmente)
            is_async = stripped.startswith("async")
            prefix = "async " if is_async and m_def else ""

            if output:
                output.append("")

            if m_def:
                output.append(f"### `{prefix}{name}()`")
            else:
                bases = m_class.group(2) or ""
                output.append(f"### `{name}{bases}`")

            output.extend(pending_decorators)
            pending_decorators = []
            output.append(line)
            i += 1
            prev_was_sep = False
            continue

        output.extend(pending_decorators)
        pending_decorators = []
        output.append(line)
        prev_was_sep = False
        i += 1

    result = "\n".join(output).strip()
    if len(result) < 100:
        result = full_text
    if len(result) > max_chars:
        result = result[:max_chars] + "\n\n[... truncado ...]"
    return result
