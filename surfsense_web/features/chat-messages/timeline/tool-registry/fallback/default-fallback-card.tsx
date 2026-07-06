"use client";

import { CheckIcon, ChevronDownIcon, XCircleIcon, BrainCircuit } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useAtomValue } from "jotai";
import { NestedScroll } from "@/components/assistant-ui/nested-scroll";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import { Spinner } from "@/components/ui/spinner";
import { getToolDisplayName } from "@/contracts/enums/toolIcons";
import { cn } from "@/lib/utils";
import { activeSearchSpaceIdAtom } from "@/atoms/search-spaces/search-space-query.atoms";
import { brainApiService } from "@/lib/apis/brain-api.service";
import type { TimelineToolComponent } from "../types";
import { ToolCardRevertButton } from "./revert-button";

// ─── helpers para "Ingestar en Brain" ────────────────────────────────────────

const MIN_CONTENT_CHARS = 150;

function extractTextContent(result: unknown): string | null {
	if (typeof result === "string" && result.trim().length >= MIN_CONTENT_CHARS) {
		// Descartar resultados que son JSON puro (sin texto narrativo)
		try {
			JSON.parse(result);
			return null; // JSON válido → no ingestable como texto
		} catch {
			return result;
		}
	}
	if (result && typeof result === "object") {
		const r = result as Record<string, unknown>;
		for (const key of ["content", "text", "body", "message"]) {
			if (typeof r[key] === "string" && (r[key] as string).length >= MIN_CONTENT_CHARS) {
				return r[key] as string;
			}
		}
	}
	return null;
}

function extractFilename(toolName: string, args: unknown): string {
	if (args && typeof args === "object") {
		const a = args as Record<string, unknown>;
		for (const key of ["filename", "file_name", "name", "title", "subject"]) {
			if (typeof a[key] === "string" && a[key]) return a[key] as string;
		}
	}
	// Fallback: toolName como base del fichero
	return `${toolName.replace(/[^a-z0-9_-]/gi, "_")}.txt`;
}

type IngestState = "idle" | "loading" | "done" | "error";

interface IngestBrainButtonProps {
	toolName: string;
	args: unknown;
	result: unknown;
	searchSpaceId: string;
}

function IngestBrainButton({ toolName, args, result, searchSpaceId }: IngestBrainButtonProps) {
	const [state, setState] = useState<IngestState>("idle");
	const [chunks, setChunks] = useState<number>(0);
	const [errMsg, setErrMsg] = useState<string>("");

	const text = useMemo(() => extractTextContent(result), [result]);
	if (!text) return null;

	async function handleIngest() {
		setState("loading");
		try {
			const resp = await brainApiService.ingestFromText({
				content:         text!,
				filename:        extractFilename(toolName, args),
				search_space_id: Number(searchSpaceId),
			});
			setChunks(resp.chunks_created);
			setState("done");
		} catch (e) {
			setErrMsg(e instanceof Error ? e.message : "Error al ingestar");
			setState("error");
		}
	}

	if (state === "done") {
		return (
			<span className="flex items-center gap-1.5 text-xs text-violet-400">
				<BrainCircuit className="h-3.5 w-3.5" />
				{chunks} chunk{chunks !== 1 ? "s" : ""} añadido{chunks !== 1 ? "s" : ""} al Brain
			</span>
		);
	}
	if (state === "error") {
		return (
			<span className="text-xs text-destructive" title={errMsg}>
				Error al ingestar
			</span>
		);
	}
	return (
		<Button
			type="button"
			variant="ghost"
			size="sm"
			onClick={handleIngest}
			disabled={state === "loading"}
			className="h-7 gap-1.5 px-2 text-xs text-violet-400 hover:text-violet-300 hover:bg-violet-500/10"
		>
			{state === "loading"
				? <Spinner size="sm" />
				: <BrainCircuit className="h-3.5 w-3.5" />}
			Ingestar en Brain
		</Button>
	);
}

/**
 * Best-effort error/cancellation reason from a tool result. Used as
 * the card subtitle when ``status`` is "error" or "cancelled". Returns
 * ``null`` if no usable text can be extracted.
 *
 * Tries: plain string → ``result.error`` → ``result.message`` →
 * stringified result. Per-tool components own richer error UIs; this
 * is the generic fallback's coarse summary.
 */
function deriveResultMessage(result: unknown): string | null {
	if (result == null) return null;
	if (typeof result === "string") return result;
	if (typeof result !== "object") return null;
	const r = result as { error?: unknown; message?: unknown };
	if (typeof r.error === "string") return r.error;
	if (typeof r.message === "string") return r.message;
	try {
		return JSON.stringify(result);
	} catch {
		return null;
	}
}

/**
 * Compact tool-call card. Used by ``FallbackToolBody`` for unregistered
 * tools whose result is not an HITL interrupt.
 *
 * shadcn composition note: ``Card`` is used as a visual frame WITHOUT
 * ``CardHeader``/``CardContent`` — the full composition's ``p-6``
 * doesn't fit a compact collapsible header that IS the trigger.
 *
 * Per-card expansion auto-syncs to ``isRunning`` (auto-expand on
 * stream start, auto-collapse on completion); manual toggle takes over
 * once streaming ends.
 */
