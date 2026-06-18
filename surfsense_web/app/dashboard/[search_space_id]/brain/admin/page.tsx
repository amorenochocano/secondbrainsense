/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/admin
 *
 * Admin Brain — configuración en caliente: chunking, retrieval, LLM, CRAG, colecciones, sistema.
 * Implementación completa en F6.10.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainAdminPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainAdminPage({ params }: BrainAdminPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">⚙️</span>
      <p className="text-sm">Admin Brain (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.10 — implementación pendiente</p>
    </div>
  );
}
