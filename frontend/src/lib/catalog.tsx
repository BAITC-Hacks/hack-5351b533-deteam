import { useMemo, useState, type ReactNode } from 'react'
import { api, type CatalogVersion } from '../api'
import { useResource } from '../hooks/useResource'
import { CatalogContext, type CatalogCtx } from './catalog-context'
import { useLiveEvents } from './live-context'

/** Каталог сценариев и клиенты: нужны почти везде, чтобы показывать названия вместо SC17 и C007 */
export function CatalogProvider({ children }: { children: ReactNode }) {
  const catalog = useResource(() => api.catalog(), 'catalog')
  const clients = useResource(() => api.clients(), 'clients')
  // Последнее известное состояние версии из catalog.changed и ответов freeze/apply/rollback.
  // С настоящим бэкендом совпадает с GET /api/catalog; мок отдаёт каталог статично — без этого заморозка «не снимается»
  const [latest, setLatest] = useState<Partial<CatalogVersion> | null>(null)

  useLiveEvents(
    (e) => {
      const version = e.version as string
      setLatest((l) => ({ ...(l?.version === version ? l : {}), version, parent: (e.parent as string) ?? null, frozen: e.frozen as boolean }))
      catalog.reload()
    },
    (e) => e.type === 'catalog.changed',
  )

  const value = useMemo<CatalogCtx>(() => {
    const byId = new Map((catalog.data?.scenarios ?? []).map((s) => [s.scenario_id, s]))
    const clientById = new Map((clients.data ?? []).map((c) => [c.client_id, c]))
    const base = catalog.data
    const merged = base && latest ? { ...base, version: { ...base.version, ...latest, hash: latest.hash ?? (latest.version === base.version.version ? base.version.hash : '—') } } : base
    return {
      catalog: merged,
      error: catalog.error ?? clients.error,
      scenario: (id) => byId.get(id),
      scenarioName: (id) => byId.get(id)?.name_ru ?? id,
      client: (id) => (id ? clientById.get(id) : undefined),
      clients: clients.data ?? [],
      reload: catalog.reload,
      setVersion: (v) => setLatest((l) => ({ ...l, ...v })),
    }
  }, [catalog.data, catalog.error, catalog.reload, clients.data, clients.error, latest])

  return <CatalogContext.Provider value={value}>{children}</CatalogContext.Provider>
}
