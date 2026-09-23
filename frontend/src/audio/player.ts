/**
 * Проигрывание ответа бота: бинарные чанки PCM16 LE (24 kHz по контракту) встают в очередь без пауз.
 * Сообщает, когда реально зазвучал первый чанк хода (playback.started) и когда ход доиграл (playback.finished).
 */
export class PcmPlayer {
  private ctx = new AudioContext()
  private next = 0
  private sources = new Set<AudioBufferSourceNode>()
  private turn: number | null = null
  private sampleRate = 24000
  private pending = new Map<number, number>() // сколько чанков хода ещё играет
  private ended = new Set<number>() // по ходу пришёл tts.end
  private started = new Set<number>()
  private timers = new Set<ReturnType<typeof setTimeout>>()

  onStarted?: (turn: number) => void
  onFinished?: (turn: number) => void

  /** tts.start: дальше пойдут бинарные кадры этого хода */
  begin(turn: number, sampleRate?: number) {
    this.turn = turn
    if (sampleRate) this.sampleRate = sampleRate
    void this.ctx.resume()
  }

  /** Бинарный кадр от сервера */
  enqueue(data: ArrayBuffer) {
    const turn = this.turn
    if (turn === null || data.byteLength < 2) return
    const pcm = new Int16Array(data, 0, data.byteLength >> 1)
    const buf = this.ctx.createBuffer(1, pcm.length, this.sampleRate)
    const ch = buf.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000

    const src = this.ctx.createBufferSource()
    src.buffer = buf
    src.connect(this.ctx.destination)
    const at = Math.max(this.ctx.currentTime + 0.02, this.next)
    src.start(at)
    this.next = at + buf.duration
    this.sources.add(src)
    this.pending.set(turn, (this.pending.get(turn) ?? 0) + 1)

    if (!this.started.has(turn)) {
      this.started.add(turn)
      const timer = setTimeout(() => {
        this.timers.delete(timer)
        this.onStarted?.(turn)
      }, Math.max(0, (at - this.ctx.currentTime) * 1000))
      this.timers.add(timer)
    }

    src.onended = () => {
      this.sources.delete(src)
      this.pending.set(turn, (this.pending.get(turn) ?? 1) - 1)
      this.checkFinished(turn)
    }
  }

  /** tts.end: новых кадров хода не будет, ждём, пока доиграет очередь */
  end(turn: number) {
    this.ended.add(turn)
    this.checkFinished(turn)
  }

  /** tts.interrupt или «стоп»: немедленно сбросить очередь */
  stop() {
    this.timers.forEach(clearTimeout)
    this.timers.clear()
    this.sources.forEach((s) => {
      s.onended = null
      try {
        s.stop()
      } catch {
        // уже остановлен
      }
    })
    this.sources.clear()
    this.pending.clear()
    this.next = 0
  }

  get playing() {
    return this.sources.size > 0
  }

  close() {
    this.stop()
    void this.ctx.close()
  }

  private checkFinished(turn: number) {
    if (this.ended.has(turn) && (this.pending.get(turn) ?? 0) <= 0 && this.started.has(turn)) {
      this.ended.delete(turn)
      this.onFinished?.(turn)
    }
  }
}
