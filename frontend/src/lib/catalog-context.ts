import { createContext, useContext } from 'react'
import type { Catalog, Client, ScenarioBrief } from '../api'

export interface CatalogCtx {
  catalog: Catalog | null
  error: string | null
  scenario: (id: string) => ScenarioBrief | undefined
  scenarioName: (id: string) => string
  client: (id: string | null | undefined) => Client | undefined
  clients: Client[]
}

export const CatalogContext = createContext<CatalogCtx | null>(null)

export function useCatalog() {
  const ctx = useContext(CatalogContext)
  if (!ctx) throw new Error('useCatalog вне CatalogProvider')
  return ctx
}
