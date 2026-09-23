import { useEffect, useState } from 'react'
import { api, type Scenario, type SupervisorStats, type Turn, type TurnFilter } from '../api'
import { TracePanel } from '../components/trace/TracePanel'
import { ConfidenceBadge, DecisionBadge, Empty, Kpi, LangBadge, Panel, RouteBadge } from '../components/ui'
import { ms, pct, time } from '../lib/format'

const FILTERS: { id: TurnFilter; label: string }[] = [
  { id: 'all', label: 'Все' },
  { id: 'low_confidence', label: 'Сомневался' },
  { id: 'clarify', label: 'Переспросил' },
  { id: 'handoff', label: 'Оператор' },
  { id: 'marked_wrong', label: 'Ошибки' },
  { id: 'slow', label: 'Медленные' },
]

/** Супервизор: общая картина, журнал реплик и разметка ошибок робота */
export function SupervisorPage() {
  const [stats, setStats] = useState<SupervisorStats | null>(null)
  const [filter, setFilter] = useState<TurnFilter>('all')
  const [turns, setTurns] = useState<Turn[]>([])
  const [selected, setSelected] = useState<Turn | null>(null)
  const [scenarios, setScenarios] = useState<Scenario[]>([])

  const [version, setVersion] = useState(0)
  const reload = () => setVersion((v) => v + 1)

  useEffect(() => {
    let alive = true
    Promise.all([api.getStats(), api.listTurns(filter)]).then(([s, t]) => {
      if (!alive) return
      setStats(s)
      setTurns(t)
      setSelected((cur) => t.find((x) => x.id === cur?.id) ?? null)
    })
    return () => {
      alive = false
    }
  }, [filter, version])
  useEffect(() => {
    api.listScenarios().then(setScenarios)
  }, [])

  async function mark(turn: Turn, correct: boolean, expected?: string) {
    await api.sendFeedback(turn.id, { correct, expected_scenario_id: expected })
    reload()
  }

  return (
    <div className="stack">
      <Panel title="Сводка" hint="Как робот справляется в целом. Точность — по разметке супервизора">
        {stats ? (
          <div className="kpis">
            <Kpi label="Сессий" value={stats.sessions} />
            <Kpi label="Реплик" value={stats.turns} />
            <Kpi label="Точность" value={stats.accuracy === null ? '—' : pct(stats.accuracy)} hint="Доля реплик, размеченных как верные" />
            <Kpi label="Выбор сценария, ср." value={ms(stats.avg_routing_ms)} tone={stats.avg_routing_ms > 500 ? 'bad' : 'ok'} />
            <Kpi label="Выбор сценария, p95" value={ms(stats.p95_routing_ms)} tone={stats.p95_routing_ms > 500 ? 'warn' : 'ok'} />
            <Kpi label="До ответа, ср." value={ms(stats.avg_total_ms)} tone={stats.avg_total_ms > 1500 ? 'bad' : 'ok'} />
            <Kpi label="Быстрый путь" value={pct(stats.fast_path_share)} hint="Доля реплик, обработанных без LLM" />
            <Kpi label="Сомнения" value={pct(stats.low_confidence_rate)} />
            <Kpi label="Переспросы" value={pct(stats.clarify_rate)} />
            <Kpi label="Оператор" value={pct(stats.handoff_rate)} />
          </div>
        ) : (
          <Empty>Загрузка…</Empty>
        )}
        {stats && stats.confusions.length > 0 && (
          <table className="table confusions">
            <thead>
              <tr>
                <th>Ожидался сценарий</th>
                <th>Робот выбрал</th>
                <th>Раз</th>
              </tr>
            </thead>
            <tbody>
              {stats.confusions.map((c) => (
                <tr key={c.expected.id + c.predicted.id}>
                  <td>{c.expected.title}</td>
                  <td>{c.predicted.title}</td>
                  <td>{c.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <div className="split">
        <Panel
          title="Журнал реплик"
          hint="Где робот сомневался, переспрашивал, звал оператора или ошибся. Клик — трассировка"
          actions={<button onClick={reload}>Обновить</button>}
        >
          <div className="tabs">
            {FILTERS.map((f) => (
              <button key={f.id} className={f.id === filter ? 'tab tab-active' : 'tab'} onClick={() => setFilter(f.id)}>
                {f.label}
              </button>
            ))}
          </div>
          {turns.length ? (
            <table className="table clickable">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Реплика</th>
                  <th>Решение</th>
                  <th>Сценарий</th>
                  <th>До ответа</th>
                </tr>
              </thead>
              <tbody>
                {turns.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => setSelected(t)}
                    className={`${t.id === selected?.id ? 'row-selected' : ''} ${t.feedback?.correct === false ? 'row-wrong' : ''}`}
                  >
                    <td className="muted small">{time(t.created_at)}</td>
                    <td>
                      <LangBadge lang={t.user.lang} /> {t.user.text}
                    </td>
                    <td>
                      <DecisionBadge decision={t.trace.decision} /> <RouteBadge path={t.trace.route_path} />
                    </td>
                    <td>
                      {t.trace.selected ? (
                        <>
                          {t.trace.selected.title} <ConfidenceBadge value={t.trace.selected.confidence} />
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{ms(t.trace.latency.total_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>Нет реплик по этому фильтру</Empty>
          )}
        </Panel>

        <TracePanel
          turn={selected}
          extra={
            selected && (
              <div className="feedback">
                <span className="muted small">Разметка:</span>
                {selected.feedback && (
                  <span className={selected.feedback.correct ? 'text-ok' : 'text-bad'}>
                    {selected.feedback.correct ? 'верно' : 'ошибка'}
                  </span>
                )}
                <button onClick={() => mark(selected, true)}>✓ Верно</button>
                <select
                  value=""
                  onChange={(e) => e.target.value && mark(selected, false, e.target.value)}
                  title="Выберите, какой сценарий был правильным"
                >
                  <option value="">✗ Ошибка — правильный сценарий…</option>
                  {scenarios.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title}
                    </option>
                  ))}
                </select>
              </div>
            )
          }
        />
      </div>
    </div>
  )
}
