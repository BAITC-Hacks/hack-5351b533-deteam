import type { Trace, VoiceEvent } from '../../api'
import { FLAG_CONFIDENCE } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { DECISION_LABEL } from '../../lib/format'
import { DecisionBadge, ScenarioChip } from '../ui'
import './SessionTimeline.css'

export type TurnTiming = {
  turn: number
  startMs: number
  endMs: number
  clientStartMs?: number
  clientEndMs?: number
  botStartMs?: number
  botEndMs?: number
}

type EventTimes = Partial<Record<'speechStart' | 'speechEnd' | 'sttFinal' | 'ttsStart' | 'ttsEnd' | 'botFinal', number>>
type TimelineModel = { turns: Map<number, TurnTiming>; durationMs: number; hasTiming: boolean }

function remember(times: EventTimes, key: keyof EventTimes, value: number, earliest: boolean) {
  const previous = times[key]
  times[key] = previous === undefined ? value : earliest ? Math.min(previous, value) : Math.max(previous, value)
}

/** Server event times share one clock; session summary dates are deliberately excluded. */
// oxlint-disable-next-line react/only-export-components -- Public model builder and view share this module by design.
export function buildSessionTimeline(events: VoiceEvent[] | null, traces: Trace[]): TimelineModel {
  const byTurn = new Map<number, EventTimes>()
  const traceTurns = new Set(traces.map((trace) => trace.turn))
  let lastEventMs = 0
  let sessionEndMs: number | undefined

  for (const event of events ?? []) {
    if (typeof event.t !== 'number' || !Number.isFinite(event.t) || event.t < 0) continue
    lastEventMs = Math.max(lastEventMs, event.t)
    if (event.type === 'session.end') sessionEndMs = Math.max(sessionEndMs ?? 0, event.t)
    if (typeof event.turn !== 'number' || !Number.isInteger(event.turn) || (event.turn !== 0 && !traceTurns.has(event.turn))) continue

    const times = byTurn.get(event.turn) ?? {}
    switch (event.type) {
      case 'vad.speech_start': remember(times, 'speechStart', event.t, true); break
      case 'vad.speech_end': remember(times, 'speechEnd', event.t, false); break
      case 'stt.final': remember(times, 'sttFinal', event.t, false); break
      case 'tts.start': remember(times, 'ttsStart', event.t, true); break
      case 'tts.end': remember(times, 'ttsEnd', event.t, false); break
      case 'bot.text.final': remember(times, 'botFinal', event.t, false); break
      default: continue
    }
    byTurn.set(event.turn, times)
  }

  const turns = new Map<number, TurnTiming>()
  for (const turn of new Set([0, ...traces.map((trace) => trace.turn)])) {
    const times = byTurn.get(turn)
    if (!times) continue

    // Final text is a fallback anchor, not a word or utterance timestamp.
    const clientStartMs = times.speechStart ?? times.speechEnd ?? times.sttFinal
    const clientEndMs = times.speechEnd ?? times.sttFinal ?? times.speechStart
    const botStartMs = times.ttsStart ?? times.botFinal ?? times.ttsEnd
    const botEndMs = times.ttsEnd ?? times.botFinal ?? times.ttsStart
    const known = [clientStartMs, clientEndMs, botStartMs, botEndMs].filter((n): n is number => n !== undefined)
    if (!known.length) continue

    turns.set(turn, {
      turn,
      startMs: Math.min(...known),
      endMs: Math.max(...known),
      clientStartMs,
      clientEndMs: clientStartMs === undefined ? undefined : Math.max(clientStartMs, clientEndMs ?? clientStartMs),
      botStartMs,
      botEndMs: botStartMs === undefined ? undefined : Math.max(botStartMs, botEndMs ?? botStartMs),
    })
  }

  const durationMs = sessionEndMs ?? lastEventMs
  return { turns, durationMs, hasTiming: durationMs > 0 && turns.size > 0 }
}

