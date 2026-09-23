import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { FLAG_CONFIDENCE, api, type Case, type DialogState, type SessionDetail, type Trace } from '../api'
import { CaseForm } from '../components/supervisor/CaseForm'
import { SessionOutcome } from '../components/supervisor/SessionOutcome'
import { TracePanel } from '../components/trace/TracePanel'
import { Badge, DecisionBadge, Empty, ErrorBox, LangBadge, Panel, ScenarioChip, TotalLatency } from '../components/ui'
import { useDebounced, useResource } from '../hooks/useResource'
import { useCatalog } from '../lib/catalog-context'
import { CHANNEL_LABEL, FAST_PATH_LABEL, STACK_REASON_LABEL, dateTime, duration } from '../lib/format'
import { useLiveEvents } from '../lib/live-context'

const isDoubtful = (t: Trace) => (t.scenarios[0]?.confidence ?? 0) < FLAG_CONFIDENCE || t.scenarios.some((s) => s.scenario_id === 'SYS_UNCLEAR')

/** Карточка звонка: лента тем, весь диалог слева, трассировка выбранного хода справа */
export function SessionPage() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const { client } = useCatalog()
  const session = useResource(() => api.session(id), `session:${id}`)
  const cases = useResource(() => api.cases(), 'cases')
  const [created, setCreated] = useState<Case[]>([]) // мок не сохраняет кейсы — держим созданные локально

  const reload = useDebounced(session.reload)
  useLiveEvents(reload, (e) => e.session_id === id)

  if (session.error) return <ErrorBox error={session.error} />
  const s = session.data
  if (!s) return <Empty>Загрузка…</Empty>

  const selectedTurn = Number(params.get('turn')) || s.traces.at(-1)?.turn || 0
  const trace = s.traces.find((t) => t.turn === selectedTurn) ?? null
  const select = (turn: number) => setParams({ turn: String(turn) }, { replace: true })
  const casesFor = (turn: number) =>
    [...(cases.data ?? []), ...created].filter((c) => c.session_id === id && c.turn === turn)
  const c = client(s.client_id)

  return (
    <div className="stack">
      <Panel
        title={c?.full_name ?? s.caller_phone ?? 'Клиент не опознан'}
        hint={[
          CHANNEL_LABEL[s.channel],
          s.caller_phone ?? c?.phone,
          `${dateTime(s.started_at)} · ${duration(s.started_at, s.ended_at)}`,
          `${s.turns} ходов`,
          `каталог ${s.catalog_version}`,
        ]
          .filter(Boolean)
          .join(' · ')}
        actions={<Link to="/supervisor">← Все звонки</Link>}
      >
        <div className="row gap wrap">
          <SessionOutcome s={s} />
          {s.flagged && <Badge tone="warn">спорный</Badge>}
          {s.languages.map((l) => (
            <LangBadge key={l} lang={l} />
          ))}
          <span className="muted small">медиана до ответа</span> <TotalLatency value={s.latency_total_p50} />
        </div>
        <h3>Лента тем</h3>
        <TopicRibbon traces={s.traces} selected={selectedTurn} onSelect={select} />
      </Panel>

      <div className="split">
        <div className="stack">
          <Panel title="Диалог" hint="Полный транскрипт. Под репликой клиента — что решил роутер. Клик — подробности справа" className="dialog-panel">
            <Dialog session={s} selected={selectedTurn} onSelect={select} hasCase={(t) => casesFor(t).length > 0} />
          </Panel>
          {s.final_state && <FinalState state={s.final_state} />}
        </div>

        {trace ? (
          <TracePanel
            trace={trace}
            extra={<CaseForm sessionId={id} trace={trace} existing={casesFor(trace.turn)} onCreated={(k) => setCreated((p) => [...p, k])} />}
          />
        ) : (
          <Panel title="Трассировка">
            <Empty>Выберите ход в диалоге</Empty>
          </Panel>
        )}
      </div>
    </div>
  )
}

/** Как шёл разговор: по шагу на ход клиента */
function TopicRibbon({ traces, selected, onSelect }: { traces: Trace[]; selected: number; onSelect: (turn: number) => void }) {
  if (!traces.length) return <span className="muted">—</span>
  return (
    <div className="ribbon">
      {traces.map((t, i) => (
        <span key={t.turn} className="ribbon-item">
          {i > 0 && <span className="ribbon-arrow">→</span>}
          <button className={`ribbon-step ${t.turn === selected ? 'ribbon-selected' : ''} ${isDoubtful(t) ? 'ribbon-doubt' : ''}`} onClick={() => onSelect(t.turn)}>
            <span className="muted small">{t.turn}</span>
            {t.decision === 'continue' || t.decision === 'confirm' || t.decision === 'goodbye' ? (
              <DecisionBadge decision={t.decision} />
            ) : (
              t.scenarios.map((s) => <ScenarioChip key={s.scenario_id} id={s.scenario_id} />)
            )}
            {t.decision === 'handoff' && <DecisionBadge decision="handoff" />}
          </button>
        </span>
      ))}
    </div>
  )
}

