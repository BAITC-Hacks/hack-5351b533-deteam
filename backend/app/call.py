"""Звонок, не зависящий от транспорта: VAD -> STT -> ход диалога -> TTS-плеер. Используется WebSocket и AudioSocket.

Замер задержки: t0 = момент последнего озвученного окна VAD (конец речи клиента), total = отправка первого аудиочанка ответа - t0.
stt_ms в stt.final и trace = от t0 до готового транскрипта (включает ожидание тишины VAD), поэтому водопад
stt + triage + router + response + tts_first_audio ~= total. Чистое время API STT идёт отдельным полем stt_api_ms.
Конец реплики (endpoint):
  semantic: после VAD_EARLY_MS тишины STT запускается спекулятивно; если тишина продолжается и транскрипт похож на
            законченную фразу (. ? !), реплика закрывается сразу по готовности транскрипта (обычно ~0.7-0.9 с после конца речи);
  silence:  иначе ждём VAD_SILENCE_MS тишины (паузы "Здравствуйте, ... хочу узнать" не режут реплику), спекулятивный транскрипт переиспользуется;
  commit:   push-to-talk (input.commit).
"""
import asyncio, os, time
from . import speech
from .audio import Segmenter, resample, guess_lang
from .session import Session
from .dialog import process_turn
from .hub import HUB

BARGE_IN = os.getenv("BARGE_IN", "1") == "1"
BARGE_MIN_MS = int(os.getenv("BARGE_MIN_MS", "300"))   # перебить бота можно только >= 300 мс речи (кашель/эхо не режут ответ)
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "800"))
VAD_EARLY_MS = int(os.getenv("VAD_EARLY_MS", "128"))
VAD_SEM_MIN_MS = int(os.getenv("VAD_SEM_MIN_MS", "300"))   # минимум тишины для семантического конца реплики
FILLER_MS = int(os.getenv("FILLER_MS", "0"))   # >0: если к этому сроку после транскрипта ответ не зазвучал, сказать короткое "Секунду." из кэша
DEFER = ("handoff", "session.end")   # эти события хода уходят только после его аудио (фронт может закрыться по session.end)

class Interrupted(Exception): pass

class Player:
    """Очередь фраз. Синтез фраз хода идёт параллельно, отправка строго по порядку.
    Аудио уходит быстрее реального времени, speak_until оценивает, когда клиент доиграет отправленное."""
    def __init__(self, call):
        self.call = call; self.q = asyncio.Queue(); self.producers = set(); self.futs = {}
        self.turn = None; self.last = None; self.speak_until = 0.0; self.cut = -1   # last: ход, чьё аудио клиент ещё может доигрывать
        self.task = asyncio.create_task(self.run())

    def say(self, turn, text, lang, template, fut=None):
        if turn <= self.cut: return   # ход перебит: остаток не озвучиваем
        chunks = asyncio.Queue()
        async def produce():
            try:
                async for c in speech.synth_stream(text, lang, store=template): await chunks.put(c)
            except asyncio.CancelledError: raise
            except Exception as e:
                await self.call.emit({"type": "error", "turn": turn, "code": "tts_unavailable", "message": str(e)[:200], "recoverable": True})
                if fut is not None and not fut.done(): fut.set_exception(RuntimeError("tts failed"))
            finally: chunks.put_nowait(None)
        p = asyncio.create_task(produce()); self.producers.add(p); p.add_done_callback(self.producers.discard)
        if fut is not None: self.futs[turn] = fut
        self.q.put_nowait(("say", turn, chunks, fut, speech.is_cached(text, lang)))

    def end_turn(self, turn, deferred=()):
        self.q.put_nowait(("end", turn, None, None, None))
        if deferred: self.q.put_nowait(("emit", turn, list(deferred), None, None))

    async def run(self):
        while True:
            kind, turn, a, fut, cached = await self.q.get()
            if kind == "emit":
                for ev in a: await self.call.emit(ev)
                continue
            if kind == "end":
                self.futs.pop(turn, None)
                if self.turn == turn: self.turn = None; await self.call.emit({"type": "tts.end", "turn": turn})
                continue
            while (c := await a.get()) is not None:
                if turn <= self.cut: continue
                if self.turn != turn:
                    self.turn = self.last = turn
                    await self.call.emit({"type": "tts.start", "turn": turn, "sample_rate": self.call.out_rate, "cached": cached})
                await self.call.send_audio(resample(c, 24000, self.call.out_rate))
                now = time.perf_counter(); self.speak_until = max(self.speak_until, now) + len(c) / 48000
                if fut is not None and not fut.done(): fut.set_result(now)

    @property
    def busy(self):
        """Бот говорит (или вот-вот доиграет отправленное)."""
        return self.turn is not None or time.perf_counter() < self.speak_until

    async def interrupt(self, cut_turn=None):
        """Перебивание: отменить синтез и очередь, фронту tts.interrupt (сбросить буфер). Отложенные события хода не теряем."""
        t = self.turn if self.turn is not None else (self.last if time.perf_counter() < self.speak_until else None)   # аудио уже отправлено, но ещё играет у клиента
        self.cut = max(self.cut, cut_turn if cut_turn is not None else -1, t if t is not None else -1)
        self.task.cancel()
        for p in list(self.producers): p.cancel()
        old, self.q = self.q, asyncio.Queue(); deferred = []
        while not old.empty():
            kind, _, a, _, _ = old.get_nowait()
            if kind == "emit": deferred += a
        for k, f in list(self.futs.items()):
            if k <= self.cut and not f.done(): f.set_exception(Interrupted())
        self.speak_until = 0.0; self.turn = None
        await self.call.clear_audio()
        self.task = asyncio.create_task(self.run())
        if t is not None: await self.call.emit({"type": "tts.interrupt", "turn": t})
        for ev in deferred: await self.call.emit(ev)

    def stop(self):
        self.task.cancel()
        for p in list(self.producers): p.cancel()

