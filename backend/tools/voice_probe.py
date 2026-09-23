"""Сквозной голосовой тест без микрофона: синтез фраз клиента -> /ws/voice кадрами по 20 мс в реальном времени -> события, задержки, проверка контракта.

  python tools/voice_probe.py "фраза 1" "фраза 2" [--caller +77010000007] [--lang ru|kk] [--url ws://127.0.0.1:8000/ws/voice]
  --mode voice   открытый микрофон: непрерывный поток кадров (тишина между фразами), конец реплики по VAD (по умолчанию)
  --mode commit  push-to-talk: кадры только пока «кнопка зажата», затем input.commit
  --mode text    text.input вместо аудио
  --mode barge   вторая фраза звучит, пока бот ещё говорит ответ на первую: ждём tts.interrupt и остановку аудио
  --noise -50    фоновый белый шум (dBFS) под речью и в паузах
  --events f.jsonl   сохранить все события (in/out)      --no-validate  не проверять события по contracts/ws-events.schema.json
  --vad-selftest     офлайн-проверка VAD (без сервера): тишина, шум, щелчки, пауза внутри фразы, pre-roll
"""
import asyncio, json, sys, time, argparse, os
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from app import speech
from app.audio import resample, Segmenter

ROOT = Path(__file__).resolve().parents[2]
FRAME = 640  # 20 мс PCM16 16 kHz

