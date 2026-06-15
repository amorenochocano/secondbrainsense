"""
brain/prompts/preprocessing/drawio.py
--------------------------------------
Preprocesado de diagramas draw.io (.drawio / .xml).

v3 — Soporte completo de los dos formatos reales de draw.io:

  FORMATO A: XML en línea (sin comprimir)
    Usado en diagramas de arquitectura Fabric, diagramas de componentes.
    El contenido de cada <diagram> son hijos XML directos.

  FORMATO B: XML comprimido (base64 + deflate + URL-encode)
    Usado en draw.io desktop y draw.io cloud (versión típica exportada).
    El contenido de cada <diagram> es un string base64 que hay que descomprimir.
    Si no se descomprime → 0 nodos extraídos (bug del preprocesador anterior).

TIPOS DE DIAGRAMA DETECTADOS
------------------------------
  Arquitectura (ARCH):
    Sin aristas o pocas aristas. Nodos = componentes del sistema.
    Clasificados por nombre en: Lakehouse, Pipeline, Notebook, Warehouse,
    Dataflow, SemanticModel, Workspace, StoredProcedure, ExternalSource, Schema.
    Output: ### por tipo de artefacto + ### por capa (raw/std/rch/ctrl/com).

  Flujo de datos (FLOW):
    Muchas aristas. Nodos = etapas del pipeline de datos.
    DFS desde entry points.
    Output: → nodo con tipo inferido, indentado por profundidad.

  Mixto:
    Diagrama con aristas y grupos.
    Output: combina ambos enfoques.

PÁGINAS MÚLTIPLES
-----------------
  Cada página genera una sección ## separada.
  Páginas vacías o de portada/índice se omiten.
  Páginas con < 2 nodos de contenido real se generan como nota breve.

CLASIFICACIÓN DE NODOS (por nombre)
-------------------------------------
  _lh_  → Lakehouse
  _pl_  → Pipeline
  _nb_  → Notebook
  _df_  → Dataflow
  _sm_  → SemanticModel
  _wh_  → Warehouse
  _sp_  → StoredProcedure
  ws_   → Workspace
  raw_  → capa raw
  std_  → capa standard
  rch_  → capa rich/enriched
  ctrl_ → capa control
  com_  → capa common
  orq_  → capa orchestrator
  github, sharepoint, gateway, autodesk, cloud, sap, bw, microstrategy,
  on-premise, repository, power bi, local pc → fuente externa / sistema externo

SALIDA TÍPICA — Arquitectura (arq_sa_data_ingtec_bim):
  ## Página: Raw ACC
  ### Pipelines (4)
  - orq_pl_tte_main_acc [orchestrator]
  - raw_pl_tte_dbo_acc_ae [raw]
  - raw_pl_tte_staging_parallel_acc [raw]
  - raw_pl_tte_mirror_parallel_acc [raw]
  ### Notebooks (6)
  - com_nb_tte_lib_log [common]
  - com_nb_sa_orchestrator_parallel_raw [common]
  ...
  ### Lakehouses (2)
  - raw_lh_tte_acc [raw]: esquemas log, config, dbo, staging, mirror, historical
  - ctrl_lh_sa_IngTec [control]
  ### Fuentes externas
  - Autodesk Construction Cloud
  - Sharepoint
  - On-premise Data Gateway (datagateway-pre-tte01)

SALIDA TÍPICA — Flujo (DYT-SGM-Flujos_y_Cadenas):
  ## Página: Flujo Ordenes Y Operaciones
  FLUJO ORDENES Y OPERACIONES REALES
  → SAP S4
    → ZC_S_COR [extractor]
      → W13 [BW]
        → ZC_S_CORH [DSO]
          → BW4/HANA
            → MicroStrategy - Informacional de Mantenimiento
"""

from __future__ import annotations
import re as _re
from collections import defaultdict


# ===========================================================================
# DESCOMPRESIÓN (formato B: base64 + deflate + URL-encode)
# ===========================================================================

def _decompress_page(text: str) -> str:
    """
    Descomprime el contenido de una página draw.io comprimida.
    draw.io usa: URL-encode → deflate (raw, sin header zlib) → base64.
    Invertimos: base64 → inflate (raw deflate) → URL-decode.
    """
    if not text or not text.strip():
        return ""
    try:
        import base64, zlib
        from urllib.parse import unquote
        compressed = base64.b64decode(text.strip())
        decompressed = zlib.decompress(compressed, -15)  # -15 = raw deflate
        return unquote(decompressed.decode("utf-8", errors="replace"))
    except Exception:
        return ""


