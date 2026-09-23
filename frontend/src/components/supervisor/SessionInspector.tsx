import type { ReactNode } from 'react'
import type { DialogState, Trace } from '../../api'
import { STACK_REASON_LABEL } from '../../lib/format'
import { Empty, Panel, ScenarioChip } from '../ui'
import { TracePanel } from '../trace/TracePanel'
import './SessionInspector.css'

export type SessionInspectorTab = 'turn' | 'outcome'

interface SessionInspectorProps {
  trace: Trace | null
  finalState: DialogState | null
  ended: boolean
  extra?: ReactNode
  activeTab: SessionInspectorTab
  onTabChange: (tab: SessionInspectorTab) => void
}

/** Решение по выбранному ходу и итог всего звонка живут рядом, но не смешиваются. */
export function SessionInspector({ trace, finalState, ended, extra, activeTab, onTabChange }: SessionInspectorProps) {
  const shownTab = activeTab === 'outcome' && finalState ? 'outcome' : 'turn'

  return (
    <Panel
      className="session-inspector trace"
      title="Разбор звонка"
      hint={ended ? 'Решение робота по ходу и состояние в конце разговора' : 'Решение робота по ходу и текущее состояние разговора'}
      actions={
        <div className="session-inspector-tabs" role="group" aria-label="Раздел разбора звонка">
          <button
            type="button"
            className="session-inspector-tab"
            aria-pressed={shownTab === 'turn'}
            onClick={() => onTabChange('turn')}
          >
            {trace ? `Ход ${trace.turn}` : 'Ход'}
          </button>
          {finalState && (
            <button
              type="button"
              className="session-inspector-tab"
              aria-pressed={shownTab === 'outcome'}
              onClick={() => onTabChange('outcome')}
            >
              {ended ? 'Итог звонка' : 'Состояние звонка'}
            </button>
          )}
        </div>
      }
    >
      {shownTab === 'outcome' && finalState ? (
        <FinalOutcome state={finalState} ended={ended} />
      ) : trace ? (
        <TracePanel trace={trace} extra={extra} embedded />
      ) : (
        <Empty>Выберите ход в диалоге, чтобы увидеть решение робота.</Empty>
      )}
    </Panel>
  )
}

/** final_state относится ко всему звонку, а не к текущему выбранному ходу. */
function FinalOutcome({ state, ended }: { state: DialogState; ended: boolean }) {
  const slots = Object.entries(state.slots ?? {})

  return (
    <div className="session-inspector-outcome">
      <p className="session-inspector-outcome-note">
        {ended ? 'Последнее сохранённое состояние разговора.' : 'Текущее сохранённое состояние разговора.'} Оно не меняется при выборе хода.
      </p>

      <dl className="session-inspector-facts">
        <div className="session-inspector-fact">
          <dt>Клиент</dt>
          <dd>{state.client ? <>{state.client.full_name} <span className="muted small">· опознан по {state.client.identified_by}</span></> : 'Не опознан'}</dd>
        </div>

        <div className="session-inspector-fact">
          <dt>Завершённые сценарии</dt>
          <dd>
            {state.completed.length ? (
              <span className="row gap wrap">{state.completed.map((id) => <ScenarioChip key={id} id={id} />)}</span>
            ) : '—'}
          </dd>
        </div>

        <div className="session-inspector-fact">
          <dt>Активный сценарий</dt>
          <dd>{state.active ? <><ScenarioChip id={state.active.scenario_id} /> <span className="muted small">шаг {state.active.step}</span></> : '—'}</dd>
        </div>

        <div className="session-inspector-fact">
          <dt>Отложенные темы</dt>
          <dd>
            {state.stack.length ? state.stack.map((item) => (
              <div className="session-inspector-stack-item" key={item.scenario_id}>
                <ScenarioChip id={item.scenario_id} />
                <span className="muted small">{STACK_REASON_LABEL[item.reason] ?? item.reason}</span>
              </div>
            )) : '—'}
          </dd>
        </div>

        {state.pending_confirmation && (
          <div className="session-inspector-fact">
            <dt>Ждёт подтверждения</dt>
            <dd><code>{state.pending_confirmation.action}</code> {state.pending_confirmation.spoken_summary}</dd>
          </div>
        )}

        {state.handoff && (
          <div className="session-inspector-fact">
            <dt>Перевод</dt>
            <dd>Очередь <code>{state.handoff.queue}</code></dd>
          </div>
        )}

        {slots.map(([key, value]) => (
          <div className="session-inspector-fact" key={key}>
            <dt><code>{key}</code></dt>
            <dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
