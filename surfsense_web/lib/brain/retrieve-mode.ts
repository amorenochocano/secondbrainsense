/**
 * @file retrieve-mode.ts
 * @module lib/brain
 *
 * Utilidad para detectar automáticamente el `retrieve_mode` óptimo
 * en función del texto de la pregunta.
 *
 * Lógica extraída del `brain_chat.py` original (Second Brain):
 * si la pregunta contiene un nombre de fichero Y palabras clave que
 * indican solicitud del contenido completo, el modo es `"full_source"`;
 * en cualquier otro caso se usa `"auto"`.
 *
 * @example
 * detectRetrieveMode("Muéstrame el fichero chunking.py completo") // "full_source"
 * detectRetrieveMode("¿Cómo funciona el chunker?")               // "auto"
 */

import type { RetrieveMode } from "@/contracts/types/brain.types";
import { BRAIN_FILE_REQUEST_PATTERNS, BRAIN_FILENAME_REGEX } from "./constants";

/**
 * Detecta el retrieve_mode adecuado para una pregunta dada.
 *
 * @param question - Texto de la pregunta del usuario
 * @returns `"full_source"` si la pregunta pide un fichero concreto completo,
 *          `"auto"` en el resto de casos
 */
export function detectRetrieveMode(question: string): RetrieveMode {
  const q = question.toLowerCase();
  const hasFilename = BRAIN_FILENAME_REGEX.test(question);
  const asksFile = (BRAIN_FILE_REQUEST_PATTERNS as readonly string[]).some((p) =>
    q.includes(p),
  );
  return hasFilename && asksFile ? "full_source" : "auto";
}
