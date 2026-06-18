/**
 * @file brain-api.service.ts
 * @module lib/apis
 *
 * Servicio de API para el módulo SecondBrainSense.
 * Encapsula todas las llamadas HTTP a los endpoints Brain del backend,
 * validando las respuestas con los esquemas Zod de brain.types.ts.
 *
 * Todos los métodos propagan errores estándar de baseApiService
 * (AuthenticationError, NotFoundError, NetworkError…) — los componentes
 * solo necesitan capturar errores genéricos o mostrar toasts.
 */

import {
  brainGraphResponse,
  type BrainGraphResponse,
  brainListResponse,
  type BrainListResponse,
  brainQueryRequest,
  type BrainQueryRequest,
  brainQueryResponse,
  type BrainQueryResponse,
  brainStatsResponse,
  type BrainStatsResponse,
  healthResponse,
  type HealthResponse,
  levelUsageResponse,
  type LevelUsageResponse,
  passportDetailResponse,
  type PassportDetailResponse,
  passportHistoryResponse,
  type PassportHistoryResponse,
} from "@/contracts/types/brain.types";
import { ValidationError } from "../error";
import { brainLogger } from "../brain/logger";
import { BRAIN_ENDPOINTS } from "../brain/constants";
import { baseApiService } from "./base-api.service";

const log = brainLogger("BrainApiService");

class BrainApiService {
  // ───────────────────────────────────────────────────────────────
  // Query
  // ───────────────────────────────────────────────────────────────

  /**
   * Envía una pregunta al endpoint de cascada multinivel Brain.
   * El backend ejecuta L1→L2→BM25→Web→L0 y devuelve la respuesta
   * con el nivel usado y las fuentes citadas.
   */
  query = async (request: BrainQueryRequest): Promise<BrainQueryResponse> => {
    const parsed = brainQueryRequest.safeParse(request);
    if (!parsed.success) {
      const msg = parsed.error.issues.map((i) => i.message).join(", ");
      log.error("Petición query inválida", { issues: msg });
      throw new ValidationError(`Petición Brain inválida: ${msg}`);
    }

    log.debug("Enviando query Brain", {
      question: parsed.data.question.slice(0, 80),
      retrieve_mode: parsed.data.retrieve_mode,
      force_l0: parsed.data.force_l0,
    });

    return baseApiService.post(BRAIN_ENDPOINTS.QUERY, parsed.data, brainQueryResponse);
  };

  // ───────────────────────────────────────────────────────────────
  // Stats y salud
  // ───────────────────────────────────────────────────────────────

  /**
   * Obtiene estadísticas de las colecciones Qdrant y la última ingesta.
   */
  getStats = async (searchSpaceId: number): Promise<BrainStatsResponse> => {
    log.debug("Obteniendo stats Brain", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.STATS}?search_space_id=${searchSpaceId}`,
      brainStatsResponse,
    );
  };

  /**
   * Obtiene la distribución de niveles de respuesta usados en el período.
   */
  getLevelUsage = async (searchSpaceId: number): Promise<LevelUsageResponse> => {
    log.debug("Obteniendo uso de niveles", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.LEVEL_USAGE}?search_space_id=${searchSpaceId}`,
      levelUsageResponse,
    );
  };

  /**
   * Comprueba el estado de los servicios dependientes (Qdrant, Ollama, PostgreSQL).
   */
  getHealth = async (): Promise<HealthResponse> => {
    log.debug("Health check");
    return baseApiService.get(BRAIN_ENDPOINTS.HEALTH, healthResponse);
  };

  // ───────────────────────────────────────────────────────────────
  // Pasaportes (Wiki)
  // ───────────────────────────────────────────────────────────────

  /**
   * Lista todos los pasaportes del search space con su metadata.
   */
  listPassports = async (searchSpaceId: number): Promise<BrainListResponse> => {
    log.debug("Listando pasaportes", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.LIST}?search_space_id=${searchSpaceId}`,
      brainListResponse,
    );
  };

  /**
   * Obtiene el contenido Markdown y metadata de un pasaporte.
   */
  getPassport = async (source: string, searchSpaceId: number): Promise<PassportDetailResponse> => {
    log.debug("Obteniendo pasaporte", { source });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.PASSPORT(source)}?search_space_id=${searchSpaceId}`,
      passportDetailResponse,
    );
  };

  /**
   * Actualiza el contenido Markdown de un pasaporte existente.
   */
  updatePassport = async (
    source: string,
    content: string,
    searchSpaceId: number,
  ): Promise<PassportDetailResponse> => {
    log.info("Actualizando pasaporte", { source });
    return baseApiService.put(
      `${BRAIN_ENDPOINTS.PASSPORT(source)}?search_space_id=${searchSpaceId}`,
      { content },
      passportDetailResponse,
    );
  };

  /**
   * Obtiene el historial de versiones de un pasaporte.
   */
  getPassportHistory = async (
    source: string,
    searchSpaceId: number,
  ): Promise<PassportHistoryResponse> => {
    log.debug("Obteniendo historial de pasaporte", { source });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.PASSPORT_HISTORY(source)}?search_space_id=${searchSpaceId}`,
      passportHistoryResponse,
    );
  };

  /**
   * Solicita la re-síntesis del pasaporte de un documento.
   */
  resynthesizePassport = async (
    source: string,
    searchSpaceId: number,
    model?: string,
  ): Promise<void> => {
    log.info("Re-sintetizando pasaporte", { source, model });
    await baseApiService.post(
      `${BRAIN_ENDPOINTS.RESYNTHESIZE(source)}`,
      { search_space_id: searchSpaceId, ...(model ? { model } : {}) },
    );
  };

  /**
   * Elimina un documento Brain y su pasaporte asociado.
   */
  deleteDocument = async (source: string, searchSpaceId: number): Promise<void> => {
    log.warn("Eliminando documento Brain", { source, searchSpaceId });
    await baseApiService.delete(
      `${BRAIN_ENDPOINTS.DELETE_DOCUMENT(source)}?search_space_id=${searchSpaceId}`,
    );
  };

  // ───────────────────────────────────────────────────────────────
  // Grafo
  // ───────────────────────────────────────────────────────────────

  /**
   * Obtiene nodos y aristas del grafo de conocimiento del search space.
   */
  getGraph = async (searchSpaceId: number): Promise<BrainGraphResponse> => {
    log.debug("Obteniendo grafo Brain", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.GRAPH}?search_space_id=${searchSpaceId}`,
      brainGraphResponse,
    );
  };
}

/** Instancia singleton del servicio Brain — usar directamente en hooks/componentes */
export const brainApiService = new BrainApiService();
