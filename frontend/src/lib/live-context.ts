import { createContext, useContext, useEffect, useRef } from 'react'
import type { SupervisorEvent } from '../api'

export type Listener = (e: SupervisorEvent) => void
export type LiveStatus = 'connecting' | 'open' | 'closed'

export const LiveContext = createContext<{ status: LiveStatus; subscribe: (fn: Listener) => () => void } | null>(null)

function useLive() {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive вне LiveProvider')
  return ctx
}

export const useLiveStatus = () => useLive().status

/** Подписка на события супервизорского потока. filter — какие события интересны */
export function useLiveEvents(onEvent: Listener, filter: (e: SupervisorEvent) => boolean = () => true) {
  const { subscribe } = useLive()
  const handler = useRef(onEvent)
  const test = useRef(filter)
  useEffect(() => {
    handler.current = onEvent
    test.current = filter
  })
  useEffect(() => subscribe((e) => test.current(e) && handler.current(e)), [subscribe])
}
