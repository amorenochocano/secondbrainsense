import { BrainModeBar } from "@/components/brain/BrainModeBar";

/**
 * Layout de la ruta unificada /chat.
 * Añade la barra Brain Mode/LLM Libre sobre el contenido de new-chat
 * sin modificar el upstream.
 */
export default function ChatLayout({ children }: { children: React.ReactNode }) {
	return (
		<div className="flex flex-col h-full">
			<BrainModeBar />
			<div className="flex-1 min-h-0">{children}</div>
		</div>
	);
}
