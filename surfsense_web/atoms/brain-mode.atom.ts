"use client";

import { atom } from "jotai";
import { disabledToolsAtom } from "@/atoms/agent-tools/agent-tools.atoms";

// Tools que brain_search reemplaza cuando Brain Mode está activo.
// brain_search se deja habilitado; el resto se deshabilita.
const BRAIN_MODE_EXCLUDED: readonly string[] = [
	"search_knowledge_base",
	"web_search",
	"scrape_webpage",
	"update_memory",
	"create_automation",
];

// Todos los tools del agente. El backend filtra por nombre exacto —
// "disabled_tools=["*"]" no tiene semántica especial, así que para LLM Libre
// deshabilitamos todos los tools conocidos explícitamente.
const ALL_AGENT_TOOLS: readonly string[] = [
	"brain_search",
	"search_knowledge_base",
	"web_search",
	"scrape_webpage",
	"update_memory",
	"create_automation",
];

const _brainModeBase = atom(false);

/**
 * Brain Mode: cuando está ON, solo brain_search está activo.
 * Al desactivar, limpia la lista de herramientas deshabilitadas.
 */
export const brainModeAtom = atom(
	(get) => get(_brainModeBase),
	(_get, set, next: boolean) => {
		set(_brainModeBase, next);
		set(disabledToolsAtom, next ? [...BRAIN_MODE_EXCLUDED] : []);
	}
);

/**
 * LLM Libre Mode: cuando está ON, todos los tools se deshabilitan
 * (el agente responde solo desde conocimiento paramétrico, sin retrieval).
 */
const _llmLibreBase = atom(false);

export const llmLibreModeAtom = atom(
	(get) => get(_llmLibreBase),
	(_get, set, next: boolean) => {
		set(_llmLibreBase, next);
		set(disabledToolsAtom, next ? [...ALL_AGENT_TOOLS] : []);
	}
);
