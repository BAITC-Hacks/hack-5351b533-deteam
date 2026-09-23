import type { ReactNode } from 'react'
import type { ScenarioScore, Trace } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { FAST_PATH_LABEL, LANG_LABEL, RESPONSE_SOURCE_LABEL, pct } from '../../lib/format'
import { Badge, DecisionBadge, LangBadge, Panel, ScenarioChip } from '../ui'
import { LatencyWaterfall } from './LatencyWaterfall'

/** Если второй кандидат ближе этого к выбранному — подсвечиваем «на грани» */
const CLOSE_MARGIN = 0.15

function Section({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className="trace-section">
      <h3>{title}</h3>
      {children}
    </div>
  )
}

function ScoreRow({ s, chosen }: { s: ScenarioScore; chosen: boolean }) {
  const { scenarioName } = useCatalog()
  return (
    <li className={chosen ? 'score score-chosen' : 'score'}>
      <div className="row between">
        <span>
          {scenarioName(s.scenario_id)} <code className="muted small">{s.scenario_id}</code>
        </span>
        <span className="num small">{pct(s.confidence)}</span>
      </div>
      <div className="bar">
        <div className="bar-fill" style={{ width: pct(s.confidence) }} />
      </div>
      {s.segment && <div className="small">«{s.segment}»</div>}
      {s.reason && <div className="muted small">{s.reason}</div>}
      {s.boundary_rule && (
        <div className="small">
          <span className="muted">Правило границы:</span> {s.boundary_rule}
        </div>
      )}
    </li>
  )
}

function ActionChip({ action }: { action: string }) {
  const [name, mode] = action.split(':')
  if (mode === 'preview') return <Badge tone="warn" title="Ждёт подтверждения клиента">{name} · ждёт «да»</Badge>
  if (mode === 'error') return <Badge tone="bad" title="Действие вернуло ошибку">{name} · ошибка</Badge>
  return <Badge tone="ok">{name}</Badge>
}

/** Трассировка хода по trace.schema.json: что услышали → что решили и почему → альтернативы → данные → задержки */
export function TracePanel({ trace, extra }: { trace: Trace; extra?: ReactNode }) {
  const top = trace.scenarios[0]
  const runnerUp = trace.alternatives[0]
  const margin = top && runnerUp ? top.confidence - runnerUp.confidence : null
  const slots = Object.entries(trace.slots ?? {})

  return (
    <Panel
      title={`Ход ${trace.turn}`}
      hint="Что услышал робот, какой сценарий выбрал, почему и за сколько"
      actions={<span className="muted small">каталог {trace.catalog_version}</span>}
      className="trace"
    >
      {extra}

      <Section title="Реплика клиента">
        <blockquote className="transcript">{trace.transcript}</blockquote>
        <div className="row gap wrap">
          <LangBadge lang={trace.language} />
          {trace.emotion && trace.emotion !== 'neutral' && <Badge tone="bad">эмоция: {trace.emotion}</Badge>}
          {trace.urgency && trace.urgency !== 'normal' && <Badge tone="warn">срочность: {trace.urgency}</Badge>}
          {trace.interrupted && <Badge tone="info">клиент перебил бота</Badge>}
        </div>
      </Section>

      <Section title="Решение">
        <div className="row gap wrap">
          <DecisionBadge decision={trace.decision} />
          {trace.fast_path ? (
            <Badge tone="ok" title="Детерминированный быстрый путь, LLM-роутер не вызывался">
              без роутера: {FAST_PATH_LABEL[trace.fast_path] ?? trace.fast_path}
            </Badge>
          ) : (
            trace.router_model && <Badge title="Модель роутера">{trace.router_model}</Badge>
          )}
          {trace.speculative_hit && (
            <Badge tone="info" title="Роутер угадал сценарий по части фразы, пока клиент ещё говорил">
              спекулятивное попадание
            </Badge>
          )}
          {trace.response_source && <Badge>ответ: {RESPONSE_SOURCE_LABEL[trace.response_source] ?? trace.response_source}</Badge>}
        </div>
        <p className="rationale">{trace.reason}</p>
      </Section>

      <Section title={trace.scenarios.length > 1 ? `Сценарии · ${trace.scenarios.length} темы в одной реплике` : 'Сценарий'}>
        {margin !== null && margin < CLOSE_MARGIN && (
          <div className="callout callout-warn">
            Альтернатива отстаёт всего на {pct(margin)} — робот был на грани. Стоит проверить.
          </div>
        )}
        {trace.scenarios.length ? (
          <ul className="scores">
            {trace.scenarios.map((s) => (
              <ScoreRow key={s.scenario_id} s={s} chosen />
            ))}
          </ul>
        ) : (
          <p className="muted">Сценарий не выбран</p>
        )}
        {trace.alternatives.length > 0 && (
          <>
            <h4>Альтернативы</h4>
            <ul className="scores">
              {trace.alternatives.map((s) => (
                <ScoreRow key={s.scenario_id} s={s} chosen={false} />
              ))}
            </ul>
          </>
        )}
      </Section>

      {trace.second_opinion && (
        <Section title="Второе мнение">
          <div className="row gap wrap">
            <Badge>{trace.second_opinion.model}</Badge>
            <Badge tone={trace.second_opinion.agreed ? 'ok' : 'warn'}>
              {trace.second_opinion.agreed ? 'согласна с роутером' : 'не согласна с роутером'}
            </Badge>
            {trace.second_opinion.scenarios.map((s) => (
              <ScenarioChip key={s.scenario_id} id={s.scenario_id} confidence={s.confidence} />
            ))}
          </div>
        </Section>
      )}

      <Section title="Слоты и действия">
        {slots.length > 0 ? (
          <table className="kv">
            <tbody>
              {slots.map(([k, v]) => (
                <tr key={k}>
                  <td>
                    <code>{k}</code>
                  </td>
                  <td>{typeof v === 'string' ? v : JSON.stringify(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted small">Слоты не извлечены</p>
        )}
        <div className="row gap wrap actions">
          {trace.actions.length ? trace.actions.map((a) => <ActionChip key={a} action={a} />) : <span className="muted small">Действий нет</span>}
        </div>
      </Section>

      <Section title="Контекст диалога">
        <table className="kv">
          <tbody>
            <tr>
              <td>Активный сценарий</td>
              <td>{trace.active_scenario ? <ScenarioChip id={trace.active_scenario} /> : '—'}</td>
            </tr>
            <tr>
              <td>Отложенные темы</td>
              <td>
                {trace.stack?.length ? (
                  <span className="row gap wrap">
                    {trace.stack.map((id) => (
                      <ScenarioChip key={id} id={id} />
                    ))}
                  </span>
                ) : (
                  '—'
                )}
              </td>
            </tr>
          </tbody>
        </table>
      </Section>

      <Section title={`Ответ бота · ${LANG_LABEL[trace.response_language] ?? trace.response_language}`}>
        <p className="bot-reply">{trace.response_text}</p>
      </Section>

      <Section title="Задержка по этапам">
        <LatencyWaterfall latency={trace.latency_ms} />
      </Section>
    </Panel>
  )
}
