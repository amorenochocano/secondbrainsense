# F6 — UI Integrada: Next.js + Brain Views
**Duración:** 2 semanas  
**Equipo:** Frontend Senior (1) + Frontend Mid (1) + UX (0.5)  
**Dependencias:** F5 completada  
**Entregable:** UI unificada en Next.js con todas las vistas de Second Brain integradas

---

## Objetivo

Integrar las capacidades de Second Brain en la UI de SurfSense (Next.js). No es portar Streamlit a Next.js — es construir las vistas Brain directamente en Next.js usando los nuevos endpoints de la API.

**Vistas a añadir:**
1. Brain Ingest — ingesta con log por fases y selector de modelo
2. Brain Chat — chat con badge de nivel L1/L2/L0 y forzar L0
3. Brain Wiki — biblioteca de pasaportes con cards enriquecidas
4. Brain Graph — grafo interactivo de relaciones
5. Brain Passport — vista detalle de un pasaporte .md
6. Admin Brain — gestión de colecciones Qdrant

---

## F6.1 — Routing y layout (Semana 1, Día 1)

```
surfsense_web/app/
├── (app)/
│   ├── brain/
│   │   ├── layout.tsx          ← sidebar específico Brain
│   │   ├── page.tsx            ← redirect a /brain/chat
│   │   ├── chat/
│   │   │   └── page.tsx        ← Brain Chat (vista principal)
│   │   ├── ingest/
│   │   │   └── page.tsx        ← Brain Ingest
│   │   ├── wiki/
│   │   │   └── page.tsx        ← Brain Wiki (biblioteca)
│   │   ├── graph/
│   │   │   └── page.tsx        ← Brain Graph
│   │   └── passport/
│   │       └── [source]/
│   │           └── page.tsx    ← Pasaporte detalle
│   └── admin/
│       └── brain/
│           └── page.tsx        ← Admin Qdrant collections
```

**Añadir al sidebar de SurfSense:**

```tsx
// surfsense_web/components/sidebar.tsx — añadir sección Brain

const BRAIN_NAV = [
  { href: "/brain/chat",   icon: MessageCircle, label: "Brain Chat" },
  { href: "/brain/ingest", icon: Upload,        label: "Ingestar" },
  { href: "/brain/wiki",   icon: BookOpen,      label: "Biblioteca" },
  { href: "/brain/graph",  icon: Network,       label: "Grafo" },
];
```

---

## F6.2 — Brain Ingest (Semana 1, Día 2-3)

Vista equivalente a `brain_ingest.py` de Second Brain pero en Next.js.

```tsx
// app/(app)/brain/ingest/page.tsx
"use client";

import { useState, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { IngestLog } from "@/components/brain/ingest-log";

const SYNTHESIS_MODELS = [
  { value: "auto",              label: "🤖 Auto (recomendado)" },
  { value: "qwen2.5-coder:7b", label: "qwen2.5-coder:7b — Código complejo" },
  { value: "qwen2.5-coder:3b", label: "qwen2.5-coder:3b — Docs ligeros" },
  { value: "deepseek-r1:1.5b", label: "deepseek-r1:1.5b — Razonamiento" },
];

export default function BrainIngestPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [model, setModel] = useState("auto");
  const [visionMode, setVisionMode] = useState<"off"|"auto"|"force">("off");
  const [logs, setLogs] = useState<IngestLogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const onDrop = useCallback((accepted: File[]) => {
    setFiles(prev => [...prev, ...accepted]);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "text/*": [".py", ".sql", ".md", ".txt", ".csv", ".json", ".xml"],
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.*": [".docx", ".pptx", ".xlsx"],
    }
  });

  const handleIngest = async () => {
    setLoading(true);
    setLogs([]);

    for (const file of files) {
      addLog({ phase: "inicio", status: "running", message: `Procesando ${file.name}...` });

      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("synthesis_model", model);
        formData.append("vision_mode", visionMode);
        formData.append("search_space_id", currentSearchSpaceId);

        const response = await fetch("/api/v1/documents/fileupload", {
          method: "POST",
          body: formData,
        });

        if (response.ok) {
          const result = await response.json();
          addLog({ phase: "completado", status: "success",
                   message: `✅ ${file.name} → ${result.source}`,
                   detail: `Pasaporte generado: ${result.passport_path}` });
        }
      } catch (err) {
        addLog({ phase: "error", status: "error",
                 message: `❌ Error en ${file.name}: ${err}` });
      }
    }
    setLoading(false);
  };

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-2xl font-bold mb-6">Ingestar Documentos</h1>

      {/* Dropzone */}
      <div {...getRootProps()} className="border-2 border-dashed rounded-lg p-8 text-center
           cursor-pointer hover:border-primary transition-colors mb-4">
        <input {...getInputProps()} />
        {isDragActive
          ? <p>Suelta los ficheros aquí...</p>
          : <p>Arrastra ficheros o haz click · .py .sql .pdf .md .docx .xlsx .pptx</p>
        }
      </div>

      {/* Opciones */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label className="text-sm font-medium">Modelo de síntesis</label>
          <Select value={model} onValueChange={setModel} options={SYNTHESIS_MODELS} />
        </div>
        <div>
          <label className="text-sm font-medium">Vision-assist (PDF/PPTX)</label>
          <Select
            value={visionMode}
            onValueChange={(v) => setVisionMode(v as any)}
            options={[
              { value: "off",   label: "Off — Solo texto" },
              { value: "auto",  label: "Auto — Si hay imágenes" },
              { value: "force", label: "Force — Siempre" },
            ]}
          />
        </div>
      </div>

      {/* Lista de ficheros */}
      {files.length > 0 && (
        <div className="mb-4">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-2 py-1 text-sm">
              <span>{f.name}</span>
              <span className="text-muted-foreground">({(f.size/1024).toFixed(1)} KB)</span>
              <button onClick={() => setFiles(fs => fs.filter((_, j) => j !== i))}
                      className="ml-auto text-destructive">✕</button>
            </div>
          ))}
        </div>
      )}

      <Button onClick={handleIngest} disabled={files.length === 0 || loading}>
        {loading ? "Procesando..." : `Ingestar ${files.length} fichero(s)`}
      </Button>

      {/* Log por fases */}
      {logs.length > 0 && <IngestLog entries={logs} className="mt-6" />}
    </div>
  );
}
```

