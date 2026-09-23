"""Звонок, не зависящий от транспорта: VAD -> STT -> ход диалога -> TTS-плеер. Используется WebSocket и AudioSocket."""
import asyncio, time, os
from . import config, speech
from .audio import Segmenter, resample, guess_lang
from .session import Session
from .dialog import process_turn
from .hub import HUB

BARGE_IN = os.getenv("BARGE_IN", "1") == "1"
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "450"))

class Player:
    """Очередь фраз: синтез следующей фразы идёт параллельно с воспроизведением текущей."""
    def __init__(self, call):
        self.call = call; self.q = asyncio.Queue(); self.producers = []; self.task = asyncio.create_task(self.run())
        self.speak_until = 0.0; self.turn = None

    def say(self, turn, text, lang, template, fut):
        chunks = asyncio.Queue()
        async def produce():
            try:
                async for c in speech.synth_stream(text, lang, store=template): await chunks.put(c)
            except Exception as e:
                await self.call.emit({"type": "error", "code": "tts_unavailable", "message": str(e)[:200], "recoverable": True})
            await chunks.put(None)
        p = asyncio.create_task(produce()); self.producers.append(p)
        self.q.put_nowait(("say", turn, chunks, fut, speech.cached(text, lang) is not None))

    def end_turn(self, turn): self.q.put_nowait(("end", turn, None, None, None))

    async def run(self):
        while True:
            kind, turn, chunks, fut, cached = await self.q.get()
            if kind == "end":
                if self.turn == turn: await self.call.emit({"type": "tts.end", "turn": turn}); self.turn = None
                continue
            if self.turn != turn:
                self.turn = turn
                await self.call.emit({"type": "tts.start", "turn": turn, "sample_rate": self.call.out_rate, "cached": cached})
            while (c := await chunks.get()) is not None:
                out = resample(c, 24000, self.call.out_rate)
                await self.call.send_audio(out)
                now = time.perf_counter(); self.speak_until = max(self.speak_until, now) + len(c) / 48000
                if fut is not None and not fut.done(): fut.set_result(now)

    @property
    def speaking(self): return time.perf_counter() < self.speak_until

    async def interrupt(self):
        for p in self.producers: p.cancel()
        self.producers = []
        self.task.cancel()
        self.q = asyncio.Queue(); self.speak_until = 0
        t = self.turn; self.turn = None
        await self.call.clear_audio()
        self.task = asyncio.create_task(self.run())
        if t is not None: await self.call.emit({"type": "tts.interrupt", "turn": t})

