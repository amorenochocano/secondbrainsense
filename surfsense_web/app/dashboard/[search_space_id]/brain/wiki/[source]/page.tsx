/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/wiki/[source]
 *
 * Detalle de pasaporte — editor Monaco + preview Markdown + historial de versiones.
 * Implementación completa en F6.6.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainWikiDetailPageProps {
  params: Promise<{ search_space_id: string; source: string }>;
}

export default function BrainWikiDetailPage({ params }: BrainWikiDetailPageProps) {
  const { search_space_id, source } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">📄</span>
      <p className="text-sm">
        Pasaporte: <code className="text-xs bg-muted px-1 rounded">{decodeURIComponent(source)}</code>
      </p>
      <p className="text-xs opacity-60">Space {search_space_id} — F6.6 implementación pendiente</p>
    </div>
  );
}
