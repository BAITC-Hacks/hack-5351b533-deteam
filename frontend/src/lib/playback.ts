import type { Turn } from '../api'

// Озвучка ответа робота: аудио от бэкенда (TTS), иначе — браузерный speechSynthesis как заглушка.
export function playBotReply(bot: Turn['bot']): Promise<void> {
  return new Promise((resolve) => {
    if (bot.audio_url) {
      const audio = new Audio(bot.audio_url)
      audio.onended = audio.onerror = () => resolve()
      audio.play().catch(() => resolve())
      return
    }
    if (!('speechSynthesis' in window)) return resolve()
    const utterance = new SpeechSynthesisUtterance(bot.text)
    utterance.lang = bot.lang === 'kk' ? 'kk-KZ' : 'ru-RU'
    utterance.onend = utterance.onerror = () => resolve()
    speechSynthesis.speak(utterance)
  })
}

export function stopPlayback() {
  if ('speechSynthesis' in window) speechSynthesis.cancel()
}
