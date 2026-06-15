# Plan Maestro de Integración
## Second Brain v4 + SurfSense → SecondBrainSense Platform

**Versión:** 1.0  
**Fecha:** Junio 2026  
**Autor:** Architecture Team  
**Estado:** Aprobado para ejecución

---

## Visión del producto

Construir una plataforma única — **SecondBrainSense** — que combine:

- La **inteligencia semántica** de Second Brain v4: pipeline de tres fases, pasaporte semántico en 7 secciones, síntesis multi-call con quality_trigger, router multinivel L1→L2→L0, 14 tipos de documento con preprocesadores específicos
- La **infraestructura corporativa** de SurfSense: 25+ conectores (GitHub, Jira, Confluence, Slack, Notion...), RBAC multi-usuario, UI Next.js madura, Celery scheduler, API REST completa

El resultado es una plataforma RAG empresarial que ninguno de los dos sistemas puede ser por separado.

---

## Principios arquitectónicos del plan

**P1 — Second Brain es el motor, SurfSense es la infraestructura.**  
La lógica de procesamiento semántico (extractores, preprocesadores, síntesis, router) viene de Second Brain. La gestión de conectores, usuarios, scheduling y UI de producción viene de SurfSense.

**P2 — Un único punto de transformación.**  
Todo documento, venga del conector que venga (GitHub, Jira, fichero local), pasa por el mismo pipeline de tres fases de Second Brain antes de llegar a Qdrant.

**P3 — Qdrant sustituye a pgvector para vectores.**  
PostgreSQL mantiene todo lo relacional (usuarios, documentos, conectores, chats). Qdrant gestiona las tres colecciones vectoriales (`brain`, `knowledge`, `code`) con embeddings diferenciados.

**P4 — Sin regresión funcional.**  
Cada fase es desplegable y testeable de forma independiente. El sistema funciona en producción al final de cada fase, no solo al final del proyecto.

**P5 — El fork de SurfSense es el repo base.**  
Second Brain migra sus componentes al monorepo del fork. No hay dos repositorios en paralelo.

---

## Stack tecnológico resultante

```
┌─────────────────────────────────────────────────────────────────┐
│  FRONTEND                                                       │
│  Next.js (SurfSense) + Streamlit embebido (Brain views)        │
├─────────────────────────────────────────────────────────────────┤
│  API LAYER                                                      │
│  FastAPI (SurfSense) — endpoints Brain añadidos como routers   │
├─────────────────────────────────────────────────────────────────┤
│  PIPELINE DE PROCESAMIENTO (Second Brain)                       │
│  Fase 1: Extractores (16 formatos)                             │
│  Fase 2: UniversalCleaner (7 operaciones)                      │
│  Fase 3: Preprocesadores semánticos (14 tipos)                 │
│  Síntesis: Planner + ModelProfiles + multi-call                │
├─────────────────────────────────────────────────────────────────┤
│  CONECTORES (SurfSense)                                         │
│  GitHub · Jira · Confluence · Slack · Notion · Drive · ...    │
├─────────────────────────────────────────────────────────────────┤
│  COLAS Y SCHEDULING (SurfSense)                                 │
│  Celery Workers + Celery Beat + Redis                          │
├─────────────────────────────────────────────────────────────────┤
│  PERSISTENCIA                                                   │
│  PostgreSQL → relacional (usuarios, docs, conectores, chats)   │
│  Qdrant     → vectorial  (brain 768d · knowledge 768d · code 2560d) │
│  /data/brain/ → pasaportes .md                                 │
├─────────────────────────────────────────────────────────────────┤
│  IA LOCAL                                                       │
│  Ollama: nomic-embed-text · qwen3-embedding:4b                 │
│          qwen2.5-coder · deepseek-r1 · llama3.2-vision        │
└─────────────────────────────────────────────────────────────────┘
```
IMPORTANTE:
El codigo completo del second brain esta : C:\Users\EN31380\OneDrive - Enagás, S.A\Documentos\Mis_proyectos\AMCH\second brain\ revisar si tienes alguna duda de como funcionan los programas o los procesos. APOLLATE EN ARQUITECTURA_SECOND_BRAIN_v4.md y ew menor medida ARQUITECTURA_SECOND_BRAIN.md que esta desactualizado pero puede darte contexto a nivel de UI y otras compomentes.
---

## Fases del proyecto

| Fase | Nombre | Duración | Dependencias |
|------|--------|----------|--------------|
| [F0](./F0-preparacion.md) | Preparación y Setup | 3 días | — |
| [F1](./F1-pipeline-tres-fases.md) | Pipeline de Tres Fases en SurfSense | 1 semana | F0 |
| [F2](./F2-qdrant-colecciones.md) | Qdrant y Tres Colecciones | 1 semana | F1 |
| [F3](./F3-sintesis-pasaporte.md) | Síntesis Multi-call y Pasaporte Semántico | 1 semana | F2 |
| [F4](./F4-router-multinivel.md) | Router Multinivel L1→L2→L0 | 1 semana | F3 |
| [F5](./F5-conectores-pipeline.md) | Conectores SurfSense → Pipeline Brain | 1 semana | F4 |
| [F6](./F6-ui-integracion.md) | UI Integrada: Next.js + Brain Views | 2 semanas | F5 |
| [F7](./F7-hardening.md) | Hardening, Deuda Técnica y Producción | 1 semana | F6 |
| [F8](./F8-crag-agente-evaluador.md) | CRAG: Agente Evaluador y Contingencia Web | 1 semana | F4 |

**Duración total estimada: 10 semanas**

---

## Criterios de aceptación globales

- [ ] Un documento `.py` ingestado desde GitHub y desde fichero local produce el mismo pasaporte semántico
- [ ] Consulta multinivel responde en < 15s con modelo qwen2.5-coder:3b en CPU
- [ ] RBAC de SurfSense controla acceso a pasaportes por Search Space
- [ ] Re-síntesis desde fuente original funciona sin contaminación circular
- [ ] Zero downtime entre fases (cada fase deployable independientemente)
- [ ] Deuda técnica DT-01 a DT-09 documentada y plan de resolución activo