class Call:
    def __init__(self, channel, send_event, send_audio, clear_audio=None, out_rate=24000, in_rate=16000):
        self.channel, self._send_event, self.send_audio_raw = channel, send_event, send_audio
        self._clear = clear_audio; self.out_rate, self.in_rate = out_rate, in_rate
        self.sess: Session | None = None; self.t_start = time.perf_counter()
        self.seg = Segmenter(silence_ms=VAD_SILENCE_MS); self.lock = asyncio.Lock(); self.player = None
        self.closed = False; self.muted = False; self.pending = 0

    async def emit(self, ev):
        if not self.sess: return
        e = {"type": ev["type"], "t": int((time.perf_counter() - self.t_start) * 1000), "session_id": self.sess.id, "turn": ev.get("turn"), **ev}
        try: await self._send_event(e)
        except Exception: pass
        await HUB.publish(e)

    async def send_audio(self, pcm):
        try: await self.send_audio_raw(pcm)
        except Exception: pass

    async def clear_audio(self):
        if self._clear:
            try: await self._clear()
            except Exception: pass

    async def start(self, caller_phone=None, lang_hint=None, session_id=None, greet=True):
        self.sess = Session(channel=self.channel, caller_phone=caller_phone, lang=lang_hint or "ru", session_id=session_id)
        HUB.register(self.sess, caller_phone)
        self.player = Player(self)
        s = self.sess
        await HUB.broadcast({"type": "session.created", "t": 0, "session_id": s.id, "turn": None, "channel": s.channel, "caller_phone": s.caller_phone, "started_at": HUB.sessions[s.id]["started_at"]})
        if s.client:
            greeting = speech.GREETING[s.language].format(name=s.client["full_name"].split()[0])
        else:
            greeting = speech.GREETING["unknown"]
        from .catalog import CATALOG
        await self.emit({"type": "session.ready", "channel": s.channel,
                         "audio_in": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20},
                         "audio_out": {"format": "pcm_s16le", "sample_rate": self.out_rate, "channels": 1},
                         "catalog_version": CATALOG.version, "frozen": CATALOG.frozen, "language": s.language,
                         "client": {k: s.client[k] for k in ("client_id", "full_name", "identified_by")} if s.client else None,
                         "greeting": greeting if greet else None})
        if greet:
            s.history.append({"role": "bot", "text": greeting})
            await self.emit({"type": "bot.text.final", "turn": 0, "text": greeting, "lang": s.language, "template": True})
            self.player.say(0, greeting, s.language if s.client else "ru", True, None); self.player.end_turn(0)

    async def on_audio(self, pcm: bytes):
        if self.closed or self.muted or not self.sess: return
        if self.in_rate != 16000: pcm = resample(pcm, self.in_rate, 16000)
        for ev in self.seg.feed(pcm):
            if ev[0] == "start":
                if BARGE_IN and self.player.speaking: await self.player.interrupt()
                await self.emit({"type": "vad.speech_start", "turn": self.sess.turn_no + 1 + self.pending})
            elif ev[0] == "end":
                await self.emit({"type": "vad.speech_end", "turn": self.sess.turn_no + 1 + self.pending})
                self.pending += 1
                asyncio.create_task(self._voice_turn(ev[1], ev[2]))

    async def commit(self):
        ev = self.seg.force_end()
        if ev:
            await self.emit({"type": "vad.speech_end", "turn": self.sess.turn_no + 1 + self.pending}); self.pending += 1
            asyncio.create_task(self._voice_turn(ev[1], time.perf_counter()))

    async def _voice_turn(self, pcm, t0):
        try:
            text, stt_ms = await speech.transcribe(pcm)
        except Exception as e:
            self.pending -= 1
            await self.emit({"type": "error", "code": "stt_unavailable", "message": str(e)[:200], "recoverable": True}); return
        if not text or len(text) < 2 or text.lower().strip(" .") in ("продолжение следует", "спасибо за просмотр", "субтитры"):
            self.pending -= 1; return
        await self._turn(text, t0=t0, stt_ms=stt_ms, lang=guess_lang(text))

    async def on_text(self, text):
        self.pending += 1
        await self._turn(text, t0=time.perf_counter(), stt_ms=0, lang=guess_lang(text), from_text=True)

    async def _turn(self, text, t0, stt_ms, lang, from_text=False):
        async with self.lock:
            self.pending -= 1
            if self.closed or self.sess.ended: return
            turn = self.sess.turn_no + 1
            await self.emit({"type": "stt.final", "turn": turn, "text": text, "lang": lang, "stt_ms": stt_ms})
            fut = asyncio.get_running_loop().create_future()
            async def speak(chunk, template): self.player.say(turn, chunk, self.sess.language, template, fut)
            try:
                await process_turn(self.sess, text, t0=t0, stt_ms=stt_ms, emit=self.emit, speak=speak, first_audio=fut)
            except Exception as e:
                import traceback; traceback.print_exc()
                await self.emit({"type": "error", "code": "internal", "message": f"{type(e).__name__}: {e}"[:300], "recoverable": True})
            self.player.end_turn(turn)

    async def wait_idle(self, timeout=15):
        t = time.perf_counter()
        while (self.player.speaking or not self.player.q.empty()) and time.perf_counter() - t < timeout:
            await asyncio.sleep(0.1)

    async def close(self, reason="client"):
        if self.closed: return
        self.closed = True
        if self.sess and not HUB.sessions[self.sess.id]["ended_at"]:
            await self.emit({"type": "session.end", "reason": reason})
        if self.sess:
            await HUB.broadcast({"type": "session.closed", "t": 0, "session_id": self.sess.id, "turn": None, "reason": reason, "turns": self.sess.turn_no})
        if self.player:
            for p in self.player.producers: p.cancel()
            self.player.task.cancel()
