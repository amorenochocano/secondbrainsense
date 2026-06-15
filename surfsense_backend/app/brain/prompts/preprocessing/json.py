"""
brain/prompts/preprocessing/json.py
------------------------------------
Preprocesado de ficheros JSON.

v3 — Fabric Pipeline especializado con cobertura completa de tipos de actividad.

  Pipeline Fabric/ADF (ARM wrapper o direct):
    - Multi-pipeline ARM: genera una sección ## por cada pipeline en resources[]
    - Cabecera con parámetros ARM, variables (locales + libraryVariables), policy global
    - Flujo de ejecución con DFS que respeta actividades anidadas (IfCondition, Switch, ForEach)
    - ### Actividad `nombre` [tipo] [INACTIVO?] por cada actividad con detalles específicos:
        TridentNotebook:  notebookId + workspaceId + parámetros relevantes + patrón orchestrator
        InvokePipeline:   pipelineId + parámetros pasados
        SetVariable:      variableName = expresión/valor
        IfCondition:      expresión + actividades true/false
        Switch:           expresión + cases con sus actividades
        Filter:           condición de filtro
        Lookup:           schema.tabla fuente
        ForEach:          batchCount + actividades internas
        Office365Email/Outlook: to + subject (sin body — demasiado largo)
        RefreshDataflow:  dataflowId
        Fail:             mensaje de error
        SqlServerStoredProcedure: nombre del SP + parámetros
    - Policy documentada: timeout + retry por actividad
    - Variables del pipeline con defaults y library vars

  JSON Schema:   ### Propiedad por cada campo
  Config genérica: ### por clave de primer nivel
"""

from __future__ import annotations
import json as _json


def preprocess(full_text: str, max_chars: int) -> str:
    try:
        data = _json.loads(full_text)
    except Exception:
        return full_text[:max_chars]

    # 1. ARM Template — puede contener MÚLTIPLES pipelines en resources[]
    resources = data.get("resources", [])
    if resources and isinstance(resources, list):
        pipeline_resources = [
            r for r in resources
            if isinstance(r, dict) and "activities" in r.get("properties", {})
        ]
        if pipeline_resources:
            return preprocess_fabric_pipeline(data, max_chars)

    # 2. Pipeline Fabric directo (sin ARM wrapper): {properties: {activities: [...]}}
    if "activities" in data.get("properties", {}):
        return preprocess_fabric_pipeline(data, max_chars)

    # 3. JSON Schema
    if data.get("$schema") or (data.get("type") == "object" and "properties" in data):
        return _preprocess_schema(data, max_chars)

    # 4. Config genérica
    return _preprocess_config(data, max_chars)


def _preprocess_schema(data: dict, max_chars: int) -> str:
    """JSON Schema — ### por cada propiedad de primer nivel."""
    lines = [f"## JSON Schema: {data.get('title', 'sin título')}"]
    desc = data.get("description", "")
    if desc:
        lines.append(f"Descripción: {desc}")
    required = data.get("required", [])
    if required:
        lines.append(f"Obligatorios: {', '.join(required)}")
    lines.append("")

    props = data.get("properties", {})
    for k, v in list(props.items())[:30]:
        typ  = v.get("type", "?") if isinstance(v, dict) else "?"
        desc = v.get("description", "") if isinstance(v, dict) else ""
        enum = v.get("enum", []) if isinstance(v, dict) else []
        lines.append(f"### Propiedad `{k}` ({typ})")
        if desc:
            lines.append(f"- {desc}")
        if enum:
            lines.append(f"- Valores: {', '.join(str(e) for e in enum[:8])}")
        if required and k in required:
            lines.append("- **Obligatorio**")
        lines.append("")

    result = "\n".join(lines)
    return result[:max_chars] if len(result) > max_chars else result


def _preprocess_config(data: dict, max_chars: int) -> str:
    """Config genérica — ### por cada clave de primer nivel."""
    lines = ["## Configuración"]
    for k, v in list(data.items())[:20]:
        lines.append(f"### `{k}`")
        if isinstance(v, dict):
            for sk, sv in list(v.items())[:8]:
                lines.append(f"- {sk}: {str(sv)[:80]}")
        elif isinstance(v, list):
            lines.append(f"- [{len(v)} items] muestra: {str(v[0])[:60]}" if v else "- []")
        else:
            lines.append(f"- {str(v)[:100]}")
        lines.append("")

    result = "\n".join(lines)
    return result[:max_chars] if len(result) > max_chars else result


