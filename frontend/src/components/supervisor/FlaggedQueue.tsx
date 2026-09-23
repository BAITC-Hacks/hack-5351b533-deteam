import { Link } from 'react-router'
import type { Stats } from '../../api'
import { ConfidenceBadge, DecisionBadge, Empty, ScenarioChip } from '../ui'

/** Очередь спорных ходов из stats.flagged_turns: низкая уверенность, SYS_UNCLEAR, ручные пометки */
export function FlaggedQueue({ turns }: { turns: Stats['flagged_turns'] }) {
  return (
    <div>
      {turns.length ? (
        <ul className="flagged">
          {turns.map((t) => (
            <li key={`${t.session_id}-${t.turn}`}>
              <Link to={`/supervisor/sessions/${encodeURIComponent(t.session_id)}?turn=${t.turn}`} onClick={() => window.scrollTo(0, 0)}>
                <div className="flagged-text">«{t.transcript}»</div>
                <div className="row gap wrap">
                  <ScenarioChip id={t.scenario_id} />
                  <ConfidenceBadge value={t.confidence} />
                  <DecisionBadge decision={t.decision} />
                </div>
                <div className="muted small">
                  {t.session_id} · ход {t.turn}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <Empty>Спорных ходов нет</Empty>
      )}
    </div>
  )
}
