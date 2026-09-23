import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Загрузка данных по ключу. reload() — перезапросить (например, по событию из /ws/supervisor).
 * Старые данные остаются на экране, пока идёт перезапрос — без мигания.
 */
export function useResource<T>(load: () => Promise<T>, key: string) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const loader = useRef(load)
  useEffect(() => {
    loader.current = load
  })

  useEffect(() => {
    let alive = true
    loader.current().then(
      (d) => {
        if (!alive) return
        setData(d)
        setError(null)
      },
      (e) => alive && setError(e instanceof Error ? e.message : String(e)),
    )
    return () => {
      alive = false
    }
  }, [key, version])

  const reload = useCallback(() => setVersion((v) => v + 1), [])
  return { data, error, reload }
}

/** Схлопывает пачку вызовов в один через delay мс — чтобы поток событий не заваливал REST */
export function useDebounced(fn: () => void, delay = 400) {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const latest = useRef(fn)
  useEffect(() => {
    latest.current = fn
  })
  useEffect(() => () => clearTimeout(timer.current), [])
  return useCallback(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => latest.current(), delay)
  }, [delay])
}