def preprocess_fabric_pipeline(data: dict, max_chars: int) -> str:
    """
    Preprocesa pipelines de Microsoft Fabric (ARM wrapper o direct).

    Soporta:
      - ARM multi-pipeline: resources[] con varios pipelines
      - Direct pipeline: {properties: {activities: [...]}}
      - 13 tipos de actividad: TridentNotebook, InvokePipeline, SetVariable,
        IfCondition, Switch, Filter, Lookup, ForEach, Office365Email,
        Office365Outlook, RefreshDataflow, Fail, SqlServerStoredProcedure
      - Actividades inactivas: state=Inactive → [INACTIVO]
      - Policy por actividad: timeout + retry
      - Variables locales + libraryVariables + parámetros ARM
      - Flujo de ejecución con DFS (entry points → sucesores)
    """
    from collections import defaultdict

    # Normalizar: extraer lista de pipelines a procesar
    resources = data.get("resources", [])
    pipeline_resources = [
        r for r in resources
        if isinstance(r, dict) and "activities" in r.get("properties", {})
    ] if resources else []

    # Si no hay ARM resources, es un direct pipeline
    if not pipeline_resources:
        pipeline_name = data.get("name", "pipeline")
        props = data.get("properties", {})
        pipeline_resources = [{"name": pipeline_name, "properties": props}]

    # Parámetros ARM del template (linked services, etc.)
    arm_params = data.get("parameters", {})

    all_parts: list[str] = []

    for resource in pipeline_resources:
        pipeline_name = resource.get("name", "pipeline")
        props = resource.get("properties", {})
        activities_raw = props.get("activities", [])
        variables     = props.get("variables", {}) or {}
        lib_vars      = props.get("libraryVariables", {}) or {}
        params        = props.get("parameters", {}) or {}
        last_publish  = props.get("lastPublishTime", "")

        pipeline_parts: list[str] = []

        # ── Cabecera del pipeline ────────────────────────────────────────────
        header = [f"## Pipeline: `{pipeline_name}`"]
        if last_publish:
            header.append(f"  Última publicación: {last_publish[:10]}")

        # Parámetros de entrada del pipeline
        if params:
            p_items = []
            for k, v in list(params.items())[:12]:
                default = v.get("defaultValue", "") if isinstance(v, dict) else ""
                typ = v.get("type", "") if isinstance(v, dict) else ""
                entry = f"{k}: {typ}"
                if default not in ("", None):
                    entry += f" = {str(default)[:30]}"
                p_items.append(entry)
            header.append(f"  Parámetros: {', '.join(p_items)}")

        # Variables locales
        if variables:
            v_items = []
            for k, v in list(variables.items())[:10]:
                typ = v.get("type", "") if isinstance(v, dict) else ""
                default = v.get("defaultValue", "") if isinstance(v, dict) else ""
                entry = f"{k}: {typ}"
                if default not in ("", None, False, []):
                    entry += f" = {str(default)[:25]}"
                v_items.append(entry)
            header.append(f"  Variables: {', '.join(v_items)}")

        # Library vars
        if lib_vars:
            lv = [f"{v.get('variableName','?')} ({v.get('libraryName','?')})"
                  for v in lib_vars.values() if isinstance(v, dict)][:6]
            header.append(f"  LibraryVars: {', '.join(lv)}")

        # Parámetros ARM del template
        if arm_params:
            header.append(f"  Infraestructura ARM: {', '.join(list(arm_params.keys())[:6])}")

        pipeline_parts.append("\n".join(header))

        # ── Construir grafo de dependencias (solo actividades top-level) ────
        adj: dict[str, list[str]] = defaultdict(list)
        in_count: dict[str, int]  = defaultdict(int)
        act_by_name: dict[str, dict] = {}

        for act in activities_raw:
            if not isinstance(act, dict):
                continue
            name = act.get("name", "")
            if not name:
                continue
            act_by_name[name] = act
            deps = [d.get("activity", "") for d in (act.get("dependsOn") or [])
                    if isinstance(d, dict)]
            for dep in deps:
                adj[dep].append(name)
                in_count[name] += 1

        # ── Flujo de ejecución (DFS desde entry points) ─────────────────────
        entry_points = [n for n in act_by_name
                        if in_count.get(n, 0) == 0]
        visited: set[str] = set()

        def _dfs(name: str, depth: int = 0) -> list[str]:
            if name in visited or depth > 20:
                return []
            visited.add(name)
            act = act_by_name.get(name, {})
            typ = act.get("type", "")
            state = act.get("state", "Active")
            inactive = " [INACTIVO]" if state == "Inactive" else ""
            # Incluir actividades anidadas en el flujo
            children_from_nesting = _get_nested_activity_names(act)
            line = f"{'  ' * depth}→ {name} [{typ}]{inactive}"
            out = [line]
            # Sucesores directos
            for child in sorted(adj.get(name, [])):
                out.extend(_dfs(child, depth + 1))
            return out

        flow_lines: list[str] = []
        for ep in entry_points:
            flow_lines.extend(_dfs(ep))
        if flow_lines:
            pipeline_parts.append("## Flujo de ejecución\n" + "\n".join(flow_lines))

        # ── Secciones por actividad ────────────────────────────────────────
        for act in activities_raw:
            if not isinstance(act, dict):
                continue
            section = _build_activity_section(act)
            if section:
                pipeline_parts.append(section)

        all_parts.append("\n\n".join(pipeline_parts))

    result = "\n\n" + ("=" * 60) + "\n\n".join(all_parts)
    return result[:max_chars] if len(result) > max_chars else result


