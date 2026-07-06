"use client";

import { useAtom } from "jotai";
import { Brain, Bot, Drill } from "lucide-react";
import { brainModeAtom, llmLibreModeAtom } from "@/atoms/brain-mode.atom";
import { cn } from "@/lib/utils";

interface ToggleChipProps {
	active: boolean;
	onToggle: () => void;
	icon: React.ReactNode;
	label: string;
	activeClasses: string;
	title: string;
}

function ToggleChip({ active, onToggle, icon, label, activeClasses, title }: ToggleChipProps) {
	return (
		<button
			type="button"
			onClick={onToggle}
			title={title}
			className={cn(
				"inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
				active
					? activeClasses
					: "border-border bg-background text-muted-foreground hover:text-foreground hover:border-foreground/30"
			)}
		>
			{icon}
			{label}
		</button>
	);
}

/**
 * Barra flotante de controles Brain Mode / LLM Libre.
 * Se monta en el layout de /chat para no modificar el upstream new-chat.
 *
 * Brain Mode  — deshabilita todos los tools excepto brain_search.
 * LLM Libre   — deshabilita todos los tools (fuerza L0 paramétrico).
 * Ambos son mutuamente excluyentes.
 */
export function BrainModeBar() {
	const [brainMode, setBrainMode] = useAtom(brainModeAtom);
	const [llmLibre, setLlmLibre] = useAtom(llmLibreModeAtom);

	function toggleBrain() {
		const next = !brainMode;
		setBrainMode(next);
		if (next && llmLibre) setLlmLibre(false);
	}

	function toggleLlmLibre() {
		const next = !llmLibre;
		setLlmLibre(next);
		if (next && brainMode) setBrainMode(false);
	}

	return (
		<div className="flex items-center gap-2 px-4 py-1.5 border-b border-border/50 bg-background/80 backdrop-blur-sm">
			<ToggleChip
				active={brainMode}
				onToggle={toggleBrain}
				icon={<Brain className="h-3.5 w-3.5" />}
				label="Brain Mode"
				activeClasses="border-violet-500/50 bg-violet-500/10 text-violet-300"
				title="Solo usa el Second Brain de Enagás. Deshabilita web_search y otros tools."
			/>
			<ToggleChip
				active={llmLibre}
				onToggle={toggleLlmLibre}
				icon={<Bot className="h-3.5 w-3.5" />}
				label="LLM libre"
				activeClasses="border-amber-500/50 bg-amber-500/10 text-amber-300"
				title="Responde desde conocimiento paramétrico sin retrieval (L0)."
			/>
			{(brainMode || llmLibre) && (
				<span className="ml-auto text-xs text-muted-foreground">
					{brainMode && "Solo Second Brain activo"}
					{llmLibre && "Sin retrieval — respuesta paramétrica"}
				</span>
			)}
		</div>
	);
}
