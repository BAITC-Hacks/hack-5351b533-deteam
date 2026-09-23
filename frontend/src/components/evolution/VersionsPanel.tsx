import type { CatalogVersion } from '../../api'
import { dateTime, pct } from '../../lib/format'
import { Badge, Empty, Panel } from '../ui'

/** Тренд primary_acc по версиям каталога: одна линия, точки подписаны значением и версией */
function AccuracyTrend({ versions }: { versions: CatalogVersion[] }) {
  const pts = versions.filter((v) => v.primary_acc != null)
  if (pts.length < 2) return null
  const W = 560
  const H = 170
  const pad = { l: 16, r: 16, t: 26, b: 34 }
  const vals = pts.map((v) => v.primary_acc!)
  const lo = Math.floor(Math.min(...vals) * 100 - 1) / 100
  const hi = Math.ceil(Math.max(...vals) * 100 + 1) / 100
  const x = (i: number) => pad.l + (i * (W - pad.l - pad.r)) / (pts.length - 1)
  const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b)

  return (
    <figure className="trend">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Точность роутера по версиям каталога">
        <line x1={pad.l} x2={W - pad.r} y1={H - pad.b} y2={H - pad.b} className="trend-axis" />
        <polyline points={pts.map((v, i) => `${x(i)},${y(v.primary_acc!)}`).join(' ')} className="trend-line" />
        {pts.map((v, i) => (
          <g key={v.version}>
            <title>{`${v.version}: ${pct(v.primary_acc!)}${v.note ? ` — ${v.note}` : ''}`}</title>
            <circle cx={x(i)} cy={y(v.primary_acc!)} r={14} className="trend-hit" />
            <circle cx={x(i)} cy={y(v.primary_acc!)} r={v.current ? 6 : 4} className={v.current ? 'trend-dot trend-dot-current' : 'trend-dot'} />
            <text x={x(i)} y={y(v.primary_acc!) - 10} textAnchor="middle" className="trend-value">
              {(v.primary_acc! * 100).toFixed(1)}%
            </text>
            <text x={x(i)} y={H - pad.b + 18} textAnchor={i === 0 ? 'start' : i === pts.length - 1 ? 'end' : 'middle'} className="trend-label">
              {v.version}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="muted small">Основной сценарий угадан (primary_acc) на dev-наборе + кейсах</figcaption>
    </figure>
  )
}

/** История версий каталога: хэш, откуда взялась, точность; откат в один клик */
export function VersionsPanel(props: { versions: CatalogVersion[]; frozen: boolean; busy: boolean; onRollback: (version: string) => void }) {
  const versions = [...props.versions].sort((a, b) => a.created_at.localeCompare(b.created_at))
  return (
    <Panel title="Версии каталога" hint="Каждый применённый патч — новая версия с хэшем. Откат возвращает любую прошлую">
      {versions.length ? (
        <>
          <AccuracyTrend versions={versions} />
          <table className="table compact">
            <thead>
              <tr>
                <th>Версия</th>
                <th>Откуда</th>
                <th className="right">Точность</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[...versions].reverse().map((v) => (
                <tr key={v.version} className={v.current ? 'row-current' : ''}>
                  <td className="nowrap">
                    <strong>{v.version}</strong> <code className="muted">{v.hash}</code>
                    <div className="muted small">{dateTime(v.created_at)}</div>
                  </td>
                  <td>
                    {v.applied_patch ? `патч ${v.applied_patch}` : 'исходная'}
                    {v.note && <div className="muted small">{v.note}</div>}
                  </td>
                  <td className="right num">{v.primary_acc != null ? pct(v.primary_acc) : '—'}</td>
                  <td className="right nowrap">
                    {v.current ? (
                      <Badge tone="ok">текущая{v.frozen ? ' · 🔒' : ''}</Badge>
                    ) : (
                      <button disabled={props.frozen || props.busy} onClick={() => props.onRollback(v.version)} title={props.frozen ? 'Каталог заморожен' : 'Сделать текущей'}>
                        Откатить
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <Empty>Версий нет</Empty>
      )}
    </Panel>
  )
}
