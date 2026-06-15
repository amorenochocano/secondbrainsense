"""
python_file.py
--------------
Extractor para ficheros .py.
Divide el fichero por funciones y clases de nivel superior (top-level)
usando el AST de Python. Nunca corta una función a mitad.

v2 — Correcciones críticas respecto a v1:
  - DUPLICACIÓN ELIMINADA: el v1 emitía 'ClassHeader' + 'ClassDef' para
    clases sin métodos, y solo 'ClassHeader' + métodos individuales para
    clases con métodos (perdiendo atributos de clase). v2 emite SIEMPRE
    un ClassDef con la clase completa, más bloques individuales por
    método para facilitar búsqueda granular.
  - ATRIBUTOS DE CLASE PRESERVADOS: 'DEFAULT_WAREHOUSE = "X"' dentro de
    una clase ya no se pierde en module_level — forma parte del bloque
    ClassDef de su clase.
  - CLASES ANIDADAS: 'class Inner' dentro de 'class Outer' ahora se
    extrae como bloque NestedClassDef con parent_class='Outer'. Antes
    su contenido caía en module_level.
  - METADATA ENRIQUECIDA: las clases incluyen lista de métodos y clases
    base en metadata para facilitar búsqueda.
"""
import ast
import logging
from pathlib import Path
from .base import BaseExtractor

log = logging.getLogger(__name__)


