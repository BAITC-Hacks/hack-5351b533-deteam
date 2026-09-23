import { TARGET_ROUTER_MS, TARGET_TOTAL_MS, WARN_TOTAL_MS, type LatencyMs } from '../../api'
import { STAGES, ms } from '../../lib/format'
import { TotalLatency } from '../ui'

/**
 * Водопад «конец речи клиента → первый звук ответа»: этапы идут друг за другом.
 * Красная риска — ориентир 1,5 с. Итог крупно: его сверяют с секундомером.
 */
export function LatencyWaterfall({ latency }: { latency: LatencyMs }) {
  const scale = Math.max(latency.total, TARGET_TOTAL_MS)
  const starts = STAGES.map((_, i) => STAGES.slice(0, i).reduce((a, st) => a + (latency[st.key] ?? 0), 0))

  return (
    <div className="latency">
      <div className="row between">
        <span className="muted small">
          До ответа · цель ≤ {TARGET_TOTAL_MS} мс, допустимо ≤ {WARN_TOTAL_MS}
        </span>
        <TotalLatency value={latency.total} big />
      </div>
      <div className="waterfall">
        {STAGES.map(({ key, label }, i) => {
          const value = latency[key] ?? 0
          const left = starts[i]
          return (
            <div className="waterfall-row" key={key} title={`${label}: ${ms(value)}`}>
              <span className="waterfall-label">{label}</span>
              <span className="waterfall-track">
                <span
                  className={`waterfall-seg seg-${key}`}
                  style={{ left: `${(left / scale) * 100}%`, width: `max(2px, ${(value / scale) * 100}%)` }}
                />
                <span className="waterfall-target" style={{ left: `${(TARGET_TOTAL_MS / scale) * 100}%` }} />
              </span>
              <span className={`waterfall-value num ${key === 'router' && value > TARGET_ROUTER_MS ? 'text-bad' : ''}`}>
                {ms(value)}
              </span>
            </div>
          )
        })}
      </div>
      <p className="muted small">Выбор сценария: цель ≤ {TARGET_ROUTER_MS} мс</p>
    </div>
  )
}
