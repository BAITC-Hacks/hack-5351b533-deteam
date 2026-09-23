import type { CallFlag, CallOutcome, Decision, Emotion, Lang, RoutePath } from '../api'

export const pct = (x: number) => `${Math.round(x * 100)}%`
export const ms = (x: number | undefined) => (x === undefined ? '—' : `${Math.round(x)} мс`)
export const time = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU')

export const LANG_LABEL: Record<Lang, string> = { ru: 'RU', kk: 'KZ', mixed: 'RU+KZ' }

export const ROUTE_LABEL: Record<RoutePath, string> = { fast: 'быстрый путь', llm: 'LLM' }

export const DECISION_LABEL: Record<Decision, string> = {
  run_scenario: 'Запуск сценария',
  switch_scenario: 'Смена темы',
  resume_scenario: 'Возврат к теме',
  clarify: 'Переспрос',
  handoff: 'Передача оператору',
}

export const EMOTION_LABEL: Record<Emotion, string> = {
  neutral: 'нейтрально',
  positive: 'позитив',
  negative: 'негатив',
}

export const OUTCOME_LABEL: Record<CallOutcome, string> = {
  active: 'идёт сейчас',
  resolved: 'решено роботом',
  unresolved: 'не решено',
  handed_off: 'оператор',
}

export const FLAG_LABEL: Record<CallFlag, string> = {
  low_confidence: 'сомневался',
  slow: 'медленно',
  marked_wrong: 'ошибка',
  mixed_lang: 'RU+KZ',
}

export function duration(fromIso: string, toIso: string) {
  const s = Math.max(0, Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000))
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`
}
