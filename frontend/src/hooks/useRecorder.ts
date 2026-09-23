import { useCallback, useRef, useState } from 'react'

// Push-to-talk: start() на нажатие, stop() на отпускание — отдаёт записанную реплику.
// TODO: VAD / потоковая отправка чанков, если бэкенд поддержит стриминг.
export function useRecorder() {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const starting = useRef<Promise<void> | null>(null)
  const chunks = useRef<Blob[]>([])

  const start = useCallback(() => {
    setError(null)
    starting.current = (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const rec = new MediaRecorder(stream)
        chunks.current = []
        rec.ondataavailable = (e) => chunks.current.push(e.data)
        rec.start()
        recorder.current = rec
        setRecording(true)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Нет доступа к микрофону')
      }
    })()
    return starting.current
  }, [])

  const stop = useCallback(async (): Promise<Blob | null> => {
    await starting.current // кнопку могли отпустить раньше, чем микрофон успел включиться
    const rec = recorder.current
    if (!rec || rec.state === 'inactive') return null
    return new Promise((resolve) => {
      rec.onstop = () => {
        rec.stream.getTracks().forEach((t) => t.stop())
        recorder.current = null
        setRecording(false)
        resolve(new Blob(chunks.current, { type: rec.mimeType }))
      }
      rec.stop()
    })
  }, [])

  return { recording, error, start, stop }
}
