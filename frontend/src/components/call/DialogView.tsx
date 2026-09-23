import { useEffect, useRef } from 'react'
import type { Turn } from '../../api'
import { DecisionBadge, Empty, LangBadge } from '../ui'

/** Лента диалога. Клик по реплике — показать её трассировку справа */
export function DialogView(props: { turns: Turn[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => end.current?.scrollIntoView({ behavior: 'smooth' }), [props.turns.length])

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
      {props.turns.map((t) => (
        <div
          key={t.id}
          className={`turn ${t.id === props.selectedId ? 'turn-selected' : ''}`}
          onClick={() => props.onSelect(t.id)}
        >
          <div className="bubble bubble-user">
            <div className="bubble-meta">
              Клиент · {t.index} <LangBadge lang={t.user.lang} />
            </div>
            {t.user.text}
          </div>
          <div className="bubble bubble-bot">
            <div className="bubble-meta">
              Робот <DecisionBadge decision={t.trace.decision} />
              {t.trace.selected && <span className="muted small"> {t.trace.selected.title}</span>}
            </div>
            {t.bot.text}
          </div>
        </div>
      ))}
      <div ref={end} />
    </div>
  )
}
