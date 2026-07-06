// Ruta unificada /chat — re-exporta el upstream new-chat sin copiarlo.
// El BrainModeBar se inyecta desde chat/layout.tsx.
export { default } from "@/app/dashboard/[search_space_id]/new-chat/[[...chat_id]]/page";
