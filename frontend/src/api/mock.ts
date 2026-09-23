// Mock-бэкенд для разработки UI без сервера. НЕ является роутером:
// «маршрутизация» здесь — примитивный поиск ключевых слов, лишь бы заполнить интерфейс данными.
// Настоящий LLM-слой, STT и TTS живут на бэкенде.
import {
  LOW_CONFIDENCE,
  TARGET_TOTAL_MS,
  type CallFlag,
  type CallOutcome,
  type CallSummary,
  type Client,
  type Decision,
  type Lang,
  type Scenario,
  type ScenarioCandidate,
  type ScenarioRef,
  type Session,
  type SupervisorStats,
  type Turn,
  type TurnInput,
  type TurnTrace,
  type VoiceRouterApi,
} from './types'

type MockScenario = Scenario & { keywords: string[]; reply: string }

const SCENARIOS: MockScenario[] = [
  {
    id: 'payment_not_confirmed',
    title: 'Оплата прошла, полис не подтверждён',
    description: 'Деньги списаны, но договор не активирован или статус не обновился.',
    boundaries: 'Не путать с «Статус выплаты» — там деньги получает клиент, здесь платит клиент.',
    params: [
      { name: 'payment_date', description: 'Дата оплаты', required: true },
      { name: 'amount', description: 'Сумма', required: false },
    ],
    actions: ['check_payment', 'activate_policy'],
    irreversible: false,
    examples: {
      ru: ['Я оплатил, деньги списались, а полис не пришёл'],
      kk: ['Мен төледім, ақша шешілді, бірақ полис расталмады'],
    },
    keywords: ['оплат', 'списал', 'не подтверд', 'төле', 'ақша', 'расталма'],
    reply: 'Вижу платёж, проверяю статус договора. Одну секунду.',
  },
  {
    id: 'change_address',
    title: 'Изменение адреса и контактов',
    description: 'Смена адреса доставки документов, телефона или email.',
    boundaries: 'Изменение условий полиса — отдельный сценарий.',
    params: [{ name: 'new_address', description: 'Новый адрес', required: true }],
    actions: ['update_contacts'],
    irreversible: false,
    examples: { ru: ['Надо поменять адрес доставки'], kk: ['Мекенжайды өзгерту керек'] },
    keywords: ['адрес', 'мекенжай', 'телефон поменять', 'почту поменять'],
    reply: 'Хорошо, продиктуйте новый адрес.',
  },
  {
    id: 'buy_auto_policy',
    title: 'Оформление автостраховки',
    description: 'Покупка или продление обязательного/добровольного автополиса.',
    boundaries: 'Страховой случай по авто — сценарий «Заявление о страховом случае».',
    params: [
      { name: 'car_number', description: 'Госномер', required: true },
      { name: 'period', description: 'Срок', required: false },
    ],
    actions: ['quote', 'issue_policy'],
    irreversible: false,
    examples: { ru: ['Хочу оформить страховку на машину'], kk: ['Көлікке сақтандыру рәсімдегім келеді'] },
    keywords: ['машин', 'авто', 'огпо', 'осаго', 'көлік'],
    reply: 'Помогу оформить полис. Назовите госномер автомобиля.',
  },
  {
    id: 'report_claim',
    title: 'Заявление о страховом случае',
    description: 'Клиент сообщает о ДТП, повреждении имущества и т.п.',
    boundaries: 'Если заявление уже подано и клиент спрашивает о деньгах — «Статус выплаты».',
    params: [
      { name: 'event_date', description: 'Дата события', required: true },
      { name: 'event_type', description: 'Тип события', required: true },
    ],
    actions: ['create_claim'],
    irreversible: false,
    examples: { ru: ['Я попал в аварию'], kk: ['Жол апатына түстім'] },
    keywords: ['дтп', 'авари', 'страховой случай', 'апат', 'затопил', 'украли'],
    reply: 'Сочувствую. Все ли в порядке? Давайте зафиксирую обращение: когда это произошло?',
  },
  {
    id: 'claim_status',
    title: 'Статус выплаты',
    description: 'Когда и сколько выплатят по поданному заявлению.',
    boundaries: 'Подача нового заявления — «Заявление о страховом случае».',
    params: [{ name: 'claim_number', description: 'Номер заявления', required: false }],
    actions: ['get_claim_status'],
    irreversible: false,
    examples: { ru: ['Когда будет выплата?'], kk: ['Төлем қашан түседі?'] },
    keywords: ['выплат', 'когда деньги', 'статус заявлен', 'төлем қашан'],
    reply: 'Проверяю статус вашего заявления.',
  },
  {
    id: 'cancel_policy',
    title: 'Расторжение полиса',
    description: 'Досрочное расторжение договора с возвратом части премии.',
    boundaries: 'Необратимое действие — только после явного подтверждения клиента.',
    params: [{ name: 'policy_number', description: 'Номер полиса', required: true }],
    actions: ['cancel_policy'],
    irreversible: true,
    examples: { ru: ['Хочу расторгнуть договор'], kk: ['Келісімшартты бұзғым келеді'] },
    keywords: ['растор', 'отказаться от полиса', 'бұз', 'аннул'],
    reply: 'Могу расторгнуть полис. Это действие необратимо — подтвердите, пожалуйста: да или нет?',
  },
  {
    id: 'policy_info',
    title: 'Информация о полисе',
    description: 'Срок действия, покрытие, что входит в страховку.',
    boundaries: 'Изменение условий — не этот сценарий.',
    params: [{ name: 'policy_number', description: 'Номер полиса', required: false }],
    actions: ['get_policy'],
    irreversible: false,
    examples: { ru: ['До какого числа действует мой полис?'], kk: ['Полисім қашанға дейін жарамды?'] },
    keywords: ['срок действ', 'что покрывает', 'мой полис', 'полисім', 'жарамды'],
    reply: 'Ваш полис действует до 31.12. Рассказать, что в него входит?',
  },
]

