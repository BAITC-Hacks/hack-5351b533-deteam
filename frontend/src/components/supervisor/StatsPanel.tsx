import { TARGET_ROUTER_MS, type LatencyStage, type Stats } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { DECISION_LABEL, LANG_LABEL, STAGES, ms, pct, totalTone } from '../../lib/format'
import { BarList } from '../ui'

const TOP_SCENARIOS = 8

/**
 * Четыре цифры «всё ли в порядке»: объём, сколько ушло к оператору, сколько раз робот не понял,
 * и насколько долго клиент ждёт ответа в худших случаях. Инженерные метрики — в «Распределениях».
 */
export function StatsKpis({ stats }: { stats: Stats }) {
  const p95 = stats.latency_ms.total?.p95
  const responseTime = p95 === undefined
    ? '—'
    : `${(p95 / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`
  return (
    <section className="overview-strip" aria-label="Ключевые показатели звонков">
      <div className="overview-volume">
        <span className="overview-label">Всего звонков</span>
        <strong className="overview-volume-value">{stats.sessions.toLocaleString('ru-RU')}</strong>
      </div>
      <div className="overview-stat">
        <span className="overview-label">Передано оператору</span>
        <strong className="overview-value">{pct(stats.handoff_rate)}</strong>
        <span className="overview-caption">доля ходов</span>
        <span className="overview-meter" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, stats.handoff_rate * 100))}%` }} /></span>
      </div>
      <div className="overview-stat">
        <span className="overview-label">Робот уточнил</span>
        <strong className="overview-value">{pct(stats.unclear_rate)}</strong>
        <span className="overview-caption">доля ходов</span>
        <span className="overview-meter" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, stats.unclear_rate * 100))}%` }} /></span>
      </div>
      <div className="overview-stat overview-stat-latency" title="95% ответов быстрее этого времени: от конца речи клиента до первого звука">
        <span className="overview-label">Время ответа</span>
        <strong className={`overview-value ${p95 === undefined ? '' : `text-${totalTone(p95)}`}`}>{responseTime}</strong>
        <span className="overview-caption">95% ответов быстрее</span>
      </div>
    </section>
  )
}

/** Распределения: где робот тратит время, что выбирает, как уверен */
export function StatsBreakdown({ stats }: { stats: Stats }) {
  const { scenarioName } = useCatalog()
  const scenarios = [...stats.by_scenario].sort((a, b) => b.count - a.count)
  const rest = scenarios.slice(TOP_SCENARIOS).reduce((a, s) => a + s.count, 0)
  const stageRows: { key: LatencyStage; label: string }[] = [...STAGES, { key: 'total', label: 'Итого до ответа' }]

  return (
    <div>
      <p className="muted small">
        Ходов: <span className="num">{stats.turns}</span> · ответ шаблоном без LLM: <span className="num">{pct(stats.template_rate)}</span> ·
        спекулятивные попадания роутера: <span className="num">{pct(stats.speculative_hit_rate)}</span>
      </p>
      <div className="breakdown">
        <div>
          <h3>Задержка по этапам</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th>Этап</th>
                <th className="right">p50</th>
                <th className="right">p95</th>
              </tr>
            </thead>
            <tbody>
              {stageRows.map(({ key, label }) => {
                const v = stats.latency_ms[key]
                const tone = (x: number) => (key === 'total' ? `text-${totalTone(x)}` : key === 'router' && x > TARGET_ROUTER_MS ? 'text-bad' : '')
                return (
                  <tr key={key} className={key === 'total' ? 'strong' : ''}>
                    <td>{label}</td>
                    <td className={`right num ${v ? tone(v.p50) : ''}`}>{ms(v?.p50)}</td>
                    <td className={`right num ${v ? tone(v.p95) : ''}`}>{ms(v?.p95)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div>
          <h3>Сценарии</h3>
          <BarList
            rows={[
              ...scenarios.slice(0, TOP_SCENARIOS).map((s) => ({ key: s.scenario_id, label: scenarioName(s.scenario_id), value: s.count, hint: s.scenario_id })),
              ...(rest ? [{ key: 'rest', label: 'остальные', value: rest }] : []),
            ]}
          />
        </div>
        <div>
          <h3>Решения</h3>
          <BarList
            rows={Object.entries(stats.by_decision)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => ({ key: k, label: DECISION_LABEL[k as keyof typeof DECISION_LABEL] ?? k, value: v }))}
          />
          <h3>Языки</h3>
          <BarList
            rows={Object.entries(stats.by_language)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => ({ key: k, label: LANG_LABEL[k] ?? k, value: v }))}
          />
        </div>
        <div>
          <h3>Уверенность роутера</h3>
          <BarList rows={stats.confidence_histogram.map((b) => ({ key: b.bucket, label: b.bucket, value: b.count }))} />
        </div>
      </div>
    </div>
  )
}
