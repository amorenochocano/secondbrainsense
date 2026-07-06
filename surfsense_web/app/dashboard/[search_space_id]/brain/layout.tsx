/**
 * @file layout.tsx
 * @module app/dashboard/[search_space_id]/brain
 *
 * Layout compartido para todas las vistas Brain de SecondBrainSense.
 * Se monta bajo el layout padre del workspace (sidebar, icon-rail),
 * añadiendo únicamente el breadcrumb contextual de sección Brain.
 *
 * El layout padre (DashboardClientLayout + LayoutDataProvider) ya provee:
 *   - Sidebar con navegación principal
 *   - IconRail multi search-space
 *   - Panel derecho deslizante
 *   - TabBar de documentos
 */

import { use } from "react";
import type { ReactNode } from "react";
import { BrainBreadcrumb } from "@/components/brain/BrainBreadcrumb";

interface BrainLayoutProps {
  children: ReactNode;
  params: Promise<{ search_space_id: string }>;
}

/**
 * Server Component — extrae search_space_id del segmento dinámico y
 * pasa el breadcrumb contextual antes del contenido de cada página Brain.
 */
export default function BrainLayout({ children, params }: BrainLayoutProps) {
  const { search_space_id } = use(params);

  return (
    <div className="flex flex-col h-full bg-background text-foreground">
      <BrainBreadcrumb searchSpaceId={search_space_id} />
      <div className="flex-1 overflow-auto">{children}</div>
    </div>
  );
}