const CLIENTS: Client[] = [
  { id: 'c1', name: 'Айгерим С.', summary: 'Автополис + страховка квартиры' },
  { id: 'c2', name: 'Иван П.', summary: 'Открытое заявление о страховом случае' },
  { id: 'c3', name: 'Данияр К.', summary: 'Новый клиент, полисов нет' },
]

// Что «распознаёт» mock-STT при голосовом вводе
const SAMPLE_UTTERANCES = [
  'Здравствуйте, я вчера оплатил, деньги списались, а полис не подтвердился… а, и ещё, адрес доставки поменять надо',
  'Сәлеметсіз бе, көлікке сақтандыру керек',
  'Мен вчера төледім, но полис әлі жоқ',
  'Когда будет выплата по моему заявлению?',
  'Хочу расторгнуть договор',
  'Соедините с оператором',
]

const ref = (s: ScenarioRef): ScenarioRef => ({ id: s.id, title: s.title })
const rand = (min: number, max: number) => Math.round(min + Math.random() * (max - min))
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function detectLang(text: string): Lang {
  const kk = /[әғқңөұүһі]/i.test(text)
  const ru = /(^|\P{L})(и|но|а|надо|вчера|хочу|мне|я|ещё|еще)(?=\P{L}|$)/iu.test(text)
  if (kk && ru) return 'mixed'
  return kk ? 'kk' : 'ru'
}

function extractParams(text: string): Record<string, string | number | null> {
  const params: Record<string, string | number | null> = {}
  if (/вчера|кеше/i.test(text)) params.date = new Date(Date.now() - 864e5).toISOString().slice(0, 10)
  const amount = text.match(/(\d[\d\s]{2,})\s*(тг|тенге|₸)/i)
  if (amount) params.amount = Number(amount[1].replace(/\s/g, ''))
  const car = text.match(/\b\d{3}\s?[a-zа-я]{3}\s?\d{2}\b/i)
  if (car) params.car_number = car[0].toUpperCase()
  return params
}

