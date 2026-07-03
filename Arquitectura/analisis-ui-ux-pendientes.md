# SecondBrainSense — Análisis UI/UX pendientes

**Fecha:** 2026-07-03  
**Rama:** `integration/f0-setup`  
**Alcance:** 2 bugs · 2 cambios UX · 1 auditoría visual · 14 ficheros afectados

---

## Resumen

| ID | Tipo | Título | Impacto | Esfuerzo |
|----|------|--------|---------|----------|
| UX-1 | UX | Unificar sidebar — top nav + footer nav en bloque único | Alto | Medio |
| UX-2 | UX | Eliminar sección Credits del sidebar | Alto | Bajo |
| UI-3 | UI | Corregir contraste y tamaño en páginas `/brain/*` | Alto | Bajo |
| BUG-1 | CRÍTICO | Backend vocabulary CRUD endpoints — 404 | Crítico | Medio |
| BUG-2 | MEDIO | ZodError en `/api/v1/admin/ollama-models` | Bajo | Mínimo |

---

## UX-1 · Sidebar — Unificar bloques de navegación

El sidebar tiene dos bloques de nav separados sin justificación UX. Items como Inbox/Automations están en el *top* y los items Brain en el *footer*, lo que fragmenta la navegación principal.

### Estado actual vs propuesta

```
Estado actual                    Propuesta
─────────────────────────────    ─────────────────────────────
┌─────────────────────────┐      ┌─────────────────────────┐
│ Header (search space)   │      │ Header (search space)   │
├─────────────────────────┤      ├─────────────────────────┤
│ New Chat                │ ←top │ New Chat                │ ← bloque
│ Inbox                   │      │  ── Workspace ──        │   único
│ Automations             │      │ Inbox                   │   scrollable
│ Documents (mobile)      │      │ Automations             │
├─────────────────────────┤      │ Documents               │
│ Recents (chats)         │      │  ── Brain ──            │
│  · Chat 1               │      │ Home · Chat · Wiki      │
├─────────────────────────┤      │ Grafo · Ingestar        │
│ SecondBrainSense        │ ←ftr │ Vocabulary              │
│  · Home, Chat, Wiki     │      ├─────────────────────────┤
│ Credits + Earn/Buy      │      │ Recents (chats)         │
│ User profile            │      ├─────────────────────────┤
└─────────────────────────┘      │ User profile            │
                                 └─────────────────────────┘
```

### Ficheros afectados

| Fichero | Líneas | Cambio |
|---------|--------|--------|
| `surfsense_web/app/components/sidebar/Sidebar.tsx` | 211–255, 317–325 | Eliminar separación top/footer. Fusionar en una sección `NavSection` scrollable entre header y profile. |
| `surfsense_web/app/components/layout/LayoutDataProvider.tsx` | 354–401 | Reordenar `navItems` en un único array con separadores `isSectionHeader`. |
| `surfsense_web/app/components/sidebar/NavSection.tsx` | — | Sin cambios. Ya soporta `isSectionHeader`. |

> **Nota:** La sección *Recents* (historial de chats) debe mantenerse separada visualmente del nav principal — entre el bloque de nav y el profile.

---

## UX-2 · Sidebar — Eliminar sección Credits

El componente `SidebarUsageFooter` expone balance en USD, enlaces "Earn credits" y "Buy credits", y para usuarios anónimos una barra de progreso de tokens con CTA de registro. No aplica en despliegue on-premise.

### Componente a eliminar

`SidebarUsageFooter` — `Sidebar.tsx:351–434`

Renderiza: `CreditBalanceDisplay`, link "Earn credits" (badge FREE), link "Buy credits", y para anónimos: progress bar tokens + botón "Create Free Account".

### Ficheros afectados

| Fichero | Líneas | Cambio |
|---------|--------|--------|
| `surfsense_web/app/components/sidebar/Sidebar.tsx` | 327–332, 351–434 | Eliminar render de `<SidebarUsageFooter>` y la función completa. Eliminar imports `CreditCard`, `Zap`, `Badge`, `Progress`. |
| `surfsense_web/app/components/sidebar/CreditBalanceDisplay.tsx` | — | Eliminar fichero completo. |
| `surfsense_web/app/components/layout/LayoutDataProvider.tsx` | 208–244 | Eliminar lógica `insufficient_credits` toast y referencia a "Buy credits". Eliminar prop `pageUsage` de `SidebarProps`. |

### Rutas candidatas a eliminar

| Ruta | Acción |
|------|--------|
| `/dashboard/[space_id]/buy-tokens` | Eliminar |
| `/dashboard/[space_id]/buy-more` | Eliminar |
| `/dashboard/[space_id]/earn-credits` | Eliminar |
| `/dashboard/[space_id]/more-pages` | Revisar — puede tener contenido no relacionado con credits |
| `/dashboard/[space_id]/purchase-success` | Eliminar |
| `/dashboard/[space_id]/purchase-cancel` | Eliminar |

> **Aviso:** Verificar referencias antes de eliminar:
> ```
> grep -r "buy-tokens\|earn-credits\|purchase-success" surfsense_web/app
> ```

---

## UI-3 · Contraste visual en páginas `/brain/*`

