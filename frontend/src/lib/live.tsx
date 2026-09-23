import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { wsUrl, type SupervisorEvent } from '../api'
import { LiveContext, type Listener, type LiveStatus } from './live-context'

/** Одно подключение к WS /ws/supervisor на всё приложение: живые события всех сессий, каталога и эволюции */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LiveStatus>('connecting')
  const listeners = useRef(new Set<Listener>())

  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let stopped = false

    const connect = () => {
      setStatus('connecting')
      ws = new WebSocket(wsUrl('/ws/supervisor'))
      ws.onopen = () => setStatus('open')
      ws.onmessage = (m) => {
        if (typeof m.data !== 'string') return
        try {
          const event = JSON.parse(m.data) as SupervisorEvent
          listeners.current.forEach((fn) => fn(event))
        } catch {
          // не JSON — игнорируем
        }
      }
      ws.onclose = () => {
        setStatus('closed')
        if (!stopped) retry = setTimeout(connect, 3000)
      }
    }
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      ws?.close()
    }
  }, [])

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn)
    return () => {
      listeners.current.delete(fn)
    }
  }, [])

  return <LiveContext.Provider value={{ status, subscribe }}>{children}</LiveContext.Provider>
}