function formatTime(ms: number) {
  const seconds = Math.floor(Math.max(0, ms) / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function isDoubtful(trace: Trace) {
  return (trace.scenarios[0]?.confidence ?? 0) < FLAG_CONFIDENCE
    || trace.scenarios.some((scenario) => scenario.scenario_id === 'SYS_UNCLEAR')
}

function topics(trace: Trace, scenarioName: (id: string) => string) {
  return trace.scenarios.length
    ? trace.scenarios.map((scenario) => scenarioName(scenario.scenario_id)).join(', ')
    : DECISION_LABEL[trace.decision]
}

function TurnContent({ trace, compact = false }: { trace: Trace; compact?: boolean }) {
  return (
    <>
      <span className="session-timeline-turn-head">
        <strong>Ход {trace.turn}</strong>
        {isDoubtful(trace) && <span className="session-timeline-doubt">сомнение</span>}
      </span>
      {!compact && (
        <span className="session-timeline-topics">
          {trace.scenarios.length
            ? trace.scenarios.map((scenario, index) => <ScenarioChip key={`${scenario.scenario_id}-${index}`} id={scenario.scenario_id} />)
            : <DecisionBadge decision={trace.decision} />}
        </span>
      )}
    </>
  )
}

type PlacedTurn = { trace: Trace; timing: TurnTiming; left: number; width: number; lane: number }

export function SessionTimeline({ model, traces, selected, onSelect }: {
  model: ReturnType<typeof buildSessionTimeline>
  traces: Trace[]
  selected: number
  onSelect: (turn: number) => void
}) {
  const { scenarioName } = useCatalog()
  if (!traces.length) return <p className="session-timeline-empty">Ходов пока нет.</p>

  if (!model.hasTiming) {
    return (
      <div className="session-timeline">
        <p className="session-timeline-note">Таймкоды пока недоступны. Ходы показаны по порядку.</p>
        <div className="session-timeline-sequence" aria-label="Последовательность тем без временной шкалы">
          {traces.map((trace) => (
            <button
              key={trace.turn}
              type="button"
              className={`session-timeline-sequence-turn${trace.turn === selected ? ' is-selected' : ''}${isDoubtful(trace) ? ' is-doubtful' : ''}`}
              aria-pressed={trace.turn === selected}
              aria-label={`Ход ${trace.turn}, ${topics(trace, scenarioName)}${isDoubtful(trace) ? ', сомнение' : ''}`}
              onClick={() => onSelect(trace.turn)}
            >
              <TurnContent trace={trace} />
            </button>
          ))}
        </div>
        <p className="session-timeline-note">Темы показаны по ходам, без привязки к отдельным словам.</p>
      </div>
    )
  }

  const timed = traces.filter((trace) => model.turns.has(trace.turn))
    .sort((a, b) => model.turns.get(a.turn)!.startMs - model.turns.get(b.turn)!.startMs)
  const withoutTiming = traces.filter((trace) => !model.turns.has(trace.turn))
  const canvasWidth = Math.min(4800, Math.max(780, traces.length * 160, Math.ceil(model.durationMs / 1000) * 28))
  const greeting = model.turns.get(0)
  const greetingWidth = greeting ? Math.min(canvasWidth, Math.max(86, Math.max(0, greeting.endMs - greeting.startMs) / model.durationMs * canvasWidth)) : 0
  const greetingLeft = greeting ? Math.min(canvasWidth - greetingWidth, Math.max(0, greeting.startMs) / model.durationMs * canvasWidth) : 0
  const occupiedUntil: number[] = greeting ? [greetingLeft + greetingWidth] : []
  const placed: PlacedTurn[] = timed.map((trace) => {
    const timing = model.turns.get(trace.turn)!
    const start = Math.min(model.durationMs, Math.max(0, timing.startMs))
    const end = Math.min(model.durationMs, Math.max(start, timing.endMs))
    const width = Math.min(canvasWidth, Math.max(46, (end - start) / model.durationMs * canvasWidth))
    const left = Math.min(canvasWidth - width, start / model.durationMs * canvasWidth)
    let lane = occupiedUntil.findIndex((until) => left >= until + 6)
    if (lane < 0) lane = occupiedUntil.length
    occupiedUntil[lane] = left + width
    return { trace, timing, left, width, lane }
  })
  const ticks = Array.from({ length: 5 }, (_, index) => index / 4)

  return (
    <div className="session-timeline">
      <div className="session-timeline-summary">
        <span>Временная шкала звонка</span>
        <span className="session-timeline-duration">{formatTime(model.durationMs)}</span>
      </div>
      <div className="session-timeline-scroll" role="region" aria-label="Временная шкала звонка, прокрутка по горизонтали" tabIndex={0}>
        <div className="session-timeline-canvas" style={{ minWidth: canvasWidth + 44 }}>
          <div className="session-timeline-axis" aria-hidden="true">
            {ticks.map((part) => (
              <span key={part} className="session-timeline-tick" style={{ left: `${part * 100}%` }}>
                {formatTime(model.durationMs * part)}
              </span>
            ))}
          </div>
          <div className="session-timeline-track" style={{ height: Math.max(82, occupiedUntil.length * 70 + 8) }}>
            {ticks.map((part) => <span key={part} className="session-timeline-guide" style={{ left: `${part * 100}%` }} aria-hidden="true" />)}
            {greeting && (
              <span
                className="session-timeline-greeting"
                style={{ left: `${greetingLeft / canvasWidth * 100}%`, top: 8, width: `${greetingWidth / canvasWidth * 100}%` }}
                title={`Приветствие бота, ${formatTime(greeting.startMs)}–${formatTime(greeting.endMs)}`}
              >
                Приветствие <span>{formatTime(greeting.startMs)}</span>
              </span>
            )}
            {placed.map(({ trace, timing, left, width, lane }) => {
              const compact = width < 100
              const label = `Ход ${trace.turn}, ${formatTime(timing.startMs)}–${formatTime(timing.endMs)}, ${topics(trace, scenarioName)}${isDoubtful(trace) ? ', сомнение' : ''}`
              return (
                <button
                  key={trace.turn}
                  type="button"
                  className={`session-timeline-block${trace.turn === selected ? ' is-selected' : ''}${isDoubtful(trace) ? ' is-doubtful' : ''}${compact ? ' is-compact' : ''}`}
                  style={{ left: `${left / canvasWidth * 100}%`, top: lane * 70 + 8, width: `${width / canvasWidth * 100}%` }}
                  aria-label={label}
                  aria-pressed={trace.turn === selected}
                  title={label}
                  onClick={() => onSelect(trace.turn)}
                >
                  <TurnContent trace={trace} compact={compact} />
                </button>
              )
            })}
          </div>
        </div>
      </div>
      {withoutTiming.length > 0 && (
        <div className="session-timeline-missing">
          <span className="session-timeline-note">Ходы без таймкода:</span>
          {withoutTiming.map((trace) => (
            <button
              key={trace.turn}
              type="button"
              className={`session-timeline-missing-turn${trace.turn === selected ? ' is-selected' : ''}${isDoubtful(trace) ? ' is-doubtful' : ''}`}
              aria-pressed={trace.turn === selected}
              aria-label={`Ход ${trace.turn}, ${topics(trace, scenarioName)}, без таймкода`}
              onClick={() => onSelect(trace.turn)}
            >
              Ход {trace.turn}
            </button>
          ))}
        </div>
      )}
      <p className="session-timeline-note">Время приблизительное: темы относятся ко всему ходу.</p>
    </div>
  )
}