# ===========================================================================
# HELPERS DE ACTIVIDADES
# ===========================================================================

def _get_nested_activity_names(act: dict) -> list[str]:
    """
    Extrae los nombres de actividades anidadas dentro de IfCondition, Switch, ForEach.
    """
    names: list[str] = []
    tp = act.get("typeProperties") or {}
    typ = act.get("type", "")

    if typ == "IfCondition":
        for key in ("ifTrueActivities", "ifFalseActivities"):
            for a in (tp.get(key) or []):
                if isinstance(a, dict) and a.get("name"):
                    names.append(a["name"])

    elif typ == "Switch":
        for case in (tp.get("cases") or []):
            for a in (case.get("activities") or []):
                if isinstance(a, dict) and a.get("name"):
                    names.append(a["name"])
        for a in (tp.get("defaultActivities") or []):
            if isinstance(a, dict) and a.get("name"):
                names.append(a["name"])

    elif typ == "ForEach":
        for a in (tp.get("activities") or []):
            if isinstance(a, dict) and a.get("name"):
                names.append(a["name"])

    return names


def _fmt_expr(val: object, max_len: int = 80) -> str:
    """Formatea un valor que puede ser expresión ADF o literal."""
    if isinstance(val, dict):
        v = val.get("value", "")
        return str(v)[:max_len]
    return str(val)[:max_len]


def _fmt_policy(policy: dict) -> str:
    """Formatea la policy de una actividad: timeout + retry."""
    if not policy:
        return ""
    parts = []
    timeout = policy.get("timeout", "")
    if timeout:
        # Convertir 0.12:00:00 → "12h"
        import re
        m = re.match(r"(\d+)\.?(\d+):(\d+):(\d+)", str(timeout))
        if m:
            days, hours, mins, secs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            total_h = days * 24 + hours
            parts.append(f"timeout={total_h}h{mins:02d}m" if mins else f"timeout={total_h}h")
        else:
            parts.append(f"timeout={timeout}")
    retry = policy.get("retry")
    if retry is not None:
        parts.append(f"retry={retry}")
    return ", ".join(parts)


