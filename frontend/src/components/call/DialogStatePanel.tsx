import type { DialogState } from '../../api'
import { STACK_REASON_LABEL } from '../../lib/format'
import { Badge, Empty, Panel, ScenarioChip } from '../ui'

/** Состояние диалога из dialog.state: клиент, активный сценарий и шаг, стек отложенных тем, слоты, ожидаемое подтверждение */
export function DialogStatePanel({ state }: { state: DialogState | null }) {
  if (!state) {
    return (
      <Panel title="Состояние диалога">
        <Empty>Появится после первого хода</Empty>
      </Panel>
    )
  }
  const slots = Object.entries(state.slots ?? {})
  return (
    <Panel title="Состояние диалога">
      <table className="kv">
        <tbody>
          <tr>
            <td>Клиент</td>
            <td>{state.client ? `${state.client.full_name} · опознан по ${state.client.identified_by}` : 'не опознан'}</td>
          </tr>
          <tr>
            <td>Активный сценарий</td>
            <td>
              {state.active ? (
                <>
                  <ScenarioChip id={state.active.scenario_id} /> <span className="muted small">шаг {state.active.step}</span>
                  {state.active.expected_slot && <div className="small">ждём слот <code>{state.active.expected_slot}</code></div>}
                  {state.active.missing_slots && state.active.missing_slots.length > 0 && (
                    <div className="small text-warn">не хватает: {state.active.missing_slots.join(', ')}</div>
                  )}
                </>
              ) : (
                '—'
              )}
            </td>
          </tr>
          <tr>
            <td>Отложенные темы</td>
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
              <td>Ждём «да»</td>
              <td>
                <Badge tone="warn">{state.pending_confirmation.action}</Badge> {state.pending_confirmation.spoken_summary}
              </td>
            </tr>
          )}
          <tr>
            <td>Завершено</td>
            <td>
              <span className="row gap wrap">{state.completed.length ? state.completed.map((id) => <ScenarioChip key={id} id={id} />) : '—'}</span>
            </td>
          </tr>
          {state.unclear_streak > 0 && (
            <tr>
              <td>Непонятно подряд</td>
              <td className="text-warn">{state.unclear_streak}</td>
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
