import { useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { FLAG_CONFIDENCE, api, type Case, type SessionDetail, type Trace } from '../api'
import { Icon } from '../components/Icon'
import { CaseForm } from '../components/supervisor/CaseForm'
import { SessionInspector } from '../components/supervisor/SessionInspector'
import { SessionOutcome } from '../components/supervisor/SessionOutcome'
import { SessionRecordingPlayer } from '../components/supervisor/SessionRecordingPlayer'
import { buildSessionTimeline, SessionTimeline, type TurnTiming } from '../components/supervisor/SessionTimeline'
import { Badge, DecisionBadge, Empty, ErrorBox, LangBadge, Panel, ScenarioChip, TotalLatency } from '../components/ui'
import { useDebounced, useResource } from '../hooks/useResource'
import { useCatalog } from '../lib/catalog-context'
import { CHANNEL_LABEL, dateTime } from '../lib/format'
import { useLiveEvents } from '../lib/live-context'

const isDoubtful = (t: Trace) => (t.scenarios[0]?.confidence ?? 0) < FLAG_CONFIDENCE || t.scenarios.some((s) => s.scenario_id === 'SYS_UNCLEAR')

/** Перемонтируем карточку при смене звонка, чтобы старые данные не показывались под новым URL. */
export function SessionPage() {
  const { id = '' } = useParams()
  return <SessionDetailView key={id} id={id} />
}

/** Карточка звонка: шкала разговора, длинный транскрипт и общий разбор справа. */
function SessionDetailView({ id }: { id: string }) {
  const [params, setParams] = useSearchParams()
  const { client } = useCatalog()
  const session = useResource(() => api.session(id), `session:${id}`)
  const events = useResource(() => api.sessionEvents(id), `session-events:${id}`)
  const cases = useResource(() => api.cases(), 'cases')
  const [created, setCreated] = useState<Case[]>([]) // мок не сохраняет кейсы — держим созданные локально
  const [inspectorTab, setInspectorTab] = useState<'turn' | 'outcome'>('turn')

  const reload = useDebounced(() => { session.reload(); events.reload() })
  useLiveEvents(reload, (e) => e.session_id === id)

  if (session.error) return <ErrorBox error={session.error} />
  const s = session.data
  if (!s || s.session_id !== id) return <div className="session-empty-state"><Empty>Загрузка звонка…</Empty></div>

  const casesFor = (turn: number) =>
    [...(cases.data ?? []), ...created].filter((c) => c.session_id === id && c.turn === turn)
  const requestedTurn = Number(params.get('turn'))
  const defaultTurn = s.flagged && s.ended_at
    ? s.traces.find((t) => isDoubtful(t) || casesFor(t.turn).length > 0)?.turn
    : undefined
  const selectedTurn = (requestedTurn && s.traces.some((t) => t.turn === requestedTurn) ? requestedTurn : undefined)
    ?? defaultTurn
    ?? s.traces.at(-1)?.turn
    ?? 0
  const trace = s.traces.find((t) => t.turn === selectedTurn) ?? null
  const timeline = buildSessionTimeline(events.data, s.traces)
  const select = (turn: number) => {
    setInspectorTab('turn')
    setParams({ turn: String(turn) }, { replace: true })
  }
  const c = client(s.client_id)

  return (
    <div className="stack session-detail" id="selected-call">
      <Panel
        title={c?.full_name ?? s.caller_phone ?? 'Клиент не опознан'}
        hint={[
          CHANNEL_LABEL[s.channel],
          s.caller_phone ?? c?.phone,
          dateTime(s.started_at),
          `${s.turns} ходов`,
        ]
          .filter(Boolean)
          .join(' · ')}
        className="session-header"
      >
        <div className="row gap wrap">
          <SessionOutcome s={s} />
          {s.flagged && <Badge tone="warn">спорный</Badge>}
          {s.languages.map((l) => (
            <LangBadge key={l} lang={l} />
          ))}
          <span className="muted small">медиана до ответа</span> <TotalLatency value={s.latency_total_p50} />
        </div>
        <SessionTimeline model={timeline} traces={s.traces} selected={selectedTurn} onSelect={select} />
      </Panel>

      <div className="split session-layout">
        <Panel title="Диалог" className="dialog-panel">
          <SessionRecordingPlayer
            recording={s.recording}
            ended={Boolean(s.ended_at)}
            selectedTurn={selectedTurn}
            selectedTiming={timeline.turns.get(selectedTurn)}
            onRefresh={session.reload}
          />
          <Dialog session={s} selected={selectedTurn} onSelect={select} hasCase={(t) => casesFor(t).length > 0} timings={timeline.turns} />
        </Panel>

        <SessionInspector
          trace={trace}
          finalState={s.final_state}
          ended={Boolean(s.ended_at)}
          activeTab={inspectorTab}
          onTabChange={setInspectorTab}
          extra={trace && <CaseForm key={`${id}-${trace.turn}`} sessionId={id} trace={trace} existing={casesFor(trace.turn)} onCreated={(k) => setCreated((p) => [...p, k])} />}
        />
      </div>
    </div>
  )
}

function formatOffset(ms: number) {
  const seconds = Math.floor(ms / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function formatInterval(start?: number, end?: number) {
  if (start === undefined) return null
  const from = formatOffset(start)
  const to = end === undefined ? from : formatOffset(end)
  return from === to ? from : `${from}–${to}`
}

function Dialog({ session, selected, onSelect, hasCase, timings }: { session: SessionDetail; selected: number; onSelect: (turn: number) => void; hasCase: (turn: number) => boolean; timings: Map<number, TurnTiming> }) {
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
      {!turns.length && <Empty>Транскрипт пока пуст</Empty>}
      {turns.map((turn) => {
        const msgs = session.transcript.filter((m) => m.turn === turn)
        const trace = session.traces.find((t) => t.turn === turn)
        const timing = timings.get(turn)
        const clientMsg = msgs.find((m) => m.role === 'client')
        const botMsgs = msgs.filter((m) => m.role === 'bot')
        const clientTime = formatInterval(timing?.clientStartMs, timing?.clientEndMs)
        const botTime = formatInterval(timing?.botStartMs, timing?.botEndMs)
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
                  {clientTime && <span className="dialog-time">{clientTime}</span>}
                </div>
                {clientMsg.text}
              </div>
            )}
            {trace && (
              <div className="turn-summary">
                <DecisionBadge decision={trace.decision} />
                {trace.scenarios.map((s) => (
                  <ScenarioChip key={s.scenario_id} id={s.scenario_id} />
                ))}
                {isDoubtful(trace) && <Badge tone="warn">сомневался</Badge>}
                {hasCase(turn) && <Badge tone="bad"><Icon name="close" size={12} />отмечено</Badge>}
                <button
                  type="button"
                  className="turn-detail-button"
                  aria-pressed={turn === selected}
                  aria-label={`Разобрать ход ${turn}`}
                  onClick={(event) => { event.stopPropagation(); onSelect(turn) }}
                >
                  {turn === selected ? 'Ход выбран' : 'Подробности'}
                </button>
              </div>
            )}
            {botMsgs.map((m, i) => (
              <div key={i} className="bubble bubble-bot">
                <div className="bubble-meta">
                  Бот {turn === 0 && '· приветствие'} <LangBadge lang={m.lang} />
                  {botTime && <span className="dialog-time">{botTime}</span>}
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
