import { TARGET_ROUTING_MS, TARGET_TOTAL_MS, type SupervisorStats } from '../../api'
import { ms, pct } from '../../lib/format'
import { Empty, Kpi, Panel } from '../ui'

/** Общая картина по всем звонкам + какие сценарии робот путает (по разметке супервизора) */
export function StatsPanel({ stats }: { stats: SupervisorStats | null }) {
  return (
    <Panel title="Сводка" hint="По всем звонкам. Точность и путаница считаются по разметке супервизора">
      {!stats ? (
        <Empty>Загрузка…</Empty>
      ) : (
        <div className="stats">
          <div className="kpis">
            <Kpi label="Звонков" value={stats.sessions} />
            <Kpi label="Реплик" value={stats.turns} />
            <Kpi label="Решено роботом" value={pct(stats.resolved_rate)} hint="Доля завершённых звонков без оператора" />
            <Kpi label="Точность" value={stats.accuracy === null ? '—' : pct(stats.accuracy)} hint="Доля размеченных реплик, где сценарий выбран верно" />
            <Kpi label="Выбор сценария, p95" value={ms(stats.p95_routing_ms)} tone={stats.p95_routing_ms > TARGET_ROUTING_MS ? 'warn' : 'ok'} hint={`Цель ≤ ${TARGET_ROUTING_MS} мс`} />
            <Kpi label="До ответа, ср." value={ms(stats.avg_total_ms)} tone={stats.avg_total_ms > TARGET_TOTAL_MS ? 'bad' : 'ok'} hint={`Цель ≤ ${TARGET_TOTAL_MS} мс`} />
            <Kpi label="Быстрый путь" value={pct(stats.fast_path_share)} hint="Доля реплик, обработанных без LLM" />
            <Kpi label="Сомнения" value={pct(stats.low_confidence_rate)} hint="Реплики с уверенностью ниже порога" />
            <Kpi label="Переспросы" value={pct(stats.clarify_rate)} />
            <Kpi label="Оператор" value={pct(stats.handoff_rate)} />
          </div>
          <div className="confusions">
            <h3>Робот путает</h3>
            {stats.confusions.length ? (
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Нужно было</th>
                    <th>Выбрал</th>
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
            ) : (
              <p className="muted small">Пока ни одной размеченной ошибки</p>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
