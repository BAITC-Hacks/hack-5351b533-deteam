/** Захват микрофона в PCM16 16 kHz кадрами по 20 мс. Вся обработка — в public/pcm-capture-worklet.js */
export interface Capture {
  stop: () => void
}

export async function startCapture(onFrame: (pcm: ArrayBuffer, level: number) => void): Promise<Capture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  })
  const ctx = new AudioContext()
  try {
    await ctx.audioWorklet.addModule('/pcm-capture-worklet.js')
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop())
    await ctx.close()
    throw e
  }
  const source = ctx.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(ctx, 'pcm-capture')
  node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => onFrame(e.data.pcm, e.data.level)
  // Узел должен быть в графе до destination, иначе браузер его не вызывает. Громкость 0 — без эха себя
  const silent = ctx.createGain()
  silent.gain.value = 0
  source.connect(node).connect(silent).connect(ctx.destination)

  return {
    stop: () => {
      node.port.onmessage = null
      source.disconnect()
      stream.getTracks().forEach((t) => t.stop())
      void ctx.close()
    },
  }
}
