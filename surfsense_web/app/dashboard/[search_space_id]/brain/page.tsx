/**
 * @file page.tsx
 * @module app/dashboard/[search_space_id]/brain
 *
 * Redirección inmediata de /brain → /brain/home.
 * Evita que el segmento raíz quede sin contenido y mantiene
 * la URL canónica en /brain/home.
 */

import { use } from "react";
import { redirect } from "next/navigation";
import { BRAIN_ROUTES } from "@/lib/brain/constants";

interface BrainRootPageProps {
  params: Promise<{ search_space_id: string }>;
}

/**
 * Server Component — redirige de forma permanente a brain/home.
 * No renderiza ningún contenido visible.
 */
export default function BrainRootPage({ params }: BrainRootPageProps) {
  const { search_space_id } = use(params);
  redirect(BRAIN_ROUTES.HOME(search_space_id));
}
