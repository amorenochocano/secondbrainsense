/**
 * @file brain.types.ts
 * @module contracts/types
 *
 * Contratos Zod para todos los endpoints del módulo SecondBrainSense.
 * Cada schema valida la respuesta del backend en tiempo de ejecución,
 * evitando que errores silenciosos lleguen a los componentes.
 */

import { z } from "zod";

// ─────────────────────────────────────────────────────────────────────────────
// Tipos base reutilizables
// ─────────────────────────────────────────────────────────────────────────────

/** Niveles de retrieval que puede devolver el backend */
export const brainLevelSchema = z.union([
  z.literal(0),
  z.literal(1),
  z.literal(2),
  z.literal(3),
  z.literal(4),
]);
export type BrainLevelValue = z.infer<typeof brainLevelSchema>;

/** Modo de recuperación del documento */
export const retrieveModeSchema = z.enum(["auto", "full_source"]);
export type RetrieveMode = z.infer<typeof retrieveModeSchema>;

/** Fuente citada en una respuesta Brain */
export const brainSourceSchema = z.object({
  source:      z.string(),
  page:        z.union([z.string(), z.number()]).optional(),
  score:       z.number().min(0).max(1),
  source_url:  z.string().url().optional().nullable(),
});
export type BrainSource = z.infer<typeof brainSourceSchema>;

/** Scopes de indexación Qdrant */
export const brainScopeSchema = z.enum(["brain", "knowledge", "code"]);
export type BrainScopeValue = z.infer<typeof brainScopeSchema>;

/** Dominios de conocimiento */
export const brainDomainSchema = z.enum([
  "engineering", "data", "business", "functional", "legal", "other",
]);
export type BrainDomainValue = z.infer<typeof brainDomainSchema>;

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/v1/brain/query
// ─────────────────────────────────────────────────────────────────────────────

export const brainQueryRequest = z.object({
  question:       z.string().min(1),
  search_space_id: z.number().int().positive(),
  retrieve_mode:  retrieveModeSchema.default("auto"),
  force_l0:       z.boolean().default(false),
  session_id:     z.string().optional(),
});
export type BrainQueryRequest = z.infer<typeof brainQueryRequest>;

export const brainQueryResponse = z.object({
  answer:              z.string(),
  level_used:          brainLevelSchema,
  level_label:         z.string(),
  model_tier:          z.string().optional().nullable(),
  sources:             z.array(z.string()).default([]),
  drill_down_available: z.boolean().default(false),
  session_id:          z.string().optional().nullable(),
});
export type BrainQueryResponse = z.infer<typeof brainQueryResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/stats
// ─────────────────────────────────────────────────────────────────────────────

export const brainCollectionStat = z.object({
  count:    z.number().int().nonnegative(),
  sources:  z.number().int().nonnegative(),
  dimension: z.number().int().positive().optional().nullable(),
});

export const brainStatsResponse = z.object({
  brain_count:     z.number().int().nonnegative(),
  knowledge_count: z.number().int().nonnegative(),
  code_count:      z.number().int().nonnegative(),
  last_ingest:     z.string().datetime({ offset: true }).nullable(),
  last_ingest_doc: z.string().nullable(),
  level_usage:     z.record(z.string(), z.number()).optional(),
  collections:     z.record(brainScopeSchema, brainCollectionStat).optional(),
});
export type BrainStatsResponse = z.infer<typeof brainStatsResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/list
// ─────────────────────────────────────────────────────────────────────────────

export const passportMetadata = z.object({
  source:        z.string(),
  title:         z.string(),
  doc_type:      z.string().optional().nullable(),
  domain:        brainDomainSchema.optional().nullable(),
  importance:    z.number().int().min(1).max(5).optional().nullable(),
  confidence:    z.number().min(0).max(1).optional().nullable(),
  tags:          z.array(z.string()).default([]),
  scopes:        z.array(brainScopeSchema).default([]),
  connector_type: z.string().optional().nullable(),
  has_pii:       z.boolean().default(false),
  updated_at:    z.string().datetime({ offset: true }).nullable(),
  search_space_id: z.number().int().positive(),
});
export type PassportMetadata = z.infer<typeof passportMetadata>;

export const brainListResponse = z.array(passportMetadata);
export type BrainListResponse = z.infer<typeof brainListResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/passport/{source}
// ─────────────────────────────────────────────────────────────────────────────

export const passportDetailResponse = z.object({
  source:   z.string(),
  content:  z.string(),
  metadata: passportMetadata,
});
export type PassportDetailResponse = z.infer<typeof passportDetailResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/passport/{source}/history
// ─────────────────────────────────────────────────────────────────────────────

