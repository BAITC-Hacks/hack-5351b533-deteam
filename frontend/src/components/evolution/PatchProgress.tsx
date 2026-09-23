import type { PatchStage } from '../../api'
import { Icon } from '../Icon'

const STEPS: { stage: PatchStage; label: string }[] = [
  { stage: 'proposing', label: 'Модель предлагает правку' },
  { stage: 'validating', label: 'Проверка правил' },
  { stage: 'regression_before', label: 'Регрессия: до' },
  { stage: 'regression_after', label: 'Регрессия: после' },
  { stage: 'ready', label: 'Готово' },
]

export interface Progress {
  stage: PatchStage
  done?: number
  total?: number
  message?: string
}

/** Стадии патча из событий patch.progress: proposing → validating → regression_before → regression_after → ready */
export function PatchProgress({ patchId, progress }: { patchId: string; progress: Progress | null }) {
  const current = progress ? STEPS.findIndex((s) => s.stage === progress.stage) : 0
  const failed = progress?.stage === 'failed'
  return (
    <div className="progress">
      <div className="muted small">
        Патч <code>{patchId}</code> {failed ? '— не удалось' : 'готовится…'}
      </div>
      <ol className="stepper">
        {STEPS.map((s, i) => (
          <li
            key={s.stage}
            className={i < current ? 'step-done' : i === current && !failed ? 'step-active' : ''}
            aria-current={i === current && !failed ? 'step' : undefined}
            aria-label={`${s.label} — ${i < current ? 'завершено' : i === current && !failed ? 'выполняется' : 'ожидает'}`}
          >
            <span className="step-mark">{i < current ? <Icon name="check" size={15} spacing="none" /> : i + 1}</span>
            {s.label}
            {i === current && progress?.total ? (
              <span className="num small">
                {' '}
                {progress.done}/{progress.total}
              </span>
            ) : null}
          </li>
        ))}
      </ol>
      {progress?.total ? (
        <div className="bar">
          <div className="bar-fill" style={{ width: `${((progress.done ?? 0) / progress.total) * 100}%` }} />
        </div>
      ) : null}
      {progress?.message && <div className="small">{progress.message}</div>}
    </div>
  )
}
