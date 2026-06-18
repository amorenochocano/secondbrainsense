/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/metrics
 *
 * Métricas Brain — estado colecciones Qdrant, distribución de niveles, top fuentes.
 * Implementación completa en F6.9.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainMetricsPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainMetricsPage({ params }: BrainMetricsPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">📊</span>
      <p className="text-sm">Métricas Brain (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.9 — implementación pendiente</p>
    </div>
  );
}
