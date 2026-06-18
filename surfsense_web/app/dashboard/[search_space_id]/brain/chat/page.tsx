/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/chat
 *
 * Brain Chat — cascada multinivel L1→L2→BM25→Web→L0 con LevelBadge.
 * Implementación completa en F6.5.
 * Placeholder de F6.2 para validar rutas y navegación.
 */

import { use } from "react";

interface BrainChatPageProps {
  params: Promise<{ search_space_id: string }>;
}

export default function BrainChatPage({ params }: BrainChatPageProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <span className="text-4xl">💬</span>
      <p className="text-sm">Brain Chat (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.5 — implementación pendiente</p>
    </div>
  );
}