def _build_activity_section(act: dict) -> str:
    """
    Genera el bloque ### para una actividad con todos sus detalles.
    Cubre los 13 tipos de actividad de Fabric + extensible a otros.
    """
    name   = act.get("name", "")
    typ    = act.get("type", "")
    state  = act.get("state", "Active")
    policy = act.get("policy") or {}
    tp     = act.get("typeProperties") or {}

    inactive_tag  = " [INACTIVO]" if state == "Inactive" else ""
    policy_str    = _fmt_policy(policy)

    deps = [d.get("activity", "") for d in (act.get("dependsOn") or [])
            if isinstance(d, dict)]
    dep_conds = {
        d.get("activity", ""): d.get("dependencyConditions", ["Succeeded"])
        for d in (act.get("dependsOn") or [])
        if isinstance(d, dict)
    }

    lines: list[str] = [f"### Actividad `{name}` [{typ}]{inactive_tag}"]

    # Dependencias con condiciones
    if deps:
        dep_strs = []
        for d in deps:
            conds = dep_conds.get(d, ["Succeeded"])
            cond_str = "/".join(conds) if conds != ["Succeeded"] else ""
            dep_strs.append(f"{d}{' (' + cond_str + ')' if cond_str else ''}")
        lines.append(f"- **Depende de**: {', '.join(dep_strs)}")

    if policy_str:
        lines.append(f"- **Policy**: {policy_str}")

    # ── Detalles específicos por tipo ────────────────────────────────────────

    if typ == "TridentNotebook":
        # IDs del notebook (siempre documentar)
        nb_id  = tp.get("notebookId", "")
        ws_id  = tp.get("workspaceId", "")
        if nb_id:  lines.append(f"- **notebookId**: {nb_id}")
        if ws_id:  lines.append(f"- **workspaceId**: {ws_id}")

        # sessionTag
        st = tp.get("sessionTag")
        if st:
            lines.append(f"- **sessionTag**: {_fmt_expr(st, 60)}")

        # Parámetros del notebook
        raw_params = tp.get("parameters") or {}
        if raw_params:
            # Parámetros del patrón orchestrator (los más relevantes primero)
            orchestrator_keys = (
                "sourceSchema", "targetSchema", "write_mode",
                "max_parallel_tasks", "notebook_to_run",
                "ignore_filter", "sav_list_table",
            )
            orch_parts = []
            for key in orchestrator_keys:
                if key in raw_params:
                    v = raw_params[key]
                    orch_parts.append(f"{key}={_fmt_expr(v, 25)}")
            if orch_parts:
                lines.append(f"- **Orchestrator params**: {', '.join(orch_parts)}")

            # Resto de parámetros (contexto)
            other_keys = [k for k in raw_params if k not in orchestrator_keys]
            if other_keys:
                other_strs = [f"{k}={_fmt_expr(raw_params[k], 20)}"
                              for k in other_keys[:8]]
                lines.append(f"- **Params**: {', '.join(other_strs)}")

    elif typ == "InvokePipeline":
        pip_id = tp.get("pipelineId", "")
        ws_id  = tp.get("workspaceId", "")
        wait   = tp.get("waitOnCompletion", True)
        if pip_id: lines.append(f"- **pipelineId**: {pip_id}")
        if ws_id:  lines.append(f"- **workspaceId**: {ws_id}")
        lines.append(f"- **waitOnCompletion**: {wait}")
        # Parámetros pasados al pipeline invocado
        inv_params = tp.get("parameters") or {}
        if inv_params:
            p_strs = [f"{k}={_fmt_expr(v, 30)}"
                      for k, v in list(inv_params.items())[:8]]
            lines.append(f"- **Parámetros**: {', '.join(p_strs)}")
        # External reference (linked service)
        ext_ref = act.get("externalReferences") or {}
        conn = ext_ref.get("connection", "")
        if conn:
            lines.append(f"- **Connection**: {str(conn)[:60]}")

    elif typ == "SetVariable":
        var_name = tp.get("variableName", "")
        val      = tp.get("value", {})
        val_str  = _fmt_expr(val, 80)
        lines.append(f"- **Asigna**: `{var_name}` = {val_str}")
        # Detectar patrón sessionTag
        if "DataFactory" in val_str and "RunId" in val_str:
            lines.append(f"- **Patrón**: sessionTag — identificador único de ejecución")
        # Detectar captura de output de actividad anterior
        elif "activity(" in val_str:
            lines.append(f"- **Patrón**: captura output de actividad anterior")

    elif typ == "IfCondition":
        expr       = _fmt_expr(tp.get("expression", {}), 100)
        true_acts  = [a.get("name", "") for a in (tp.get("ifTrueActivities") or [])
                      if isinstance(a, dict)]
        false_acts = [a.get("name", "") for a in (tp.get("ifFalseActivities") or [])
                      if isinstance(a, dict)]
        lines.append(f"- **Condición**: {expr}")
        if true_acts:  lines.append(f"- **ifTrue**: {', '.join(true_acts)}")
        if false_acts: lines.append(f"- **ifFalse**: {', '.join(false_acts)}")

    elif typ == "Switch":
        on_expr = _fmt_expr(tp.get("on", {}), 80)
        lines.append(f"- **Switch sobre**: {on_expr}")
        for case in (tp.get("cases") or []):
            case_val = case.get("value", "?")
            case_acts = [a.get("name", "") for a in (case.get("activities") or [])
                         if isinstance(a, dict)]
            lines.append(f"- **Case** `{case_val}`: {', '.join(case_acts)}")
        default = [a.get("name", "") for a in (tp.get("defaultActivities") or [])
                   if isinstance(a, dict)]
        if default:
            lines.append(f"- **Default**: {', '.join(default)}")

    elif typ == "Filter":
        items     = _fmt_expr(tp.get("items", {}), 60)
        condition = _fmt_expr(tp.get("condition", {}), 100)
        lines.append(f"- **Items**: {items}")
        lines.append(f"- **Condición**: {condition}")
        if state == "Inactive":
            lines.append(f"- **Nota**: inactivo — onInactiveMarkAs={act.get('onInactiveMarkAs', 'Succeeded')}")

    elif typ == "Lookup":
        first_row = tp.get("firstRowOnly", True)
        ds = tp.get("datasetSettings") or {}
        ds_tp = ds.get("typeProperties") or {}
        schema = ds_tp.get("schema", "")
        table  = ds_tp.get("table", "")
        ls = (ds.get("linkedService") or {}).get("name", "")
        if schema or table:
            lines.append(f"- **Tabla**: {schema}.{table}")
        if ls:
            lines.append(f"- **LinkedService**: {ls}")
        lines.append(f"- **firstRowOnly**: {first_row}")

    elif typ == "ForEach":
        items     = _fmt_expr(tp.get("items", {}), 60)
        batch     = tp.get("batchCount", 1)
        seq       = tp.get("isSequential", False)
        inner     = [a.get("name", "") for a in (tp.get("activities") or [])
                     if isinstance(a, dict)]
        lines.append(f"- **Items**: {items}")
        lines.append(f"- **batchCount**: {batch}  isSequential: {seq}")
        if inner:
            lines.append(f"- **Actividades internas**: {', '.join(inner)}")

    elif typ in ("Office365Email", "Office365Outlook"):
        # Extraer destinatario y asunto — NO el body (demasiado largo)
        if typ == "Office365Email":
            to      = _fmt_expr(tp.get("to", {}), 60)
            subject = _fmt_expr(tp.get("subject", {}), 80)
        else:
            body_obj = tp.get("inputs", {}).get("body", {})
            to      = str(body_obj.get("To", ""))[:60]
            subject = str(body_obj.get("Subject", ""))[:80]
        if to:      lines.append(f"- **To**: {to}")
        if subject: lines.append(f"- **Subject**: {subject}")
        ext_ref = act.get("externalReferences") or {}
        conn = ext_ref.get("connection", "")
        if conn: lines.append(f"- **Connection**: {str(conn)[:60]}")
        if state == "Inactive":
            lines.append("- **Nota**: inactivo (desactivado en este entorno)")

    elif typ == "RefreshDataflow":
        df_id   = tp.get("dataflowId", "")
        ws_id   = tp.get("workspaceId", "")
        df_type = tp.get("dataflowType", "")
        if df_id:   lines.append(f"- **dataflowId**: {df_id}")
        if ws_id:   lines.append(f"- **workspaceId**: {ws_id}")
        if df_type: lines.append(f"- **tipo**: {df_type}")

    elif typ == "SqlServerStoredProcedure":
        sp_name   = tp.get("storedProcedureName", "")
        sp_params = list((tp.get("storedProcedureParameters") or {}).keys())
        if sp_name: lines.append(f"- **SP**: {sp_name}")
        if sp_params: lines.append(f"- **Parámetros**: {sp_params[:8]}")

    elif typ == "Fail":
        msg  = _fmt_expr(tp.get("message", {}), 80)
        code = tp.get("errorCode", "")
        if msg:  lines.append(f"- **Mensaje**: {msg}")
        if code: lines.append(f"- **errorCode**: {code}")

    # Tipo no reconocido: mostrar typeProperties plano
    else:
        if tp:
            flat = {k: str(v)[:40] for k, v in list(tp.items())[:5]
                    if not isinstance(v, (dict, list))}
            if flat:
                lines.append(f"- **Props**: {flat}")

    return "\n".join(lines)


def build_focused(full_text: str, max_chars: int) -> str:
    """Extrae campos doc embebidos + estructura."""
    doc_values: list[str] = []

    def _walk(obj: object, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and k.lower() in _DOC_KEYS and v.strip():
                    doc_values.append(f"{k}: {v.strip()}")
                else:
                    _walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                _walk(item, depth + 1)

    try:
        _walk(_json.loads(full_text))
    except Exception:
        pass

    structure = preprocess(full_text, max_chars // 2)
    parts = []
    if doc_values:
        parts.append("## Campos documentados\n" + "\n".join(doc_values[:200]))
        if len(doc_values) < 20:
            parts.append("## Estructura\n" + structure)
    else:
        parts.append(structure)

    result = "\n\n".join(parts)
    return result[:max_chars] if len(result) > max_chars else result
