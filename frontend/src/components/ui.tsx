import type { ReactNode } from 'react'
import { FLAG_CONFIDENCE, type Decision } from '../api'
import { useCatalog } from '../lib/catalog-context'
import { DECISION_LABEL, LANG_LABEL, isSystemScenario, ms, pct, totalTone } from '../lib/format'

/** Блок экрана. hint — короткая подпись «что здесь и зачем», чтобы интерфейс читался без разработчика */
export function Panel(props: { title: ReactNode; hint?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
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

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div className="callout callout-bad">
      Не удалось загрузить данные: {error}. Бэкенд или мок‑сервер запущен на :8000?
    </div>
  )
}

export type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'info'

export function Badge({ tone = 'neutral', title, children }: { tone?: Tone; title?: string; children: ReactNode }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  )
}

export function LangBadge({ lang }: { lang: string }) {
  return <Badge tone={lang === 'mixed' ? 'info' : 'neutral'}>{LANG_LABEL[lang] ?? lang}</Badge>
}

export function ConfidenceBadge({ value }: { value: number }) {
  const tone = value >= 0.9 ? 'ok' : value >= FLAG_CONFIDENCE ? 'neutral' : 'warn'
  return (
    <Badge tone={tone} title={value < FLAG_CONFIDENCE ? `Ниже порога ${FLAG_CONFIDENCE} — ход спорный` : 'Уверенность роутера'}>
      {pct(value)}
    </Badge>
  )
}

const DECISION_TONE: Record<Decision, Tone> = {
  run: 'ok',
  continue: 'ok',
  confirm: 'info',
  cancel: 'neutral',
  clarify: 'warn',
  handoff: 'bad',
  out_of_scope: 'warn',
  goodbye: 'neutral',
}

export function DecisionBadge({ decision }: { decision: Decision }) {
  return <Badge tone={DECISION_TONE[decision] ?? 'neutral'}>{DECISION_LABEL[decision] ?? decision}</Badge>
}

/** Сценарий по-русски, id мелко рядом. Системные (SYS_*) — отдельным цветом */
export function ScenarioChip({ id, confidence, title }: { id: string; confidence?: number; title?: string }) {
  const { scenarioName } = useCatalog()
  return (
    <span className={`chip ${isSystemScenario(id) ? 'chip-sys' : ''}`} title={title ?? id}>
      {scenarioName(id)}
      {confidence !== undefined && <span className="chip-conf">{pct(confidence)}</span>}
    </span>
  )
}

/** Задержка «до ответа» с цветом по порогам 1500 / 3000 мс. Цвет всегда дублируется числом */
export function TotalLatency({ value, big }: { value: number; big?: boolean }) {
  return <span className={`num text-${totalTone(value)} ${big ? 'num-big' : ''}`}>{ms(value)}</span>
}

export function Kpi({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ''}`} title={hint}>
      <div className="kpi-value num">{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  )
}

/**
 * Горизонтальные полоски «сколько чего»: один ряд, один цвет, значение подписано числом.
 * Отсортированный список с полосками — он же и табличное представление.
 */
export function BarList(props: { rows: { key: string; label: ReactNode; value: number; hint?: string }[]; format?: (v: number) => string }) {
  const max = Math.max(1, ...props.rows.map((r) => r.value))
  const total = props.rows.reduce((a, r) => a + r.value, 0)
  if (!props.rows.length) return <p className="muted small">Нет данных</p>
  return (
    <table className="barlist">
      <tbody>
        {props.rows.map((r) => (
          <tr key={r.key} title={r.hint ?? `${r.value} из ${total}`}>
            <td className="barlist-label">{r.label}</td>
            <td className="barlist-bar">
              <div className="bar">
                <div className="bar-fill" style={{ width: `${(r.value / max) * 100}%` }} />
              </div>
            </td>
            <td className="barlist-value num">{props.format ? props.format(r.value) : r.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
