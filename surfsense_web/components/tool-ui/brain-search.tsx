"use client";

import { useState } from "react";
import { Brain, ChevronDown, ChevronRight, FileText, Layers, AlertCircle } from "lucide-react";
import { LevelBadge } from "@/components/brain/LevelBadge";
import type { BrainLevel } from "@/lib/brain/constants";
import type { TimelineToolProps } from "@/features/chat-messages/timeline/tool-registry/types";
import { cn } from "@/lib/utils";

// ─── parser de resultado brain_search ────────────────────────────────────────

interface ParsedBrainResult {
	level: BrainLevel | null;
	sourcesConsulted: number;
	sources: string[];
	snippets: Array<{ rank: number; source: string; score: number; content: string }>;
	noResults: boolean;
	raw: string;
}

const LEVEL_LABEL_MAP: Record<string, BrainLevel> = {
	"brain":      1,
	"knowledge":  2,
	"web":        4,
	"llm":        0,
};

function parseLevelFromLabel(label: string): BrainLevel | null {
	const lower = label.toLowerCase();
	for (const [key, lvl] of Object.entries(LEVEL_LABEL_MAP)) {
		if (lower.includes(key)) return lvl;
	}
	return null;
}

function parseBrainResult(raw: string): ParsedBrainResult {
	if (!raw) return { level: null, sourcesConsulted: 0, sources: [], snippets: [], noResults: true, raw };

	const levelMatch = raw.match(/level="([^"]+)"/);
	const level = levelMatch ? parseLevelFromLabel(levelMatch[1]) : null;

	const sourcesConsultedMatch = raw.match(/sources_consulted=(\d+)/);
	const sourcesConsulted = sourcesConsultedMatch ? Number(sourcesConsultedMatch[1]) : 0;

	const fuentesMatch = raw.match(/Fuentes consultadas: (.+)/);
	const sources = fuentesMatch
		? fuentesMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
		: [];

	// Extrae bloques [N] source — score: X \n content
	const snippetRegex = /\[(\d+)\] (.+?) \(pág\..+?\) — score: ([\d.]+)\n([\s\S]+?)(?=\n\[|\nFuentes|\n<\/brain|$)/g;
	const snippets: ParsedBrainResult["snippets"] = [];
	let m: RegExpExecArray | null;
	while ((m = snippetRegex.exec(raw)) !== null) {
		snippets.push({
			rank: Number(m[1]),
			source: m[2].trim(),
			score: Number(m[3]),
			content: m[4].trim().slice(0, 280),
		});
	}

	const noResults =
		raw.includes("No se encontró información relevante") ||
		raw.includes("no relevant information") ||
		snippets.length === 0;

	return { level, sourcesConsulted, sources, snippets, noResults, raw };
}

// ─── componente ──────────────────────────────────────────────────────────────

export function BrainSearchToolUI({ args, result, status }: TimelineToolProps) {
	const [expanded, setExpanded] = useState(false);

	const query = (args as { query?: string })?.query ?? "";
	const forceLevel = (args as { force_level?: number | null })?.force_level;

	const parsed = parseBrainResult(typeof result === "string" ? result : "");

	const isPending = status === "running" || status === "requires-action";

	return (
		<div className="rounded-lg border border-border bg-card/50 text-sm overflow-hidden">
			{/* Header */}
			<div className="flex items-center gap-2 px-3 py-2 bg-violet-500/5 border-b border-border/50">
				<Brain className="h-4 w-4 text-violet-400 shrink-0" />
				<span className="font-medium text-foreground truncate flex-1">
					{query || "Brain Search"}
				</span>
				{parsed.level !== null && !isPending && (
					<LevelBadge level={parsed.level} />
				)}
				{forceLevel != null && (
					<span className="text-xs text-muted-foreground border border-border/50 rounded px-1">
						L{forceLevel}
					</span>
				)}
			</div>

			{/* Body */}
			{isPending ? (
				<div className="flex items-center gap-2 px-3 py-2 text-muted-foreground animate-pulse">
					<Layers className="h-3.5 w-3.5" />
					Buscando en el Second Brain…
				</div>
			) : parsed.noResults ? (
				<div className="flex items-center gap-2 px-3 py-2 text-muted-foreground">
					<AlertCircle className="h-3.5 w-3.5 text-amber-400" />
					Sin resultados relevantes
				</div>
			) : (
				<>
					{/* Sources summary */}
					<div className="flex items-center gap-2 px-3 py-1.5 text-xs text-muted-foreground border-b border-border/30">
						<FileText className="h-3 w-3" />
						<span>
							{parsed.snippets.length} fragmento{parsed.snippets.length !== 1 ? "s" : ""}
							{parsed.sources.length > 0 && (
								<> · {parsed.sources.slice(0, 3).join(", ")}
								{parsed.sources.length > 3 && ` +${parsed.sources.length - 3} más`}</>
							)}
						</span>
					</div>

					{/* Expandable snippets */}
					{parsed.snippets.length > 0 && (
						<div>
							<button
								type="button"
								onClick={() => setExpanded((v) => !v)}
								className="flex items-center gap-1 w-full px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
							>
								{expanded
									? <ChevronDown className="h-3 w-3" />
									: <ChevronRight className="h-3 w-3" />}
								{expanded ? "Ocultar" : "Ver"} fragmentos
							</button>
							{expanded && (
								<div className="divide-y divide-border/30">
									{parsed.snippets.map((s) => (
										<div key={s.rank} className="px-3 py-2 space-y-1">
											<div className="flex items-center gap-2 text-xs">
												<span className={cn(
													"font-mono rounded px-1",
													s.score >= 0.8 ? "bg-violet-500/15 text-violet-300" :
													s.score >= 0.6 ? "bg-blue-500/15 text-blue-300" :
													"bg-muted text-muted-foreground"
												)}>
													{s.score.toFixed(2)}
												</span>
												<span className="text-muted-foreground truncate">{s.source}</span>
											</div>
											<p className="text-xs text-foreground/80 line-clamp-3 leading-relaxed">
												{s.content}
											</p>
										</div>
									))}
								</div>
							)}
						</div>
					)}
				</>
			)}
		</div>
	);
}