def _get_page_xml(diag_el) -> str | None:
    """
    Devuelve el XML de la página como string, ya sea inline o descomprimido.
    Retorna None si la página está vacía.
    """
    import xml.etree.ElementTree as ET

    # Formato A: XML inline (hijos directos)
    children = list(diag_el)
    if children:
        return None  # Ya tiene XML accesible como hijos del elemento

    # Formato B: contenido comprimido como texto
    compressed = (diag_el.text or "").strip()
    if not compressed:
        return None

    xml_str = _decompress_page(compressed)
    return xml_str if xml_str else None


# ===========================================================================
# EXTRACCIÓN DE NODOS Y ARISTAS
# ===========================================================================

def _clean_label(raw: str) -> str:
    """Limpia HTML y entidades de un label draw.io."""
    t = _re.sub(r"<[^>]+>", "", raw or "")
    for ent, rep in [
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&#xa;", "\n"), ("&quot;", '"'), ("&#39;", "'"),
    ]:
        t = t.replace(ent, rep)
    return t.strip()


def _extract_nodes_edges(diag_el):
    """
    Extrae nodos y aristas de un elemento <diagram>.
    Soporta formato A (inline) y B (comprimido).
    Retorna (nodes: dict[id→dict], edges: list[(src,tgt,label)]).
    """
    import xml.etree.ElementTree as ET

    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str, str]] = []

    def _parse_cells(root_el):
        for c in root_el.findall(".//mxCell"):
            cid = c.get("id", "")
            if cid in ("0", "1"):
                continue
            val = _clean_label(c.get("value", ""))
            style = c.get("style", "") or ""

            if c.get("vertex") == "1" and val and len(val) > 1:
                # Excluir nodos con imágenes base64 o muy largos
                if "data:image" not in val and len(val) < 400:
                    nodes[cid] = {
                        "label":  val,
                        "style":  style,
                        "parent": c.get("parent", "1"),
                    }

            if c.get("edge") == "1":
                src, tgt = c.get("source", ""), c.get("target", "")
                elabel = _clean_label(c.get("value", ""))
                if src and tgt:
                    edges.append((src, tgt, elabel))

    # Formato A: inline
    children = list(diag_el)
    if children:
        _parse_cells(diag_el)
        return nodes, edges

    # Formato B: comprimido
    xml_str = _get_page_xml(diag_el)
    if xml_str:
        try:
            page_root = ET.fromstring(xml_str)
            _parse_cells(page_root)
        except ET.ParseError:
            pass

    return nodes, edges


# ===========================================================================
# CLASIFICACIÓN DE NODOS
# ===========================================================================

# Mapa prefijo/substring → (tipo_fabric, capa)
_NAME_PATTERNS: list[tuple[str, str, str]] = [
    # (patrón en nombre_lower, tipo, capa)
    ("_lh_",  "Lakehouse",        ""),
    ("_pl_",  "Pipeline",         ""),
    ("_nb_",  "Notebook",         ""),
    ("_df_",  "Dataflow",         ""),
    ("_sm_",  "SemanticModel",    ""),
    ("_wh_",  "Warehouse",        ""),
    ("_sp_",  "StoredProcedure",  ""),
    ("ws_",   "Workspace",        ""),
    ("raw_",  "artifact",         "raw"),
    ("std_",  "artifact",         "standard"),
    ("rch_",  "artifact",         "rich"),
    ("ctrl_", "artifact",         "control"),
    ("com_",  "artifact",         "common"),
    ("orq_",  "artifact",         "orchestrator"),
    ("sp_",   "StoredProcedure",  ""),
]

_EXTERNAL_KW = frozenset({
    "github", "sharepoint", "gateway", "autodesk", "cloud",
    "on-premise", "repository", "power bi", "local pc", "developers",
    "sap", "bw4", "bw/", "hana", "microstrategy", "s4", "sicar",
    "cr&a", "financial", "mdp_", "bpc",
})

# Tipos de artefacto Fabric que son leyendas (no objetos reales)
_LEGEND_LABELS = frozenset({
    "shortcut", "conexion", "movimiento datos", "ejecucion nb log",
    "ejecucion nb carga", "mov. datos configuracion", "paso configuracion",
    "paso carga datos", "ejecucion sp carga",
})

# Labels que son números sueltos (leyenda numérica)
_NUMERIC_LABEL = _re.compile(r"^\d+$")

# Schemas habituales de lakehouses
_SCHEMA_KW = frozenset({
    "log", "config", "dbo", "staging", "mirror", "historical",
    "main", "process", "loads", "data", "control",
})

