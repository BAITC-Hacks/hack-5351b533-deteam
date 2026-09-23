import type { SessionSummary } from '../../api'
import { END_REASON_LABEL } from '../../lib/format'
import { Badge } from '../ui'

/** Итог звонка: идёт / завершён / переведён в очередь … */
export function SessionOutcome({ s }: { s: SessionSummary }) {
  if (!s.ended_at) return <Badge tone="info">идёт сейчас</Badge>
  if (s.handoff_queue) return <Badge tone="bad" title="Перевод на оператора">оператор · {s.handoff_queue}</Badge>
  const reason = s.end_reason ?? 'ended'
  return <Badge tone={reason === 'goodbye' ? 'ok' : 'neutral'}>{END_REASON_LABEL[reason] ?? reason}</Badge>
}