---

## F6.3 — Brain Chat con badges de nivel (Semana 1, Día 3-5)

```tsx
// app/(app)/brain/chat/page.tsx
"use client";

import { useState } from "react";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { SourceCitation } from "@/components/brain/source-citation";

type LevelBadge = "🧠 Brain" | "📚 Knowledge" | "🤖 LLM";

const LEVEL_COLORS: Record<LevelBadge, string> = {
  "🧠 Brain":      "bg-purple-100 text-purple-800",
  "📚 Knowledge":  "bg-blue-100 text-blue-800",
  "🤖 LLM":       "bg-gray-100 text-gray-700",
};

export default function BrainChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [forceL0, setForceL0] = useState(false);
  const [loading, setLoading] = useState(false);

  const sendMessage = async () => {
    if (!input.trim()) return;
    const question = input;
    setInput("");
    setLoading(true);

    const userMsg: ChatMessage = { role: "user", content: question };
    setMessages(prev => [...prev, userMsg]);

    try {
      const res = await fetch("/api/brain/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          search_space_id: currentSearchSpaceId,
          chat_history: messages.slice(-6),
          force_l0: forceL0,
        }),
      });

      const data = await res.json();

      const assistantMsg: ChatMessage = {
        role: "assistant",
        content: data.answer,
        level: data.level,
        level_label: data.level_label as LevelBadge,
        sources: data.sources,
      };
      setMessages(prev => [...prev, assistantMsg]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-3 p-3 border-b">
        <span className="text-sm font-medium">Brain Chat</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Forzar LLM libre</span>
          <Switch checked={forceL0} onCheckedChange={setForceL0} />
        </div>
      </div>

      {/* Mensajes */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-lg p-3 ${
              msg.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
            }`}>
              {/* Badge de nivel para respuestas del asistente */}
              {msg.role === "assistant" && msg.level_label && (
                <div className="flex items-center gap-2 mb-2">
                  <Badge className={LEVEL_COLORS[msg.level_label as LevelBadge]}>
                    {msg.level_label}
                  </Badge>
                </div>
              )}

              <p className="text-sm whitespace-pre-wrap">{msg.content}</p>

              {/* Fuentes citadas */}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 pt-2 border-t border-border/50">
                  <p className="text-xs text-muted-foreground mb-1">Fuentes:</p>
                  <div className="flex flex-wrap gap-1">
                    {msg.sources.map((src, j) => (
                      <SourceCitation key={j} source={src} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && <div className="text-center text-muted-foreground text-sm">Consultando...</div>}
      </div>

      {/* Input */}
      <div className="p-4 border-t">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage()}
            placeholder="Pregunta a tu knowledge base..."
            className="flex-1 rounded-md border px-3 py-2 text-sm"
          />
          <button onClick={sendMessage} disabled={loading}
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm">
            Enviar
          </button>
        </div>
      </div>
    </div>
  );
}
```

---

## F6.4 — Brain Wiki: biblioteca de pasaportes (Semana 2, Día 1-2)

```tsx
// app/(app)/brain/wiki/page.tsx

// Cards enriquecidas con: tipo, subdominio, confianza, scope, idioma, PII flag

interface PassportCard {
  source: string;
  title: string;
  doc_type: string;
  domain: string;
  avg_quality_score: number;
  embedding_scope: string[];
  has_pii: boolean;
  languages: string[];
  created_at: string;
  connector?: string;
}

