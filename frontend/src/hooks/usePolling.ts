import { useEffect, useState } from 'react'

// Загрузка с автообновлением — чтобы супервизор видел идущие звонки вживую.
// TODO: заменить на WebSocket/SSE, если бэкенд будет пушить события.
export function usePolling<T>(load: () => Promise<T>, key: string, intervalMs = 2000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    let alive = true
    const tick = () =>
      load().then(
        (d) => alive && (setData(d), setError(null)),
        (e) => alive && setError(e instanceof Error ? e.message : String(e)),
      )
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
    // load пересоздаётся на каждом рендере, перезапускаемся только по key
  }, [key, version, intervalMs]) // eslint-disable-line react-hooks/exhaustive-deps

  return { data, error, reload: () => setVersion((v) => v + 1) }
}