function Dialog({ session, selected, onSelect, hasCase }: { session: SessionDetail; selected: number; onSelect: (turn: number) => void; hasCase: (turn: number) => boolean }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const selectedRef = useRef<HTMLDivElement>(null)
  // Прокручиваем только ленту диалога, а не всю страницу (scrollIntoView двигает и окно)
  useEffect(() => {
    const box = boxRef.current
    const el = selectedRef.current
    if (!box || !el) return
    const top = el.offsetTop - box.offsetTop
    if (top < box.scrollTop || top + el.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTop = top - 8
    }
  }, [selected])

  const turns = [...new Set(session.transcript.map((m) => m.turn))].sort((a, b) => a - b)
  return (
    <div className="dialog" ref={boxRef}>
      {turns.map((turn) => {
        const msgs = session.transcript.filter((m) => m.turn === turn)
        const trace = session.traces.find((t) => t.turn === turn)
        const clientMsg = msgs.find((m) => m.role === 'client')
        const botMsgs = msgs.filter((m) => m.role === 'bot')
        return (
          <div
            key={turn}
            ref={turn === selected ? selectedRef : undefined}
            className={`turn ${trace ? 'turn-clickable' : ''} ${turn === selected ? 'turn-selected' : ''}`}
            onClick={() => trace && onSelect(turn)}
          >
            {clientMsg && (
              <div className="bubble bubble-user">
                <div className="bubble-meta">
                  Клиент · ход {turn} <LangBadge lang={clientMsg.lang} />
                </div>
                {clientMsg.text}
              </div>
            )}
            {trace && (
              <div className="turn-summary">
                <DecisionBadge decision={trace.decision} />
                {trace.scenarios.map((s) => (
                  <ScenarioChip key={s.scenario_id} id={s.scenario_id} confidence={s.confidence} />
                ))}
                {trace.fast_path && <Badge tone="ok">без роутера: {FAST_PATH_LABEL[trace.fast_path] ?? trace.fast_path}</Badge>}
                <TotalLatency value={trace.latency_ms.total} />
                {isDoubtful(trace) && <Badge tone="warn">сомневался</Badge>}
                {hasCase(turn) && <Badge tone="bad">✗ отмечено</Badge>}
              </div>
            )}
            {botMsgs.map((m, i) => (
              <div key={i} className="bubble bubble-bot">
                <div className="bubble-meta">
                  Бот {turn === 0 && '· приветствие'} <LangBadge lang={m.lang} />
                </div>
                {m.text}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

/** final_state (dialog-state.schema.json): чем закончился диалог */
function FinalState({ state }: { state: DialogState }) {
  const slots = Object.entries(state.slots ?? {})
  return (
    <Panel title="Состояние в конце звонка" hint="Кто клиент, что сделано, что осталось отложенным">
      <table className="kv">
        <tbody>
          <tr>
            <td>Клиент</td>
            <td>{state.client ? `${state.client.full_name} · опознан по ${state.client.identified_by}` : 'не опознан'}</td>
          </tr>
          <tr>
            <td>Завершённые сценарии</td>
            <td>
              <span className="row gap wrap">{state.completed.length ? state.completed.map((id) => <ScenarioChip key={id} id={id} />) : '—'}</span>
            </td>
          </tr>
          <tr>
            <td>Активный</td>
            <td>{state.active ? <><ScenarioChip id={state.active.scenario_id} /> <span className="muted small">шаг {state.active.step}</span></> : '—'}</td>
          </tr>
          <tr>
            <td>Отложено</td>
            <td>
              {state.stack.length
                ? state.stack.map((x) => (
                    <div key={x.scenario_id}>
                      <ScenarioChip id={x.scenario_id} /> <span className="muted small">{STACK_REASON_LABEL[x.reason] ?? x.reason}</span>
                    </div>
                  ))
                : '—'}
            </td>
          </tr>
          {state.pending_confirmation && (
            <tr>
              <td>Ждёт подтверждения</td>
              <td>
                <code>{state.pending_confirmation.action}</code> {state.pending_confirmation.spoken_summary}
              </td>
            </tr>
          )}
          {state.handoff && (
            <tr>
              <td>Перевод</td>
              <td>очередь {state.handoff.queue}</td>
            </tr>
          )}
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
    </Panel>
  )
}
