/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/wiki
 *
 * Wiki semántica — lista de pasaportes con filtros y CRUD.
 * Implementación completa en F6.6.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainWikiPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainWikiPage({ params }: BrainWikiPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">📚</span>
      <p className="text-sm">Wiki semántica (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.6 — implementación pendiente</p>
    </div>
  );
}
