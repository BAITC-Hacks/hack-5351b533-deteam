import { Panel } from '../components/ui'

/** Экран звонка — следующий этап. Протокол: WS /ws/voice, docs/frontend/README.md */
export function CallPage() {
  return (
    <Panel title="Звонок" hint="В работе: переносим на WebSocket /ws/voice по контракту">
      <p>Что здесь будет:</p>
      <ul>
        <li>выбор «звоню с номера» из клиентов, микрофон (открытый и push‑to‑talk), текстовый ввод как резерв;</li>
        <li>транскрипт по мере речи (stt.partial / stt.final) и живая гипотеза роутера чипами сценариев;</li>
        <li>ответ бота текстом и голосом (PCM 24 kHz), карточка подтверждения необратимого действия;</li>
        <li>трассировка каждого хода — та же панель, что у супервизора, и медиана задержки по сессии.</li>
      </ul>
      <p className="muted small">
        Мок‑сервер уже проигрывает готовые диалоги по этому протоколу: <code>/ws/voice?fixture=web-d03</code>, <code>web-d04</code>,{' '}
        <code>phone-p01</code>.
      </p>
    </Panel>
  )
}
