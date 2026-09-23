// AudioWorklet: микрофон (частота контекста, обычно 48 kHz) → PCM16 LE 16 kHz mono,
// кадры по 20 мс = 320 сэмплов = 640 байт (контракт /ws/voice, ws-events.schema.json).
// Понижение частоты — усреднение по окну: заодно работает как простой фильтр от наложения.
const OUT_RATE = 16000
const FRAME = 320

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.ratio = sampleRate / OUT_RATE
    this.acc = 0
    this.cnt = 0
    this.pos = 0
    this.frame = new Int16Array(FRAME)
    this.n = 0
    this.energy = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      this.acc += ch[i]
      this.cnt++
      this.pos++
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio
        const v = Math.max(-1, Math.min(1, this.acc / this.cnt))
        this.acc = 0
        this.cnt = 0
        this.frame[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff
        this.energy += v * v
        if (this.n === FRAME) {
          const pcm = this.frame.buffer.slice(0)
          this.port.postMessage({ pcm, level: Math.sqrt(this.energy / FRAME) }, [pcm])
          this.n = 0
          this.energy = 0
        }
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCapture)
