# M2 — Search Spaces y RBAC
> Nivel: Admin · Tiempo estimado: 3h  
> Objetivo: diseñar espacios de conocimiento y controlar quién accede a qué

---

## 2.1 Qué es un Search Space

Un Search Space es la unidad de aislamiento de SurfSense. Cada uno tiene:

- Sus propios **documentos** indexados
- Sus propios **conectores** (GitHub, Jira, Slack...)
- Sus propios **miembros** con roles diferenciados
- Sus propios **chats** e historial
- Su propia **configuración de LLM**

Los Search Spaces no comparten datos entre sí. Un miembro de "Proyecto Alpha" no ve nada de "Infraestructura" a menos que sea invitado explícitamente.

---

## 2.2 Estrategia de diseño para empresa IT

No crees un solo Search Space para todo el equipo. La granularidad es seguridad y relevancia.

**Estructura recomendada:**

```
┌─────────────────────────────────────────────────────┐
│ SEARCH SPACE: "Plataforma Core"                     │
│  Conectores: GitHub (monorepo), Confluence (arq.)   │
│  Miembros: todo el equipo (Viewer) + leads (Editor) │
├─────────────────────────────────────────────────────┤
│ SEARCH SPACE: "Proyecto Alpha"                      │
│  Conectores: GitHub (repo alpha), Jira (board)      │
│  Miembros: equipo alpha únicamente                  │
├─────────────────────────────────────────────────────┤
│ SEARCH SPACE: "Ops & Infraestructura"               │
│  Conectores: runbooks PDF, wikis ops, alertas       │
│  Miembros: ops team (Editor) + devs senior (Viewer) │
├─────────────────────────────────────────────────────┤
│ SEARCH SPACE: "RRHH / Empresa" (datos sensibles)    │
│  Conectores: políticas PDF, onboarding              │
│  Miembros: solo RRHH + management                   │
└─────────────────────────────────────────────────────┘
```

**Principio:** un dev de frontend no necesita ver runbooks de base de datos. Menos ruido = mejores respuestas del RAG.

---

## 2.3 Roles y permisos

| Acción | Owner | Admin | Editor | Viewer |
|--------|-------|-------|--------|--------|
| Eliminar el Search Space | ✅ | ❌ | ❌ | ❌ |
| Gestionar miembros y roles | ✅ | ✅ | ❌ | ❌ |
| Crear/eliminar conectores | ✅ | ✅ | ✅ | ❌ |
| Subir/eliminar documentos | ✅ | ✅ | ✅ | ❌ |
| Configurar LLM del Space | ✅ | ✅ | ❌ | ❌ |
| Ver audit de actividad | ✅ | ✅ | ❌ | ❌ |
| Crear chats propios | ✅ | ✅ | ✅ | ✅ |
| Buscar y consultar docs | ✅ | ✅ | ✅ | ✅ |

> **Consejo:** empieza con todos como Viewer. Escala a Editor solo cuando alguien necesite subir documentos regularmente.

---

## 2.4 Crear un Search Space

### Desde la UI

1. Ir a `http://localhost:3000` → Login
2. Dashboard → botón **"New Search Space"**
3. Rellenar: nombre, descripción
4. Seleccionar LLM para este Space (puede ser diferente al global)
5. Guardar — el Space queda activo inmediatamente

### Desde la API

```bash
# Primero obtén tu token de autenticación
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@empresa.com","password":"tupassword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token: $TOKEN"

# Crear un Search Space
curl -X POST http://localhost:8000/api/v1/search-spaces \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Plataforma Core",
    "description": "Knowledge base del monorepo y arquitectura"
  }'
```

---

## 2.5 Gestionar invitaciones

### Crear un link de invitación (UI)

1. Search Space → **"Manage Members"** → **"Create Invite Link"**
2. Configurar:
   - **Rol**: Viewer / Editor / Admin
   - **Límite de usos**: cuántas personas pueden usar el link
   - **Fecha de expiración**: opcional
3. Copiar link y enviarlo por Slack/email

### Crear invitación vía API

```bash
# Crear invitación con límite de 10 usos, caduca en 30 días
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/invitations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "viewer",
    "max_uses": 10,
    "expires_at": "2026-12-31T00:00:00Z"
  }'

# Listar miembros actuales de un Space
curl "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/members" \
  -H "Authorization: Bearer $TOKEN"

# Cambiar el rol de un miembro
curl -X PATCH "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/members/{USER_ID}" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role": "editor"}'

# Eliminar un miembro
curl -X DELETE "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/members/{USER_ID}" \
  -H "Authorization: Bearer $TOKEN"
```

---

## 2.6 Configurar LLM por Search Space

Cada Search Space puede usar un modelo diferente. Útil para:
- Spaces con documentos técnicos → modelo más potente (qwen2.5:14b)
- Spaces de uso general → modelo más rápido (llama3.2)
- Spaces sensibles → modelo 100% local sin API externa

```bash
# Configurar LLM para un Space específico
curl -X POST "http://localhost:8000/api/v1/search-spaces/{SPACE_ID}/llm-config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "ollama",
    "model": "qwen2.5:14b",
    "base_url": "http://host.docker.internal:11434"
  }'
```

---

## 2.7 Habilitar chat colaborativo

Para que el equipo pueda chatear en tiempo real en el mismo hilo:

1. Abrir un chat dentro del Search Space
2. Botón **"Make chat shared"** (icono de compartir)
3. El chat queda visible para todos los miembros del Space
4. Los mensajes aparecen en tiempo real (Electric SQL sync)

---

## Checklist del módulo

- [ ] Definida la estructura de Search Spaces para tu empresa
- [ ] Creado al menos un Space de prueba
- [ ] Creado link de invitación para el equipo
- [ ] Roles asignados correctamente (no todo el mundo es Admin)
- [ ] LLM configurado por Space según necesidad

---

**Anterior:** [M1 — Arquitectura y Diagnóstico](./M1-arquitectura-diagnostico.md)  
**Siguiente:** [M3 — Conectores](./M3-conectores.md)