export const passportVersion = z.object({
  version_id:  z.string(),
  created_at:  z.string().datetime({ offset: true }),
  content:     z.string(),
});
export const passportHistoryResponse = z.array(passportVersion);
export type PassportHistoryResponse = z.infer<typeof passportHistoryResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/graph
// ─────────────────────────────────────────────────────────────────────────────

export const graphNodeSchema = z.object({
  id:      z.string(),
  label:   z.string(),
  domain:  brainDomainSchema.optional().nullable(),
  val:     z.number().optional(),         // tamaño proporcional a importancia
  tags:    z.array(z.string()).default([]),
  summary: z.string().optional().nullable(),
  color:   z.string().optional().nullable(),
});
export type GraphNode = z.infer<typeof graphNodeSchema>;

export const graphEdgeTypeSchema = z.enum(["related", "serie", "tag_overlap"]);
export type GraphEdgeType = z.infer<typeof graphEdgeTypeSchema>;

export const graphEdgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  type:   graphEdgeTypeSchema,
  color:  z.string().optional().nullable(),
});
export type GraphEdge = z.infer<typeof graphEdgeSchema>;

export const brainGraphResponse = z.object({
  nodes: z.array(graphNodeSchema),
  edges: z.array(graphEdgeSchema),
});
export type BrainGraphResponse = z.infer<typeof brainGraphResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/brain/level-usage
// ─────────────────────────────────────────────────────────────────────────────

export const levelUsageResponse = z.object({
  usage: z.record(z.string(), z.number()),
  period_hours: z.number().default(24),
});
export type LevelUsageResponse = z.infer<typeof levelUsageResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/health
// ─────────────────────────────────────────────────────────────────────────────

export const serviceHealthStatus = z.enum(["ok", "degraded", "error", "unknown"]);

export const healthResponse = z.object({
  qdrant:     serviceHealthStatus,
  ollama:     serviceHealthStatus,
  postgresql: serviceHealthStatus,
  redis:      serviceHealthStatus.optional(),
});
export type HealthResponse = z.infer<typeof healthResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// POST /api/v1/brain/ingest/url + SSE /api/v1/brain/ingest/stream
// ─────────────────────────────────────────────────────────────────────────────

export const ingestUrlRequest = z.object({
  url:             z.string().url(),
  search_space_id: z.number().int().positive(),
  model:           z.string().optional(),
});
export type IngestUrlRequest = z.infer<typeof ingestUrlRequest>;

export const ingestJobResponse = z.object({
  job_id:     z.string(),
  message:    z.string().optional().nullable(),
  /** true cuando el fichero supera BRAIN_INGEST_BACKGROUND_THRESHOLD_BYTES */
  background: z.boolean().default(false),
});
export type IngestJobResponse = z.infer<typeof ingestJobResponse>;

/** Estado de cada fase del pipeline durante el streaming SSE */
export const ingestPhaseStatus = z.enum(["idle", "running", "ok", "warn", "error", "skip"]);
export type IngestPhaseStatus = z.infer<typeof ingestPhaseStatus>;

/** Evento SSE emitido por el backend por cada fase del pipeline */
export const ingestPhaseEvent = z.object({
  phase:   z.enum(["extraction", "synthesis", "chunking", "vectorization"]),
  status:  ingestPhaseStatus,
  detail:  z.string().optional().nullable(),
  /** Número de chunks generados (solo en fase chunking) */
  chunks:  z.number().int().nonnegative().optional().nullable(),
});
export type IngestPhaseEvent = z.infer<typeof ingestPhaseEvent>;

// ─────────────────────────────────────────────────────────────────────────────
// GET /api/v1/admin/ollama-models
// ─────────────────────────────────────────────────────────────────────────────

export const ollamaModelsResponse = z.object({
  models: z.array(z.object({ name: z.string() })),
});
export type OllamaModelsResponse = z.infer<typeof ollamaModelsResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// GET|POST /api/v1/admin/config
// ─────────────────────────────────────────────────────────────────────────────

