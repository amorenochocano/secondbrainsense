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
  adminConfigResponse,
  type AdminConfigResponse,
  brainDomainListResponse,
  type BrainDomainRecord,
  brainDocTypeListResponse,
  type BrainDocTypeRecord,
  brainEntityHintListResponse,
  type BrainEntityHintRecord,
  brainVocabularyListResponse,
  type BrainVocabularyRecord,
  vocabularyLookupResponse,
  type VocabularyLookupResponse,
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
  ingestJobResponse,
  type IngestJobResponse,
  type IngestPhaseEvent,
  ingestPhaseEvent,
  ingestUrlRequest,
  levelUsageResponse,
  type LevelUsageResponse,
  ollamaModelsResponse,
  type OllamaModelsResponse,
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

  /**
   * Inicia la ingesta de un documento desde una URL.
   * Devuelve un job_id que se usa para subscribirse al stream SSE de progreso.
   */
  ingestUrl = async (
    url: string,
    searchSpaceId: number,
    model?: string,
  ): Promise<IngestJobResponse> => {
    const payload = ingestUrlRequest.parse({ url, search_space_id: searchSpaceId, model });
    log.info("Iniciando ingesta de URL", { url, searchSpaceId, model });
    return baseApiService.post(BRAIN_ENDPOINTS.INGEST_URL, payload, ingestJobResponse);
  };

  /**
   * Subscripción SSE al progreso de un job de ingesta.
   * Usa fetch + ReadableStream en lugar de EventSource para soportar
   * la cabecera Authorization: Bearer.
   *
   * @param jobId     - ID del job obtenido de ingestUrl()
   * @param onPhase   - Callback por cada evento de fase recibido
   * @param onDone    - Callback cuando el stream termina normalmente
   * @param onError   - Callback si el stream falla
   * @returns         - Función de limpieza (aborta el stream)
   */
  streamIngestJob = (
    jobId: string,
    onPhase: (event: IngestPhaseEvent) => void,
    onDone: () => void,
    onError: (err: Error) => void,
  ): (() => void) => {
    const controller = new AbortController();
    const fullUrl = `${baseApiService.baseUrl}${BRAIN_ENDPOINTS.INGEST_STREAM(jobId)}`;

    log.debug("Suscribiéndose al stream SSE de ingesta", { jobId, fullUrl });

    fetch(fullUrl, {
      headers: {
        Authorization: `Bearer ${baseApiService.bearerToken}`,
        Accept: "text/event-stream",
      },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok || !response.body) {
          throw new Error(`SSE error: HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();

          if (done) {
            log.debug("Stream SSE completado", { jobId });
            onDone();
            break;
          }

          buffer += decoder.decode(value, { stream: true });
          // Procesar líneas completas (SSE usa \n\n como separador de eventos)
          const parts = buffer.split("\n");
          buffer = parts.pop() ?? "";

          for (const line of parts) {
            const trimmed = line.trim();
            if (!trimmed.startsWith("data:")) continue;
            const jsonStr = trimmed.slice(5).trim();
            if (!jsonStr || jsonStr === "[DONE]") {
              onDone();
              return;
            }
            try {
              const parsed = ingestPhaseEvent.parse(JSON.parse(jsonStr));
              onPhase(parsed);
            } catch (parseErr) {
              log.warn("Evento SSE con formato inesperado ignorado", { line, parseErr });
            }
          }
        }
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          const error = err instanceof Error ? err : new Error(String(err));
          log.error("Error en stream SSE de ingesta", { jobId, error });
          onError(error);
        }
      });

    return () => {
      log.debug("Abortando stream SSE de ingesta", { jobId });
      controller.abort();
    };
  };

  // ─── Admin Brain ──────────────────────────────────────────────────────────

  /** Obtiene la configuración activa del pipeline Brain */
  getAdminConfig = async (): Promise<AdminConfigResponse> => {
    log.debug("Obteniendo configuración Admin Brain");
    return baseApiService.get(BRAIN_ENDPOINTS.ADMIN_CONFIG, adminConfigResponse);
  };

  /**
   * Actualiza parámetros de configuración del pipeline Brain.
   * Semántica PATCH: solo se envían los campos modificados.
   */
  updateAdminConfig = async (patch: Partial<AdminConfigResponse>): Promise<AdminConfigResponse> => {
    log.info("Actualizando configuración Admin Brain", { patch });
    return baseApiService.post(BRAIN_ENDPOINTS.ADMIN_CONFIG, patch, adminConfigResponse);
  };

  /** Obtiene la lista de modelos Ollama disponibles en el servidor */
  getOllamaModels = async (): Promise<OllamaModelsResponse> => {
    log.debug("Obteniendo modelos Ollama");
    return baseApiService.get(BRAIN_ENDPOINTS.OLLAMA_MODELS, ollamaModelsResponse);
  };

  // ─── Vocabulario — Dominios ───────────────────────────────────────────────

  /** Lista todos los dominios (globales + específicos del space) */
  listDomains = async (searchSpaceId: number) => {
    log.debug("Listando dominios Brain", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.ADMIN_DOMAINS}?search_space_id=${searchSpaceId}`,
      brainDomainListResponse,
    );
  };

  /** Crea un nuevo dominio */
  createDomain = async (data: Omit<BrainDomainRecord, "id">) => {
    log.info("Creando dominio Brain", { domainKey: data.domain_key });
    return baseApiService.post(BRAIN_ENDPOINTS.ADMIN_DOMAINS, data, brainDomainListResponse.element);
  };

  /** Actualiza un dominio existente */
  updateDomain = async (id: number, data: Partial<BrainDomainRecord>) => {
    log.info("Actualizando dominio Brain", { id });
    return baseApiService.put(`${BRAIN_ENDPOINTS.ADMIN_DOMAINS}/${id}`, data, brainDomainListResponse.element);
  };

  /** Elimina un dominio del space (no afecta a dominios globales) */
  deleteDomain = async (id: number) => {
    log.warn("Eliminando dominio Brain", { id });
    return baseApiService.delete(`${BRAIN_ENDPOINTS.ADMIN_DOMAINS}/${id}`);
  };

  /** Activa o desactiva un dominio */
  toggleDomainActive = async (id: number) => {
    log.info("Toggle activo dominio Brain", { id });
    return baseApiService.patch(`${BRAIN_ENDPOINTS.ADMIN_DOMAINS}/${id}/toggle-active`, {}, brainDomainListResponse.element);
  };

  // ─── Vocabulario — Tipos de documento ────────────────────────────────────

  /** Lista todos los tipos de documento */
  listDocTypes = async (searchSpaceId: number) => {
    log.debug("Listando tipos de documento Brain", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.ADMIN_DOC_TYPES}?search_space_id=${searchSpaceId}`,
      brainDocTypeListResponse,
    );
  };

  /** Crea un nuevo tipo de documento */
  createDocType = async (data: Omit<BrainDocTypeRecord, "id">) => {
    log.info("Creando tipo de documento Brain", { typeKey: data.type_key });
    return baseApiService.post(BRAIN_ENDPOINTS.ADMIN_DOC_TYPES, data, brainDocTypeListResponse.element);
  };

  /** Actualiza un tipo de documento */
  updateDocType = async (id: number, data: Partial<BrainDocTypeRecord>) => {
    log.info("Actualizando tipo de documento Brain", { id });
    return baseApiService.put(`${BRAIN_ENDPOINTS.ADMIN_DOC_TYPES}/${id}`, data, brainDocTypeListResponse.element);
  };

  /** Elimina un tipo de documento del space */
  deleteDocType = async (id: number) => {
    log.warn("Eliminando tipo de documento Brain", { id });
    return baseApiService.delete(`${BRAIN_ENDPOINTS.ADMIN_DOC_TYPES}/${id}`);
  };

  // ─── Vocabulario — Entity Hints ───────────────────────────────────────────

  /** Lista todos los entity hints */
  listEntityHints = async (searchSpaceId: number) => {
    log.debug("Listando entity hints Brain", { searchSpaceId });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.ADMIN_ENTITY_HINTS}?search_space_id=${searchSpaceId}`,
      brainEntityHintListResponse,
    );
  };

  /** Crea un nuevo entity hint */
  createEntityHint = async (data: Omit<BrainEntityHintRecord, "id">) => {
    log.info("Creando entity hint Brain", { hintKey: data.hint_key });
    return baseApiService.post(BRAIN_ENDPOINTS.ADMIN_ENTITY_HINTS, data, brainEntityHintListResponse.element);
  };

  /** Actualiza un entity hint */
  updateEntityHint = async (id: number, data: Partial<BrainEntityHintRecord>) => {
    log.info("Actualizando entity hint Brain", { id });
    return baseApiService.put(`${BRAIN_ENDPOINTS.ADMIN_ENTITY_HINTS}/${id}`, data, brainEntityHintListResponse.element);
  };

  /** Elimina un entity hint del space */
  deleteEntityHint = async (id: number) => {
    log.warn("Eliminando entity hint Brain", { id });
    return baseApiService.delete(`${BRAIN_ENDPOINTS.ADMIN_ENTITY_HINTS}/${id}`);
  };

  // ─── Vocabulario — Vocabulario canónico ───────────────────────────────────

  /** Lista entradas del vocabulario con búsqueda opcional */
  listVocabulary = async (searchSpaceId: number, q?: string) => {
    const params = new URLSearchParams({ search_space_id: String(searchSpaceId) });
    if (q) params.set("q", q);
    log.debug("Listando vocabulario Brain", { searchSpaceId, q });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.ADMIN_VOCABULARY}?${params.toString()}`,
      brainVocabularyListResponse,
    );
  };

  /** Crea una nueva entrada canónica */
  createVocabularyEntry = async (data: Omit<BrainVocabularyRecord, "id">) => {
    log.info("Creando entrada vocabulario Brain", { canonical: data.canonical_tag });
    return baseApiService.post(BRAIN_ENDPOINTS.ADMIN_VOCABULARY, data, brainVocabularyListResponse.element);
  };

  /** Actualiza aliases de una entrada canónica */
  updateVocabularyEntry = async (id: number, data: Partial<BrainVocabularyRecord>) => {
    log.info("Actualizando vocabulario Brain", { id });
    return baseApiService.put(`${BRAIN_ENDPOINTS.ADMIN_VOCABULARY}/${id}`, data, brainVocabularyListResponse.element);
  };

  /** Elimina una entrada canónica */
  deleteVocabularyEntry = async (id: number) => {
    log.warn("Eliminando entrada vocabulario Brain", { id });
    return baseApiService.delete(`${BRAIN_ENDPOINTS.ADMIN_VOCABULARY}/${id}`);
  };

  /** Busca la canónica correspondiente a un alias o tag dado */
  lookupVocabulary = async (tag: string): Promise<VocabularyLookupResponse> => {
    log.debug("Lookup vocabulario Brain", { tag });
    return baseApiService.get(
      `${BRAIN_ENDPOINTS.ADMIN_VOCABULARY}/lookup?tag=${encodeURIComponent(tag)}`,
      vocabularyLookupResponse,
    );
  };
}

/** Instancia singleton del servicio Brain — usar directamente en hooks/componentes */
export const brainApiService = new BrainApiService();