# Tipos de objeto Fabric (en leyendas)
_FABRIC_OBJECT_TYPES = frozenset({
    "notebook", "variables", "pipeline", "dataflow gen2", "pipeline main",
    "lakehouse", "warehouse", "stored procedure", "semantic model",
    "reporting", "dashboard", "store procedures",
})


def _classify_node(label: str) -> tuple[str, str]:
    """
    Clasifica un nodo por su nombre.
    Retorna (tipo, capa).  tipo puede ser Fabric type o 'external', 'schema', 'legend', 'other'.
    """
    nl = label.lower()

    # Leyenda o número → descartar
    if nl in _LEGEND_LABELS or _NUMERIC_LABEL.match(nl):
        return ("legend", "")

    # Tipo de objeto Fabric (leyenda de icons)
    if nl in _FABRIC_OBJECT_TYPES:
        return ("fabric_type", "")

    # Fuente externa / sistema externo
    if any(kw in nl for kw in _EXTERNAL_KW):
        return ("external", "")

    # Schema de lakehouse
    if nl in _SCHEMA_KW:
        return ("schema", "")

    # Patrones de nombre Fabric
    for pat, tipo, capa in _NAME_PATTERNS:
        if pat in nl:
            # Determinar tipo efectivo
            if tipo == "artifact":
                # Inferir tipo por patrón más específico dentro de artifact
                for p2, t2, _ in _NAME_PATTERNS:
                    if t2 not in ("artifact", "Workspace") and p2 in nl:
                        return (t2, capa)
                return ("artifact", capa)
            return (tipo, capa)

    # Detectar por nombre si empieza con prefijo de capa
    for prefix, capa in [("raw_","raw"),("std_","standard"),("rch_","rich"),
                          ("ctrl_","control"),("com_","common"),("orq_","orchestrator")]:
        if nl.startswith(prefix):
            return ("artifact", capa)

    return ("other", "")


def _infer_layer(label: str) -> str:
    """Infiere la capa del artefacto por su nombre."""
    nl = label.lower()
    for prefix, capa in [
        ("raw_","raw"),("std_","standard"),("rch_","rich"),
        ("ctrl_","control"),("com_","common"),("orq_","orchestrator"),
    ]:
        if nl.startswith(prefix) or f"_{prefix.strip('_')}_" in nl:
            return capa
    return ""


# ===========================================================================
# DETECCIÓN DEL TIPO DE DIAGRAMA
# ===========================================================================

def _detect_diagram_type(nodes: dict, edges: list) -> str:
    """
    Detecta si el diagrama es arquitectura (ARCH), flujo (FLOW) o mixto.
    FLOW: muchas aristas relativas al número de nodos.
    ARCH: pocas o ninguna arista; nodos = artefactos del sistema.
    """
    if not nodes:
        return "EMPTY"

    n_nodes = len(nodes)
    n_edges = len(edges)
    ratio = n_edges / n_nodes if n_nodes > 0 else 0

    # Señales de flujo: muchas aristas, nodos con nombres de extractores/sistemas
    labels_lower = " ".join(n["label"].lower() for n in nodes.values())
    has_extractors = bool(_re.search(r"zc_s_|zen_s_|zopa_|zadso_|0pm_", labels_lower))
    has_sap = any(kw in labels_lower for kw in ["sap", "bw4", "hana", "microstrategy"])

    if ratio >= 0.5 or (n_edges >= 5 and (has_extractors or has_sap)):
        return "FLOW"

    # Señales de arquitectura: nodos con prefijos Fabric, sin aristas o pocas
    has_fabric = any(
        any(p in n["label"].lower() for p, _, _ in _NAME_PATTERNS)
        for n in nodes.values()
    )
    if has_fabric or ratio < 0.2:
        return "ARCH"

    return "FLOW" if n_edges >= 3 else "ARCH"


# ===========================================================================
# RENDERIZADO: ARQUITECTURA
# ===========================================================================

