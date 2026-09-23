import { TARGET_ROUTING_MS, TARGET_TOTAL_MS, type Latency } from '../../api'
import { ms } from '../../lib/format'

const STAGES: { key: keyof Latency; label: string; target?: number }[] = [
  { key: 'stt_ms', label: 'Распознавание (STT)' },
  { key: 'routing_ms', label: 'Выбор сценария', target: TARGET_ROUTING_MS },
  { key: 'response_ms', label: 'Генерация ответа' },
  { key: 'tts_ms', label: 'Синтез (TTS)' },
]

/** Таймлайн «конец реплики → начало ответа» по этапам, с ориентирами из ТЗ */
export function LatencyBars({ latency }: { latency: Latency }) {
  const scale = Math.max(latency.total_ms, TARGET_TOTAL_MS)
  const over = latency.total_ms > TARGET_TOTAL_MS

  return (
    <div className="latency">
      <div className="latency-track">
        {STAGES.map(({ key }) =>
          latency[key] ? (
            <div
              key={key}
              className={`latency-seg seg-${key}`}
              style={{ width: `${(latency[key]! / scale) * 100}%` }}
            />
          ) : null,
        )}
        <div className="latency-target" style={{ left: `${(TARGET_TOTAL_MS / scale) * 100}%` }} title="Ориентир 1,5 с" />
      </div>
      <table className="latency-table">
        <tbody>
          {STAGES.map(({ key, label, target }) => (
            <tr key={key}>
              <td>
                <i className={`dot seg-${key}`} /> {label}
              </td>
              <td className={target && (latency[key] ?? 0) > target ? 'text-bad' : ''}>{ms(latency[key])}</td>
              <td className="muted">{target ? `цель ≤ ${target}` : ''}</td>
            </tr>
          ))}
          <tr className="latency-total">
            <td>Итого до ответа</td>
            <td className={over ? 'text-bad' : 'text-ok'}>{ms(latency.total_ms)}</td>
            <td className="muted">цель ≤ {TARGET_TOTAL_MS}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}
