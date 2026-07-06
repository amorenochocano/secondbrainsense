- `brain_search` — Busca en el Second Brain de Enagás usando retrieval en cascada
  multinivel (L1 pasaportes semánticos → L2 knowledge/code con RRF y reranking cross-encoder).
  - **Esta es tu herramienta principal** para cualquier pregunta sobre documentación
    interna de Enagás: procedimientos, decisiones técnicas, arquitectura de sistemas,
    proyectos, manuales, informes, código fuente indexado. Úsala ANTES de `web_search`
    y ANTES de responder desde conocimiento general.
  - Diferencia con `search_knowledge_base`: brain_search aplica cascada multinivel
    con reranking semántico, lo que produce resultados más precisos para el dominio
    específico de Enagás. Úsalos en orden: primero `brain_search`, luego
    `search_knowledge_base` si necesitas ampliar con fuentes externas indexadas.
  - El nivel de respuesta se indica en el resultado: "Brain L1" para respuestas
    de alto nivel (pasaportes), "Knowledge L2" para detalle documental completo.
    Usa `force_level=2` explícitamente cuando el usuario pida pasos exactos,
    código, configuración o citas literales.
  - Si el resultado indica que no hay información relevante, informa al usuario
    y usa `web_search` como complemento.
  - Args: `query` (específica, con entidades/proyectos/términos concretos),
    `force_level` (1=resumen, 2=detalle, None=automático), `top_k` (default 4, max 10).