Las páginas Brain usan fondos con opacidad (`bg-slate-800/30`, `bg-slate-800/40`) sobre un fondo de página en light mode. El color efectivo resultante es gris medio (~#a5b2c7), haciendo que textos diseñados para fondos oscuros sean prácticamente invisibles.

### Ratios de contraste — estado actual

| Elemento | Color texto | Fondo efectivo (light) | Ratio | WCAG |
|----------|-------------|------------------------|-------|------|
| Número vectores "brain" | `text-violet-400` #a78bfa | bg-slate-800/40 on white ≈ #a5b2c7 | 1.25:1 | ❌ FAIL |
| Número vectores "knowledge" | `text-blue-400` #60a5fa | ≈ #a5b2c7 | 1.15:1 | ❌ FAIL |
| Labels "Vectores / Fuentes" | `text-xs text-slate-500` | ≈ #a5b2c7 | 1.40:1 | ❌ FAIL |
| Número fuentes | `text-slate-200` #e2e8f0 | ≈ #a5b2c7 | 1.80:1 | ❌ FAIL |

### Fix rápido — máximo impacto (recomendado)

Añadir clase `dark` al wrapper de todas las páginas `/brain/*`. Los colores hardcodeados `slate-*` funcionan como fueron diseñados sin modificar cada componente.

```tsx
// Antes
<div className="flex flex-col ...">

// Después
<div className="dark flex flex-col ...">
```

### Fix quirúrgico — componente a componente

Eliminar el modificador de opacidad en todos los fondos afectados:

```diff
- "rounded-xl border bg-slate-800/40 p-5 space-y-3"
+ "rounded-xl border bg-slate-800 p-5 space-y-3"

- "... bg-slate-800/30 ..."
+ "... bg-slate-800 ..."
```

### Ficheros con bg-opacity afectados

| Fichero | Líneas | Detalle |
|---------|--------|---------|
| `brain/metrics/page.tsx` | 72, 294 | `bg-slate-800/40` y `bg-slate-800/30` en CollectionCard |
| `brain/admin/page.tsx` | 87, 264, 302, 376, 454, 597, 625 | 7 ocurrencias de `bg-slate-800/30` |

### Problema secundario — tamaño de texto

| Elemento | Clase actual | Fix |
|----------|-------------|-----|
| Labels de sección (metrics/admin) | `text-xs` + `tracking-wider` | → `text-sm` |
| Labels min/max del slider | `text-xs text-slate-600` | → `text-xs text-slate-400` |
| Valor numérico slider | `text-violet-300` | → `text-violet-200` o variable semántica |

---

## BUG-1 · Vocabulary CRUD endpoints — 404 🔴

La página `/brain/vocabulary` llama a 8 endpoints REST que no existen en el backend. El frontend y la BD están completamente implementados — solo falta la capa de rutas.

**Error en consola:**
```
{"status":404,"statusText":"Not Found","code":"NOT_FOUND"}
BaseApiService.request — brain-api.service.ts:1634
```

### Estado por capa

| Capa | Fichero | Estado |
|------|---------|--------|
| DB models | `db.py` — `BrainDomain`, `BrainDocType`, `BrainEntityHint`, `BrainVocabulary` | ✅ OK |
| Migraciones | `160_brain_metadata_tables.py` | ✅ APLICADA |
| Frontend API | `brain-api.service.ts:383–511` | ✅ OK |
| Frontend UI | `brain/vocabulary/page.tsx` | ✅ OK |
| Backend routes | `brain_routes.py` | ❌ NO IMPLEMENTADO |

### Endpoints que deben implementarse

| Método | Endpoint | Propósito |
|--------|----------|-----------|
| GET | `/api/v1/brain/admin/vocabulary` | Listar vocabulario (soporta `?q=` para búsqueda) |
| POST | `/api/v1/brain/admin/vocabulary` | Crear entrada |
| DELETE | `/api/v1/brain/admin/vocabulary/{id}` | Eliminar entrada |
| GET | `/api/v1/brain/admin/vocabulary/lookup` | Lookup alias → tag canónica |
| GET / POST | `/api/v1/brain/admin/domains` | CRUD dominios |
| GET / POST | `/api/v1/brain/admin/doc-types` | CRUD tipos de documento |
| GET / POST | `/api/v1/brain/admin/entity-hints` | CRUD entity hints |

**Fichero donde implementar:** `surfsense_backend/app/brain/router.py`

> **Aviso:** El endpoint `/vocabulary/lookup` debe registrarse **antes** de `/{id}` en el router para que FastAPI no lo interprete como un ID con valor "lookup".

---

## BUG-2 · ZodError en `/api/v1/admin/ollama-models` 🟡

El schema Zod del frontend y la respuesta del backend son compatibles. El error indica que en runtime se aplica un schema con `z.array(z.string())` (versión anterior) en lugar del schema actual `z.array(z.object({ name: z.string() }))`.

**Error en consola:**
```
"Invalid API response schema - /api/v1/admin/ollama-models"
expected: "string", path: ["models", 0]
```

### Diagnóstico

| Capa | Estado | Detalle |
|------|--------|---------|
| `admin_routes.py:62` | ✅ OK | Devuelve `{"models": [{"name": "modelo"}, ...]}` |
| `brain.types.ts:233–235` | ✅ OK | `z.array(z.object({ name: z.string() }))` — compatible |
| Runtime (build compilado) | ⚠️ STALE | Build cacheado usa schema antiguo con `z.string()` |

**Impacto:** No bloqueante. `BaseApiService.safeParse` logea el error y retorna los datos sin validar. La página funciona.

### Corrección

```powershell
# Limpiar cache Next.js y reiniciar
Remove-Item -Recurse -Force surfsense_web/.next
npm run dev
```

Si persiste tras limpiar cache:
```bash
grep -r "z\.array.*z\.string" surfsense_web/app --include="*.ts"
```

### Ficheros de referencia

| Fichero | Líneas | Nota |
|---------|--------|------|
| `surfsense_web/app/lib/brain.types.ts` | 233–235 | Schema correcto, no necesita cambio |
| `surfsense_web/app/lib/base-api.service.ts` | 250–260 | `safeParse` — no rompe la UI |
| `surfsense_backend/app/routes/admin_routes.py` | 62 | Respuesta correcta |
