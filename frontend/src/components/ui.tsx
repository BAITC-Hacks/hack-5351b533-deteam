import type { ReactNode } from 'react'
import { LOW_CONFIDENCE, type Decision, type Lang, type RoutePath } from '../api'
import { DECISION_LABEL, LANG_LABEL, ROUTE_LABEL, pct } from '../lib/format'

/** Блок экрана. hint — короткая подпись «что здесь и зачем», чтобы интерфейс читался без разработчика */
export function Panel(props: { title: string; hint?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${props.className ?? ''}`}>
      <header className="panel-header">
        <div>
          <h2>{props.title}</h2>
          {props.hint && <p className="hint">{props.hint}</p>}
        </div>
        {props.actions}
      </header>
      <div className="panel-body">{props.children}</div>
    </section>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'info'

export function Badge({ tone = 'neutral', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  )
}

export function LangBadge({ lang }: { lang: Lang }) {
  return <Badge tone={lang === 'mixed' ? 'info' : 'neutral'}>{LANG_LABEL[lang]}</Badge>
}

export function ConfidenceBadge({ value }: { value: number }) {
  const tone = value >= 0.8 ? 'ok' : value >= LOW_CONFIDENCE ? 'warn' : 'bad'
  return (
    <Badge tone={tone} title="Уверенность LLM-слоя">
      {pct(value)}
    </Badge>
  )
}

const DECISION_TONE: Record<Decision, Tone> = {
  run_scenario: 'ok',
  switch_scenario: 'info',
  resume_scenario: 'info',
  clarify: 'warn',
  handoff: 'bad',
}

export function DecisionBadge({ decision }: { decision: Decision }) {
  return <Badge tone={DECISION_TONE[decision]}>{DECISION_LABEL[decision]}</Badge>
}

export function RouteBadge({ path }: { path: RoutePath }) {
  return (
    <Badge tone={path === 'fast' ? 'ok' : 'neutral'} title="Какой путь маршрутизации сработал">
      {ROUTE_LABEL[path]}
    </Badge>
  )
}

export function Kpi({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ''}`} title={hint}>
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  )
}