// Card component
function PassportCard({ card }: { card: PassportCard }) {
  return (
    <div className="border rounded-lg p-4 hover:shadow-md transition-shadow cursor-pointer"
         onClick={() => router.push(`/brain/passport/${card.source}`)}>
      <div className="flex items-start justify-between mb-2">
        <div>
          <h3 className="font-semibold text-sm truncate">{card.title}</h3>
          <p className="text-xs text-muted-foreground">{card.source}</p>
        </div>
        <DocTypeBadge type={card.doc_type} />
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        {card.embedding_scope.map(scope => (
          <Badge key={scope} variant="outline" className="text-xs">{scope}</Badge>
        ))}
        {card.has_pii && (
          <Badge variant="destructive" className="text-xs">⚠️ PII</Badge>
        )}
        {card.languages.map(lang => (
          <Badge key={lang} variant="secondary" className="text-xs">{lang}</Badge>
        ))}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <QualityBar score={card.avg_quality_score} />
        <span className="text-xs text-muted-foreground">
          {(card.avg_quality_score * 100).toFixed(0)}% calidad
        </span>
      </div>
    </div>
  );
}
```

---

## F6.5 — Brain Graph: grafo de relaciones (Semana 2, Día 3-4)

Usa `react-force-graph` (ya disponible en el ecosistema Next.js) para renderizar el grafo.

```tsx
// app/(app)/brain/graph/page.tsx
"use client";

import dynamic from "next/dynamic";
import { useState, useEffect } from "react";

// react-force-graph no funciona en SSR
const ForceGraph2D = dynamic(() => import("react-force-graph").then(m => m.ForceGraph2D), {
  ssr: false
});

export default function BrainGraphPage() {
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    fetch("/api/brain/graph")
      .then(r => r.json())
      .then(data => setGraphData(data));
  }, []);

  return (
    <div className="flex h-full">
      {/* Grafo */}
      <div className="flex-1">
        <ForceGraph2D
          graphData={graphData}
          nodeLabel="title"
          nodeColor={node => NODE_COLORS[node.doc_type] || "#94a3b8"}
          linkLabel={link => link.relation_type}
          onNodeClick={node => setSelected(node)}
          nodeRelSize={6}
        />
      </div>

      {/* Panel detalle */}
      {selected && (
        <div className="w-80 border-l p-4 overflow-y-auto">
          <h3 className="font-semibold mb-2">{selected.title}</h3>
          <Badge>{selected.doc_type}</Badge>
          <p className="text-sm text-muted-foreground mt-2">{selected.summary}</p>
          <div className="mt-4 flex gap-2">
            <Button size="sm" onClick={() => router.push(`/brain/passport/${selected.source}`)}>
              Ver pasaporte
            </Button>
            <Button size="sm" variant="outline"
                    onClick={() => router.push(`/brain/chat?q=cuéntame sobre ${selected.title}`)}>
              Preguntar al Brain
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
```

---

## F6.6 — Admin Brain: gestión de colecciones (Semana 2, Día 5)

```tsx
// app/(app)/admin/brain/page.tsx

// Vista de administración de las tres colecciones Qdrant
// Muestra: nombre, vectores, dimensiones, modelo de embedding
// Acción: Recrear colección (con confirmación — borra todos los vectores)

export default function AdminBrainPage() {
  const [collections, setCollections] = useState({});

  useEffect(() => {
    fetch("/api/admin/qdrant/collections")
      .then(r => r.json())
      .then(setCollections);
  }, []);

  const recreateCollection = async (name: string) => {
    if (!confirm(`¿Recrear ${name}? Se borrarán TODOS los vectores.`)) return;
    await fetch(`/api/admin/qdrant/collection/${name}/recreate`, { method: "POST" });
    // Recargar stats
  };

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-6">Administración Brain · Colecciones Qdrant</h1>
      <div className="grid grid-cols-3 gap-4">
        {Object.entries(collections).map(([name, stats]) => (
          <CollectionCard
            key={name}
            name={name}
            stats={stats}
            onRecreate={() => recreateCollection(name)}
          />
        ))}
      </div>
    </div>
  );
}
```

---

## Checklist F6

- [ ] Routing `/brain/*` añadido al layout de SurfSense
- [ ] Sidebar actualizado con sección Brain
- [ ] Brain Ingest: dropzone, selector de modelo, log por fases
- [ ] Brain Chat: badges L1/L2/L0, force L0, citas de fuentes
- [ ] Brain Wiki: cards con quality score, PII flag, scope, idioma
- [ ] Brain Passport: vista detalle del .md con render Markdown
- [ ] Brain Graph: grafo D3 con panel de detalle y acción "Preguntar"
- [ ] Admin Brain: stats de colecciones Qdrant + botón recrear
- [ ] Design system: usar componentes shadcn/ui de SurfSense (sin nueva lib)
- [ ] Responsive: funcional en tablet (uso en reuniones)

---

**Anterior:** [F5 — Conectores → Pipeline Brain](./F5-conectores-pipeline.md)  
**Siguiente:** [F7 — Hardening y Producción](./F7-hardening.md)
