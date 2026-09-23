import { useState } from 'react'
import { api, type Case, type Trace } from '../../api'
import { useCatalog } from '../../lib/catalog-context'
import { ScenarioChip } from '../ui'

/**
 * «Неверно» у хода: супервизор выбирает правильные сценарии по порядку (первый — основной),
 * из этого создаётся кейс регрессии POST /api/cases. Дальше кейс попадает в «Эволюцию».
 */
export function CaseForm({ sessionId, trace, existing, onCreated }: { sessionId: string; trace: Trace; existing: Case[]; onCreated: (c: Case) => void }) {
  const { catalog, scenarioName } = useCatalog()
  const [open, setOpen] = useState(false)
  const [expected, setExpected] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const observed = trace.scenarios.map((s) => s.scenario_id)

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      const created = await api.createCase({
        session_id: sessionId,
        turn: trace.turn,
        text: trace.transcript,
        lang: trace.language,
        expected,
        note: note.trim() || undefined,
      })
      onCreated(created)
      setOpen(false)
      setExpected([])
      setNote('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="case-form">
      {existing.map((c) => (
        <div key={c.case_id} className="callout callout-bad">
          Отмечено как ошибка · кейс <code>{c.case_id}</code>. Нужно было:{' '}
          {c.expected.map((id) => (
            <ScenarioChip key={id} id={id} />
          ))}
          {c.note && <div className="small">{c.note}</div>}
        </div>
      ))}

      {!open ? (
        <div className="row gap">
          <span className="muted small">Робот выбрал неправильно?</span>
          <button className="btn-bad" onClick={() => setOpen(true)}>
            ✗ Неверно
          </button>
        </div>
      ) : (
        <div className="case-editor">
          <div className="small">
            Робот выбрал:{' '}
            {observed.length ? observed.map((id) => <ScenarioChip key={id} id={id} />) : <span className="muted">ничего</span>}
          </div>
          <label className="small">Правильные сценарии по порядку — первый основной:</label>
          <div className="row gap wrap">
            {expected.map((id, i) => (
              <span key={id} className="chip chip-picked">
                {i + 1}. {scenarioName(id)}
                <button className="chip-x" onClick={() => setExpected(expected.filter((x) => x !== id))} title="Убрать">
                  ×
                </button>
              </span>
            ))}
            <select value="" onChange={(e) => e.target.value && setExpected([...expected, e.target.value])}>
              <option value="">+ добавить сценарий…</option>
              {(catalog?.scenarios ?? [])
                .filter((s) => !expected.includes(s.scenario_id))
                .map((s) => (
                  <option key={s.scenario_id} value={s.scenario_id}>
                    {s.scenario_id} · {s.name_ru}
                  </option>
                ))}
            </select>
          </div>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Комментарий: почему это ошибка (необязательно)" rows={2} />
          {error && <div className="callout callout-bad">{error}</div>}
          <div className="row gap">
            <button className="primary" disabled={!expected.length || saving} onClick={submit}>
              {saving ? 'Сохраняю…' : 'Создать кейс'}
            </button>
            <button onClick={() => setOpen(false)}>Отмена</button>
          </div>
        </div>
      )}
    </div>
  )
}
