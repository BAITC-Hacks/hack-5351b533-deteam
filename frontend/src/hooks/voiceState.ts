import type { DialogState, HandoffEvent, ScenarioScore, Trace, VoiceEvent } from '../api'

/** Один ход звонка, собранный из потока событий /ws/voice */
export interface LiveTurn {
  turn: number
  user: { text: string; final: boolean; lang?: string; speaking: boolean; typed: boolean } | null
  hypotheses: { partial_text: string; scenarios: ScenarioScore[] }[]
  actions: {
    action: string
    inputs: Record<string, unknown>
    status: 'preview' | 'done' | 'error'
    irreversible?: boolean
    result?: unknown
    error?: { code: string; message: string } | null
  }[]
  bot: { text: string; final: boolean; lang?: string; template?: boolean } | null
  trace: Trace | null
  speaking: boolean // бот говорит (между tts.start и tts.end)
  interrupted: boolean
}

export interface CallState {
  status: 'idle' | 'connecting' | 'live' | 'ended'
  sessionId: string
  ready: { channel: string; catalog_version: string; frozen: boolean; language: string; client: Record<string, unknown> | null } | null
  turns: LiveTurn[]
  dialog: DialogState | null
  handoff: HandoffEvent | null
  endReason: string | null
  errors: string[]
}

export const initialCall: CallState = {
  status: 'idle',
  sessionId: '',
  ready: null,
  turns: [],
  dialog: null,
  handoff: null,
  endReason: null,
  errors: [],
}

export type CallAction =
  | { kind: 'connecting' }
  | { kind: 'event'; event: VoiceEvent }
  | { kind: 'typed'; text: string }
  | { kind: 'closed'; reason?: string }
  | { kind: 'error'; message: string }

const emptyTurn = (turn: number): LiveTurn => ({
  turn,
  user: null,
  hypotheses: [],
  actions: [],
  bot: null,
  trace: null,
  speaking: false,
  interrupted: false,
})

function withTurn(state: CallState, turn: number, fn: (t: LiveTurn) => LiveTurn): CallState {
  const exists = state.turns.some((t) => t.turn === turn)
  const turns = exists ? state.turns.map((t) => (t.turn === turn ? fn(t) : t)) : [...state.turns, fn(emptyTurn(turn))].sort((a, b) => a.turn - b.turn)
  return { ...state, turns }
}

export function callReducer(state: CallState, a: CallAction): CallState {
  switch (a.kind) {
    case 'connecting':
      return { ...initialCall, status: 'connecting' }
    case 'closed':
      return state.status === 'ended' ? state : { ...state, status: 'ended', endReason: state.endReason ?? a.reason ?? 'connection_closed' }
    case 'error':
      return { ...state, errors: [...state.errors, a.message] }
    case 'typed': {
      // Текстовую реплику показываем сразу; номер хода — следующий после последнего
      const next = (state.turns.at(-1)?.turn ?? 0) + 1
      return withTurn(state, next, (t) => ({ ...t, user: { text: a.text, final: false, speaking: false, typed: true } }))
    }
    case 'event':
      return applyEvent(state, a.event)
  }
}

function applyEvent(state: CallState, e: VoiceEvent): CallState {
  const turn = typeof e.turn === 'number' ? e.turn : null
  const str = (k: string) => e[k] as string

  switch (e.type) {
    case 'session.ready':
      return {
        ...state,
        status: 'live',
        sessionId: e.session_id,
        ready: {
          channel: str('channel'),
          catalog_version: str('catalog_version'),
          frozen: Boolean(e.frozen),
          language: str('language'),
          client: (e.client as Record<string, unknown>) ?? null,
        },
      }
    case 'vad.speech_start':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, user: { text: t.user?.text ?? '', final: false, speaking: true, typed: false } }))
    case 'stt.partial':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, user: { ...(t.user ?? { final: false, speaking: true, typed: false }), text: str('text') } }))
    case 'vad.speech_end':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, user: t.user && { ...t.user, speaking: false } }))
    case 'stt.final':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, user: { text: str('text'), final: true, lang: str('lang'), speaking: false, typed: t.user?.typed ?? false } }))
    case 'router.hypothesis':
      return turn === null
        ? state
        : withTurn(state, turn, (t) => ({ ...t, hypotheses: [...t.hypotheses, { partial_text: str('partial_text'), scenarios: (e.scenarios as ScenarioScore[]) ?? [] }] }))
    case 'action.preview':
      return turn === null
        ? state
        : withTurn(state, turn, (t) => ({
            ...t,
            actions: [...t.actions, { action: str('action'), inputs: (e.inputs as Record<string, unknown>) ?? {}, status: 'preview', irreversible: Boolean(e.irreversible) }],
          }))
    case 'action.executed': {
      if (turn === null) return state
      const err = (e.error as { code: string; message: string } | null) ?? null
      const done = { action: str('action'), inputs: (e.inputs as Record<string, unknown>) ?? {}, status: err ? 'error' : 'done', result: e.result, error: err } as const
      // Подтверждённое действие превращает карточку «ждём да» из прошлого хода в «выполнено»
      const isPreview = (x: LiveTurn['actions'][number]) => x.status === 'preview' && x.action === done.action
      if (state.turns.some((t) => t.actions.some(isPreview))) {
        return { ...state, turns: state.turns.map((t) => ({ ...t, actions: t.actions.map((x) => (isPreview(x) ? { ...x, ...done } : x)) })) }
      }
      return withTurn(state, turn, (t) => ({ ...t, actions: [...t.actions, { ...done }] }))
    }
    case 'bot.text.delta':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, bot: { text: (t.bot?.text ?? '') + str('delta'), final: false, lang: t.bot?.lang } }))
    case 'bot.text.final':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, bot: { text: str('text'), final: true, lang: str('lang'), template: Boolean(e.template) } }))
    case 'tts.start':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, speaking: true }))
    case 'tts.end':
      return turn === null ? state : withTurn(state, turn, (t) => ({ ...t, speaking: false }))
    case 'tts.interrupt':
      return { ...state, turns: state.turns.map((t) => (t.speaking ? { ...t, speaking: false, interrupted: true } : t)) }
    case 'turn.trace': {
      const trace = e.trace as Trace
      return withTurn(state, trace.turn ?? turn ?? 0, (t) => ({ ...t, trace }))
    }
    case 'dialog.state':
      return { ...state, dialog: e.state as DialogState }
    case 'handoff':
      return { ...state, handoff: e as HandoffEvent }
    case 'error':
      return { ...state, errors: [...state.errors, `${str('code')}: ${str('message')}`] }
    case 'session.end':
      return { ...state, status: 'ended', endReason: str('reason') }
    default:
      return state
  }
}
