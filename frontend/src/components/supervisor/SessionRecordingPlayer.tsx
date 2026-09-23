import { useRef, useState } from 'react'
import type { SessionRecording } from '../../api'
import type { TurnTiming } from './SessionTimeline'
import './SessionRecordingPlayer.css'

type Props = {
  recording?: SessionRecording | null
  ended: boolean
  selectedTurn: number
  selectedTiming?: TurnTiming
  onRefresh: () => void
}

function formatTime(ms: number) {
  const seconds = Math.floor(Math.max(0, ms) / 1000)
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

export function SessionRecordingPlayer({ recording, ended, selectedTurn, selectedTiming, onRefresh }: Props) {
  const status = recording?.status
  const readyRecording = recording?.status === 'ready' && recording.url ? recording : null

  return (
    <section className="session-recording" aria-label="Запись звонка">
      <div className="session-recording-heading">
        <strong>Запись звонка</strong>
        {readyRecording && <span className="session-recording-duration">{formatTime(readyRecording.duration_ms)}</span>}
      </div>
      {readyRecording ? (
        <ReadyRecording
          key={readyRecording.url}
          recording={readyRecording}
          selectedTurn={selectedTurn}
          selectedTiming={selectedTiming}
          onRefresh={onRefresh}
        />
      ) : (
        <div className="session-recording-unavailable">
          <span>
            {status === 'ready' ? 'Ссылка на запись недоступна.'
              : status === 'processing' ? 'Запись обрабатывается. Проверьте её позже.'
              : status === 'failed' ? 'Не удалось подготовить запись.'
                : status === 'unavailable' || recording === null ? 'Для этого звонка запись недоступна.'
                  : ended ? 'Запись пока недоступна.' : 'Запись можно будет прослушать после завершения звонка.'}
          </span>
          {(status === 'ready' || status === 'processing' || status === 'failed' || (!status && ended && recording !== null)) && (
            <button type="button" className="session-recording-refresh" onClick={onRefresh}>Проверить ещё раз</button>
          )}
        </div>
      )}
    </section>
  )
}

type ReadyRecording = Extract<SessionRecording, { status: 'ready' }>

function ReadyRecording({ recording, selectedTurn, selectedTiming, onRefresh }: {
  recording: ReadyRecording
  selectedTurn: number
  selectedTiming?: TurnTiming
  onRefresh: () => void
}) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [metadataReady, setMetadataReady] = useState(false)
  const [retry, setRetry] = useState(0)
  const startMs = selectedTiming?.clientStartMs ?? selectedTiming?.startMs
  const canSeek = metadataReady && recording.timebase === 'session_start' && startMs !== undefined && selectedTurn > 0

  const listenFromTurn = () => {
    const audio = audioRef.current
    if (!audio || startMs === undefined) return
    const duration = Number.isFinite(audio.duration) ? audio.duration : recording.duration_ms / 1000
    audio.currentTime = Math.min(startMs / 1000, Math.max(0, duration - 0.1))
    void audio.play().catch(() => { /* Браузер оставит управление в нативном плеере. */ })
  }

  return (
    <div className="session-recording-ready">
      <audio
        key={retry}
        ref={audioRef}
        controls
        preload="metadata"
        src={recording.url}
        aria-label="Воспроизвести запись звонка"
        onError={() => { setLoadFailed(true); setMetadataReady(false) }}
        onLoadedMetadata={() => { setLoadFailed(false); setMetadataReady(true) }}
      >
        Браузер не поддерживает воспроизведение записи.
      </audio>
      {loadFailed ? (
        <div className="session-recording-unavailable" role="alert">
          <span>Не удалось открыть запись.</span>
          <button type="button" className="session-recording-refresh" onClick={() => { setLoadFailed(false); setMetadataReady(false); setRetry((n) => n + 1); onRefresh() }}>Обновить ссылку</button>
        </div>
      ) : canSeek ? (
        <div className="session-recording-jump">
          <button type="button" className="session-recording-jump-button" onClick={listenFromTurn}>
            Слушать с хода {selectedTurn} · ≈{formatTime(startMs)}
          </button>
          <span>Отметка хода приблизительная</span>
        </div>
      ) : null}
    </div>
  )
}
