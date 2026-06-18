/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/ingest
 *
 * Ingesta avanzada — dropzone, recomendación de modelo, log de fases SSE.
 * Implementación completa en F6.8.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainIngestPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainIngestPage({ params }: BrainIngestPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">📥</span>
      <p className="text-sm">Ingesta avanzada (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.8 — implementación pendiente</p>
    </div>
  );
}