class Call:
    def __init__(self, channel, send_event, send_audio, clear_audio=None, out_rate=24000, in_rate=16000):
        self.channel, self._send_event, self.send_audio_raw = channel, send_event, send_audio
        self._clear = clear_audio; self.out_rate, self.in_rate = out_rate, in_rate
        self.sess: Session | None = None; self.t_start = time.perf_counter()
        self.seg = Segmenter(silence_ms=VAD_SILENCE_MS, early_ms=VAD_EARLY_MS); self.lock = asyncio.Lock(); self.player = None
        self.closed = False; self.muted = False; self.pending = 0; self.active_turn = None
        self.in_speech = False; self.spec = None; self.tasks = set(); self.barge = False

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

    def _spawn(self, coro):
        t = asyncio.create_task(coro); self.tasks.add(t); t.add_done_callback(self.tasks.discard); return t

    def _next_turn(self): return max(self.sess.turn_no, self.active_turn or 0) + 1 + self.pending

    async def start(self, caller_phone=None, lang_hint=None, session_id=None, greet=True):
        self.sess = Session(channel=self.channel, caller_phone=caller_phone, lang=lang_hint or "ru", session_id=session_id)
        HUB.register(self.sess, caller_phone)
        self.player = Player(self)
        s = self.sess
        await HUB.broadcast({"type": "session.created", "t": 0, "session_id": s.id, "turn": None, "channel": s.channel, "caller_phone": s.caller_phone, "started_at": HUB.sessions[s.id]["started_at"]})
        greeting = speech.greeting(s.client, s.language)
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

    # ---------- голос
    async def on_audio(self, pcm: bytes):
        if self.closed or self.muted or not self.sess or self.sess.ended: return
        if self.in_rate != 16000: pcm = resample(pcm, self.in_rate, 16000)
        try: evs = self.seg.feed(pcm)
        except Exception as e:
            await self.emit({"type": "error", "code": "bad_frame", "message": f"{type(e).__name__}: {e}"[:200], "recoverable": True}); return
        for ev in evs:
            if ev[0] == "start": await self._speech_start()
            elif ev[0] == "pause": self._spec_start(ev[1])
            elif ev[0] == "end": await self._speech_end(ev[1], ev[2], ev[3], "silence")
            elif ev[0] == "drop": self._spec_cancel()
        if self.barge and self.seg.speaking and self.seg.voiced_total * self.seg.W >= BARGE_MIN_MS: await self._barge()
        if self.spec and self.seg.speaking: await self._semantic_end()

    async def _barge(self):
        self.barge = False
        if self.player.busy: await self.player.interrupt(self.active_turn)

    def set_muted(self, muted: bool):
        self.muted = bool(muted); self.seg.reset(); self._spec_cancel(); self.in_speech = False; self.barge = False

    async def _semantic_end(self):
        sp = self.spec; task = sp["task"]; s = self.seg
        if not task.done() or task.cancelled() or task.exception() is not None: return
        if s.t_last_voice != sp["t_voice"] or s.silent_run * s.W < max(VAD_SEM_MIN_MS, VAD_EARLY_MS): return
        text = task.result()[0]
        if not speech.looks_final(text) or speech.is_hallucination(text): return
        voiced = s.voiced_total * s.W; s.reset()
        await self._speech_end(sp["pcm"], sp["t_voice"], voiced, "semantic")

    async def commit(self):
        """Push-to-talk: конец реплики без ожидания тишины."""
        if not self.sess or self.sess.ended: return
        was = self.seg.speaking
        ev = self.seg.force_end()
        if not ev:
            if was: self.in_speech = False; self._spec_cancel()
            return
        if not self.in_speech: await self._speech_start()
        await self._speech_end(ev[1], ev[2], ev[3], "commit")

    async def _speech_start(self):
        self.in_speech = True
        self.barge = BARGE_IN and self.player.busy       # перебивание подтвердится длиной речи (_barge) или словами в STT
        await self.emit({"type": "vad.speech_start", "turn": self._next_turn()})

    def _spec_start(self, pcm):
        self._spec_cancel()
        self.spec = {"pcm": pcm, "t_voice": self.seg.t_last_voice, "task": self._spawn(speech.transcribe(pcm))}

    def _spec_cancel(self):
        if self.spec: self.spec["task"].cancel(); self.spec = None

    async def _speech_end(self, pcm, t0, voiced_ms, how):
        self.in_speech = False
        spec = None
        if self.spec and len(self.spec["pcm"]) == len(pcm): spec = self.spec["task"]   # речь после паузы не продолжилась: транскрипт уже в пути
        elif self.spec: self.spec["task"].cancel()
        self.spec = None
        barge, self.barge = self.barge, False
        await self.emit({"type": "vad.speech_end", "turn": self._next_turn(), "endpoint": how,
                         "t_speech_end": max(0, int((t0 - self.t_start) * 1000))})   # t0 замера: последний озвученный фрейм
        self.pending += 1
        self._spawn(self._voice_turn(pcm, t0, voiced_ms, spec, how, barge))

    async def _voice_turn(self, pcm, t0, voiced_ms, spec, how, barge=False):
        t_end = time.perf_counter(); text = None
        if spec:
            try: text, api_ms = await spec
            except BaseException: text = None
        if text is None:
            spec = None
            try: text, api_ms = await speech.transcribe(pcm)
            except Exception as e:
                self.pending -= 1
                await self.emit({"type": "error", "code": "stt_unavailable", "message": f"{type(e).__name__}: {e}"[:200], "recoverable": True}); return
        t_txt = max(time.perf_counter(), t_end)
        if speech.is_hallucination(text, voiced_ms):
            self.pending -= 1
            print(f"[stt] dropped {text!r} (voiced {voiced_ms} ms)", flush=True); return
        if barge and self.player.busy: await self.player.interrupt(self.active_turn)   # короткая реплика поверх бота, но со словами
        extra = {"stt_api_ms": api_ms, "endpoint": how, "endpoint_ms": int((t_end - t0) * 1000), "stt_speculative": bool(spec), "voiced_ms": voiced_ms}
        await self._turn(text, t0=t0, stt_ms=int((t_txt - t0) * 1000), lang=guess_lang(text), extra=extra)

    # ---------- текст
    async def on_text(self, text):
        if not self.sess or self.sess.ended: return
        if BARGE_IN and self.player.busy: await self.player.interrupt(self.active_turn)
        self.pending += 1
        await self._turn(text, t0=time.perf_counter(), stt_ms=0, lang=guess_lang(text))

    # ---------- ход
    async def _turn(self, text, t0, stt_ms, lang, extra=None):
        async with self.lock:
            self.pending -= 1
            if self.closed or self.sess.ended: return
            turn = self.sess.turn_no + 1; self.active_turn = turn
            await self.emit({"type": "stt.final", "turn": turn, "text": text, "lang": lang, "stt_ms": stt_ms, **(extra or {})})
            fut = asyncio.get_running_loop().create_future(); deferred = []
            async def emit(ev):
                if ev["type"] in DEFER: deferred.append({**ev, "turn": ev.get("turn") or turn}); return
                if ev["type"] == "turn.trace" and turn <= self.player.cut: ev = {**ev, "trace": {**ev["trace"], "interrupted": True}}
                await self.emit(ev)
            spoke = []
            async def speak(chunk, template): spoke.append(1); self.player.say(turn, chunk, self.sess.language, template, fut)
            async def filler():
                await asyncio.sleep(FILLER_MS / 1000)
                if not spoke and not fut.done() and turn > self.player.cut:
                    lang = self.sess.language if getattr(self.sess, "lang_lock", None) or self.sess.turn_no > 1 else ("ru" if lang_in == "ru" else "kk")
                    spoke.append(1); self.player.say(turn, speech.FILLER[lang], lang, True, fut)
                    await self.emit({"type": "bot.text.delta", "turn": turn, "delta": speech.FILLER[lang] + " ", "filler": True})
            lang_in = lang; ft = asyncio.create_task(filler()) if FILLER_MS > 0 else None
            try:
                await process_turn(self.sess, text, t0=t0, stt_ms=stt_ms, emit=emit, speak=speak, first_audio=fut)
            except Exception as e:
                import traceback; traceback.print_exc()
                await self.emit({"type": "error", "turn": turn, "code": "internal", "message": f"{type(e).__name__}: {e}"[:300], "recoverable": True})
            finally:
                if ft: ft.cancel()
                self.active_turn = None
                if not fut.done(): fut.cancel()
                self.player.end_turn(turn, deferred)

    async def wait_idle(self, timeout=15):
        """Дождаться, пока клиент доиграет всё отправленное (для телефона перед hangup)."""
        t = time.perf_counter()
        while (self.player.busy or not self.player.q.empty() or self.lock.locked()) and time.perf_counter() - t < timeout:
            await asyncio.sleep(0.05)

    async def close(self, reason="client"):
        if self.closed: return
        self.closed = True
        self._spec_cancel()
        if self.sess and not HUB.sessions[self.sess.id]["ended_at"]:
            await self.emit({"type": "session.end", "reason": reason})
        if self.sess:
            await HUB.broadcast({"type": "session.closed", "t": 0, "session_id": self.sess.id, "turn": None, "reason": reason, "turns": self.sess.turn_no})
        if self.player: self.player.stop()
