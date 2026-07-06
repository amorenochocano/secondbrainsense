Example — usuario pregunta sobre un proceso interno:

User: "¿Cómo se realiza el proceso de alta de un nuevo proveedor en SAP?"

→ Llama a `brain_search` primero:
```
brain_search(query="proceso alta nuevo proveedor SAP", force_level=None)
```

Si el resultado es nivel "Knowledge L2" y contiene los pasos, responde citando las fuentes.
Si el resultado dice "sin información relevante", usa `web_search` como complemento
e indica al usuario que el documento puede no estar indexado aún en el Brain.

Example — usuario pide código o configuración exacta:

User: "¿Cuál es la configuración exacta del pipeline de ingesta en Fabric?"

→ Fuerza nivel 2 para obtener detalle:
```
brain_search(query="configuración pipeline ingesta Fabric parámetros", force_level=2)
```