class PythonExtractor(BaseExtractor):
    """
    Extrae funciones y clases (incluyendo anidadas) de ficheros .py como
    bloques de código independientes (content_type: code).
    """

    def extract(self, source: str) -> list[dict]:
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            source_code = f.read()

        module_name = Path(source).stem

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            log.warning("[python] SyntaxError en '%s'. Devolviendo fichero completo.", source)
            return [{
                "content":      source_code,
                "content_type": "code",
                "language":     "python",
                "page":         1,
                "module":       module_name,
                "function":     None,
                "metadata":     {"parse_error": True},
            }]

        lines = source_code.splitlines()
        imports = self._extract_imports(tree)
        blocks: list[dict] = []
        covered_ranges: list[tuple[int, int]] = []

        for node in tree.body:
            # Funciones top-level
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                block = self._build_symbol_block(
                    node=node, lines=lines, module_name=module_name,
                    page=node.lineno, parent_class=None,
                    imports=imports, symbol_type=type(node).__name__,
                )
                blocks.append(block)
                covered_ranges.append(
                    (block["metadata"]["line_start"], block["metadata"]["line_end"])
                )
                continue

            # Clases top-level: SIEMPRE emitir como un bloque completo
            if isinstance(node, ast.ClassDef):
                class_block = self._build_symbol_block(
                    node=node, lines=lines, module_name=module_name,
                    page=node.lineno, parent_class=None,
                    imports=imports, symbol_type="ClassDef",
                )
                blocks.append(class_block)
                covered_ranges.append(
                    (class_block["metadata"]["line_start"], class_block["metadata"]["line_end"])
                )

                # Bloques individuales para métodos y clases anidadas
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_block = self._build_symbol_block(
                            node=child, lines=lines, module_name=module_name,
                            page=node.lineno, parent_class=node.name,
                            imports=imports, symbol_type="ClassMethod",
                        )
                        blocks.append(method_block)
                    elif isinstance(child, ast.ClassDef):
                        # Clase anidada: emitir como bloque propio
                        nested_block = self._build_symbol_block(
                            node=child, lines=lines, module_name=module_name,
                            page=node.lineno, parent_class=node.name,
                            imports=imports, symbol_type="NestedClassDef",
                        )
                        blocks.append(nested_block)
                        # Métodos de la clase anidada
                        for inner in child.body:
                            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                inner_method = self._build_symbol_block(
                                    node=inner, lines=lines, module_name=module_name,
                                    page=child.lineno,
                                    parent_class=f"{node.name}.{child.name}",
                                    imports=imports, symbol_type="ClassMethod",
                                )
                                blocks.append(inner_method)

        # Bloque module_level: lo que NO está dentro de ninguna clase/función
        module_level = self._build_module_level_block(
            lines, module_name, imports, covered_ranges,
        )
        if module_level:
            blocks.append(module_level)

        # Script plano sin definiciones top-level
        if not blocks:
            blocks.append({
                "content":      source_code,
                "content_type": "code",
                "language":     "python",
                "page":         1,
                "module":       module_name,
                "function":     None,
                "metadata":     {},
            })

        log.debug("[python] '%s' → %d bloques (funciones/clases)", source, len(blocks))
        return blocks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_imports(tree: ast.AST) -> list[str]:
        imports: list[str] = []
        for node in getattr(tree, "body", []):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imports.append(mod)
        return sorted(set(i for i in imports if i))

    @staticmethod
    def _extract_docstring(node: ast.AST) -> str:
        return ast.get_docstring(node) or ""

    def _get_start_with_decorators(self, node: ast.AST, lines: list[str]) -> int:
        """Devuelve el índice de línea (0-based) de inicio incluyendo decoradores."""
        start = max(getattr(node, "lineno", 1) - 1, 0)
        while start > 0:
            prev_line = lines[start - 1].strip()
            if prev_line.startswith("@"):
                start -= 1
                continue
            break
        return start

    @staticmethod
    def _extract_decorator_names(node: ast.AST) -> list[str]:
        names: list[str] = []
        for dec in getattr(node, "decorator_list", []):
            if isinstance(dec, ast.Name):
                names.append(dec.id)
            elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                names.append(f"{dec.value.id}.{dec.attr}")
            elif isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Name):
                    names.append(func.id)
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    names.append(f"{func.value.id}.{func.attr}")
        return names

    @staticmethod
    def _has_type_hints(node: ast.AST) -> bool:
        args = getattr(node, "args", None)
        if args is None:
            return False
        if getattr(node, "returns", None) is not None:
            return True
        all_args = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        if args.vararg:
            all_args.append(args.vararg)
        if args.kwarg:
            all_args.append(args.kwarg)
        return any(getattr(a, "annotation", None) is not None for a in all_args)

    def _build_symbol_block(
        self,
        node: ast.AST,
        lines: list[str],
        module_name: str,
        page: int,
        parent_class: str | None,
        imports: list[str],
        symbol_type: str,
    ) -> dict:
        start_idx = self._get_start_with_decorators(node, lines)
        end_idx = getattr(node, "end_lineno", getattr(node, "lineno", 1))
        chunk_code = "\n".join(lines[start_idx:end_idx]).strip("\n")
        decorators = self._extract_decorator_names(node)

        metadata = {
            "type": symbol_type,
            "line_start": start_idx + 1,
            "line_end": end_idx,
            "docstring": self._extract_docstring(node),
            "has_decorators": bool(decorators),
            "decorators": decorators,
            "imports_used": imports,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "has_type_hints": (
                self._has_type_hints(node)
                if not isinstance(node, ast.ClassDef) else False
            ),
            "parent_class": parent_class,
            "language": "python",
            "oversized": False,
        }

        # Para clases: añadir lista de métodos y bases a metadata
        if isinstance(node, ast.ClassDef):
            method_names = [
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            base_names = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
                elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                    base_names.append(f"{base.value.id}.{base.attr}")
            metadata["methods"] = method_names
            metadata["bases"] = base_names

        return {
            "content": chunk_code,
            "content_type": "code",
            "language": "python",
            "page": page,
            "module": module_name,
            "function": getattr(node, "name", None),
            "metadata": metadata,
        }

    @staticmethod
    def _line_covered(line_no: int, ranges: list[tuple[int, int]]) -> bool:
        return any(start <= line_no <= end for start, end in ranges)

    def _build_module_level_block(
        self,
        lines: list[str],
        module_name: str,
        imports: list[str],
        covered_ranges: list[tuple[int, int]],
    ) -> dict | None:
        """
        Construye el bloque con líneas fuera de funciones/clases top-level:
        docstring del módulo, imports, constantes, código suelto.

        IMPORTANTE: como covered_ranges abarca el ClassDef completo, este
        bloque NO incluye el interior de clases (atributos, métodos, etc.).
        """
        selected: list[str] = []
        first_line = None
        last_line = None
        for idx, line in enumerate(lines, start=1):
            if self._line_covered(idx, covered_ranges):
                continue
            if not line.strip() and not selected:
                continue
            selected.append(line)
            first_line = idx if first_line is None else first_line
            last_line = idx

        content = "\n".join(selected).strip()
        if not content:
            return None

        return {
            "content": content,
            "content_type": "code",
            "language": "python",
            "page": first_line or 1,
            "module": module_name,
            "function": None,
            "metadata": {
                "type": "module_level",
                "line_start": first_line or 1,
                "line_end": last_line or (first_line or 1),
                "imports_used": imports,
                "language": "python",
                "oversized": False,
            },
        }
