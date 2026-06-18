/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/home
 *
 * Dashboard Brain — estado del sistema SecondBrainSense.
 * Implementación completa en F6.4.
 * Placeholder de F6.0 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainHomePageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainHomePage({ params }: BrainHomePageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">🧠</span>
      <p className="text-sm">
        SecondBrainSense — Dashboard (space {search_space_id})
      </p>
      <p className="text-xs opacity-60">F6.4 — implementación pendiente</p>
    </div>
  );
}
