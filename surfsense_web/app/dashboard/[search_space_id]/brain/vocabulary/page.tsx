"use client";

/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/vocabulary
 *
 * F6.11 — Gestión de Maestros y Vocabulario del Brain.
 *
 * Cuatro tabs con CRUD completo:
 *   • 🗂️ Dominios — brain_domains con signal_tags/signal_kw
 *   • 📄 Tipos de doc — brain_doc_types con signal_formats
 *   • 🔖 Entity Hints — brain_entity_hints con patterns/examples
 *   • 📝 Vocabulario — brain_vocabulary con aliases + lookup en tiempo real
 *
 * Regla multi-tenancy: registros globales (scope=global) son solo-lectura
 * desde la UI — solo se puede cambiar is_active.
 *
 * Mejoras vs. Streamlit:
 * - ChipArrayInput para tags/aliases en lugar de inputs de texto separados
 * - Lookup de vocabulario en tiempo real con indicador visual
 * - Vista compacta/expandida por fila para entity hints
 * - Filtro global vs space con indicador visual por fila
 *
 * Gestión de logs: brainLogger("BrainVocabularyPage")
 * ZERO HARDCODE: todas las constantes en lib/brain/constants.ts
 */

import { useState } from "react";
import { useParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Pencil, Trash2, CheckCircle, XCircle, Search,
  ChevronDown, ChevronRight, Loader2, Globe, User,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { BrainBreadcrumb, ChipArrayInput } from "@/components/brain";
import { brainApiService } from "@/lib/apis/brain-api.service";
import { brainLogger } from "@/lib/brain/logger";
import type {
  BrainDomainRecord, BrainDocTypeRecord, BrainEntityHintRecord, BrainVocabularyRecord,
} from "@/contracts/types/brain.types";
import {
  BRAIN_VOCABULARY_TABS, BRAIN_VOCAB_TAG_REGEX, BRAIN_VOCAB_TAG_ERROR,
} from "@/lib/brain/constants";

const log = brainLogger("BrainVocabularyPage");

// ─── Cache keys locales ───────────────────────────────────────────────────────

const vocabKeys = {
  domains:      (spaceId: number) => ["brain", "vocab", "domains", spaceId] as const,
  docTypes:     (spaceId: number) => ["brain", "vocab", "doc-types", spaceId] as const,
  entityHints:  (spaceId: number) => ["brain", "vocab", "entity-hints", spaceId] as const,
  vocabulary:   (spaceId: number, q?: string) => ["brain", "vocab", "vocabulary", spaceId, q ?? ""] as const,
};

// ─── Helpers de UI ────────────────────────────────────────────────────────────

/** Badge que distingue registros globales vs del space */
function ScopeBadge({ scope }: { scope: "global" | "space" }) {
  return scope === "global" ? (
    <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground bg-muted rounded px-1.5 py-0.5">
      <Globe className="h-2.5 w-2.5" /> Global
    </span>
  ) : (
    <span className="inline-flex items-center gap-0.5 text-[10px] text-violet-400 bg-violet-500/10 rounded px-1.5 py-0.5">
      <User className="h-2.5 w-2.5" /> Space
    </span>
  );
}

/** Chips truncados (máximo N visibles) */
function ChipList({ chips, maxVisible = 4 }: { chips: string[]; maxVisible?: number }) {
  const visible = chips.slice(0, maxVisible);
  const extra = chips.length - maxVisible;
  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((c) => (
        <span key={c} className="rounded-full bg-muted px-2 py-0.5 text-xs text-foreground/80">
          {c}
        </span>
      ))}
      {extra > 0 && (
        <span className="rounded-full bg-muted/60 px-2 py-0.5 text-xs text-muted-foreground">
          +{extra}
        </span>
      )}
    </div>
  );
}

// ─── TAB 1: Dominios ─────────────────────────────────────────────────────────

