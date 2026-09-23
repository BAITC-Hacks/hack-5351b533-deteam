import type { Case, CaseCreate, Catalog, Client, SessionDetail, SessionSummary, Stats } from './types'

// REST из /contracts/openapi.yaml. В dev Vite проксирует /api и /ws на бэкенд (см. vite.config.ts)
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, init)
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} /api${path} → ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  clients: () => request<Client[]>('/clients'),
  catalog: () => request<Catalog>('/catalog'),
  stats: () => request<Stats>('/stats'),
  sessions: () => request<SessionSummary[]>('/sessions?limit=200'),
  session: (id: string) => request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),
  cases: () => request<Case[]>('/cases'),
  createCase: (body: CaseCreate) => request<Case>('/cases', post(body)),
}

/** URL вебсокета на том же хосте, что и страница (в dev его проксирует Vite) */
export function wsUrl(path: string) {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
}
