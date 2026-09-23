// Типы фронта по контрактам команды. Источник правды — /contracts:
//   trace.schema.json, dialog-state.schema.json, evolution.schema.json, openapi.yaml, ws-events.schema.json
// Здесь только то, что фронт реально использует. Меняется контракт — правим здесь.

export type Lang = 'ru' | 'kk' | 'mixed'
export type ResponseLang = 'ru' | 'kk'
export type Channel = 'web' | 'phone' | 'text'

/** SC01…SC40 или SYS_OUT_OF_SCOPE / SYS_UNCLEAR / SYS_GOODBYE */
export type ScenarioId = string

export type Decision = 'run' | 'continue' | 'confirm' | 'cancel' | 'clarify' | 'handoff' | 'out_of_scope' | 'goodbye'

export type FastPath = 'slot_pattern' | 'yes_no' | 'operator_request' | 'goodbye_word' | 'fast_path_scenario'

export interface ScenarioScore {
  scenario_id: ScenarioId
  confidence: number
  name?: string
  segment?: string // кусок реплики, к которому относится сценарий
  reason?: string // на английском, до 12 слов (ROUTER.md)
  boundary_rule?: string | null
}

export interface LatencyMs {
  stt: number
  triage: number
  router: number
  response: number
  tts_first_audio: number
  total: number // от конца речи клиента до первого звука ответа
}

/** trace.schema.json — трассировка одного хода */
export interface Trace {
  turn: number
  transcript: string
  language: Lang
  scenarios: ScenarioScore[] // несколько — если в реплике несколько тем
  alternatives: ScenarioScore[]
  reason: string
  slots: Record<string, unknown>
  actions: string[] // find_client, book_appointment:preview, get_claim:error
  latency_ms: LatencyMs
  decision: Decision
  response_language: ResponseLang
  response_text: string
  response_source?: 'template' | 'llm' | 'kb_template'
  fast_path?: FastPath | null
  speculative_hit?: boolean
  second_opinion?: { model: string; scenarios: ScenarioScore[]; agreed: boolean } | null
  router_model?: string | null
  catalog_version: string
  active_scenario?: ScenarioId | null
  stack?: ScenarioId[]
  emotion?: string
  urgency?: string
  channel?: Channel
  interrupted?: boolean
  client_id?: string | null
}

/** dialog-state.schema.json */
export interface DialogState {
  session_id: string
  channel: Channel
  language: ResponseLang
  client: { client_id: string; full_name: string; phone?: string; identified_by: string } | null
  active: {
    scenario_id: ScenarioId
    name?: string
    step: string
    expected_slot?: string | null
    missing_slots?: string[]
  } | null
  stack: { scenario_id: ScenarioId; name?: string; reason: 'topic_switch' | 'multi_intent' | 'deferred_by_client'; slots?: Record<string, unknown> }[]
  slots: Record<string, unknown>
  pending_confirmation: { action: string; inputs: Record<string, unknown>; spoken_summary?: string } | null
  unclear_streak: number
  completed: ScenarioId[]
  handoff?: { queue: string } | null
}

/** GET /api/clients — персоны из mock_backend */
export interface Client {
  client_id: string
  full_name: string
  phone: string
  city?: string
  preferred_language?: string
  products?: string[]
}

export interface ScenarioBrief {
  scenario_id: ScenarioId
  name: string
  name_ru: string
  domain: string
  category: string
  priority: 'normal' | 'high' | 'urgent'
  fast_path_eligible: boolean
  requires_identification: boolean
  requires_confirmation: boolean
}

/** evolution.schema.json#/$defs/CatalogVersion */
export interface CatalogVersion {
  version: string
  parent: string | null
  hash: string
  applied_patch: string | null
  created_at: string
  frozen: boolean
  current?: boolean
  note?: string
  primary_acc?: number | null
}

/** GET /api/catalog */
export interface Catalog {
  version: CatalogVersion
  scenarios: ScenarioBrief[]
}

/** openapi SessionSummary — строка журнала */
export interface SessionSummary {
  session_id: string
  channel: Channel
  caller_phone: string | null
  client_id: string | null
  started_at: string
  ended_at: string | null // null — звонок идёт
  end_reason: string | null
  turns: number
  languages: Lang[]
  scenarios: ScenarioId[]
  handoff_queue: string | null
  latency_total_p50: number
  catalog_version: string
  flagged: boolean // ход с низкой уверенностью, SYS_UNCLEAR или ручная пометка
}

/** GET /api/sessions/{id} */
export interface SessionDetail extends SessionSummary {
  transcript: { turn: number; role: 'client' | 'bot'; text: string; lang: string }[]
  traces: Trace[]
  final_state: DialogState | null
}

export type LatencyStage = keyof LatencyMs

/** openapi Stats — агрегаты для супервизора */
export interface Stats {
  sessions: number
  turns: number
  by_scenario: { scenario_id: ScenarioId; count: number }[]
  by_language: Record<string, number>
  by_decision: Record<string, number>
  confidence_histogram: { bucket: string; count: number }[]
  latency_ms: Partial<Record<LatencyStage, { p50: number; p95: number }>>
  speculative_hit_rate: number
  template_rate: number
  handoff_rate: number
  unclear_rate: number
  flagged_turns: {
    session_id: string
    turn: number
    transcript: string
    scenario_id: ScenarioId
    confidence: number
    decision: Decision
  }[]
}

/** evolution.schema.json#/$defs/Case */
export interface Case {
  case_id: string
  source: 'supervisor' | 'synthetic' | 'dev'
  text: string
  lang: Lang
  expected: ScenarioId[]
  observed?: ScenarioId[]
  session_id?: string | null
  turn?: number | null
  note?: string
  created_at: string
}

/** POST /api/cases — «неверно» у хода */
export interface CaseCreate {
  session_id?: string
  turn?: number
  text: string
  lang?: Lang
  expected: ScenarioId[] // порядок важен: первый — основной
  note?: string
}

/** Событие WS /ws/supervisor. Конверт общий, остальные поля зависят от type */
export interface SupervisorEvent {
  type: string
  t: number
  session_id: string
  turn?: number | null
  [key: string]: unknown
}

// Пороги из концепта (ARCHITECTURE.md, docs/frontend)
export const FLAG_CONFIDENCE = 0.75 // ниже — ход спорный
export const TARGET_ROUTER_MS = 500
export const TARGET_TOTAL_MS = 1500 // зелёный
export const WARN_TOTAL_MS = 3000 // жёлтый, выше — красный
