import type {
  Client,
  Scenario,
  Session,
  SupervisorStats,
  Turn,
  TurnFeedback,
  TurnFilter,
  TurnInput,
  VoiceRouterApi,
} from './types'

// Все запросы идут на /api, в dev Vite проксирует их на бэкенд (см. vite.config.ts)
const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init)
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} → ${res.status} ${await res.text()}`)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const httpApi: VoiceRouterApi = {
  listClients: () => request<Client[]>('/clients'),
  listScenarios: () => request<Scenario[]>('/scenarios'),
  startSession: (clientId) => request<Session>('/sessions', json({ client_id: clientId })),

  sendTurn(sessionId, input: TurnInput) {
    const path = `/sessions/${sessionId}/turns`
    if (input.kind === 'text') return request<Turn>(path, json({ text: input.text }))
    const form = new FormData()
    form.append('audio', input.audio, 'utterance.webm')
    return request<Turn>(path, { method: 'POST', body: form })
  },

  endSession: (sessionId) => request<void>(`/sessions/${sessionId}/end`, { method: 'POST' }),
  getStats: () => request<SupervisorStats>('/supervisor/stats'),
  listTurns: (filter: TurnFilter) => request<Turn[]>(`/supervisor/turns?filter=${filter}`),
  sendFeedback: (turnId, feedback: TurnFeedback) =>
    request<void>(`/turns/${turnId}/feedback`, json(feedback)),
}
