import { createContext, useContext } from 'react'
import type { Catalog, CatalogVersion, Client, ScenarioBrief } from '../api'

export interface CatalogCtx {
  catalog: Catalog | null
  error: string | null
  scenario: (id: string) => ScenarioBrief | undefined
  scenarioName: (id: string) => string
  client: (id: string | null | undefined) => Client | undefined
  clients: Client[]
  reload: () => void
  /** Обновить версию каталога по ответу freeze/apply/rollback */
  setVersion: (v: Partial<CatalogVersion>) => void
}

export const CatalogContext = createContext<CatalogCtx | null>(null)

export function useCatalog() {
  const ctx = useContext(CatalogContext)
  if (!ctx) throw new Error('useCatalog вне CatalogProvider')
  return ctx
}
