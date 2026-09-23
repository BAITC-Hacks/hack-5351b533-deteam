import { useState } from 'react'
import { Link } from 'react-router'
import type { Channel, SessionSummary } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { CHANNEL_LABEL, dateTime, duration } from '../../lib/format'
import { Badge, Empty, LangBadge, Panel, ScenarioChip, TotalLatency } from '../ui'
import { SessionOutcome } from './SessionOutcome'

type FilterId = 'all' | 'live' | 'flagged' | 'handoff'

const FILTERS: { id: FilterId; label: string; test: (session: SessionSummary) => boolean }[] = [
  { id: 'all', label: 'Все', test: () => true },
  { id: 'live', label: 'Идут сейчас', test: (session) => !session.ended_at },
  { id: 'flagged', label: 'Спорные', test: (session) => session.flagged },
  { id: 'handoff', label: 'Переведены оператору', test: (session) => !!session.handoff_queue },
]

/** Общий журнал супервизора: фильтры сохраняются при переходе между карточками звонков. */
export function SessionJournal({ sessions, selectedId }: { sessions: SessionSummary[] | null; selectedId?: string }) {
  const { client } = useCatalog()
  const [filter, setFilter] = useState<FilterId>('all')
  const [channel, setChannel] = useState<Channel | ''>('')
  const [lang, setLang] = useState('')
  const [query, setQuery] = useState('')

  const search = query.trim().toLocaleLowerCase()
  const searchDigits = /^[\d+()\s-]+$/.test(search) ? search.replace(/\D/g, '') : ''
  const scoped = (sessions ?? []).filter((session) => {
    if (channel && session.channel !== channel) return false
    if (lang && !session.languages.includes(lang as SessionSummary['languages'][number])) return false
    if (!search) return true

    const known = client(session.client_id)
    const phone = session.caller_phone ?? known?.phone ?? ''
    const haystack = [known?.full_name, phone, session.session_id, session.client_id]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase()
    return haystack.includes(search) || (searchDigits.length > 0 && phone.replace(/\D/g, '').includes(searchDigits))
  })
  const active = FILTERS.find((item) => item.id === filter)!
  const rows = scoped.filter(active.test).sort((a, b) => b.started_at.localeCompare(a.started_at))
  const selectedHidden = selectedId && sessions?.some((session) => session.session_id === selectedId) && !rows.some((session) => session.session_id === selectedId)

  return (
    <Panel className="journal-panel" title="Журнал звонков" hint="Выберите звонок, чтобы посмотреть диалог и решение роутера по каждому ходу">
      <div className="journal-toolbar">
        <input
          className="journal-search"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Имя, телефон или ID звонка"
          aria-label="Поиск звонков по имени клиента, телефону или ID"
        />
        <select value={channel} onChange={(event) => setChannel(event.target.value as Channel | '')} aria-label="Фильтр по каналу">
          <option value="">Все каналы</option>
          {Object.entries(CHANNEL_LABEL).map(([key, label]) => (
            <option key={key} value={key}>{label}</option>
          ))}
        </select>
        <select value={lang} onChange={(event) => setLang(event.target.value)} aria-label="Фильтр по языку клиента">
          <option value="">Любой язык</option>
          <option value="ru">RU</option>
          <option value="kk">KZ</option>
          <option value="mixed">RU+KZ</option>
        </select>
      </div>

      <div className="tabs journal-filter-row" role="group" aria-label="Статус звонков">
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={item.id === filter ? 'tab tab-active' : 'tab'}
            aria-pressed={item.id === filter}
            onClick={() => setFilter(item.id)}
          >
            {item.label} <span className="muted">{scoped.filter(item.test).length}</span>
          </button>
        ))}
      </div>

      {sessions && (
        <p className="journal-meta" role="status">
          Показано {rows.length} из {sessions.length} звонков
          {selectedHidden && <span> · открытый звонок скрыт фильтром</span>}
        </p>
      )}

      {rows.length ? (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Начало</th>
                <th>Клиент</th>
                <th>Сценарии</th>
                <th className="right">Ходов</th>
                <th>Язык</th>
                <th className="right" title="Медиана задержки до ответа по ходам звонка">Задержка p50</th>
                <th>Итог</th>
                <th>Действие</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((session) => {
                const known = client(session.client_id)
                const phone = session.caller_phone ?? known?.phone
                const isSelected = session.session_id === selectedId
                const path = `/supervisor/sessions/${encodeURIComponent(session.session_id)}`
                return (
                  <tr
                    key={session.session_id}
                    className={`${!session.ended_at ? 'row-live' : ''} ${session.flagged ? 'row-flagged' : ''} ${isSelected ? 'journal-row-current' : ''}`}
                  >
                    <td className="nowrap" data-label="Начало">
                      {dateTime(session.started_at)}
                      <div className="muted small">{CHANNEL_LABEL[session.channel]} · {duration(session.started_at, session.ended_at)}</div>
                    </td>
                    <td data-label="Клиент">
                      <Link className="session-link" to={path} aria-current={isSelected ? 'page' : undefined} aria-label={`${isSelected ? 'Открыт' : 'Открыть'} звонок ${session.session_id}: ${known?.full_name ?? 'клиент не опознан'}`}>
                        {known?.full_name ?? 'Клиент не опознан'}
                      </Link>
                      <div className="muted small">{phone && <>{phone} · </>}<code>{session.session_id}</code></div>
                    </td>
                    <td data-label="Сценарии">
                      <span className="row gap wrap">
                        {session.scenarios.map((id) => <ScenarioChip key={id} id={id} />)}
                      </span>
                    </td>
                    <td className="right num" data-label="Ходов">{session.turns}</td>
                    <td data-label="Язык">
                      <span className="row gap wrap">
                        {session.languages.map((language) => <LangBadge key={language} lang={language} />)}
                      </span>
                    </td>
                    <td className="right" data-label="Задержка p50"><TotalLatency value={session.latency_total_p50} /></td>
                    <td data-label="Итог">
                      <span className="row gap wrap">
                        <SessionOutcome s={session} />
                        {session.flagged && <Badge tone="warn">спорный</Badge>}
                      </span>
                    </td>
                    <td data-label="Действие">
                      {isSelected ? (
                        <span className="session-row-action" aria-label={`Открыт звонок ${session.session_id}`}>Открыт</span>
                      ) : (
                        <Link className="session-row-action" to={path} aria-label={`Открыть звонок ${session.session_id}`}>Открыть</Link>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>{sessions ? 'Звонков по этим условиям нет' : 'Загрузка звонков…'}</Empty>
      )}
    </Panel>
  )
}
