import { useState } from 'react'
import type { BenchRun, Metrics } from '../../api'
import { LANG_LABEL, dateTime, ms, pct } from '../../lib/format'
import { Badge, BarList, Empty, Panel, ScenarioChip } from '../ui'

const TYPE_LABEL: Record<string, string> = {
  single: 'одна тема',
  multi_intent: 'несколько тем',
  out_of_scope: 'вне области',
  unclear: 'неясные',
}

function MetricsTable({ title, rows, labels }: { title: string; rows: Record<string, Metrics>; labels: Record<string, string> }) {
  return (
    <div>
      <h3>{title}</h3>
      <table className="table compact">
        <thead>
          <tr>
            <th />
            <th className="right">n</th>
            <th className="right" title="Основной сценарий угадан">основной</th>
            <th className="right" title="Все сценарии угаданы">все</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([k, m]) => (
            <tr key={k}>
              <td>{labels[k] ?? k}</td>
              <td className="right num">{m.n}</td>
              <td className="right num">{pct(m.primary_acc)}</td>
              <td className="right num">{pct(m.full_match)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Прогоны bench: точность по языкам и типам реплик, по корзинам уверенности, список ошибок */
export function BenchPanel(props: { runs: BenchRun[]; progress: { done: number; total: number } | null; onStart: () => void; busy: boolean }) {
  const runs = [...props.runs].sort((a, b) => (b.started_at ?? '').localeCompare(a.started_at ?? ''))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const run = runs.find((r) => r.run_id === selectedId) ?? runs[0]

  return (
    <Panel
      title="Прогоны bench"
      hint="Тот же роутер, что в звонке, на dev-наборе и накопленных кейсах"
      actions={
        <button disabled={props.busy || !!props.progress} onClick={props.onStart}>
          Запустить прогон
        </button>
      }
    >
      {props.progress && (
        <div className="progress">
          <div className="small">
            Прогон идёт: <span className="num">{props.progress.done}/{props.progress.total}</span>
          </div>
          <div className="bar">
            <div className="bar-fill" style={{ width: `${(props.progress.done / Math.max(1, props.progress.total)) * 100}%` }} />
          </div>
        </div>
      )}
      {!run ? (
        <Empty>Прогонов ещё не было</Empty>
      ) : (
        <>
          <div className="tabs">
            {runs.map((r) => (
              <button key={r.run_id} className={r.run_id === run.run_id ? 'tab tab-active' : 'tab'} onClick={() => setSelectedId(r.run_id)}>
                {r.run_id} <span className="muted">{r.catalog_version}</span>
              </button>
            ))}
          </div>
          <div className="row gap wrap">
            <Badge tone={run.status === 'finished' ? 'ok' : run.status === 'failed' ? 'bad' : 'info'}>{run.status}</Badge>
            <span className="muted small">
              {run.started_at && dateTime(run.started_at)} · dev {run.dataset?.dev ?? '—'} + кейсы {run.dataset?.cases ?? '—'}
            </span>
          </div>
          <div className="kpis compact-kpis">
            <div className="kpi">
              <div className="kpi-value num">{pct(run.metrics.all.primary_acc)}</div>
              <div className="kpi-label">основной сценарий угадан · n={run.metrics.all.n}</div>
            </div>
            <div className="kpi">
              <div className="kpi-value num">{pct(run.metrics.all.full_match)}</div>
              <div className="kpi-label">все сценарии угаданы</div>
            </div>
            {run.metrics.intent_recall != null && (
              <div className="kpi">
                <div className="kpi-value num">{pct(run.metrics.intent_recall)}</div>
                <div className="kpi-label">intent recall</div>
              </div>
            )}
            {run.latency_ms?.router_p50 != null && (
              <div className="kpi">
                <div className="kpi-value num">{ms(run.latency_ms.router_p50)}</div>
                <div className="kpi-label">роутер p50 · p95 {ms(run.latency_ms.router_p95)}</div>
              </div>
            )}
          </div>
          <div className="breakdown">
            {run.metrics.by_lang && <MetricsTable title="По языкам" rows={run.metrics.by_lang} labels={LANG_LABEL} />}
            {run.metrics.by_type && <MetricsTable title="По типам реплик" rows={run.metrics.by_type} labels={TYPE_LABEL} />}
            {run.metrics.confidence_buckets && (
              <div>
                <h3>Точность по уверенности</h3>
                <BarList
                  rows={run.metrics.confidence_buckets.map((b) => ({ key: b.bucket, label: `${b.bucket} · n=${b.n}`, value: b.primary_acc, hint: `${b.n} реплик, точность ${pct(b.primary_acc)}` }))}
                  format={pct}
                />
              </div>
            )}
          </div>
          {run.errors && run.errors.length > 0 && (
            <>
              <h3>Ошибки · {run.errors.length}</h3>
              <table className="table compact">
                <tbody>
                  {run.errors.map((e) => (
                    <tr key={e.id}>
                      <td>
                        «{e.text}» <code className="muted small">{e.id}</code>
                      </td>
                      <td className="nowrap">
                        <span className="muted small">ждали </span>
                        {e.expected.map((id) => (
                          <ScenarioChip key={id} id={id} />
                        ))}
                      </td>
                      <td className="nowrap">
                        <span className="muted small">получили </span>
                        {e.got.map((id) => (
                          <ScenarioChip key={id} id={id} />
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </Panel>
  )
}
