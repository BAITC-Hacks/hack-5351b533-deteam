import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, type Client } from '../../api'
import type { useCallSession } from '../../hooks/useCallSession'
import { useRecorder } from '../../hooks/useRecorder'
import { Badge, Panel } from '../ui'
import { DialogView } from './DialogView'

type Call = ReturnType<typeof useCallSession>

const STATUS_LABEL = {
  idle: 'Готов слушать',
  listening: 'Слушаю…',
  thinking: 'Думаю…',
  speaking: 'Отвечаю…',
} as const

const SESSION_LABEL = { active: 'идёт звонок', ended: 'звонок завершён', handed_off: 'передан оператору' } as const

/** Левая часть симулятора: звонок глазами клиента */
export function CallPanel({ call }: { call: Call }) {
  const [clients, setClients] = useState<Client[]>([])
  const [clientId, setClientId] = useState<string>('')
  const [text, setText] = useState('')
  const recorder = useRecorder()
  const pressed = useRef(false)

  useEffect(() => {
    api.listClients().then(setClients)
  }, [])

  const busy = call.status === 'thinking' || call.status === 'speaking'
  const active = call.session?.status === 'active'

  function micDown() {
    if (!call.canSpeak || busy) return
    pressed.current = true
    call.setStatus('listening')
    recorder.start()
  }

  async function micUp() {
    if (!pressed.current) return
    pressed.current = false
    const audio = await recorder.stop()
    if (audio) await call.send({ kind: 'audio', audio })
    else call.setStatus('idle')
  }

  async function submitText(e: FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    const value = text.trim()
    setText('')
    await call.send({ kind: 'text', text: value })
  }

  return (
    <Panel
      title="Звонок клиента"
      hint="Симуляция звонка в контакт-центр: говорите в микрофон (основной канал) или пишите текстом"
      className="call"
      actions={
        <div className="row gap">
          <select value={clientId} onChange={(e) => setClientId(e.target.value)} disabled={active}>
            <option value="">Без клиента</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id} title={c.summary}>
                {c.name}
              </option>
            ))}
          </select>
          {active ? (
            <button onClick={call.end}>Завершить</button>
          ) : (
            <button className="primary" onClick={() => call.start(clientId || null)}>
              {call.session ? 'Новый звонок' : 'Начать звонок'}
            </button>
          )}
        </div>
      }
    >
      {call.session && (
        <div className="row between call-status">
          <span>
            <Badge tone={active ? 'ok' : call.session.status === 'handed_off' ? 'bad' : 'neutral'}>
              {SESSION_LABEL[call.session.status]}
            </Badge>{' '}
            {call.session.client && <span className="muted">{call.session.client.name}</span>}
          </span>
          <span className="muted small">
            реплик {call.turns.length}/{call.session.max_turns}
          </span>
        </div>
      )}

      <DialogView turns={call.turns} selectedId={call.selectedTurn?.id ?? null} onSelect={call.selectTurn} />

      {call.error && <div className="callout callout-bad">{call.error}</div>}
      {recorder.error && <div className="callout callout-bad">Микрофон: {recorder.error}</div>}

      <div className="composer">
        <button
          className={`mic mic-${call.status}`}
          disabled={!call.canSpeak || busy}
          onPointerDown={micDown}
          onPointerUp={micUp}
          onPointerLeave={micUp}
          title="Удерживайте, пока говорите"
        >
          🎤
        </button>
        <div className="composer-main">
          <div className="muted small">
            {call.session ? STATUS_LABEL[call.status] : 'Начните звонок'} · удерживайте микрофон, пока говорите
          </div>
          <form onSubmit={submitText} className="row gap">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Или напишите реплику (резервный канал)…"
              disabled={!call.canSpeak || busy}
            />
            <button type="submit" disabled={!call.canSpeak || busy || !text.trim()}>
              Отправить
            </button>
          </form>
        </div>
        <label className="small muted" title="Не озвучивать ответы робота">
          <input type="checkbox" checked={call.muted} onChange={(e) => call.setMuted(e.target.checked)} /> без звука
        </label>
      </div>
    </Panel>
  )
}