async def client_audio(text, lang):
    """PCM16 16 kHz фразы клиента, обрезанный по тишине (чтобы клиентский конец речи = реальный конец речи)."""
    buf = b""
    async for c in speech.synth_stream(text, lang, store=True): buf += c
    x = np.frombuffer(resample(buf, 24000, 16000), np.int16)
    fr = x[: len(x) // 320 * 320].reshape(-1, 320).astype(np.float32)
    rms = np.sqrt((fr ** 2).mean(axis=1)); on = np.where(rms > rms.max() * 0.03)[0]
    a, b = (on[0], on[-1] + 1) if len(on) else (0, len(fr))
    return fr[a:b].astype(np.int16).tobytes()

def noise(n_bytes, dbfs, rng=np.random.default_rng(0)):
    if dbfs is None: return b"\x00" * n_bytes
    return np.clip(rng.normal(0, 32768 * 10 ** (dbfs / 20), n_bytes // 2), -32768, 32767).astype(np.int16).tobytes()

def mix(pcm, dbfs):
    if dbfs is None: return pcm
    x = np.frombuffer(pcm, np.int16).astype(np.int32) + np.frombuffer(noise(len(pcm), dbfs), np.int16)
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()

# ---------------- контракт
def validators():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    schemas = {p.name: json.loads(p.read_text()) for p in (ROOT / "contracts").glob("*.schema.json")}
    reg = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
    ws = schemas["ws-events.schema.json"]; base = ws["$id"]
    V = lambda ref: Draft202012Validator({"$ref": base + ref}, registry=reg)
    return ws, V

def validate(log):
    ws, V = validators(); cache = {}; bad = 0; extra = {}
    for d, e in log:
        side = "server" if d == "in" else "client"; name = f"{side}.{e['type']}"
        if name not in ws["$defs"]:
            bad += 1; print(f"  CONTRACT unknown event {name}"); continue
        v = cache.get(name) or cache.setdefault(name, V(f"#/$defs/{name}"))
        for err in v.iter_errors(e):
            bad += 1; print(f"  CONTRACT {name} {list(err.absolute_path)} {err.message[:150]}")
        props = set(ws["$defs"][name].get("properties", {})) | {"type", "t", "session_id", "turn"}
        for k in set(e) - props: extra.setdefault(name, set()).add(k)
    print(f"contract: {len(log)} events, {bad} errors" + ("" if not extra else f"; extra fields: " + ", ".join(f"{k}: {sorted(v)}" for k, v in extra.items())))
    return bad

def check_order(log):
    """Порядок внутри хода: tts.start до аудио; trace после первого аудио; handoff/session.end после всего аудио и tts.end хода."""
    probs = []; cur = None; last_tts = None; first_audio = {}; tts_end = {}
    for i, (d, e) in enumerate(log):
        if d != "in": continue
        tp, tn = e["type"], e.get("turn")
        if tp == "audio":
            if cur is None: probs.append(f"audio without tts.start at #{i}")
            else: first_audio.setdefault(cur, i)
            continue
        if tp == "tts.start": cur = last_tts = tn
        if tp in ("tts.end", "tts.interrupt"): tts_end[tn] = i; cur = None
        if tp == "turn.trace" and tn in first_audio and i < first_audio[tn]: probs.append(f"trace turn {tn} before first audio")
        if tp in ("handoff", "session.end") and last_tts is not None and last_tts not in tts_end:
            probs.append(f"{tp} (turn {tn}) while audio of turn {last_tts} still streaming")
    print("order: " + ("OK" if not probs else "; ".join(probs)))
    return probs

# ---------------- сценарий звонка
async def run(a):
    import websockets
    utts = [await client_audio(t, a.lang) for t in a.text] if a.mode != "text" else [None] * len(a.text)
    url = a.url + (f"?caller_phone={a.caller.replace('+', '%2B')}" if a.caller else "")
    log = []; st = {"cur": None, "play_until": 0.0, "first_audio": {}, "bytes": {}, "orphan": 0, "after_int": 0, "stt": {}, "traces": {}, "ended": False, "interrupted": set()}
    ev_waiters = []
    async with websockets.connect(url, max_size=None) as ws:
        async def send_json(e):
            log.append(("out", e)); await ws.send(json.dumps(e, ensure_ascii=False))
        await send_json({"type": "session.start", "t": 0, "session_id": "", "channel": "web", "caller_phone": a.caller, "lang_hint": None})
        # поток кадров «микрофона»: открытый микрофон шлёт всегда, push-to-talk только пока зажато
        mic = asyncio.Queue(); streaming = {"on": a.mode in ("voice", "barge")}; sent = {}
        async def sender():
            t_next = time.perf_counter()
            while True:
                try: item = mic.get_nowait()
                except asyncio.QueueEmpty: item = None
                if item is None and not streaming["on"]:
                    await asyncio.sleep(0.02); t_next = time.perf_counter(); continue
                fr, tag = item if item else (noise(FRAME, a.noise), None)
                await ws.send(fr)
                if tag: sent[tag] = time.perf_counter()
                t_next += 0.02; await asyncio.sleep(max(0, t_next - time.perf_counter()))
        async def reader():
            async for m in ws:
                now = time.perf_counter()
                if isinstance(m, bytes):
                    log.append(("in", {"type": "audio", "n": len(m)}))
                    c = st["cur"]
                    if c is None: st["orphan"] += 1; continue
                    if c in st["interrupted"]: st["after_int"] += 1
                    st["first_audio"].setdefault(c, now); st["bytes"][c] = st["bytes"].get(c, 0) + len(m)
                    st["play_until"] = max(st["play_until"], now) + len(m) / 48000
                    continue
                e = json.loads(m); log.append(("in", e)); tp = e["type"]
                if tp == "tts.start": st["cur"] = e["turn"]
                if tp in ("tts.end", "tts.interrupt"): st["cur"] = None
                if tp == "tts.interrupt": st["interrupted"].add(e["turn"]); st["play_until"] = 0
                if tp == "stt.final": st["stt"][e["turn"]] = e
                if tp == "session.end": st["ended"] = True
                if tp in ("stt.final", "bot.text.final", "vad.speech_start", "vad.speech_end", "handoff", "error", "tts.interrupt", "session.end", "action.preview"):
                    txt = e.get("text") or e.get("summary") or e.get("message") or e.get("reason") or e.get("action") or ""
                    print(f"  [{e['t']:>6}] {tp:16} turn={e.get('turn')} {txt}")
                if tp == "turn.trace":
                    tr = e["trace"]; st["traces"][e["turn"]] = tr
                    print(f"  [{e['t']:>6}] trace scen={[(s['scenario_id'], s['confidence']) for s in tr['scenarios']]} dec={tr['decision']} acts={tr['actions']} lat={tr['latency_ms']}")
                for w in list(ev_waiters):
                    if w[0](e): w[1].set_result(e); ev_waiters.remove(w)
        async def wait_for(pred, timeout):
            f = asyncio.get_running_loop().create_future(); ev_waiters.append((pred, f))
            try: return await asyncio.wait_for(f, timeout)
            except asyncio.TimeoutError: return None
        async def idle(extra=0.3):
            while time.perf_counter() < st["play_until"] + extra: await asyncio.sleep(0.05)
        rt = asyncio.create_task(reader()); stt = asyncio.create_task(sender())
        await wait_for(lambda e: e["type"] == "tts.end" and e["turn"] == 0, 10); await idle(0.5)
        results = []
        for i, text in enumerate(a.text, 1):
            if st["ended"]: print("  session ended, skip:", text); break
            t_end = None
            if a.mode == "text":
                await send_json({"type": "text.input", "t": 0, "session_id": "", "turn": None, "text": text}); t_end = time.perf_counter()
            else:
                if a.mode == "barge" and i > 1:
                    await wait_for(lambda e: e["type"] == "tts.start" and e["turn"] >= 1, 20); await asyncio.sleep(a.barge_delay)
                    print(f"  >>> barge-in: speaking while bot plays (busy={time.perf_counter() < st['play_until']})")
                pcm = mix(utts[i - 1], a.noise); frames = [pcm[k:k + FRAME].ljust(FRAME, b"\x00") for k in range(0, len(pcm), FRAME)]
                if a.mode == "commit": streaming["on"] = True
                for j, fr in enumerate(frames): mic.put_nowait((fr, f"u{i}" if j == len(frames) - 1 else None))
                while f"u{i}" not in sent: await asyncio.sleep(0.005)
                t_end = sent[f"u{i}"]
                if a.mode == "commit":
                    streaming["on"] = False; await send_json({"type": "input.commit", "t": 0, "session_id": "", "turn": None})
            e = await wait_for(lambda e: e["type"] == "stt.final" or e["type"] == "error", 15)
            if not e or e["type"] != "stt.final": print("  no stt.final for", text); results.append((i, None, t_end)); continue
            T = e["turn"]
            if a.mode == "barge" and i < len(a.text): results.append((i, T, t_end)); continue
            await wait_for(lambda e: e["type"] in ("tts.end", "tts.interrupt") and e["turn"] == T or e["type"] == "session.end", 30)
            results.append((i, T, t_end)); await idle(0.4 if i < len(a.text) else 0.2)
        await asyncio.sleep(1.0)
        if not st["ended"]: await send_json({"type": "session.end", "t": 0, "session_id": "", "turn": None})
        await asyncio.sleep(0.5); rt.cancel(); stt.cancel()
    # ---- отчёт
    print("\nturn | stt_ms endp api spec | triage router resp tts1 | total(server) | client end->first audio | audio s")
    for i, T, t_end in results:
        if T is None: continue
        tr = st["traces"].get(T, {}); lat = tr.get("latency_ms", {}); s = st["stt"].get(T, {})
        fa = st["first_audio"].get(T)
        print(f"{T:>4} | {lat.get('stt', '-'):>6} {s.get('endpoint_ms', '-'):>4} {s.get('stt_api_ms', '-'):>4} {str(s.get('stt_speculative', '-'))[:1]:>4} | "
              f"{lat.get('triage', '-'):>6} {lat.get('router', '-'):>6} {lat.get('response', '-'):>4} {lat.get('tts_first_audio', '-'):>4} | {lat.get('total', '-'):>13} | "
              f"{int((fa - t_end) * 1000) if fa and t_end else '-':>23} | {st['bytes'].get(T, 0) / 48000:.1f}")
    if st["interrupted"]: print(f"interrupted turns: {sorted(st['interrupted'])}; audio frames after tts.interrupt: {st['after_int']}")
    print(f"orphan audio frames (outside tts.start..end): {st['orphan']}")
    if a.events:
        with open(a.events, "w") as f:
            for d, e in log: f.write(json.dumps({"dir": d, **e} if e["type"] == "audio" else ({"dir": d} | e), ensure_ascii=False) + "\n")
    bad = 0 if a.no_validate else validate([(d, e) for d, e in log if e["type"] != "audio"])
    probs = check_order(log)
    return 1 if bad or probs else 0

# ---------------- офлайн VAD
async def vad_selftest(a):
    """Segmenter с параметрами call.py (VAD_SILENCE_MS/VAD_EARLY_MS). Тишина = шум -75 dBFS (ниже порогов), чтобы найти границы реплики точно."""
    from app.call import VAD_SILENCE_MS, VAD_EARLY_MS
    rng = np.random.default_rng(1)
    def feed(pcm):
        s = Segmenter(silence_ms=VAD_SILENCE_MS, early_ms=VAD_EARLY_MS); evs = []
        for k in range(0, len(pcm), FRAME): evs += s.feed(pcm[k:k + FRAME])
        return evs
    sil = lambda sec: noise(int(32000 * sec) // 2 * 2, -75, rng)
    wn = lambda sec, db: noise(int(32000 * sec) // 2 * 2, db, rng)
    add = lambda x, y: np.clip(np.frombuffer(x, np.int16).astype(np.int32) + np.frombuffer(y, np.int16)[: len(x) // 2], -32768, 32767).astype(np.int16).tobytes()
    def pink(sec, db):
        w = rng.normal(0, 1, int(16000 * sec)); f = np.fft.rfft(w); f /= np.sqrt(np.arange(1, len(f) + 1)); y = np.fft.irfft(f, len(w))
        return np.clip(y / y.std() * 32768 * 10 ** (db / 20), -32768, 32767).astype(np.int16).tobytes()
    def hum(sec, db): t = np.arange(int(16000 * sec)) / 16000; return (np.sin(2 * np.pi * 50 * t) * 32768 * 10 ** (db / 20) * 1.41).astype(np.int16).tobytes()
    def clicks(sec, n=6):
        x = np.zeros(int(16000 * sec), np.int16)
        for p in rng.integers(0, len(x) - 800, n): x[p:p + 800] = rng.normal(0, 8000, 800).astype(np.int16)   # 50 мс щелчки/стуки
        return x.tobytes()
    u1 = await client_audio("Здравствуйте, хочу узнать, что с моим заявлением по каско.", "ru")
    u2 = await client_audio("Сәлеметсіз бе, Түркияға баруға сақтандыру керек.", "kk")
    u3 = await client_audio("Да.", "ru"); u4 = await client_audio("Иә.", "kk")
    half = int(16000 * 1.5) * 2   # середина озвученного участка
    wrap = lambda u: sil(1) + u + sil(2)
    cases = [("silence 10s", sil(10), 0), ("white -60dBFS", wn(10, -60), 0), ("white -45dBFS", wn(10, -45), 0), ("white -35dBFS", wn(10, -35), 0),
             ("pink -40dBFS", pink(10, -40), 0), ("hum 50Hz -30dBFS", hum(10, -30), 0), ("clicks 50ms x6", clicks(10), 0),
             ("ru utterance (512ms pause)", wrap(u1), 1), ("kk utterance", wrap(u2), 1), ("short 'Да.'", wrap(u3), 1), ("short 'Иә.'", wrap(u4), 1),
             ("ru + white -45", add(wrap(u1), wn(9, -45)), 1), ("ru + pink -35", add(wrap(u1), pink(9, -35)), 1),
             ("ru + extra 600ms pause", sil(1) + u1[:half] + sil(0.6) + u1[half:] + sil(2), 1),
             ("two utterances 1.2s apart", sil(1) + u3 + sil(1.2) + u1 + sil(2), 2)]
    ok = True
    for name, pcm, want in cases:
        evs = feed(pcm); ends = [e for e in evs if e[0] == "end"]; starts = [e for e in evs if e[0] == "start"]
        good = len(ends) == want and len(starts) == want; ok &= good; det = ""
        if ends and want == 1 and "+" not in name:
            off = pcm.find(ends[0][1]) // 32; ln = len(ends[0][1]) // 32; sp = len(pcm) - int(32000 * 2) // 2 * 2
            det = f"  pre-roll {1000 - off} ms, tail {off + ln - sp // 32} ms, voiced {ends[0][3]} ms, pauses {sum(e[0] == 'pause' for e in evs)}"
        print(f"  {'OK ' if good else 'BAD'} {name:28} starts={len(starts)} ends={len(ends)} drops={sum(e[0] == 'drop' for e in evs)} want={want}{det}")
    print(f"VAD selftest (silence {VAD_SILENCE_MS} ms, early {VAD_EARLY_MS} ms):", "OK" if ok else "FAIL")
    for name, pcm in [("white -50 2s", wn(2, -50)), ("pink -45 1s", pink(1, -45)), ("clicks 2s", clicks(2, 3))]:
        try:
            txt, ms = await speech.transcribe(pcm); print(f"  STT on {name}: {txt!r} ({ms} ms) -> {'dropped' if speech.is_hallucination(txt, 0) else 'KEPT'}")
        except Exception as e: print("  STT error", e)
    return 0 if ok else 1

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="*"); ap.add_argument("--caller"); ap.add_argument("--lang", default="ru")
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws/voice"); ap.add_argument("--mode", default="voice", choices=["voice", "text", "barge", "commit"])
    ap.add_argument("--noise", type=float, default=None); ap.add_argument("--barge-delay", type=float, default=0.6)
    ap.add_argument("--events"); ap.add_argument("--no-validate", action="store_true"); ap.add_argument("--vad-selftest", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(vad_selftest(a) if a.vad_selftest else run(a)))
