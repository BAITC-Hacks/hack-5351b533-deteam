import { useEffect, useRef } from 'react'
import type { LiveTurn } from '../../hooks/voiceState'
import { useCatalog } from '../../lib/catalog-context'
import { FAST_PATH_LABEL } from '../../lib/format'
import { Badge, DecisionBadge, LangBadge, ScenarioChip, TotalLatency } from '../ui'

/** Смена лидирующего кандидата посреди фразы — «вау-момент» живой гипотезы */
function HypothesisTrail({ turn }: { turn: LiveTurn }) {
  const { scenarioName } = useCatalog()
  const tops: string[] = []
  for (const h of turn.hypotheses) {
    const top = h.scenarios[0]?.scenario_id
    if (top && tops.at(-1) !== top) tops.push(top)
  }
  const latest = turn.hypotheses.at(-1)
  if (!latest) return null
  const listening = !turn.user?.final

  return (
    <div className={`hypothesis ${listening ? 'hypothesis-live' : ''}`}>
      <span className="muted small">{listening ? 'Роутер думает:' : 'Гипотезы по ходу фразы:'}</span>
      {listening ? (
        latest.scenarios.map((s) => <ScenarioChip key={s.scenario_id} id={s.scenario_id} confidence={s.confidence} />)
      ) : (
        <span className="small">{tops.map((id) => scenarioName(id)).join(' → ')}</span>
      )}
      {tops.length > 1 && <Badge tone="info">кандидат сменился посреди фразы</Badge>}
    </div>
  )
}

function ActionCard({ a }: { a: LiveTurn['actions'][number] }) {
  const inputs = Object.entries(a.inputs ?? {})
  const title =
    a.status === 'preview' ? `Ждём подтверждения клиента: ${a.action}` : a.status === 'error' ? `Ошибка: ${a.action}` : `Выполнено: ${a.action}`
  return (
    <div className={`action-card action-${a.status}`}>
      <div className="small">
        <strong>{title}</strong>
        {a.status === 'preview' && a.irreversible && <Badge tone="warn">необратимо</Badge>}
      </div>
      {a.error && <div className="small">{a.error.code}: {a.error.message}</div>}
      {a.status === 'preview' && inputs.length > 0 && (
        <table className="kv small">
          <tbody>
            {inputs.map(([k, v]) => (
              <tr key={k}>
                <td>
                  <code>{k}</code>
                </td>
                <td>{typeof v === 'string' ? v : JSON.stringify(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {a.status === 'done' && a.result != null && <code className="small muted">{JSON.stringify(a.result).slice(0, 160)}</code>}
    </div>
  )
}

/** Транскрипт звонка крупно — читается с проектора. Под репликой клиента — итог роутинга, клик раскрывает трассировку */
export function LiveTranscript(props: { turns: LiveTurn[]; selected: number | null; onSelect: (turn: number) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const last = props.turns.at(-1)
  // Держим низ ленты в поле зрения, прокручивая только саму ленту
  useEffect(() => {
    const b = box.current
    if (b) b.scrollTop = b.scrollHeight
  }, [props.turns.length, last?.user?.text, last?.bot?.text, last?.actions.length])

  return (
    <div className="live-dialog" ref={box}>
      {props.turns.map((t) => (
        <div
          key={t.turn}
          className={`turn ${t.trace ? 'turn-clickable' : ''} ${t.turn === props.selected ? 'turn-selected' : ''}`}
          onClick={() => t.trace && props.onSelect(t.turn)}
        >
          {t.hypotheses.length > 0 && <HypothesisTrail turn={t} />}
          {t.user && (
            <div className={`bubble bubble-user big ${t.user.final ? '' : 'bubble-partial'}`}>
              <div className="bubble-meta">
                Клиент · ход {t.turn} {t.user.lang && <LangBadge lang={t.user.lang} />}
                {t.user.typed && <Badge>текстом</Badge>}
                {t.user.speaking && <Badge tone="info">● говорит</Badge>}
              </div>
              {t.user.text || '…'}
            </div>
          )}
          {t.trace && (
            <div className="turn-summary">
              <DecisionBadge decision={t.trace.decision} />
              {t.trace.scenarios.map((s) => (
                <ScenarioChip key={s.scenario_id} id={s.scenario_id} confidence={s.confidence} />
              ))}
              {t.trace.fast_path && <Badge tone="ok">без роутера: {FAST_PATH_LABEL[t.trace.fast_path] ?? t.trace.fast_path}</Badge>}
              <TotalLatency value={t.trace.latency_ms.total} />
            </div>
          )}
          {t.actions.map((a, i) => (
            <ActionCard key={i} a={a} />
          ))}
          {t.bot && (
            <div className="bubble bubble-bot big">
              <div className="bubble-meta">
                Бот {t.turn === 0 && '· приветствие'} {t.bot.lang && <LangBadge lang={t.bot.lang} />}
                {t.bot.template && <Badge>шаблон</Badge>}
                {t.speaking && <Badge tone="info">🔊 говорит</Badge>}
                {t.interrupted && <Badge tone="warn">перебит</Badge>}
              </div>
              {t.bot.text}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
