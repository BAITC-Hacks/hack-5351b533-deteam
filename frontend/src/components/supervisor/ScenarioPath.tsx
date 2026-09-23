import type { CallSummary } from '../../api'

type Step = CallSummary['scenario_path'][number]

function label(step: Step) {
  if (step.decision === 'handoff') return '👤 Оператор'
  if (step.decision === 'clarify') return '? переспрос'
  const title = step.scenario?.title ?? '—'
  return step.decision === 'resume_scenario' ? `↩ ${title}` : title
}

/**
 * Как шёл разговор: по шагу на реплику, одинаковые подряд схлопываются.
 * Кликабельный вариант (onSelect) — лента тем в карточке звонка.
 */
export function ScenarioPath(props: { path: Step[]; selectedIndex?: number; onSelect?: (index: number) => void }) {
  const steps: { step: Step; indexes: number[] }[] = []
  props.path.forEach((step, i) => {
    const prev = steps.at(-1)
    const same = prev && prev.step.decision !== 'clarify' && step.decision === 'run_scenario' && prev.step.scenario?.id === step.scenario?.id
    if (same) prev.indexes.push(i)
    else steps.push({ step, indexes: [i] })
  })

  if (!steps.length) return <span className="muted">—</span>

  return (
    <span className="path">
      {steps.map(({ step, indexes }, i) => (
        <span key={i} className="path-item">
          {i > 0 && <span className="path-arrow">→</span>}
          <span
            className={`path-step path-${step.decision} ${props.selectedIndex !== undefined && indexes.includes(props.selectedIndex) ? 'path-selected' : ''} ${props.onSelect ? 'path-clickable' : ''}`}
            onClick={props.onSelect && (() => props.onSelect!(indexes[0]))}
            title={`Реплики ${indexes.map((x) => x + 1).join(', ')}`}
          >
            {label(step)}
          </span>
        </span>
      ))}
    </span>
  )
}
