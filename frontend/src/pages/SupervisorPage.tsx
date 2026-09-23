import { useState } from 'react'
import { useNavigate } from 'react-router'
import { api, type Channel, type SessionSummary } from '../api'
import { FlaggedQueue } from '../components/supervisor/FlaggedQueue'
import { SessionOutcome } from '../components/supervisor/SessionOutcome'
import { StatsBreakdown, StatsKpis } from '../components/supervisor/StatsPanel'
import { Badge, Empty, ErrorBox, LangBadge, Panel, ScenarioChip, TotalLatency } from '../components/ui'
import { useDebounced, useResource } from '../hooks/useResource'
import { useCatalog } from '../lib/catalog-context'
import { CHANNEL_LABEL, dateTime, duration } from '../lib/format'
import { useLiveEvents } from '../lib/live-context'

const FILTERS: { id: string; label: string; test: (s: SessionSummary) => boolean }[] = [
  { id: 'all', label: 'Все', test: () => true },
  { id: 'live', label: 'Идут сейчас', test: (s) => !s.ended_at },
  { id: 'flagged', label: 'Спорные', test: (s) => s.flagged },
  { id: 'handoff', label: 'Переведены оператору', test: (s) => !!s.handoff_queue },
]

// Эти события меняют журнал или статистику
const LIVE_TYPES = new Set(['session.created', 'session.closed', 'turn.trace', 'case.created'])

/** Супервизор: сводка, журнал всех звонков, очередь спорных ходов, распределения */
export function SupervisorPage() {
  const navigate = useNavigate()
  const { client } = useCatalog()
  const sessions = useResource(() => api.sessions(), 'sessions')
  const stats = useResource(() => api.stats(), 'stats')
  const [filter, setFilter] = useState('all')
  const [channel, setChannel] = useState<Channel | ''>('')
  const [lang, setLang] = useState('')

  const reloadAll = useDebounced(() => {
    sessions.reload()
    stats.reload()
  })
  useLiveEvents(reloadAll, (e) => LIVE_TYPES.has(e.type))

  const all = sessions.data ?? []
  const scoped = all.filter((s) => (!channel || s.channel === channel) && (!lang || s.languages.includes(lang as SessionSummary['languages'][number])))
  const active = FILTERS.find((f) => f.id === filter)!
  const rows = scoped.filter(active.test).sort((a, b) => b.started_at.localeCompare(a.started_at))

  return (
    <div className="stack">
      <ErrorBox error={sessions.error ?? stats.error} />
      {stats.data && <StatsKpis stats={stats.data} />}

      <div className="split-wide">
        <Panel
          title="Журнал звонков"
          hint="Все звонки, новые сверху. Обновляется сам по живому потоку. Клик — весь диалог и трассировка каждого хода"
          actions={
            <div className="row gap">
              <select value={channel} onChange={(e) => setChannel(e.target.value as Channel | '')} title="Канал">
                <option value="">Все каналы</option>
                {Object.entries(CHANNEL_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
              <select value={lang} onChange={(e) => setLang(e.target.value)} title="Язык клиента">
                <option value="">Любой язык</option>
                <option value="ru">RU</option>
                <option value="kk">KZ</option>
                <option value="mixed">RU+KZ</option>
              </select>
            </div>
          }
        >
          <div className="tabs">
            {FILTERS.map((f) => (
              <button key={f.id} className={f.id === filter ? 'tab tab-active' : 'tab'} onClick={() => setFilter(f.id)}>
                {f.label} <span className="muted">{scoped.filter(f.test).length}</span>
              </button>
            ))}
          </div>

          {rows.length ? (
            <div className="table-scroll">
              <table className="table clickable">
                <thead>
                  <tr>
                    <th>Начало</th>
                    <th>Клиент</th>
                    <th>Сценарии</th>
                    <th className="right">Ходов</th>
                    <th>Язык</th>
                    <th className="right" title="Медиана задержки до ответа по ходам звонка">Задержка p50</th>
                    <th>Итог</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => {
                    const c = client(s.client_id)
                    return (
                      <tr
                        key={s.session_id}
                        onClick={() => navigate(`/supervisor/sessions/${encodeURIComponent(s.session_id)}`)}
                        className={`${!s.ended_at ? 'row-live' : ''} ${s.flagged ? 'row-flagged' : ''}`}
                      >
                        <td className="nowrap">
                          {dateTime(s.started_at)}
                          <div className="muted small">
                            {CHANNEL_LABEL[s.channel]} · {duration(s.started_at, s.ended_at)}
                          </div>
                        </td>
                        <td className="nowrap">
                          {c?.full_name ?? <span className="muted">не опознан</span>}
                          <div className="muted small">{s.caller_phone ?? c?.phone ?? ''}</div>
                        </td>
                        <td>
                          <span className="row gap wrap">
                            {s.scenarios.map((id) => (
                              <ScenarioChip key={id} id={id} />
                            ))}
                          </span>
                        </td>
                        <td className="right num">{s.turns}</td>
                        <td>
                          <span className="row gap wrap">
                            {s.languages.map((l) => (
                              <LangBadge key={l} lang={l} />
                            ))}
                          </span>
                        </td>
                        <td className="right">
                          <TotalLatency value={s.latency_total_p50} />
                        </td>
                        <td>
                          <span className="row gap wrap">
                            <SessionOutcome s={s} />
                            {s.flagged && <Badge tone="warn">спорный</Badge>}
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty>{sessions.data ? 'Нет звонков по этому фильтру' : 'Загрузка…'}</Empty>
          )}
        </Panel>

        {stats.data && <FlaggedQueue turns={stats.data.flagged_turns} />}
      </div>

      {stats.data && <StatsBreakdown stats={stats.data} />}
    </div>
  )
}