def _render_arch(page_name: str, nodes: dict, edges: list) -> str:
    """
    Renderiza un diagrama de arquitectura: ### por tipo de artefacto + ### por capa.
    """
    # Clasificar todos los nodos
    by_type: dict[str, list[str]] = defaultdict(list)
    by_layer: dict[str, list[str]] = defaultdict(list)
    schemas_of: dict[str, list[str]] = defaultdict(list)  # lakehouse → schemas

    # Detectar relaciones padre→hijo para inferir schemas de lakehouses
    parent_labels: dict[str, str] = {}
    for cid, n in nodes.items():
        parent_labels[cid] = n["label"]

    child_to_parent: dict[str, str] = {}
    for cid, n in nodes.items():
        pid = n["parent"]
        if pid in parent_labels and pid != cid:
            child_to_parent[cid] = pid

    # Asignar schemas a su lakehouse padre
    for cid, n in nodes.items():
        label = n["label"]
        if label.lower() in _SCHEMA_KW:
            pid = child_to_parent.get(cid, "")
            if pid:
                plabel = parent_labels.get(pid, "")
                if plabel:
                    schemas_of[plabel].append(label)

    for cid, n in nodes.items():
        label = n["label"]
        typ, capa = _classify_node(label)
        if typ in ("legend", "schema", "fabric_type"):
            continue
        by_type[typ].append(label)
        layer = capa or _infer_layer(label)
        if layer:
            by_layer[layer].append(label)

    lines: list[str] = [f"## Página: {page_name}" if page_name else "## Diagrama"]

    # Orden de tipos de Fabric (más importante primero)
    type_order = ["Pipeline", "Notebook", "Lakehouse", "Warehouse", "Dataflow",
                  "SemanticModel", "StoredProcedure", "Workspace", "artifact"]
    rendered_types = set()

    for ftype in type_order:
        items = sorted(set(by_type.get(ftype, [])))
        if not items:
            continue
        rendered_types.add(ftype)
        label_plural = {
            "Pipeline": "Pipelines", "Notebook": "Notebooks",
            "Lakehouse": "Lakehouses", "Warehouse": "Warehouses",
            "Dataflow": "Dataflows", "SemanticModel": "Semantic Models",
            "StoredProcedure": "Stored Procedures", "Workspace": "Workspaces",
            "artifact": "Artefactos",
        }.get(ftype, ftype + "s")
        block = [f"### {label_plural} ({len(items)})"]
        for item in items:
            layer = _infer_layer(item)
            layer_tag = f" [{layer}]" if layer else ""
            schemas = schemas_of.get(item, [])
            schema_tag = f": esquemas {', '.join(sorted(schemas))}" if schemas else ""
            block.append(f"- {item}{layer_tag}{schema_tag}")
        lines.append("\n".join(block))

    # Fuentes externas
    externals = sorted(set(by_type.get("external", [])))
    if externals:
        block = ["### Fuentes externas y sistemas"]
        for ext in externals:
            block.append(f"- {ext}")
        lines.append("\n".join(block))

    # Otros nodos sin clasificar (relevantes)
    others = [lbl for lbl in by_type.get("other", [])
              if len(lbl) > 3 and not _NUMERIC_LABEL.match(lbl)]
    if others:
        block = ["### Otros componentes"]
        for o in sorted(set(others))[:20]:
            block.append(f"- {o}")
        lines.append("\n".join(block))

    # Organización por capa
    layer_order = ["orchestrator", "raw", "standard", "rich", "control", "common"]
    layer_section = ["### Organización por capa"]
    has_layers = False
    for lyr in layer_order:
        items = sorted(set(by_layer.get(lyr, [])))
        if items:
            layer_section.append(f"- [{lyr}] {', '.join(items)}")
            has_layers = True
    if has_layers:
        lines.append("\n".join(layer_section))

    # Flujos con etiqueta (si los hay)
    named_flows = [
        (nodes[s]["label"], nodes[t]["label"], lbl)
        for s, t, lbl in edges
        if s in nodes and t in nodes and lbl and len(lbl) > 1
    ]
    if named_flows:
        block = [f"### Flujos ({len(named_flows)})"]
        for sn, tn, lbl in named_flows[:20]:
            block.append(f"- {sn} → {tn} [{lbl}]")
        lines.append("\n".join(block))

    return "\n\n".join(lines)


# ===========================================================================
# RENDERIZADO: FLUJO DE DATOS (DFS)
# ===========================================================================

