import { useEffect, useState } from 'react'
import { api, type Scenario, type Turn } from '../../api'

/** Разметка реплики супервизором: из неё считаются точность и «Робот путает» */
export function FeedbackBar({ turn, onSaved }: { turn: Turn; onSaved: () => void }) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  useEffect(() => {
    api.listScenarios().then(setScenarios)
  }, [])

  async function mark(correct: boolean, expected?: string) {
    await api.sendFeedback(turn.id, { correct, expected_scenario_id: expected })
    onSaved()
  }

  const expected = scenarios.find((s) => s.id === turn.feedback?.expected_scenario_id)

  return (
    <div className="feedback">
      <span className="muted small">Сценарий выбран верно?</span>
      <button className={turn.feedback?.correct === true ? 'btn-ok' : ''} onClick={() => mark(true)}>
        ✓ Верно
      </button>
      <select
        className={turn.feedback?.correct === false ? 'btn-bad' : ''}
        value={turn.feedback?.correct === false ? (turn.feedback.expected_scenario_id ?? '') : ''}
        onChange={(e) => e.target.value && mark(false, e.target.value)}
        title="Выберите, какой сценарий был правильным"
      >
        <option value="">✗ Ошибка — нужно было…</option>
        {scenarios.map((s) => (
          <option key={s.id} value={s.id}>
            {s.title}
          </option>
        ))}
      </select>
      {expected && <span className="small text-bad">нужно было: {expected.title}</span>}
    </div>
  )
}
