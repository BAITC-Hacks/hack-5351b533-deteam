import type { ReactNode } from 'react'
import type { Turn } from '../../api'
import { EMOTION_LABEL } from '../../lib/format'
import { Badge, ConfidenceBadge, DecisionBadge, Empty, LangBadge, Panel, RouteBadge } from '../ui'
import { LatencyBars } from './LatencyBars'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="trace-section">
      <h3>{title}</h3>
      {children}
    </div>
  )
}

/** Трассировка одной реплики: что услышали → что решили и почему → альтернативы → контекст → задержки */
export function TracePanel({ turn, extra }: { turn: Turn | null; extra?: ReactNode }) {
  if (!turn) {
    return (
      <Panel title="Трассировка" hint="После каждой реплики здесь появится решение роутера">
        <Empty>Реплик пока нет. Начните звонок и скажите что-нибудь.</Empty>
      </Panel>
    )
  }
  const { trace, user } = turn

  return (
    <Panel
      title={`Трассировка · реплика ${turn.index}`}
      hint="Что услышал робот, какой сценарий выбрал, почему и за сколько"
      actions={trace.model && <span className="muted small">модель: {trace.model}</span>}
      className="trace"
    >
      {extra}

      <Section title="Транскрипт">
        <blockquote className="transcript">{user.text}</blockquote>
        <div className="row gap">
          <LangBadge lang={user.lang} />
          <Badge>{user.input === 'voice' ? 'голос' : 'текст'}</Badge>
          {user.emotion && user.emotion !== 'neutral' && (
            <Badge tone={user.emotion === 'negative' ? 'bad' : 'ok'}>{EMOTION_LABEL[user.emotion]}</Badge>
          )}
        </div>
      </Section>

      <Section title="Решение">
        <div className="row gap">
          <DecisionBadge decision={trace.decision} />
          <RouteBadge path={trace.route_path} />
        </div>
        {trace.selected ? (
          <div className="scenario-pick">
            <div className="row between">
              <strong>{trace.selected.title}</strong>
              <ConfidenceBadge value={trace.selected.confidence} />
            </div>
            <code className="muted small">{trace.selected.id}</code>
          </div>
        ) : (
          <p className="muted">Сценарий не выбран</p>
        )}
        <p className="rationale">{trace.rationale}</p>
        {trace.handoff && (
          <div className="callout callout-bad">Передача оператору: {trace.handoff.reason}. Контекст диалога уходит вместе с клиентом.</div>
        )}
        {trace.pending_confirmation && (
          <div className="callout callout-warn">
            Необратимое действие <code>{trace.pending_confirmation.action}</code> — ждём подтверждения клиента.
            <br />
            {trace.pending_confirmation.description}
          </div>
        )}
      </Section>

      <Section title="Альтернативы">
        {trace.alternatives.length ? (
          <ul className="candidates">
            {trace.alternatives.map((a) => (
              <li key={a.id}>
                <div className="row between">
                  <span>{a.title}</span>
                  <ConfidenceBadge value={a.confidence} />
                </div>
                <div className="muted small">{a.reason}</div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">Нет</p>
        )}
      </Section>

      <Section title="Параметры из речи">
        {Object.keys(trace.params).length || trace.missing_params.length ? (
          <table className="kv">
            <tbody>
              {Object.entries(trace.params).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td>{String(v ?? '—')}</td>
                </tr>
              ))}
              {trace.missing_params.map((k) => (
                <tr key={k} className="text-warn">
                  <td>{k}</td>
                  <td>не хватает — робот спросит</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Не извлечены</p>
        )}
      </Section>

      <Section title="Контекст диалога">
        <table className="kv">
          <tbody>
            <tr>
              <td>Активная тема</td>
              <td>{trace.context.active?.title ?? '—'}</td>
            </tr>
            <tr>
              <td>Прервано (вернёмся)</td>
              <td>{trace.context.interrupted.map((s) => s.title).join(' → ') || '—'}</td>
            </tr>
            <tr>
              <td>В очереди</td>
              <td>{trace.context.queued.map((s) => s.title).join(', ') || '—'}</td>
            </tr>
          </tbody>
        </table>
      </Section>

      <Section title="Задержка по этапам">
        <LatencyBars latency={trace.latency} />
      </Section>
    </Panel>
  )
}
