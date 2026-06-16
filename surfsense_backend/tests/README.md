# Tests — SecondBrainSense

Cómo está organizada la suite de tests del backend y las convenciones a seguir al añadir nuevos tests.

## Estructura real actual

```
tests/
├── conftest.py                        # fixtures globales + pinning de DATABASE_URL
├── brain/                             # tests del pipeline Brain — CREADOS EN F1
│   ├── __init__.py
│   └── test_pipeline_f1.py            # 20 tests — F1 completo ✅
├── unit/                              # tests unitarios del proyecto SurfSense base
│   └── (tests originales SurfSense, no modificados)
├── integration/                       # tests de integración del proyecto SurfSense base
│   └── (tests originales SurfSense, no modificados)
├── fixtures/                          # ficheros de ejemplo para tests Brain
│   ├── ejemplo.py                     # Python con funciones documentadas (chunking.py de raw/)
│   ├── sp_ejemplo.sql                 # Stored procedure SQL real (sp_load_sm_tables.sql de raw/)
│   ├── nota.md                        # Markdown con headings H1/H2/H3
│   ├── sample.pdf                     # PDF de ejemplo (original SurfSense)
│   └── sample.txt                     # Texto plano (original SurfSense)
└── utils/                             # helpers de test compartidos (original SurfSense)
```

## Estado actual de tests Brain

| Fase | Fichero | Tests | Estado |
|------|---------|-------|--------|
| F1.6 | `brain/test_pipeline_f1.py` | 20 | ✅ Pasando |
| F2.6 | `brain/test_qdrant_f2.py` | — | 🔲 Por crear en F2 |
| F4.5 | `brain/test_router_f4.py` | — | 🔲 Por crear en F4 |
| F4.5 | `brain/test_crag_evaluator.py` | — | 🔲 Por crear en F4 |
| F7.7 | `integration/test_brain_e2e.py` | — | 🔲 Por crear en F7 |

## Cómo ejecutar los tests Brain

```bash
# Suite completa F1 — contenedor sbs-brain-tests:
docker compose -f docker/docker-compose.dev.yml --profile tests run --rm tests

# Con cobertura:
docker compose -f docker/docker-compose.dev.yml --profile tests run --rm tests \
  pytest tests/brain/ -v --cov=app/brain --cov-report=term-missing
```

## Principios

- **Comportamiento, no implementación.** Verifica salidas observables, nunca helpers privados.
- **Mock solo en fronteras del sistema** (APIs externas, LLMs, brokers), nunca colaboradores internos.
- **Unit tests primero** — la mayor parte de la cobertura Brain es unitaria con mocks.
- **Integration/E2E solo cuando genuinamente necesario** — DB real, Qdrant real, stack completo.
