import { useState } from 'react'
import { useNavigate } from 'react-router'
import { api, type CallSummary, type Lang } from '../api'
import { ScenarioPath } from '../components/supervisor/ScenarioPath'
import { StatsPanel } from '../components/supervisor/StatsPanel'
import { Empty, FlagBadges, LangBadge, OutcomeBadge, Panel } from '../components/ui'
import { usePolling } from '../hooks/usePolling'
import { duration, ms, time } from '../lib/format'

const FILTERS: { id: string; label: string; test: (c: CallSummary) => boolean }[] = [
  { id: 'all', label: 'Все', test: () => true },
  { id: 'active', label: 'Идут сейчас', test: (c) => c.outcome === 'active' },
  { id: 'problems', label: 'С проблемами', test: (c) => c.flags.length > 0 || c.outcome === 'unresolved' },
  { id: 'handed_off', label: 'Оператор', test: (c) => c.outcome === 'handed_off' },
  { id: 'unresolved', label: 'Не решены', test: (c) => c.outcome === 'unresolved' },
  { id: 'wrong', label: 'Размечены ошибки', test: (c) => c.flags.includes('marked_wrong') },
]

/** Журнал супервизора: все звонки подряд, проблемные подсвечены флагами. Клик — карточка звонка */
export function SupervisorPage() {
  const navigate = useNavigate()
  const [filter, setFilter] = useState('all')
  const [lang, setLang] = useState<Lang | ''>('')
  const calls = usePolling(() => api.listCalls(), 'calls')
  const stats = usePolling(() => api.getStats(), 'stats')

  const all = calls.data ?? []
  const byLang = lang ? all.filter((c) => c.langs.includes(lang)) : all
  const active = FILTERS.find((f) => f.id === filter)!
  const rows = byLang.filter(active.test)

  return (
    <div className="stack">
      <StatsPanel stats={stats.data} />

      <Panel
        title="Журнал звонков"
        hint="Все звонки, новые сверху. Обновляется сам. Клик по строке — весь диалог и трассировка каждой реплики"
        actions={
          <select value={lang} onChange={(e) => setLang(e.target.value as Lang | '')} title="Язык клиента">
            <option value="">Любой язык</option>
            <option value="ru">RU</option>
            <option value="kk">KZ</option>
            <option value="mixed">RU+KZ</option>
          </select>
        }
      >
        <div className="tabs">
          {FILTERS.map((f) => (
            <button key={f.id} className={f.id === filter ? 'tab tab-active' : 'tab'} onClick={() => setFilter(f.id)}>
              {f.label} <span className="muted">{byLang.filter(f.test).length}</span>
            </button>
          ))}
        </div>

        {calls.error && <div className="callout callout-bad">{calls.error}</div>}

        {rows.length ? (
          <table className="table clickable">
            <thead>
              <tr>
                <th>Начало</th>
                <th>Клиент</th>
                <th>Путь по сценариям</th>
                <th>Реплик</th>
                <th>Язык</th>
                <th title="Среднее / максимальное время от конца реплики до ответа">Ответ ср / макс</th>
                <th>Итог</th>
                <th>Флаги</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr
                  key={c.session.id}
                  onClick={() => navigate(`/supervisor/calls/${c.session.id}`)}
                  className={c.outcome === 'active' ? 'row-live' : ''}
                >
                  <td className="nowrap">
                    {time(c.session.started_at)}
                    <div className="muted small">{duration(c.session.started_at, c.last_activity_at)}</div>
                  </td>
                  <td className="nowrap">{c.session.client?.name ?? <span className="muted">аноним</span>}</td>
                  <td>
                    <ScenarioPath path={c.scenario_path} />
                  </td>
                  <td>
                    {c.turns_count}/{c.session.max_turns}
                  </td>
                  <td>
                    <span className="row gap wrap">
                      {c.langs.map((l) => (
                        <LangBadge key={l} lang={l} />
                      ))}
                    </span>
                  </td>
                  <td className="nowrap">
                    {ms(c.avg_total_ms)} / <span className={c.max_total_ms > 1500 ? 'text-bad' : ''}>{ms(c.max_total_ms)}</span>
                  </td>
                  <td>
                    <OutcomeBadge outcome={c.outcome} />
                  </td>
                  <td>
                    <FlagBadges flags={c.flags} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>{calls.data ? 'Нет звонков по этому фильтру' : 'Загрузка…'}</Empty>
        )}
      </Panel>
    </div>
  )
}