/** Configuración activa del pipeline Brain — todos los campos son opcionales en PATCH */
export const adminConfigResponse = z.object({
  // Chunking
  BRAIN_CHUNK_STRATEGY:      z.string().optional().nullable(),
  BRAIN_CHUNK_SIZE:          z.number().int().optional().nullable(),
  BRAIN_CHUNK_OVERLAP:       z.number().int().optional().nullable(),
  // Retrieval
  ROUTER_L1_HIGH_SCORE:      z.number().optional().nullable(),
  ROUTER_L1_MIN_SCORE:       z.number().optional().nullable(),
  BRAIN_TOP_K:               z.number().int().optional().nullable(),
  BRAIN_RERANKING_ENABLED:   z.boolean().optional().nullable(),
  // LLM síntesis
  BRAIN_LLM_PROVIDER:        z.string().optional().nullable(),
  BRAIN_LLM_MODEL:           z.string().optional().nullable(),
  BRAIN_LLM_TEMPERATURE:     z.number().optional().nullable(),
  BRAIN_LLM_MAX_TOKENS:      z.number().int().optional().nullable(),
  // Ingesta (F5 unificada)
  BRAIN_INGESTION_ENABLED:   z.boolean().optional().nullable(),
  BRAIN_SYNTHESIS_ENABLED:   z.boolean().optional().nullable(),
  BRAIN_EMBEDDING_MODEL:     z.string().optional().nullable(),
  BRAIN_QUALITY_THRESHOLD:   z.number().optional().nullable(),
  // CRAG
  CRAG_EVALUATOR_ENABLED:    z.boolean().optional().nullable(),
  CRAG_EVALUATOR_PROVIDER:   z.string().optional().nullable(),
  CRAG_EVALUATOR_MODEL:      z.string().optional().nullable(),
  CRAG_MAX_EVAL_CHUNKS:      z.number().int().optional().nullable(),
  CRAG_EVAL_TIMEOUT:         z.number().int().optional().nullable(),
  CRAG_REWRITER_MODEL:       z.string().optional().nullable(),
}).passthrough();
export type AdminConfigResponse = z.infer<typeof adminConfigResponse>;

// ─────────────────────────────────────────────────────────────────────────────
// Vocabulary — GET/POST/PUT/DELETE /api/v1/brain/admin/domains | doc-types | etc.
// ─────────────────────────────────────────────────────────────────────────────

/** Un dominio semántico registrado en brain_domains */
export const brainDomainRecord = z.object({
  id:           z.number().int(),
  domain_key:   z.string(),
  label:        z.string(),
  description:  z.string().optional().nullable(),
  signal_tags:  z.array(z.string()).default([]),
  signal_kw:    z.array(z.string()).default([]),
  scope:        z.enum(["global", "space"]).default("space"),
  is_active:    z.boolean().default(true),
  search_space_id: z.number().int().optional().nullable(),
});
export type BrainDomainRecord = z.infer<typeof brainDomainRecord>;
export const brainDomainListResponse = z.array(brainDomainRecord);

/** Un tipo de documento registrado en brain_doc_types */
export const brainDocTypeRecord = z.object({
  id:               z.number().int(),
  type_key:         z.string(),
  label:            z.string(),
  description:      z.string().optional().nullable(),
  signal_formats:   z.array(z.string()).default([]),
  signal_kw:        z.array(z.string()).default([]),
  scope:            z.enum(["global", "space"]).default("space"),
  is_active:        z.boolean().default(true),
  search_space_id:  z.number().int().optional().nullable(),
});
export type BrainDocTypeRecord = z.infer<typeof brainDocTypeRecord>;
export const brainDocTypeListResponse = z.array(brainDocTypeRecord);

/** Un hint de entidad registrado en brain_entity_hints */
export const brainEntityHintRecord = z.object({
  id:           z.number().int(),
  hint_key:     z.string(),
  label:        z.string(),
  domain_key:   z.string().optional().nullable(),
  doc_type_key: z.string().optional().nullable(),
  patterns:     z.array(z.string()).default([]),
  examples:     z.array(z.string()).default([]),
  is_active:    z.boolean().default(true),
  search_space_id: z.number().int().optional().nullable(),
});
export type BrainEntityHintRecord = z.infer<typeof brainEntityHintRecord>;
export const brainEntityHintListResponse = z.array(brainEntityHintRecord);

/** Una entrada canónica del vocabulario en brain_vocabulary */
export const brainVocabularyRecord = z.object({
  id:             z.number().int(),
  canonical_tag:  z.string(),
  aliases:        z.array(z.string()).default([]),
  scope:          z.enum(["global", "space"]).default("space"),
  is_active:      z.boolean().default(true),
  search_space_id: z.number().int().optional().nullable(),
});
export type BrainVocabularyRecord = z.infer<typeof brainVocabularyRecord>;
export const brainVocabularyListResponse = z.array(brainVocabularyRecord);

/** Respuesta del lookup de alias → canónica */
export const vocabularyLookupResponse = z.object({
  canonical_tag:  z.string().optional().nullable(),
  aliases:        z.array(z.string()).default([]),
  found:          z.boolean(),
});
export type VocabularyLookupResponse = z.infer<typeof vocabularyLookupResponse>;

