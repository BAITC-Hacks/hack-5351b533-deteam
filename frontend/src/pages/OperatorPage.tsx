import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, type HandoffEvent } from '../api'
import { Badge, Empty, ErrorBox, LangBadge, Panel, ScenarioChip } from '../components/ui'
import { Icon } from '../components/Icon'
import { useCatalog } from '../lib/catalog-context'
import { useLiveEvents } from '../lib/live-context'

interface Card {
  event: HandoffEvent
  receivedAt: number
  live: boolean // пришла только что по живому потоку
}

/**
 * Экран «живого специалиста»: карточки передачи звонка из событий handoff.
 * Оператор сразу видит резюме, клиента, темы, слоты и хвост разговора — переспрашивать не нужно.
 */
export function OperatorPage() {
  const [cards, setCards] = useState<Card[]>([])
  const [taken, setTaken] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  // Прошлые переводы: сессии с handoff_queue → их события → событие handoff
  useEffect(() => {
    let alive = true
    api
      .sessions()
      .then((list) => Promise.all(list.filter((s) => s.handoff_queue).map((s) => api.sessionEvents(s.session_id).then((ev) => ({ s, ev })))))
      .then((rows) => {
        if (!alive) return
        const past = rows.flatMap(({ s, ev }) =>
          ev.filter((e) => e.type === 'handoff').map((e) => ({ event: e as HandoffEvent, receivedAt: Date.parse(s.ended_at ?? s.started_at), live: false })),
        )
        setCards((cur) => [...cur.filter((c) => c.live), ...past.filter((p) => !cur.some((c) => c.event.session_id === p.event.session_id))])
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [])

  useLiveEvents(
    (e) => setCards((cur) => [{ event: e as HandoffEvent, receivedAt: Date.now(), live: true }, ...cur.filter((c) => c.event.session_id !== e.session_id)]),
    (e) => e.type === 'handoff',
  )

  const queue = cards.filter((c) => !taken.has(c.event.session_id)).sort((a, b) => b.receivedAt - a.receivedAt)
  const done = cards.filter((c) => taken.has(c.event.session_id))

  return (
    <div className="stack">
      <Panel title={`Очередь переводов · ${queue.length}`}>
        <ErrorBox error={error} />
        {queue.length ? (
          <div className="handoffs">
            {queue.map((c) => (
              <HandoffCard key={c.event.session_id} card={c} onTake={() => setTaken((t) => new Set(t).add(c.event.session_id))} />
            ))}
          </div>
        ) : (
          <Empty>Переводов нет. Они приходят событием handoff из живого потока</Empty>
        )}
      </Panel>
      {done.length > 0 && (
        <Panel title={`Взяты в работу · ${done.length}`}>
          <div className="handoffs">
            {done.map((c) => (
              <HandoffCard key={c.event.session_id} card={c} />
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}

function HandoffCard({ card, onTake }: { card: Card; onTake?: () => void }) {
  const { client: findClient } = useCatalog()
  const { event: e } = card
  const ctx = e.context ?? {}
  const clientId = (ctx.client?.client_id as string | undefined) ?? undefined
  const known = findClient(clientId)
  const name = (ctx.client?.full_name as string | undefined) ?? known?.full_name
  const phone = (ctx.client?.phone as string | undefined) ?? known?.phone
  const slots = Object.entries(ctx.slots ?? {})

  return (
    <article className={`handoff ${card.live ? 'handoff-live' : ''}`}>
      <header className="row between">
        <span className="row gap wrap">
          <Badge tone="bad">очередь {e.queue}</Badge>
          {card.live && <Badge tone="info">только что</Badge>}
          {ctx.language && <LangBadge lang={ctx.language} />}
          {ctx.emotion && ctx.emotion !== 'neutral' && <Badge tone="warn">эмоция: {ctx.emotion}</Badge>}
        </span>
        <span className="muted small">{new Date(card.receivedAt).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</span>
      </header>

      <div className="handoff-client">
        <strong>{name ?? 'Клиент не опознан'}</strong> {phone && <span className="muted">{phone}</span>} {clientId && <code className="muted small">{clientId}</code>}
      </div>

      <p className="handoff-summary">{e.summary}</p>

      {ctx.scenarios && ctx.scenarios.length > 0 && (
        <div className="row gap wrap">
          <span className="muted small">Темы:</span>
          {ctx.scenarios.map((id) => (
            <ScenarioChip key={id} id={id} />
          ))}
        </div>
      )}

      {slots.length > 0 && (
        <table className="kv small">
          <tbody>
            {slots.map(([k, v]) => (
              <tr key={k}>
                <td>
                  <code>{k}</code>
                </td>
                <td>{typeof v === 'string' ? v : JSON.stringify(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {ctx.transcript_tail && ctx.transcript_tail.length > 0 && (
        <div className="tail">
          {ctx.transcript_tail.map((m, i) => (
            <div key={i} className={`tail-${m.role}`}>
              <span className="muted small">{m.role === 'client' ? 'Клиент' : 'Бот'}:</span> {m.text}
            </div>
          ))}
        </div>
      )}

      <footer className="row between">
        <Link to={`/supervisor/sessions/${encodeURIComponent(e.session_id)}`} className="small">
          Весь разговор и трассировка<Icon name="arrowRight" size={15} spacing="after" />
        </Link>
        {onTake && (
          <button className="primary" onClick={onTake}>
            Взять звонок
          </button>
        )}
      </footer>
    </article>
  )
}
