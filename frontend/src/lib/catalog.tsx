import { useMemo, type ReactNode } from 'react'
import { api } from '../api'
import { useResource } from '../hooks/useResource'
import { CatalogContext, type CatalogCtx } from './catalog-context'
import { useLiveEvents } from './live-context'

/** Каталог сценариев и клиенты: нужны почти везде, чтобы показывать названия вместо SC17 и C007 */
export function CatalogProvider({ children }: { children: ReactNode }) {
  const catalog = useResource(() => api.catalog(), 'catalog')
  const clients = useResource(() => api.clients(), 'clients')
  // Версия каталога меняется после применения патча, отката или заморозки
  useLiveEvents(catalog.reload, (e) => e.type === 'catalog.changed')

  const value = useMemo<CatalogCtx>(() => {
    const byId = new Map((catalog.data?.scenarios ?? []).map((s) => [s.scenario_id, s]))
    const clientById = new Map((clients.data ?? []).map((c) => [c.client_id, c]))
    return {
      catalog: catalog.data,
      error: catalog.error ?? clients.error,
      scenario: (id) => byId.get(id),
      scenarioName: (id) => byId.get(id)?.name_ru ?? id,
      client: (id) => (id ? clientById.get(id) : undefined),
      clients: clients.data ?? [],
    }
  }, [catalog.data, catalog.error, clients.data, clients.error])

  return <CatalogContext.Provider value={value}>{children}</CatalogContext.Provider>
}