interface SessionState {
  session: Session
  turns: Turn[]
  active: ScenarioRef | null
  interrupted: ScenarioRef[]
}

const sessions = new Map<string, SessionState>()
let seq = 0
const nextId = (p: string) => `${p}_${++seq}`

function route(state: SessionState, text: string, input: 'voice' | 'text'): Turn {
  const lower = text.toLowerCase()
  const scored: ScenarioCandidate[] = SCENARIOS.map((s) => {
    const hits = s.keywords.filter((k) => lower.includes(k))
    return {
      ...ref(s),
      confidence: hits.length ? Math.min(0.97, 0.5 + 0.18 * hits.length) : Math.random() * 0.15,
      reason: hits.length ? `Совпадения: ${hits.join(', ')}` : 'Нет признаков этой темы',
    }
  }).sort((a, b) => b.confidence - a.confidence)

  const [top, second] = scored
  const wantsOperator = /оператор|маман|человек/i.test(text)
  let decision: Decision
  let selected: ScenarioCandidate | null = top
  const queued: ScenarioRef[] = []

  if (wantsOperator) {
    decision = 'handoff'
    selected = null
  } else if (top.confidence < LOW_CONFIDENCE) {
    decision = 'clarify'
  } else if (state.interrupted.some((s) => s.id === top.id)) {
    decision = 'resume_scenario'
    state.interrupted = state.interrupted.filter((s) => s.id !== top.id)
  } else if (state.active && state.active.id !== top.id) {
    decision = 'switch_scenario'
    state.interrupted.push(state.active)
  } else {
    decision = 'run_scenario'
  }
  if (decision !== 'clarify' && decision !== 'handoff') {
    state.active = selected && ref(selected)
    if (second.confidence >= LOW_CONFIDENCE) queued.push(ref(second))
  }

  const scenario = SCENARIOS.find((s) => s.id === selected?.id)
  const params = extractParams(text)
  const obvious = top.confidence >= 0.8 || (decision !== 'clarify' && decision !== 'handoff' && text.length < 40)
  const routePath = obvious && queued.length === 0 ? 'fast' : 'llm'
  const latency = {
    stt_ms: input === 'voice' ? rand(150, 320) : undefined,
    routing_ms: routePath === 'fast' ? rand(15, 60) : rand(250, 750),
    response_ms: rand(150, 400),
    tts_ms: rand(120, 260),
    total_ms: 0,
  }
  latency.total_ms = (latency.stt_ms ?? 0) + latency.routing_ms + latency.response_ms + latency.tts_ms

  let botText: string
  if (decision === 'handoff') botText = 'Перевожу на оператора — он уже видит суть вашего вопроса.'
  else if (decision === 'clarify')
    botText = `Уточните, пожалуйста: вы про «${top.title}» или про «${second.title}»?`
  else botText = scenario!.reply + (queued.length ? ` Затем помогу с темой «${queued[0].title}».` : '')

  const trace: TurnTrace = {
    route_path: routePath,
    decision,
    selected: decision === 'handoff' ? null : selected,
    alternatives: scored.slice(1, 4),
    rationale: {
      run_scenario: `Запрос однозначно относится к «${top.title}».`,
      switch_scenario: `Клиент сменил тему: «${state.interrupted.at(-1)?.title}» отложена, запускаем «${top.title}».`,
      resume_scenario: `Клиент вернулся к прерванной теме «${top.title}».`,
      clarify: `Уверенность ${Math.round(top.confidence * 100)}% ниже порога — переспрашиваем вместо угадывания.`,
      handoff: 'Клиент явно попросил оператора. Передаём диалог с контекстом.',
    }[decision],
    params,
    missing_params: (scenario?.params ?? []).filter((p) => p.required && !(p.name in params)).map((p) => p.name),
    context: { active: state.active, interrupted: [...state.interrupted], queued },
    pending_confirmation:
      scenario?.irreversible && decision !== 'clarify'
        ? { action: scenario.actions[0], description: `${scenario.title}: ждём «да» от клиента` }
        : null,
    handoff: decision === 'handoff' ? { reason: 'Запрос клиента' } : null,
    latency,
    model: 'mock',
  }

  const lang = detectLang(text)
  const turn: Turn = {
    id: nextId('turn'),
    session_id: state.session.id,
    index: state.turns.length + 1,
    created_at: new Date().toISOString(),
    user: { text, lang, input, emotion: /!|безобраз|сколько можно/i.test(text) ? 'negative' : 'neutral' },
    bot: { text: botText, lang: lang === 'kk' ? 'kk' : 'ru', audio_url: null },
    trace,
  }
  state.turns.push(turn)
  if (decision === 'handoff') state.session.status = 'handed_off'
  return turn
}

