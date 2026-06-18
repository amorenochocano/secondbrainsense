/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/graph
 *
 * Grafo interactivo de conocimiento — react-force-graph con panel de detalle.
 * Implementación completa en F6.7.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainGraphPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainGraphPage({ params }: BrainGraphPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">🕸️</span>
      <p className="text-sm">Grafo de conocimiento (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.7 — implementación pendiente</p>
    </div>
  );
}
