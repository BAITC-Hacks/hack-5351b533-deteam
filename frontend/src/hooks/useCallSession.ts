import { useCallback, useState } from 'react'
import { api, type Session, type Turn, type TurnInput } from '../api'
import { playBotReply, stopPlayback } from '../lib/playback'

/** idle → listening (пишем микрофон) → thinking (ждём бэкенд) → speaking (играет ответ) → idle */
export type CallStatus = 'idle' | 'listening' | 'thinking' | 'speaking'

export function useCallSession() {
  const [session, setSession] = useState<Session | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [status, setStatus] = useState<CallStatus>('idle')
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [muted, setMuted] = useState(false)

  const start = useCallback(async (clientId: string | null) => {
    setError(null)
    setTurns([])
    setSelectedTurnId(null)
    setSession(await api.startSession(clientId))
  }, [])

  const end = useCallback(async () => {
    if (!session) return
    stopPlayback()
    await api.endSession(session.id)
    setSession({ ...session, status: 'ended' })
    setStatus('idle')
  }, [session])

  const send = useCallback(
    async (input: TurnInput) => {
      if (!session) return
      setError(null)
      setStatus('thinking')
      try {
        const turn = await api.sendTurn(session.id, input)
        setTurns((prev) => [...prev, turn])
        setSelectedTurnId(turn.id)
        if (turn.trace.decision === 'handoff') setSession((s) => s && { ...s, status: 'handed_off' })
        if (!muted) {
          setStatus('speaking')
          await playBotReply(turn.bot)
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setStatus('idle')
      }
    },
    [session, muted],
  )

  const canSpeak = session?.status === 'active' && turns.length < session.max_turns
  const selectedTurn = turns.find((t) => t.id === selectedTurnId) ?? turns.at(-1) ?? null

  return {
    session,
    turns,
    status,
    setStatus,
    error,
    canSpeak,
    selectedTurn,
    selectTurn: setSelectedTurnId,
    muted,
    setMuted,
    start,
    end,
    send,
  }
}
