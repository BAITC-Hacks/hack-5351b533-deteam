import { useEffect, useState, type ReactNode } from 'react'
import { Outlet, useMatch } from 'react-router'
import { api } from '../api'
import { Icon } from '../components/Icon'
import { FlaggedQueue } from '../components/supervisor/FlaggedQueue'
import { SessionJournal } from '../components/supervisor/SessionJournal'
import { StatsBreakdown, StatsKpis } from '../components/supervisor/StatsPanel'
import { ErrorBox } from '../components/ui'
import { useDebounced, useResource } from '../hooks/useResource'
import { useLiveEvents } from '../lib/live-context'

const LIVE_TYPES = new Set(['session.created', 'session.closed', 'turn.trace', 'case.created'])

/** Единое рабочее место: карточка выбранного звонка и журнал живут на одном экране. */
export function SupervisorPage() {
  const match = useMatch('/supervisor/sessions/:id')
  const selectedId = match?.params.id
  const sessions = useResource(() => api.sessions(), 'sessions')
  const stats = useResource(() => api.stats(), 'stats')
  const [showFlagged, setShowFlagged] = useState(false)
  const [showBreakdown, setShowBreakdown] = useState(false)

  const reloadAll = useDebounced(() => {
    sessions.reload()
    stats.reload()
  })
  useLiveEvents(reloadAll, (event) => LIVE_TYPES.has(event.type))

  // Журнал находится под карточкой: после выбора другого звонка показываем его начало.
  useEffect(() => {
    if (selectedId) window.scrollTo(0, 0)
  }, [selectedId])

  return (
    <div className="stack supervisor-page">
      <div className="supervisor-header">
        <h1>{selectedId ? 'Разбор звонка' : 'Рабочая панель'}</h1>
      </div>

      <ErrorBox error={sessions.error ?? stats.error} />
      <Outlet />

      {!selectedId && stats.data && (
        <StatsKpis stats={stats.data} />
      )}

      <SessionJournal sessions={sessions.data} selectedId={selectedId} />

      {stats.data && (
        <div className="stack supervisor-tools">
          <Disclosure
            id="flagged-turns"
            title="Спорные ходы"
            count={stats.data.flagged_turns.length}
            open={showFlagged}
            onToggle={() => setShowFlagged((value) => !value)}
          >
            <FlaggedQueue turns={stats.data.flagged_turns} />
          </Disclosure>
          <Disclosure
            id="stats-breakdown"
            title="Распределения"
            open={showBreakdown}
            onToggle={() => setShowBreakdown((value) => !value)}
          >
            <StatsBreakdown stats={stats.data} />
          </Disclosure>
        </div>
      )}
    </div>
  )
}

function Disclosure({
  id, title, count, open, onToggle, children,
}: {
  id: string
  title: string
  count?: number
  open: boolean
  onToggle: () => void
  children: ReactNode
}) {
  return (
    <section className="disclosure">
      <button className="disclosure-trigger" type="button" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        <span className="disclosure-heading">
          <span className="disclosure-title">{title}</span>
          {count !== undefined && <span className={`disclosure-count ${count === 0 ? 'disclosure-count-empty' : ''}`}>{count}</span>}
        </span>
        <Icon name="chevronDown" className="disclosure-chevron" spacing="none" />
      </button>
      <div id={id} className="disclosure-content" hidden={!open}>
        {children}
      </div>
    </section>
  )
}
