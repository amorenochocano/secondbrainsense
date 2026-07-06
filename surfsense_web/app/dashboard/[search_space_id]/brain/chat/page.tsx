// F4: /brain/chat migrado a /chat (ruta unificada).
// El chat Brain anterior tenía lógica propia. Ahora la funcionalidad equivalente
// (Brain Mode toggle) está disponible en /chat via BrainModeBar.
import { redirect } from "next/navigation";

interface Props {
	params: Promise<{ search_space_id: string }>;
}

export default async function BrainChatRedirectPage({ params }: Props) {
	const { search_space_id } = await params;
	redirect(`/dashboard/${search_space_id}/chat`);
}
