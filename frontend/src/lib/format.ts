import { TARGET_TOTAL_MS, WARN_TOTAL_MS, type Channel, type Decision, type FastPath, type LatencyStage } from '../api'

export const pct = (x: number) => `${Math.round(x * 100)}%`
export const ms = (x: number | undefined | null) => (x === undefined || x === null ? '—' : `${Math.round(x)} мс`)
export const time = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

export function duration(fromIso: string, toIso: string | null) {
  const s = Math.max(0, Math.round(((toIso ? Date.parse(toIso) : Date.now()) - Date.parse(fromIso)) / 1000))
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин ${s % 60} с`
}

/** Цвет задержки «до ответа» по порогам из docs/frontend: зелёный ≤ 1500, жёлтый ≤ 3000 */
export const totalTone = (x: number) => (x <= TARGET_TOTAL_MS ? 'ok' : x <= WARN_TOTAL_MS ? 'warn' : 'bad')

export const LANG_LABEL: Record<string, string> = { ru: 'RU', kk: 'KZ', mixed: 'RU+KZ' }

export const CHANNEL_LABEL: Record<Channel, string> = { web: 'веб', phone: 'телефон', text: 'текст' }

export const DECISION_LABEL: Record<Decision, string> = {
  run: 'Запуск сценария',
  continue: 'Продолжение',
  confirm: 'Подтверждение',
  cancel: 'Отмена',
  clarify: 'Уточнение',
  handoff: 'Перевод оператору',
  out_of_scope: 'Вне области',
  goodbye: 'Прощание',
}

export const FAST_PATH_LABEL: Record<FastPath, string> = {
  slot_pattern: 'слот по шаблону',
  yes_no: 'да/нет',
  operator_request: 'просьба оператора',
  goodbye_word: 'прощание',
  fast_path_scenario: 'быстрый сценарий',
}

export const RESPONSE_SOURCE_LABEL: Record<string, string> = {
  template: 'шаблон',
  kb_template: 'шаблон из базы знаний',
  llm: 'LLM',
}

export const STAGES: { key: LatencyStage; label: string }[] = [
  { key: 'stt', label: 'Распознавание (STT)' },
  { key: 'triage', label: 'Триаж' },
  { key: 'router', label: 'Выбор сценария' },
  { key: 'response', label: 'Ответ' },
  { key: 'tts_first_audio', label: 'Синтез до первого звука' },
]

export const STACK_REASON_LABEL: Record<string, string> = {
  topic_switch: 'смена темы',
  multi_intent: 'несколько тем в реплике',
  deferred_by_client: 'клиент отложил',
}

export const END_REASON_LABEL: Record<string, string> = {
  goodbye: 'завершён',
  handoff: 'оператор',
  hangup: 'клиент положил трубку',
  timeout: 'таймаут',
  error: 'ошибка',
}

export const isSystemScenario = (id: string) => id.startsWith('SYS_')
