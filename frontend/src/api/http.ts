import type { BenchRun, Case, CaseCreate, Catalog, CatalogVersion, Client, Patch, SessionDetail, SessionSummary, Stats, VoiceEvent } from './types'

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
  /** Все события сессии для реплея (JSON Lines) */
  sessionEvents: async (id: string): Promise<VoiceEvent[]> => {
    const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/events`)
    if (!res.ok) throw new Error(`GET /api/sessions/${id}/events → ${res.status}`)
    return (await res.text())
      .split(/\r?\n/)
      .filter((l) => l.trim())
      .map((l) => JSON.parse(l) as VoiceEvent)
  },
  cases: () => request<Case[]>('/cases'),
  createCase: (body: CaseCreate) => request<Case>('/cases', post(body)),
  deleteCase: (id: string) => request<void>(`/cases/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  // эволюция каталога
  patches: () => request<Patch[]>('/patches'),
  patch: (id: string) => request<Patch>(`/patches/${encodeURIComponent(id)}`),
  proposePatch: (caseIds: string[]) => request<Patch>('/patches', post({ case_ids: caseIds })),
  applyPatch: (id: string) => request<CatalogVersion>(`/patches/${encodeURIComponent(id)}/apply`, post({})),
  rejectPatch: (id: string) => request<Patch>(`/patches/${encodeURIComponent(id)}/reject`, post({})),
  versions: () => request<CatalogVersion[]>('/catalog/versions'),
  rollback: (version: string) => request<CatalogVersion>('/catalog/rollback', post({ version })),
  freeze: (frozen: boolean) => request<CatalogVersion>('/catalog/freeze', post({ frozen })),
  benchRuns: () => request<BenchRun[]>('/bench/runs'),
  startBench: () => request<{ run_id: string }>('/bench', post({ include_cases: true })),
}

/** URL вебсокета на том же хосте, что и страница (в dev его проксирует Vite) */
export function wsUrl(path: string) {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
}
