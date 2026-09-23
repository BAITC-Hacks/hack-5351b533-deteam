// Контракт между фронтендом и бэкендом Voice Router.
// Бэкенд возвращает ровно эти структуры (snake_case), фронт их только отображает.
// Если стартовый кит или бэкенд диктуют другие поля — правим здесь, остальное подтянется по типам.

export type Lang = 'ru' | 'kk' | 'mixed'

/** fast — быстрый путь для очевидных запросов, llm — LLM-слой для сложных */
export type RoutePath = 'fast' | 'llm'

/** Что робот решил сделать на этой реплике */
export type Decision =
  | 'run_scenario' // запустить сценарий
  | 'switch_scenario' // клиент сменил тему — переключаемся
  | 'resume_scenario' // возврат к прерванной теме
  | 'clarify' // не уверен — переспрашивает вместо угадывания
  | 'handoff' // передача оператору вместе с контекстом

export type Emotion = 'neutral' | 'positive' | 'negative'

export interface Client {
  id: string
  name: string
  summary?: string // коротко: какие полисы, что в mock_backend
}

export interface ScenarioRef {
  id: string
  title: string
}

export interface ScenarioCandidate extends ScenarioRef {
  confidence: number // 0..1
  reason: string // почему выбран / почему отвергнут
}

/** Задержки по этапам, мс. Ориентиры из ТЗ: routing ≤ 500, total ≤ 1500 */
export interface Latency {
  stt_ms?: number
  routing_ms: number
  response_ms?: number // генерация текста ответа
  tts_ms?: number // до первого звука
  total_ms: number // от конца реплики до начала ответа
}

export interface DialogContext {
  active: ScenarioRef | null
  interrupted: ScenarioRef[] // стек прерванных тем, к которым можно вернуться
  queued: ScenarioRef[] // темы, названные в одной реплике, но ещё не обработанные
}

export interface TurnTrace {
  route_path: RoutePath
  decision: Decision
  selected: ScenarioCandidate | null
  alternatives: ScenarioCandidate[]
  rationale: string // обоснование выбора для супервизора
  params: Record<string, string | number | null> // извлечённые из речи параметры
  missing_params: string[]
  context: DialogContext
  pending_confirmation: { action: string; description: string } | null // необратимое действие ждёт «да» клиента
  handoff: { reason: string } | null
  latency: Latency
  model?: string
}

export interface Turn {
  id: string
  session_id: string
  index: number // 1..10
  created_at: string
  user: { text: string; lang: Lang; input: 'voice' | 'text'; emotion?: Emotion }
  bot: { text: string; lang: Lang; audio_url: string | null }
  trace: TurnTrace
  feedback?: TurnFeedback
}

export type SessionStatus = 'active' | 'ended' | 'handed_off'

export interface Session {
  id: string
  client: Client | null
  status: SessionStatus
  started_at: string
  max_turns: number
}

export type TurnInput = { kind: 'audio'; audio: Blob } | { kind: 'text'; text: string }

export interface ScenarioParam {
  name: string
  description: string
  required: boolean
}

/** Элемент каталога (scenarios.json из стартового кита) */
export interface Scenario {
  id: string
  title: string
  description: string
  boundaries: string // чем отличается от соседних сценариев
  params: ScenarioParam[]
  actions: string[]
  irreversible: boolean // требует подтверждения клиента
  examples: { ru: string[]; kk: string[] }
}

export interface TurnFeedback {
  correct: boolean
  expected_scenario_id?: string
  comment?: string
}

export interface SupervisorStats {
  sessions: number
  turns: number
  accuracy: number | null // по разметке супервизора
  avg_routing_ms: number
  p95_routing_ms: number
  avg_total_ms: number
  fast_path_share: number
  clarify_rate: number
  handoff_rate: number
  low_confidence_rate: number
  confusions: { expected: ScenarioRef; predicted: ScenarioRef; count: number }[]
}

export type TurnFilter = 'all' | 'low_confidence' | 'handoff' | 'clarify' | 'marked_wrong' | 'slow'

export interface VoiceRouterApi {
  listClients(): Promise<Client[]>
  listScenarios(): Promise<Scenario[]>
  startSession(clientId: string | null): Promise<Session>
  sendTurn(sessionId: string, input: TurnInput): Promise<Turn>
  endSession(sessionId: string): Promise<void>
  getStats(): Promise<SupervisorStats>
  listTurns(filter: TurnFilter): Promise<Turn[]>
  sendFeedback(turnId: string, feedback: TurnFeedback): Promise<void>
}

export const LOW_CONFIDENCE = 0.6
export const TARGET_ROUTING_MS = 500
export const TARGET_TOTAL_MS = 1500