def _render_flow(page_name: str, nodes: dict, edges: list) -> str:
    """
    Renderiza un diagrama de flujo con DFS desde los entry points.
    Cada nodo tiene su tipo inferido.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    in_count: dict[str, int] = defaultdict(int)

    for s, t, _ in edges:
        if s in nodes and t in nodes:
            adj[s].append(t)
            in_count[t] += 1

    entry_points = [cid for cid in nodes if in_count.get(cid, 0) == 0]
    visited: set[str] = set()

    def _infer_node_type(label: str) -> str:
        nl = label.lower()
        if _re.match(r"zc_s_|zen_s_", nl):      return "DSO/extractor"
        if _re.match(r"z[a-z]", nl):             return "objeto SAP/BW"
        if "extractor" in nl:                     return "extractor"
        if any(kw in nl for kw in ["sap", "s4"]): return "SAP"
        if any(kw in nl for kw in ["bw4", "bw/", "hana"]): return "BW/HANA"
        if "microstrategy" in nl or "informacional" in nl: return "BI"
        if "flujo" in nl or "cadena" in nl or "flow" in nl: return "título"
        if "origen" in nl:                        return "origen"
        return ""

    def _dfs(cid: str, depth: int = 0) -> list[str]:
        if cid in visited or depth > 20:
            return []
        visited.add(cid)
        label = nodes[cid]["label"]
        ntype = _infer_node_type(label)
        type_tag = f" [{ntype}]" if ntype else ""
        indent = "  " * depth
        out = [f"{indent}→ {label}{type_tag}"]
        for child in adj.get(cid, []):
            out.extend(_dfs(child, depth + 1))
        return out

    lines: list[str] = [f"## Página: {page_name}" if page_name else "## Flujo"]

    # Título del flujo (nodo que contiene "flujo", "cadena", "flow")
    title_nodes = [n["label"] for n in nodes.values()
                   if any(kw in n["label"].lower() for kw in ("flujo", "cadena", "flow"))
                   and len(n["label"]) > 5]
    if title_nodes:
        lines.append(title_nodes[0])

    flow_lines: list[str] = []
    for ep in entry_points:
        flow_lines.extend(_dfs(ep))

    # Nodos no visitados (huérfanos)
    orphans = [nodes[cid]["label"] for cid in nodes if cid not in visited
               and not _NUMERIC_LABEL.match(nodes[cid]["label"])]

    if flow_lines:
        lines.append("\n".join(flow_lines))
    if orphans:
        lines.append("Componentes adicionales:\n" +
                     "\n".join(f"- {o}" for o in sorted(set(orphans))[:15]))

    return "\n\n".join(lines)


# ===========================================================================
# PREPROCESADOR PRINCIPAL
# ===========================================================================

def preprocess(full_text: str, max_chars: int, root=None) -> str:
    """
    Preprocesa cualquier fichero draw.io generando secciones semánticas por página.

    Soporta:
      - Formato inline (XML directo, ej: diagramas de arquitectura Fabric)
      - Formato comprimido (base64+deflate, ej: draw.io desktop/cloud)
      - Diagramas de arquitectura: ### por tipo de artefacto Fabric
      - Diagramas de flujo: DFS con tipos de nodo inferidos
      - Multi-página: ## por página relevante
    """
    import xml.etree.ElementTree as ET

    if root is None:
        try:
            root = ET.fromstring(full_text.strip())
        except Exception:
            return full_text[:max_chars]

    return _process_root(root, max_chars)


def _process_root(root, max_chars: int) -> str:
    import xml.etree.ElementTree as ET

    diagrams = root.findall("diagram") or [root]
    all_sections: list[str] = []

    for diag in diagrams:
        page_name = diag.get("name", "").strip()
        nodes, edges = _extract_nodes_edges(diag)

        # Páginas vacías o de portada → nota breve
        if not nodes:
            if page_name and page_name.lower() not in ("portada", "índice", "indice", "leyenda", "legend"):
                all_sections.append(f"## Página: {page_name}\n(Sin contenido extraíble)")
            continue

        # Páginas muy pequeñas (solo título, portada real)
        real_nodes = {cid: n for cid, n in nodes.items()
                      if not _NUMERIC_LABEL.match(n["label"])
                      and n["label"].lower() not in _LEGEND_LABELS
                      and n["label"].lower() not in _FABRIC_OBJECT_TYPES}
        if len(real_nodes) < 2:
            # Página de portada/índice — nota breve
            if real_nodes:
                first = next(iter(real_nodes.values()))["label"]
                all_sections.append(f"## Página: {page_name}\n{first}")
            continue

        diag_type = _detect_diagram_type(real_nodes, edges)

        if diag_type == "ARCH":
            section = _render_arch(page_name, nodes, edges)
        elif diag_type == "FLOW":
            section = _render_flow(page_name, nodes, edges)
        else:
            section = _render_arch(page_name, nodes, edges)

        all_sections.append(section)

    if not all_sections:
        # Fallback: extraer todos los labels
        all_labels: list[str] = []
        for cell in root.iter("mxCell"):
            val = _re.sub(r"<[^>]+>", "", cell.get("value", "") or "").strip()
            if val and len(val) > 1 and "data:image" not in val:
                all_labels.append(f"- {val}")
        return ("## Nodos del diagrama\n" + "\n".join(all_labels[:80]))[:max_chars]

    result = "\n\n---\n\n".join(all_sections)
    return result[:max_chars] if len(result) > max_chars else result