export const DefaultFallbackCard: TimelineToolComponent = ({
	toolCallId,
	toolName,
	args,
	argsText,
	result,
	status,
	langchainToolCallId,
}) => {
	const searchSpaceId = useAtomValue(activeSearchSpaceIdAtom);
	const isCancelled = status === "cancelled";
	const isError = status === "error";
	const isRunning = status === "running";

	const [isExpanded, setIsExpanded] = useState(isRunning);
	useEffect(() => {
		setIsExpanded(isRunning);
	}, [isRunning]);

	const serializedResult = useMemo(
		() =>
			result !== undefined && typeof result !== "string" ? JSON.stringify(result, null, 2) : null,
		[result]
	);

	const subtitle = useMemo(
		() => (isError || isCancelled ? deriveResultMessage(result) : null),
		[isError, isCancelled, result]
	);

	const displayName = getToolDisplayName(toolName);

	return (
		<Card
			className={cn(
				"my-4 max-w-lg overflow-hidden",
				isCancelled && "opacity-60",
				isError && "border-destructive/30"
			)}
		>
			<Collapsible
				className="group"
				open={isExpanded}
				onOpenChange={(next) => {
					if (isRunning) return;
					setIsExpanded(next);
				}}
			>
				<div className="flex items-stretch transition-colors hover:bg-accent hover:text-accent-foreground">
					<CollapsibleTrigger asChild>
						<Button
							variant="ghost"
							type="button"
							className={cn(
								"h-auto flex-1 min-w-0 justify-start gap-3 rounded-none py-4 pl-5 pr-2 text-left font-normal hover:bg-transparent",
								"focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
								"disabled:cursor-default"
							)}
						>
							<div
								className={cn(
									"flex size-8 shrink-0 items-center justify-center rounded-lg",
									isError ? "bg-destructive/10" : isCancelled ? "bg-muted" : "bg-primary/10"
								)}
							>
								{isError ? (
									<XCircleIcon className="size-4 text-destructive" />
								) : isCancelled ? (
									<XCircleIcon className="size-4 text-muted-foreground" />
								) : isRunning ? (
									<Spinner size="sm" className="text-primary" />
								) : (
									<CheckIcon className="size-4 text-primary" />
								)}
							</div>

							<div className="flex flex-1 min-w-0 flex-col gap-0.5">
								<div className="flex items-center gap-2">
									<p
										className={cn(
											"text-sm font-semibold truncate",
											isCancelled && "text-muted-foreground line-through",
											isError && "text-destructive"
										)}
									>
										{displayName}
									</p>
									{isRunning && <Badge variant="secondary">Running</Badge>}
									{isError && <Badge variant="destructive">Failed</Badge>}
									{isCancelled && <Badge variant="outline">Cancelled</Badge>}
								</div>
								{subtitle && (
									<p
										className={cn(
											"text-xs truncate",
											isError ? "text-destructive/80" : "text-muted-foreground"
										)}
									>
										{subtitle}
									</p>
								)}
							</div>
						</Button>
					</CollapsibleTrigger>

					<div className="flex shrink-0 items-center gap-2 pl-2 pr-5">
						{status === "ok" && searchSpaceId && (
							<IngestBrainButton
								toolName={toolName}
								args={args}
								result={result}
								searchSpaceId={searchSpaceId}
							/>
						)}
						<ToolCardRevertButton
							toolCallId={toolCallId}
							toolName={toolName}
							langchainToolCallId={langchainToolCallId}
						/>
						<CollapsibleTrigger asChild>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								aria-label={isExpanded ? "Collapse details" : "Expand details"}
								className="size-7 shrink-0"
							>
								<ChevronDownIcon
									className={cn(
										"size-4 transition-transform duration-200",
										"group-data-[state=open]:rotate-180"
									)}
								/>
							</Button>
						</CollapsibleTrigger>
					</div>
				</div>

				<CollapsibleContent>
					<Separator />
					<div className="flex flex-col gap-3 px-5 py-3">
						{(argsText || isRunning) && (
							<div className="flex flex-col gap-1 min-w-0">
								<p className="text-xs font-medium text-muted-foreground">Inputs</p>
								<NestedScroll className="max-h-48 overflow-auto rounded-md bg-muted/40">
									{argsText ? (
										<pre className="px-3 py-2 text-xs text-foreground/80 whitespace-pre-wrap break-all font-mono">
											{argsText}
										</pre>
									) : (
										<p className="px-3 py-2 text-xs italic text-muted-foreground">
											Waiting for input…
										</p>
									)}
								</NestedScroll>
							</div>
						)}
						{!isCancelled && result !== undefined && (
							<>
								<Separator />
								<div className="flex flex-col gap-1 min-w-0">
									<p className="text-xs font-medium text-muted-foreground">Result</p>
									<NestedScroll className="max-h-64 overflow-auto rounded-md bg-muted/40">
										<pre className="px-3 py-2 text-xs text-foreground/80 whitespace-pre-wrap break-all font-mono">
											{typeof result === "string" ? result : serializedResult}
										</pre>
									</NestedScroll>
								</div>
							</>
						)}
					</div>
				</CollapsibleContent>
			</Collapsible>
		</Card>
	);
};
