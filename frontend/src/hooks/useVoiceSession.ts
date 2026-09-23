import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { wsUrl, type VoiceEvent } from '../api'
import { startCapture, type Capture } from '../audio/capture'
import { PcmPlayer } from '../audio/player'
import { callReducer, initialCall } from './voiceState'

export type MicMode = 'open' | 'ptt'

export interface StartOptions {
  callerPhone: string | null
  /** Только для мок-сервера: какой диалог из fixtures проигрывать */
  fixture?: string
}

/**
 * Голосовая сессия по WS /ws/voice (docs/frontend/README.md):
 * session.start → кадры PCM16 16 kHz с микрофона → события хода → аудио ответа 24 kHz.
 */
export function useVoiceSession() {
  const [state, dispatch] = useReducer(callReducer, initialCall)
  const [mode, setMode] = useState<MicMode>('open')
  const [bargeIn, setBargeIn] = useState(true)
  const [muted, setMutedState] = useState(false)
  const [pttDown, setPttDown] = useState(false)
  const [level, setLevel] = useState(0)
  const [micError, setMicError] = useState<string | null>(null)
  const [botSpeaking, setBotSpeaking] = useState(false)
  const [micOn, setMicOn] = useState(false)

  const ws = useRef<WebSocket | null>(null)
  const player = useRef<PcmPlayer | null>(null)
  const capture = useRef<Capture | null>(null)
  const t0 = useRef(0) // performance.now() в момент t=0 сессии по часам сервера
  const sessionId = useRef('')
  const lastTurn = useRef<number | null>(null)
  // Актуальные настройки для обработчика кадров микрофона (он живёт вне рендера)
  const opts = useRef({ mode, bargeIn, muted })
  useEffect(() => {
    opts.current = { mode, bargeIn, muted }
  })
  const lastLevelAt = useRef(0)
  const talking = useRef(false) // кнопка push-to-talk зажата — ref, чтобы не зависеть от рендера

  const send = useCallback((type: string, extra: Record<string, unknown> = {}) => {
    const sock = ws.current
    if (sock?.readyState !== WebSocket.OPEN) return
    const t = t0.current ? Math.max(0, Math.round(performance.now() - t0.current)) : 0
    sock.send(JSON.stringify({ type, t, session_id: sessionId.current, turn: lastTurn.current, ...extra }))
  }, [])

  const teardown = useCallback(() => {
    capture.current?.stop()
    capture.current = null
    player.current?.close()
    player.current = null
    const sock = ws.current
    ws.current = null
    if (sock && sock.readyState <= WebSocket.OPEN) sock.close()
    setBotSpeaking(false)
    setLevel(0)
    setMicOn(false)
  }, [])

  useEffect(() => teardown, [teardown])

  const start = useCallback(
    async (o: StartOptions) => {
      teardown()
      dispatch({ kind: 'connecting' })
      setMicError(null)
      sessionId.current = ''
      t0.current = 0
      lastTurn.current = null

      const p = new PcmPlayer() // создаём по клику «Позвонить» — браузер разрешит звук
      p.onStarted = (turn) => send('playback.started', { turn, t_client_ms: Math.round(performance.now() - t0.current) })
      p.onFinished = (turn) => {
        send('playback.finished', { turn })
        setBotSpeaking(false)
      }
      player.current = p

      const params = new URLSearchParams({ channel: 'web' })
      if (o.callerPhone) params.set('caller_phone', o.callerPhone)
      if (o.fixture) params.set('fixture', o.fixture)
      const sock = new WebSocket(wsUrl(`/ws/voice?${params}`))
      sock.binaryType = 'arraybuffer'
      ws.current = sock

      sock.onopen = () => {
        sock.send(JSON.stringify({ type: 'session.start', t: 0, session_id: '', turn: null, channel: 'web', caller_phone: o.callerPhone, lang_hint: null }))
      }
      sock.onmessage = (m) => {
        if (typeof m.data !== 'string') {
          player.current?.enqueue(m.data as ArrayBuffer)
          return
        }
        let e: VoiceEvent
        try {
          e = JSON.parse(m.data)
        } catch {
          return
        }
        if (e.type === 'session.ready') {
          sessionId.current = e.session_id
          t0.current = performance.now() - (e.t ?? 0)
        }
        if (typeof e.turn === 'number') lastTurn.current = e.turn
        if (e.type === 'tts.start' && typeof e.turn === 'number') {
          player.current?.begin(e.turn, e.sample_rate as number | undefined)
          setBotSpeaking(true)
        }
        if (e.type === 'tts.end' && typeof e.turn === 'number') player.current?.end(e.turn)
        if (e.type === 'tts.interrupt') {
          player.current?.stop()
          setBotSpeaking(false)
        }
        dispatch({ kind: 'event', event: e })
      }
      sock.onerror = () => dispatch({ kind: 'error', message: 'Ошибка соединения с /ws/voice. Бэкенд или мок‑сервер запущен?' })
      sock.onclose = () => {
        if (ws.current === sock) {
          capture.current?.stop()
          capture.current = null
          setLevel(0)
          setMicOn(false)
        }
        dispatch({ kind: 'closed' })
      }

      // Микрофон: без него звонок всё равно работает через текст
      try {
        const cap = await startCapture((pcm, lvl) => {
          const now = performance.now()
          if (now - lastLevelAt.current > 80) {
            lastLevelAt.current = now
            setLevel(lvl)
          }
          const s = opts.current
          if (s.muted) return
          if (s.mode === 'ptt' && !talking.current) return
          if (!s.bargeIn && player.current?.playing) return // перебивание выключено: пока бот говорит, не шлём
          if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(pcm)
        })
        // Пока браузер спрашивал доступ к микрофону, звонок могли уже завершить
        if (ws.current !== sock || sock.readyState > WebSocket.OPEN) {
          cap.stop()
          return
        }
        capture.current = cap
        setMicOn(true)
      } catch (e) {
        setMicError(e instanceof Error ? e.message : String(e))
      }
    },
    [send, teardown],
  )

  const end = useCallback(() => {
    send('session.end')
    setTimeout(teardown, 300)
  }, [send, teardown])

  const sendText = useCallback(
    (text: string) => {
      dispatch({ kind: 'typed', text })
      send('text.input', { text })
    },
    [send],
  )

  const pressTalk = useCallback(() => {
    talking.current = true
    setPttDown(true)
  }, [])
  const releaseTalk = useCallback(() => {
    if (!talking.current) return
    talking.current = false
    setPttDown(false)
    send('input.commit') // явный конец реплики — сервер не ждёт тишины
  }, [send])

  const interrupt = useCallback(() => {
    player.current?.stop()
    setBotSpeaking(false)
    send('control.interrupt')
  }, [send])

  const setMuted = useCallback(
    (m: boolean) => {
      setMutedState(m)
      send('control.mute', { muted: m })
    },
    [send],
  )

  return {
    state,
    start,
    end,
    sendText,
    mode,
    setMode,
    bargeIn,
    setBargeIn,
    muted,
    setMuted,
    pttDown,
    pressTalk,
    releaseTalk,
    interrupt,
    level,
    micError,
    botSpeaking,
    micOn,
  }
}
