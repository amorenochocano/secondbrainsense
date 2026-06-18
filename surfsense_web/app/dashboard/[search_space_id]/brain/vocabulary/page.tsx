/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/vocabulary
 *
 * Gestión de Maestros y Vocabulario — tabs: Dominios, Tipos de doc, Entity Hints, Vocabulario.
 * Implementación completa en F6.11.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainVocabularyPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainVocabularyPage({ params }: BrainVocabularyPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">📖</span>
      <p className="text-sm">Maestros y Vocabulario (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.11 — implementación pendiente</p>
    </div>
  );
}
