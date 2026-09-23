import { useEffect, useState, type FormEvent } from 'react'
import { DialogStatePanel } from '../components/call/DialogStatePanel'
import { LiveTranscript } from '../components/call/LiveTranscript'
import { TracePanel } from '../components/trace/TracePanel'
import { Icon } from '../components/Icon'
import { Badge, Empty, Panel, TotalLatency } from '../components/ui'
import { useVoiceSession } from '../hooks/useVoiceSession'
import { useCatalog } from '../lib/catalog-context'
import { END_REASON_LABEL } from '../lib/format'

// Диалоги мок-сервера (tools/mock-server). Настоящий бэкенд параметр fixture игнорирует
const FIXTURES = [
  { id: 'web-d03', label: 'КАСКО: смена темы и возврат' },
  { id: 'web-d04', label: 'ДМС: kk/ru, подтверждение записи' },
  { id: 'phone-p01', label: 'Жалоба → перевод оператору' },
]

const median = (xs: number[]) => {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = s.length >> 1
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2)
}

/** Экран звонка для жюри: говорим в микрофон, видим транскрипт, живую гипотезу, ответ и трассировку каждого хода */
export function CallPage() {
  const v = useVoiceSession()
  const { clients } = useCatalog()
  const [phone, setPhone] = useState('')
  const [fixture, setFixture] = useState(FIXTURES[0].id)
  const [text, setText] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const s = v.state
  const inCall = s.status === 'connecting' || s.status === 'live'

  // Push-to-talk пробелом (кроме полей ввода)
  const { mode, pressTalk, releaseTalk } = v
  useEffect(() => {
    if (mode !== 'ptt' || !inCall) return
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || typing(e)) return
      e.preventDefault()
      if (!e.repeat) pressTalk()
    }
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || typing(e)) return
      e.preventDefault()
      releaseTalk()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [mode, inCall, pressTalk, releaseTalk])

  const traced = s.turns.filter((t) => t.trace)
  const selectedTurn = s.turns.find((t) => t.turn === selected && t.trace) ?? traced.at(-1) ?? null
  const med = median(traced.map((t) => t.trace!.latency_ms.total))

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!text.trim()) return
    v.sendText(text.trim())
    setText('')
  }

  return (
    <div className="stack call-page">
      <Panel
        title="Звонок"
        actions={
          inCall ? (
            <button className="btn-bad" onClick={v.end}>
              <Icon name="phoneOff" />
              Положить трубку
            </button>
          ) : (
            <span className="row gap wrap">
              <select value={phone} onChange={(e) => setPhone(e.target.value)} title="Имитация номера звонящего (caller ID)">
                <option value="">Звоню с неизвестного номера</option>
                {clients.map((c) => (
                  <option key={c.client_id} value={c.phone}>
                    {c.phone} · {c.full_name}
                  </option>
                ))}
              </select>
              <select value={fixture} onChange={(e) => setFixture(e.target.value)} title="Только для мок-сервера: какой записанный диалог проигрывать">
                {FIXTURES.map((f) => (
                  <option key={f.id} value={f.id}>
                    мок: {f.label}
                  </option>
                ))}
              </select>
              <button className="primary" onClick={() => { setSelected(null); void v.start({ callerPhone: phone || null, fixture }) }}>
                <Icon name="phone" />Позвонить
              </button>
            </span>
          )
        }
      >
        <div className="call-bar">
          <span className="row gap wrap">
            <Badge tone={s.status === 'live' ? 'ok' : s.status === 'connecting' ? 'info' : 'neutral'}>
              {s.status === 'live' && <Icon name="statusDot" size={12} />}
              {s.status === 'live' ? 'на линии' : s.status === 'connecting' ? 'соединяю…' : s.status === 'ended' ? 'звонок завершён' : 'не на линии'}
            </Badge>
            {s.sessionId && <code className="muted small">{s.sessionId}</code>}
            {s.ready?.frozen && <Badge tone="info"><Icon name="lock" size={14} />Каталог заморожен</Badge>}
            {s.ready?.client && <span className="small">клиент: {String(s.ready.client.full_name ?? s.ready.client.client_id)}</span>}
          </span>

          {inCall && (
            <span className="row gap wrap">
              <span className="mic-meter" title={v.micOn ? 'Уровень микрофона' : 'Микрофон не включён'}>
                <Icon name="microphone" spacing="none" />
                <span className="mic-meter-track">
                  <span className="mic-meter-fill" style={{ width: `${Math.min(100, v.level * 400)}%` }} />
                </span>
              </span>
              <div className="segmented" role="group" aria-label="Режим микрофона">
                <button className={v.mode === 'open' ? 'seg-active' : ''} onClick={() => v.setMode('open')}>
                  Открытый микрофон
                </button>
                <button className={v.mode === 'ptt' ? 'seg-active' : ''} onClick={() => v.setMode('ptt')}>
                  Push‑to‑talk
                </button>
              </div>
              {v.mode === 'ptt' && (
                <button
                  className={`ptt ${v.pttDown ? 'ptt-down' : ''}`}
                  onPointerDown={v.pressTalk}
                  onPointerUp={v.releaseTalk}
                  onPointerLeave={v.releaseTalk}
                  title="Удерживайте кнопку или пробел, пока говорите"
                >
                  {v.pttDown ? 'Говорите…' : 'Зажать и говорить (пробел)'}
                </button>
              )}
              <label className="small" title="Если выключено, пока бот говорит, звук с микрофона не отправляется">
                <input type="checkbox" checked={v.bargeIn} onChange={(e) => v.setBargeIn(e.target.checked)} /> перебивание
              </label>
              <button onClick={() => v.setMuted(!v.muted)}><Icon name={v.muted ? 'microphoneOff' : 'microphone'} />{v.muted ? 'Включить микрофон' : 'Выключить микрофон'}</button>
              {v.botSpeaking && (
                <button className="btn-bad" onClick={v.interrupt}>
                  <Icon name="stop" />Стоп
                </button>
              )}
            </span>
          )}
        </div>
        {v.micError && <div className="callout callout-warn">Микрофон недоступен ({v.micError}). Можно говорить текстом ниже.</div>}
        {s.errors.map((e, i) => (
          <div key={i} className="callout callout-bad">
            {e}
          </div>
        ))}
      </Panel>

      <div className="split">
        <div className="stack">
          <Panel title="Разговор" className="call-transcript">
            {s.turns.length ? (
              <LiveTranscript turns={s.turns} selected={selectedTurn?.turn ?? null} onSelect={setSelected} />
            ) : (
              <Empty>{s.status === 'idle' ? 'Выберите номер и нажмите «Позвонить»' : 'Ждём приветствие бота…'}</Empty>
            )}
            {s.handoff && (
              <div className="callout callout-bad handoff-banner">
                <strong>Переводим в очередь {s.handoff.queue}.</strong> {s.handoff.summary}
              </div>
            )}
            {s.status === 'ended' && !s.handoff && (
              <div className="callout callout-warn">Звонок завершён: {END_REASON_LABEL[s.endReason ?? ''] ?? s.endReason}</div>
            )}
            <form onSubmit={submit} className="row gap text-input">
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Написать вместо голоса…" disabled={s.status !== 'live'} />
              <button type="submit" disabled={s.status !== 'live' || !text.trim()}>
                Отправить
              </button>
            </form>
          </Panel>
          <DialogStatePanel state={s.dialog} />
        </div>

        <div className="stack">
          {selectedTurn?.trace ? (
            <TracePanel trace={selectedTurn.trace} />
          ) : (
            <Panel title="Трассировка">
              <Empty>Появится после первого ответа</Empty>
            </Panel>
          )}
        </div>
      </div>

      <div className="median-bar">
        <span className="muted">Медиана задержки по звонку · от конца речи до первого звука</span>
        {med !== null ? <TotalLatency value={med} big /> : <span className="num num-big muted">—</span>}
        <span className="muted small">ходов с трассировкой: {traced.length}</span>
      </div>
    </div>
  )
}
