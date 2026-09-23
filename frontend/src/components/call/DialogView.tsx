import { useEffect, useRef } from 'react'
import { LOW_CONFIDENCE, TARGET_TOTAL_MS, type Turn } from '../../api'
import { ms } from '../../lib/format'
import { Badge, ConfidenceBadge, DecisionBadge, Empty, LangBadge, RouteBadge } from '../ui'

/** Лента диалога. Под каждым ответом робота — краткий итог роутинга. Клик по реплике — её трассировка */
export function DialogView(props: { turns: Turn[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [props.turns.length])

  if (!props.turns.length) {
    return (
      <Empty>
        Скажите в микрофон или напишите, например:
        <br />
        <em>«Я вчера оплатил, деньги списались, а полис не подтвердился… и адрес доставки поменять надо»</em>
      </Empty>
    )
  }

  return (
    <div className="dialog">
      {props.turns.map((t) => {
        const doubt = t.trace.decision !== 'handoff' && (t.trace.selected?.confidence ?? 0) < LOW_CONFIDENCE
        const slow = t.trace.latency.total_ms > TARGET_TOTAL_MS
        return (
          <div
            key={t.id}
            className={`turn ${t.id === props.selectedId ? 'turn-selected' : ''} ${t.feedback?.correct === false ? 'turn-wrong' : ''}`}
            onClick={() => props.onSelect(t.id)}
          >
            <div className="bubble bubble-user">
              <div className="bubble-meta">
                Клиент · {t.index} <LangBadge lang={t.user.lang} />
                {t.user.emotion === 'negative' && <Badge tone="bad">негатив</Badge>}
              </div>
              {t.user.text}
            </div>
            <div className="bubble bubble-bot">
              <div className="bubble-meta">
                Робот <DecisionBadge decision={t.trace.decision} />
                {t.trace.selected && (
                  <>
                    <span>{t.trace.selected.title}</span>
                    <ConfidenceBadge value={t.trace.selected.confidence} />
                  </>
                )}
                <RouteBadge path={t.trace.route_path} />
                <span className={slow ? 'text-bad' : ''}>{ms(t.trace.latency.total_ms)}</span>
                {doubt && <Badge tone="warn">сомневался</Badge>}
                {t.feedback && (
                  <Badge tone={t.feedback.correct ? 'ok' : 'bad'}>{t.feedback.correct ? '✓ верно' : '✗ ошибка'}</Badge>
                )}
              </div>
              {t.bot.text}
            </div>
          </div>
        )
      })}
      <div ref={end} />
    </div>
  )
}