function createSession(clientId: string | null): SessionState {
  const session: Session = {
    id: nextId('session'),
    client: CLIENTS.find((c) => c.id === clientId) ?? null,
    status: 'active',
    started_at: new Date().toISOString(),
    max_turns: 10,
  }
  const state: SessionState = { session, turns: [], active: null, interrupted: [] }
  sessions.set(session.id, state)
  return state
}

// Демо-журнал: несколько разных звонков, чтобы панель супервизора не была пустой
function seedCall(clientId: string | null, minutesAgo: number, utterances: string[], tweak?: (turns: Turn[]) => void) {
  const state = createSession(clientId)
  const start = Date.now() - minutesAgo * 60_000
  state.session.started_at = new Date(start).toISOString()
  utterances.forEach((text, i) => {
    route(state, text, 'voice').created_at = new Date(start + (i + 1) * 25_000).toISOString()
  })
  tweak?.(state.turns)
  if (state.session.status === 'active') state.session.status = 'ended'
}

seedCall('c1', 42, [
  'Здравствуйте, я вчера оплатил, деньги списались, а полис не подтвердился… а, и ещё, адрес доставки поменять надо',
  'Мекенжайды өзгерту керек',
  'Мен вчера төледім, но полис әлі жоқ',
], (t) => (t[0].feedback = { correct: true }))
seedCall('c2', 35, ['Я попал в аварию вчера', 'Когда будет выплата?'])
seedCall(null, 28, ['Хочу расторгнуть договор', 'Соедините с оператором'], (t) => (t[0].feedback = { correct: true }))
seedCall('c3', 20, ['Сәлеметсіз бе, көлікке сақтандыру керек', 'Полисім қашанға дейін жарамды?'])
seedCall('c2', 12, ['Ну я не знаю, там что-то с документами', 'В общем непонятно, деньги где'], (t) => {
  t[0].trace.latency = { stt_ms: 280, routing_ms: 940, response_ms: 420, tts_ms: 260, total_ms: 1900 }
  t[1].feedback = { correct: false, expected_scenario_id: 'claim_status', comment: 'Спрашивал про выплату' }
})
seedCall('c1', 5, ['До какого числа действует мой полис?', 'А сколько можно ждать выплату!'])

const allTurns = () => [...sessions.values()].flatMap((s) => s.turns)

const isLowConfidence = (t: Turn) => (t.trace.selected?.confidence ?? 0) < LOW_CONFIDENCE && t.trace.decision !== 'handoff'

