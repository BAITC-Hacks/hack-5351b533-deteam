import { httpApi } from './http'
import { mockApi } from './mock'
import type { VoiceRouterApi } from './types'

// Пока бэкенда нет — работаем на моках. Для реального бэка: VITE_USE_MOCK=false в .env.local
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const api: VoiceRouterApi = USE_MOCK ? mockApi : httpApi

export * from './types'
