/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain/vocabulary/doc-types
 * Implementación completa en F6.11 — CRUD brain_doc_types.
 */
import { use } from "react";
interface Props { params: Promise<{ search_space_id: string }> }
export default function DocTypesPage({ params }: Props) {
  const { search_space_id } = use(params);
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
      <p className="text-sm">Tipos de documento (space {search_space_id})</p>
      <p className="text-xs opacity-60">F6.11 — implementación pendiente</p>
    </div>
  );
}