function summarize({ session, turns }: SessionState): CallSummary {
  const last = turns.at(-1)
  const outcome: CallOutcome =
    session.status === 'active'
      ? 'active'
      : session.status === 'handed_off'
        ? 'handed_off'
        : last && last.trace.decision !== 'clarify'
          ? 'resolved'
          : 'unresolved'
  const langs = [...new Set(turns.map((t) => t.user.lang))]
  const flags: CallFlag[] = []
  if (turns.some(isLowConfidence)) flags.push('low_confidence')
  if (turns.some((t) => t.trace.latency.total_ms > TARGET_TOTAL_MS)) flags.push('slow')
  if (turns.some((t) => t.feedback?.correct === false)) flags.push('marked_wrong')
  if (langs.includes('mixed')) flags.push('mixed_lang')
  const totals = turns.map((t) => t.trace.latency.total_ms)
  return {
    session,
    turns_count: turns.length,
    outcome,
    scenario_path: turns.map((t) => ({ scenario: t.trace.selected && ref(t.trace.selected), decision: t.trace.decision })),
    langs,
    flags,
    avg_total_ms: totals.length ? Math.round(totals.reduce((a, b) => a + b, 0) / totals.length) : 0,
    max_total_ms: totals.length ? Math.max(...totals) : 0,
    last_activity_at: last?.created_at ?? session.started_at,
  }
}

export const mockApi: VoiceRouterApi = {
  async listClients() {
    return CLIENTS
  },
  async listScenarios() {
    return SCENARIOS
  },
  async startSession(clientId) {
    return createSession(clientId).session
  },
  async sendTurn(sessionId, input: TurnInput) {
    const state = sessions.get(sessionId)
    if (!state) throw new Error('Сессия не найдена')
    const text =
      input.kind === 'text' ? input.text : SAMPLE_UTTERANCES[Math.floor(Math.random() * SAMPLE_UTTERANCES.length)]
    const turn = route(state, text, input.kind === 'audio' ? 'voice' : 'text')
    await sleep(turn.trace.latency.total_ms)
    return turn
  },
  async endSession(sessionId) {
    const state = sessions.get(sessionId)
    if (state && state.session.status === 'active') state.session.status = 'ended'
  },
  async getStats(): Promise<SupervisorStats> {
    const turns = allTurns()
    const n = turns.length || 1
    const routing = turns.map((t) => t.trace.latency.routing_ms).sort((a, b) => a - b)
    const marked = turns.filter((t) => t.feedback)
    const confusions = new Map<string, SupervisorStats['confusions'][number]>()
    for (const t of marked) {
      const expected = SCENARIOS.find((s) => s.id === t.feedback?.expected_scenario_id)
      if (t.feedback?.correct || !expected || !t.trace.selected) continue
      const key = `${expected.id}->${t.trace.selected.id}`
      const row = confusions.get(key) ?? { expected: ref(expected), predicted: ref(t.trace.selected), count: 0 }
      row.count++
      confusions.set(key, row)
    }
    const finished = [...sessions.values()].map(summarize).filter((c) => c.outcome !== 'active')
    const share = (f: (t: Turn) => boolean) => turns.filter(f).length / n
    return {
      sessions: sessions.size,
      turns: turns.length,
      resolved_rate: finished.length ? finished.filter((c) => c.outcome === 'resolved').length / finished.length : 0,
      accuracy: marked.length ? marked.filter((t) => t.feedback!.correct).length / marked.length : null,
      avg_routing_ms: Math.round(routing.reduce((a, b) => a + b, 0) / n),
      p95_routing_ms: routing[Math.min(routing.length - 1, Math.floor(routing.length * 0.95))] ?? 0,
      avg_total_ms: Math.round(turns.reduce((a, t) => a + t.trace.latency.total_ms, 0) / n),
      fast_path_share: share((t) => t.trace.route_path === 'fast'),
      clarify_rate: share((t) => t.trace.decision === 'clarify'),
      handoff_rate: share((t) => t.trace.decision === 'handoff'),
      low_confidence_rate: share(isLowConfidence),
      confusions: [...confusions.values()],
    }
  },
  async listCalls() {
    return [...sessions.values()]
      .map(summarize)
      .sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))
  },
  async getCall(sessionId) {
    const state = sessions.get(sessionId)
    if (!state) throw new Error('Звонок не найден')
    return { summary: summarize(state), turns: [...state.turns] }
  },
  async sendFeedback(turnId, feedback) {
    const turn = allTurns().find((t) => t.id === turnId)
    if (turn) turn.feedback = feedback
  },
}
