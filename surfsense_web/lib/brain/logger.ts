/**
 * @file logger.ts
 * @module lib/brain
 *
 * Logger estructurado para el módulo SecondBrainSense.
 *
 * Proporciona niveles de log diferenciados (debug, info, warn, error) con
 * prefijo de namespace para facilitar el filtrado en la consola del navegador.
 *
 * En producción (NODE_ENV !== "development") los mensajes de nivel "debug" se
 * suprimen automáticamente para no exponer información sensible.
 *
 * Uso:
 *   import { brainLogger } from "@/lib/brain/logger"
 *   const log = brainLogger("LevelBadge")
 *   log.info("Component mounted", { level: 1 })
 */

const BRAIN_LOG_PREFIX = "[SecondBrainSense]";

/** Niveles de log disponibles */
export type LogLevel = "debug" | "info" | "warn" | "error";

/** Contexto adicional adjunto a cada entrada de log */
export type LogContext = Record<string, unknown>;

export interface BrainLogger {
  debug: (message: string, context?: LogContext) => void;
  info:  (message: string, context?: LogContext) => void;
  warn:  (message: string, context?: LogContext) => void;
  error: (message: string, context?: LogContext) => void;
}

/**
 * Crea un logger con namespace para el módulo Brain.
 *
 * @param namespace - Nombre del componente o módulo (p.ej. "BrainChat", "LayoutDataProvider")
 * @returns Objeto logger con métodos debug / info / warn / error
 */
export function brainLogger(namespace: string): BrainLogger {
  const prefix = `${BRAIN_LOG_PREFIX}[${namespace}]`;
  const isDev = process.env.NODE_ENV === "development";

  const format = (message: string, context?: LogContext): [string, ...unknown[]] =>
    context ? [message, context] : [message];

  return {
    debug: (message, context) => {
      if (!isDev) return; // debug suprimido en producción
      // eslint-disable-next-line no-console
      console.debug(prefix, ...format(message, context));
    },
    info: (message, context) => {
      // eslint-disable-next-line no-console
      console.info(prefix, ...format(message, context));
    },
    warn: (message, context) => {
      // eslint-disable-next-line no-console
      console.warn(prefix, ...format(message, context));
    },
    error: (message, context) => {
      // eslint-disable-next-line no-console
      console.error(prefix, ...format(message, context));
    },
  };
}