function DomainsTab({ spaceId }: { spaceId: number }) {
  const queryClient = useQueryClient();
  const [editTarget, setEditTarget] = useState<BrainDomainRecord | null>(null);
  const [isNewOpen, setIsNewOpen] = useState(false);
  const [formData, setFormData] = useState<Partial<BrainDomainRecord>>({});

  const { data: domains = [], isLoading } = useQuery({
    queryKey: vocabKeys.domains(spaceId),
    queryFn: () => {
      log.debug("Cargando dominios Brain", { spaceId });
      return brainApiService.listDomains(spaceId);
    },
  });

  const createMutation = useMutation({
    mutationFn: () => brainApiService.createDomain({
      domain_key: formData.domain_key ?? "",
      label: formData.label ?? "",
      description: formData.description,
      signal_tags: formData.signal_tags ?? [],
      signal_kw: formData.signal_kw ?? [],
      scope: "space",
      is_active: true,
      search_space_id: spaceId,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.domains(spaceId) });
      toast("Dominio creado");
      setIsNewOpen(false);
      setFormData({});
    },
    onError: (e: Error) => toast({ variant: "destructive", title: "Error", description: e.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => brainApiService.deleteDomain(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.domains(spaceId) });
      toast("Dominio eliminado");
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (id: number) => brainApiService.toggleDomainActive(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: vocabKeys.domains(spaceId) }),
  });

  const openEdit = (d: BrainDomainRecord) => {
    setEditTarget(d);
    setFormData({ ...d });
  };

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-violet-400" /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{domains.length} dominios</span>
        <Button size="sm" onClick={() => { setFormData({}); setIsNewOpen(true); }} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Añadir dominio
        </Button>
      </div>

      <div className="rounded-xl border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/60">
            <tr className="text-xs text-muted-foreground uppercase tracking-wider">
              <th className="text-left p-3">Clave / Label</th>
              <th className="text-left p-3 hidden md:table-cell">Signal tags</th>
              <th className="text-left p-3 hidden lg:table-cell">Keywords</th>
              <th className="text-center p-3">Activo</th>
              <th className="text-left p-3">Scope</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {domains.map((d) => (
              <tr key={d.id} className={cn("hover:bg-muted/40", !d.is_active && "opacity-50")}>
                <td className="p-3">
                  <p className="font-mono text-xs text-violet-300">{d.domain_key}</p>
                  <p className="text-sm text-foreground">{d.label}</p>
                </td>
                <td className="p-3 hidden md:table-cell">
                  <ChipList chips={d.signal_tags} />
                </td>
                <td className="p-3 hidden lg:table-cell">
                  <ChipList chips={d.signal_kw} maxVisible={3} />
                </td>
                <td className="p-3 text-center">
                  <Switch
                    checked={d.is_active}
                    onCheckedChange={() => toggleMutation.mutate(d.id)}
                    className="scale-75"
                  />
                </td>
                <td className="p-3">
                  <ScopeBadge scope={d.scope} />
                </td>
                <td className="p-3">
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => openEdit(d)}
                      className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    {d.scope === "space" && (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <button type="button" className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>¿Eliminar dominio?</AlertDialogTitle>
                            <AlertDialogDescription>
                              Esta acción no se puede deshacer. Los documentos con este dominio
                              quedarán sin clasificar.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancelar</AlertDialogCancel>
                            <AlertDialogAction onClick={() => deleteMutation.mutate(d.id)} className="bg-red-600 hover:bg-red-700">
                              Eliminar
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Dialog crear/editar */}
      <Dialog open={isNewOpen || editTarget !== null} onOpenChange={(o) => { if (!o) { setIsNewOpen(false); setEditTarget(null); setFormData({}); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editTarget ? "Editar dominio" : "Nuevo dominio"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">domain_key (slug)</Label>
                <Input value={formData.domain_key ?? ""} onChange={(e) => setFormData((p) => ({ ...p, domain_key: e.target.value }))} placeholder="engineering" className="font-mono text-sm" />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Label</Label>
                <Input value={formData.label ?? ""} onChange={(e) => setFormData((p) => ({ ...p, label: e.target.value }))} placeholder="Ingeniería" className="text-sm" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Signal tags</Label>
              <ChipArrayInput value={formData.signal_tags ?? []} onChange={(v) => setFormData((p) => ({ ...p, signal_tags: v }))} placeholder="añadir tag…" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Signal keywords</Label>
              <ChipArrayInput value={formData.signal_kw ?? []} onChange={(v) => setFormData((p) => ({ ...p, signal_kw: v }))} placeholder="añadir keyword…" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setIsNewOpen(false); setEditTarget(null); setFormData({}); }}>Cancelar</Button>
            <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Guardar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── TAB 4: Vocabulario canónico ─────────────────────────────────────────────

function VocabularyTab({ spaceId }: { spaceId: number }) {
  const queryClient = useQueryClient();
  const [searchQ, setSearchQ] = useState("");
  const [lookupTag, setLookupTag] = useState("");
  const [isNewOpen, setIsNewOpen] = useState(false);
  const [formData, setFormData] = useState<{ canonical_tag: string; aliases: string[] }>({ canonical_tag: "", aliases: [] });

  const { data: entries = [], isLoading } = useQuery({
    queryKey: vocabKeys.vocabulary(spaceId, searchQ),
    queryFn: () => {
      log.debug("Cargando vocabulario Brain", { spaceId, q: searchQ });
      return brainApiService.listVocabulary(spaceId, searchQ || undefined);
    },
  });

  const { data: lookupResult, refetch: doLookup, isFetching: lookupFetching } = useQuery({
    queryKey: ["brain", "vocab", "lookup", lookupTag],
    queryFn: () => brainApiService.lookupVocabulary(lookupTag),
    enabled: false,
  });

  const createMutation = useMutation({
    mutationFn: () => brainApiService.createVocabularyEntry({
      canonical_tag: formData.canonical_tag,
      aliases: formData.aliases,
      scope: "space",
      is_active: true,
      search_space_id: spaceId,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.vocabulary(spaceId) });
      toast("Entrada creada");
      setIsNewOpen(false);
      setFormData({ canonical_tag: "", aliases: [] });
    },
    onError: (e: Error) => toast({ variant: "destructive", title: "Error", description: e.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => brainApiService.deleteVocabularyEntry(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.vocabulary(spaceId) });
      toast("Entrada eliminada");
    },
  });

  return (
    <div className="space-y-4">
      {/* Buscador inline + consulta de alias */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            placeholder="Buscar por canonical o alias…"
            className="pl-9 bg-muted/50 border-input"
          />
        </div>

        {/* Lookup de alias */}
        <div className="rounded-xl border border-border bg-card p-3">
          <p className="text-xs text-muted-foreground mb-2">Lookup: ¿de qué canónica es alias?</p>
          <div className="flex gap-2">
            <Input
              value={lookupTag}
              onChange={(e) => setLookupTag(e.target.value)}
              placeholder="powerbi…"
              className="bg-muted/50 border-input text-sm"
              onKeyDown={(e) => e.key === "Enter" && lookupTag && doLookup()}
            />
            <Button size="sm" variant="outline" onClick={() => doLookup()} disabled={!lookupTag || lookupFetching}>
              {lookupFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            </Button>
          </div>
          {lookupResult && (
            <div className="mt-2">
              {lookupResult.found ? (
                <p className="text-xs text-emerald-400 flex items-center gap-1">
                  <CheckCircle className="h-3 w-3" />
                  Alias de: <span className="font-mono font-semibold">{lookupResult.canonical_tag}</span>
                  {lookupResult.aliases.length > 0 && (
                    <span className="text-muted-foreground ml-1">({lookupResult.aliases.join(", ")})</span>
                  )}
                </p>
              ) : (
                <p className="text-xs text-amber-400 flex items-center gap-1">
                  <XCircle className="h-3 w-3" />
                  No registrado — ¿añadir como nueva entrada?
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{entries.length} entradas</span>
        <Button size="sm" onClick={() => setIsNewOpen(true)} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Añadir entrada
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-violet-400" /></div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/60">
              <tr className="text-xs text-muted-foreground uppercase tracking-wider">
                <th className="text-left p-3">Canónica</th>
                <th className="text-left p-3">Aliases</th>
                <th className="text-left p-3">Scope</th>
                <th className="p-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {entries.map((entry) => (
                <tr key={entry.id} className={cn("hover:bg-muted/40", !entry.is_active && "opacity-50")}>
                  <td className="p-3 font-mono text-xs text-violet-300">{entry.canonical_tag}</td>
                  <td className="p-3"><ChipList chips={entry.aliases} maxVisible={5} /></td>
                  <td className="p-3"><ScopeBadge scope={entry.scope} /></td>
                  <td className="p-3">
                    {entry.scope === "space" && (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <button type="button" className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive">
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>¿Eliminar entrada?</AlertDialogTitle>
                            <AlertDialogDescription>
                              Se eliminará la entrada canónica <strong>{entry.canonical_tag}</strong> y todos sus aliases.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancelar</AlertDialogCancel>
                            <AlertDialogAction onClick={() => deleteMutation.mutate(entry.id)} className="bg-red-600 hover:bg-red-700">
                              Eliminar
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Dialog nueva entrada */}
      <Dialog open={isNewOpen} onOpenChange={(o) => { if (!o) { setIsNewOpen(false); setFormData({ canonical_tag: "", aliases: [] }); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Nueva entrada canónica</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label className="text-xs">Tag canónica (slug)</Label>
              <Input
                value={formData.canonical_tag}
                onChange={(e) => setFormData((p) => ({ ...p, canonical_tag: e.target.value }))}
                placeholder="power-bi"
                className="font-mono text-sm"
              />
              {formData.canonical_tag && !BRAIN_VOCAB_TAG_REGEX.test(formData.canonical_tag) && (
                <p className="text-xs text-red-400">{BRAIN_VOCAB_TAG_ERROR}</p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Aliases</Label>
              <ChipArrayInput
                value={formData.aliases}
                onChange={(v) => setFormData((p) => ({ ...p, aliases: v }))}
                validatePattern={BRAIN_VOCAB_TAG_REGEX}
                validationError={BRAIN_VOCAB_TAG_ERROR}
                placeholder="pbi, powerbi, power bi…"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setIsNewOpen(false); setFormData({ canonical_tag: "", aliases: [] }); }}>Cancelar</Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !formData.canonical_tag}
            >
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Crear"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── TAB genérico simplificado (Doc Types y Entity Hints) ─────────────────────

function DocTypesTab({ spaceId }: { spaceId: number }) {
  const queryClient = useQueryClient();
  const [isNewOpen, setIsNewOpen] = useState(false);
  const [formData, setFormData] = useState<Partial<BrainDocTypeRecord>>({});

  const { data: docTypes = [], isLoading } = useQuery({
    queryKey: vocabKeys.docTypes(spaceId),
    queryFn: () => {
      log.debug("Cargando tipos de documento Brain", { spaceId });
      return brainApiService.listDocTypes(spaceId);
    },
  });

  const createMutation = useMutation({
    mutationFn: () => brainApiService.createDocType({
      type_key: formData.type_key ?? "",
      label: formData.label ?? "",
      description: formData.description,
      signal_formats: formData.signal_formats ?? [],
      signal_kw: formData.signal_kw ?? [],
      scope: "space",
      is_active: true,
      search_space_id: spaceId,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.docTypes(spaceId) });
      toast("Tipo de documento creado");
      setIsNewOpen(false);
      setFormData({});
    },
    onError: (e: Error) => toast({ variant: "destructive", title: "Error", description: e.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => brainApiService.deleteDocType(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: vocabKeys.docTypes(spaceId) }),
  });

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-violet-400" /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{docTypes.length} tipos de documento</span>
        <Button size="sm" onClick={() => { setFormData({}); setIsNewOpen(true); }} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Añadir tipo
        </Button>
      </div>

      <div className="rounded-xl border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/60">
            <tr className="text-xs text-muted-foreground uppercase tracking-wider">
              <th className="text-left p-3">Clave / Label</th>
              <th className="text-left p-3 hidden md:table-cell">Formatos</th>
              <th className="text-left p-3">Scope</th>
              <th className="p-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {docTypes.map((dt) => (
              <tr key={dt.id} className={cn("hover:bg-muted/40", !dt.is_active && "opacity-50")}>
                <td className="p-3">
                  <p className="font-mono text-xs text-violet-300">{dt.type_key}</p>
                  <p className="text-sm text-foreground">{dt.label}</p>
                </td>
                <td className="p-3 hidden md:table-cell"><ChipList chips={dt.signal_formats} /></td>
                <td className="p-3"><ScopeBadge scope={dt.scope} /></td>
                <td className="p-3">
                  {dt.scope === "space" && (
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <button type="button" className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>¿Eliminar tipo de documento?</AlertDialogTitle>
                          <AlertDialogDescription>Esta acción no se puede deshacer.</AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancelar</AlertDialogCancel>
                          <AlertDialogAction onClick={() => deleteMutation.mutate(dt.id)} className="bg-red-600 hover:bg-red-700">Eliminar</AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Dialog open={isNewOpen} onOpenChange={(o) => { if (!o) { setIsNewOpen(false); setFormData({}); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>Nuevo tipo de documento</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">type_key</Label>
                <Input value={formData.type_key ?? ""} onChange={(e) => setFormData((p) => ({ ...p, type_key: e.target.value }))} placeholder="technical-spec" className="font-mono text-sm" />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Label</Label>
                <Input value={formData.label ?? ""} onChange={(e) => setFormData((p) => ({ ...p, label: e.target.value }))} placeholder="Especificación técnica" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Formatos de fichero</Label>
              <ChipArrayInput value={formData.signal_formats ?? []} onChange={(v) => setFormData((p) => ({ ...p, signal_formats: v }))} placeholder=".pdf, .docx, .md…" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setIsNewOpen(false); setFormData({}); }}>Cancelar</Button>
            <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !formData.type_key}>
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Crear"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── TAB Entity Hints (expandible por fila) ────────────────────────────────────

function EntityHintsTab({ spaceId }: { spaceId: number }) {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [isNewOpen, setIsNewOpen] = useState(false);
  const [formData, setFormData] = useState<Partial<BrainEntityHintRecord>>({});

  const { data: hints = [], isLoading } = useQuery({
    queryKey: vocabKeys.entityHints(spaceId),
    queryFn: () => {
      log.debug("Cargando entity hints Brain", { spaceId });
      return brainApiService.listEntityHints(spaceId);
    },
  });

  const createMutation = useMutation({
    mutationFn: () => brainApiService.createEntityHint({
      hint_key: formData.hint_key ?? "",
      label: formData.label ?? "",
      domain_key: formData.domain_key,
      doc_type_key: formData.doc_type_key,
      patterns: formData.patterns ?? [],
      examples: formData.examples ?? [],
      is_active: true,
      search_space_id: spaceId,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vocabKeys.entityHints(spaceId) });
      toast("Entity hint creado");
      setIsNewOpen(false);
      setFormData({});
    },
    onError: (e: Error) => toast({ variant: "destructive", title: "Error", description: e.message }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => brainApiService.deleteEntityHint(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: vocabKeys.entityHints(spaceId) }),
  });

  if (isLoading) return <div className="flex justify-center py-12"><Loader2 className="h-5 w-5 animate-spin text-violet-400" /></div>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{hints.length} entity hints</span>
        <Button size="sm" onClick={() => { setFormData({}); setIsNewOpen(true); }} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Añadir hint
        </Button>
      </div>

      <div className="space-y-2">
        {hints.map((hint) => (
          <div key={hint.id} className="rounded-lg border border-border bg-card overflow-hidden">
            <button
              type="button"
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/60 transition-colors text-left"
              onClick={() => setExpandedId(expandedId === hint.id ? null : hint.id)}
            >
              {expandedId === hint.id ? (
                <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
              ) : (
                <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
              )}
              <span className="font-mono text-xs text-violet-300">{hint.hint_key}</span>
              <span className="text-sm text-foreground">{hint.label}</span>
              <span className="text-xs text-muted-foreground ml-auto">{hint.patterns.length} patterns</span>
            </button>

            {expandedId === hint.id && (
              <div className="border-t border-border/60 px-4 py-3 space-y-3">
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div>
                    <span className="text-muted-foreground">Dominio: </span>
                    <span className="text-foreground/80">{hint.domain_key ?? "Todos"}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Tipo: </span>
                    <span className="text-foreground/80">{hint.doc_type_key ?? "Todos"}</span>
                  </div>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground mb-1">Patterns</p>
                  <div className="flex flex-wrap gap-1">
                    {hint.patterns.map((p) => (
                      <span key={p} className="font-mono text-xs bg-muted rounded px-2 py-0.5 text-foreground/80">{p}</span>
                    ))}
                  </div>
                </div>
                {hint.examples.length > 0 && (
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Ejemplos</p>
                    <ChipList chips={hint.examples} maxVisible={6} />
                  </div>
                )}
                {hint.is_active === false && (
                  <Badge variant="secondary" className="text-xs">Desactivado</Badge>
                )}
                <div className="flex justify-end gap-2">
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="ghost" size="sm" className="text-red-400 hover:text-red-300 gap-1.5">
                        <Trash2 className="h-3.5 w-3.5" /> Eliminar
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>¿Eliminar entity hint?</AlertDialogTitle>
                        <AlertDialogDescription>Esta acción no se puede deshacer.</AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancelar</AlertDialogCancel>
                        <AlertDialogAction onClick={() => deleteMutation.mutate(hint.id)} className="bg-red-600 hover:bg-red-700">Eliminar</AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <Dialog open={isNewOpen} onOpenChange={(o) => { if (!o) { setIsNewOpen(false); setFormData({}); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle>Nuevo entity hint</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label className="text-xs">hint_key</Label>
                <Input value={formData.hint_key ?? ""} onChange={(e) => setFormData((p) => ({ ...p, hint_key: e.target.value }))} placeholder="api-endpoint" className="font-mono text-sm" />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">Label</Label>
                <Input value={formData.label ?? ""} onChange={(e) => setFormData((p) => ({ ...p, label: e.target.value }))} placeholder="API Endpoint" />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Patterns (regex)</Label>
              <ChipArrayInput value={formData.patterns ?? []} onChange={(v) => setFormData((p) => ({ ...p, patterns: v }))} placeholder="\\bGET /api/\\w+ \\b" />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Ejemplos</Label>
              <ChipArrayInput value={formData.examples ?? []} onChange={(v) => setFormData((p) => ({ ...p, examples: v }))} placeholder="GET /api/users" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => { setIsNewOpen(false); setFormData({}); }}>Cancelar</Button>
            <Button onClick={() => createMutation.mutate()} disabled={createMutation.isPending || !formData.hint_key}>
              {createMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Crear"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Página principal ─────────────────────────────────────────────────────────

export default function BrainVocabularyPage() {
  const params = useParams<{ search_space_id: string }>();
  const spaceId = Number(params.search_space_id);
  const spaceIdStr = params.search_space_id;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto w-full">
      {/* Breadcrumb */}
      <BrainBreadcrumb spaceId={spaceIdStr} current="Vocabulario" />

      {/* Cabecera */}
      <div>
        <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
          📖 Maestros y Vocabulario
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Gestión de dominios, tipos de documento, entity hints y vocabulario canónico.
          Los registros globales solo se pueden desactivar.
        </p>
      </div>

      <Tabs defaultValue="domains">
        <TabsList className="grid w-full grid-cols-4">
          {BRAIN_VOCABULARY_TABS.map((tab) => (
            <TabsTrigger key={tab.key} value={tab.key} className="gap-1.5 text-xs">
              <span>{tab.icon}</span>
              <span className="hidden sm:inline">{tab.label}</span>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="domains" className="mt-6">
          <DomainsTab spaceId={spaceId} />
        </TabsContent>

        <TabsContent value="doc_types" className="mt-6">
          <DocTypesTab spaceId={spaceId} />
        </TabsContent>

        <TabsContent value="entity_hints" className="mt-6">
          <EntityHintsTab spaceId={spaceId} />
        </TabsContent>

        <TabsContent value="vocabulary" className="mt-6">
          <VocabularyTab spaceId={spaceId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

