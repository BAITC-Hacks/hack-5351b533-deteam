"""Сквозной тест телефонии без Asterisk: притворяемся Asterisk-ом.
1) POST /api/telephony/calls (caller, channel) -> UUID;  2) TCP AudioSocket: 0x01 UUID, затем 0x10 кадры 20 мс slin 8 kHz
в реальном темпе (синтезированная речь клиента + тишина);  3) считаем входящий звук, ловим 0x00;  4) /ws/supervisor — поток событий.
Проверки: приветствие по имени, ответ на обычную реплику, «оператор» -> outcome transfer:<queue> + 0x00; задержка конец речи -> первый кадр.

python tools/phone_probe.py [--http http://127.0.0.1:8013] [--as 127.0.0.1:9093] [--caller +77010000004] [--barge] ["реплика" ...]
python tools/phone_probe.py --expect hangup "Какой у меня лимит по ДМС?" "Спасибо, до свидания"
"""
import argparse, asyncio, json, os, struct, sys, time, uuid, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websockets
from app import speech
from app.audio import resample

DEFAULT = ["Что с моим заявлением?", "Соедините меня с оператором"]
FRAME = 320


def pack(kind, payload=b""): return struct.pack(">BH", kind, len(payload)) + payload


async def client_audio(text, lang):
    buf = b""
    async for c in speech.synth_stream(text, lang, store=True): buf += c
    return resample(buf, 24000, 8000)


def voiced_end(pcm, thr=600):
    """Байтовое смещение конца голоса (TTS добавляет хвост тишины — его не считаем в задержку)."""
    import numpy as np
    x = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.int32))
    idx = np.nonzero(x > thr)[0]
    return int(idx[-1] + 1) * 2 if len(idx) else len(pcm)


def voiced_start(pcm, thr=600):
    import numpy as np
    idx = np.nonzero(np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.int32)) > thr)[0]
    return int(idx[0]) * 2 if len(idx) else 0


def http(method, url, data=None, accept=None):
    req = urllib.request.Request(url, method=method, data=urllib.parse.urlencode(data).encode() if data is not None else None)
    if data is not None: req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if accept: req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=10) as r: return r.read().decode()


def validator():
    """Схема ws-events из /contracts (как в tools/validate_contracts.py). None, если jsonschema нет."""
    try:
        from pathlib import Path
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        R = Path(__file__).resolve().parents[2] / "contracts"
        schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in R.glob("*.schema.json")}
        reg = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
        return Draft202012Validator({"$ref": schemas["ws-events.schema.json"]["$id"]}, registry=reg)
    except Exception as e:
        print("schema validation off:", e); return None


async def main(a):
    t_boot = time.perf_counter()
    texts = a.text or DEFAULT
    utts = [await client_audio(t, a.lang) for t in texts]
    print(f"synthesized {len(utts)} client utterances in {time.perf_counter() - t_boot:.1f}s: " + ", ".join(f"{len(u) / 16000:.1f}s" for u in utts))
    chan = "PJSIP/1001-0000probe"
    if a.unregistered:   # AudioSocket с неизвестным UUID: звонок всё равно идёт, но без caller id
        uid = str(uuid.uuid4())
    else:
        uid = await asyncio.to_thread(http, "POST", a.http + "/api/telephony/calls",
                                      {"caller": a.caller, "dnid": "7575", "channel": chan, "uniqueid": f"{time.time():.1f}"})
        uid = uid.strip(); uuid.UUID(uid)
    sid = f"phone-{uid[:8]}"
    print(f"{'UNREGISTERED' if a.unregistered else 'registered'} call uuid={uid} -> session {sid}")

    # --- супервизор
    events = []; V = validator(); bad = []
    sup = await websockets.connect(a.http.replace("http", "ws", 1) + "/ws/supervisor", max_size=None)
    async def sup_reader():
        async for m in sup:
            e = json.loads(m)
            if e.get("session_id") != sid: continue
            e["_rx"] = time.perf_counter(); events.append(e)
            if V is not None:
                errs = [f"{list(x.absolute_path)} {x.message[:120]}" for x in V.iter_errors({k: v for k, v in e.items() if k != "_rx"})]
                if errs: bad.append((e["type"], errs[:2]))
            tp = e["type"]
            if tp in ("session.created", "session.ready", "vad.speech_start", "vad.speech_end", "stt.final", "bot.text.final", "tts.start", "tts.end",
                      "tts.interrupt", "handoff", "session.end", "session.closed", "error", "action.executed"):
                extra = e.get("text") or e.get("summary") or e.get("message") or e.get("reason") or e.get("action") or e.get("caller_phone") or ""
                if tp == "session.ready": extra = f"client={e.get('client')} out={e['audio_out']['sample_rate']}Hz"
                if tp == "handoff": extra = f"queue={e['queue']} | {e['summary']}"
                print(f"   sup [{e.get('t', 0):>6}] {tp:17} turn={e.get('turn')} {extra}")
            if tp == "turn.trace":
                tr = e["trace"]
                print(f"   sup [{e['t']:>6}] turn.trace        turn={e['turn']} scen={[(s['scenario_id'], s['confidence']) for s in tr['scenarios']]} "
                      f"dec={tr['decision']} src={tr['response_source']} lat={tr['latency_ms']}")
    sup_task = asyncio.create_task(sup_reader())
    await asyncio.sleep(0.2)

    # --- AudioSocket
    h, p = a.audiosocket.rsplit(":", 1)
    rd, wr = await asyncio.open_connection(h, int(p))
    wr.write(pack(0x01, uuid.UUID(uid).bytes)); await wr.drain()
    t_conn = time.perf_counter()
    rx = []                       # (t, nbytes) входящие кадры с речью
    rx_all = [0]                  # все кадры, включая тишину
    rx_sizes = set(); hangup = {"t": None}; speak = {"pcm": b"", "pos": 0, "t_end": None, "t_voice_end": None, "vend": 0}
    async def receiver():
        while True:
            try: hd = await rd.readexactly(3)
            except Exception: break
            kind, n = hd[0], struct.unpack(">H", hd[1:])[0]
            pl = await rd.readexactly(n) if n else b""
            if kind == 0x10:
                rx_sizes.add(len(pl)); rx_all[0] += 1
                if pl.count(0) < len(pl): rx.append((time.perf_counter(), len(pl)))   # кадры тишины (keepalive) не считаем речью бота
            elif kind == 0x00: hangup["t"] = time.perf_counter(); print(f"   AS  <- 0x00 HANGUP at +{hangup['t'] - t_conn:.2f}s"); break
            else: print(f"   AS  <- kind {kind:#x} len {n}")
    async def sender():            # как Asterisk: непрерывно 20 мс кадры (речь или тишина)
        loop = asyncio.get_running_loop(); nxt = loop.time()
        while hangup["t"] is None:
            s = speak
            if s["pos"] < len(s["pcm"]):
                fr = s["pcm"][s["pos"]:s["pos"] + FRAME]; s["pos"] += FRAME
                if s["t_voice_end"] is None and s["pos"] >= s["vend"]: s["t_voice_end"] = time.perf_counter() + 0.02
                if s["pos"] >= len(s["pcm"]): s["t_end"] = time.perf_counter()
                fr = fr + b"\x00" * (FRAME - len(fr))
            else:
                fr = b"\x00" * FRAME
            try: wr.write(pack(0x10, fr)); await wr.drain()
            except Exception: break
            nxt += 0.02; await asyncio.sleep(max(0, nxt - loop.time()))
    rtask = asyncio.create_task(receiver()); stask = asyncio.create_task(sender())

    def last_rx(): return rx[-1][0] if rx else 0
    async def wait_quiet(quiet=1.0, timeout=40, after=0.0):
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout and hangup["t"] is None:
            if rx and rx[-1][0] > after and time.perf_counter() - last_rx() >= quiet: return True
            await asyncio.sleep(0.05)
        return hangup["t"] is not None

    await wait_quiet(1.0, 30)
    greet_frames = [x for x in rx]
    g_audio = sum(n for _, n in greet_frames) / 16000
    g_wall = (greet_frames[-1][0] - greet_frames[0][0] + 0.02) if greet_frames else 0
    print(f"greeting: first frame +{(greet_frames[0][0] - t_conn) * 1000:.0f} ms after connect, audio {g_audio:.2f}s over {g_wall:.2f}s wall"
          f" (pacing x{g_audio / g_wall if g_wall else 0:.2f})" if greet_frames else "greeting: NO AUDIO")

    results = []; srv_turn = 0
    async def wait_turn_done(turn, after):
        t0 = time.perf_counter()   # ждём tts.end этого хода сервера (или конец сессии) и тишину в канале
        while time.perf_counter() - t0 < 40 and hangup["t"] is None and not any(
                e["type"] in ("tts.end", "session.end") and (e.get("turn") or 99) >= turn for e in events):
            await asyncio.sleep(0.05)
        await wait_quiet(0.8, 40, after=after)

    async def say(pcm):
        speak.update(pcm=pcm, pos=0, t_end=None, t_voice_end=None, vend=voiced_end(pcm))
        while speak["t_end"] is None and hangup["t"] is None: await asyncio.sleep(0.01)
        return speak["t_voice_end"] or speak["t_end"]

    for i, (text, pcm) in enumerate(zip(texts, utts), 1):
        n0 = len(rx); srv_turn += 1
        print(f">> client turn {i}: «{text}» ({len(pcm) / 16000:.1f}s)")
        t_end = await say(pcm)
        t0 = time.perf_counter()
        while len(rx) == n0 and time.perf_counter() - t0 < 20 and hangup["t"] is None: await asyncio.sleep(0.005)
        first = rx[n0][0] if len(rx) > n0 else None
        lat = int((first - t_end) * 1000) if first else None
        if a.barge and i == 1 and first:
            # перебиваем бота через 1 с после начала ответа и смотрим, когда перестали приходить кадры
            await asyncio.sleep(1.0); t_b = time.perf_counter()
            print(f">> barge-in: client repeats «{texts[0]}» over the bot")
            t_b_end = await say(utts[0])
            await asyncio.sleep(0.3)
            after = [x[0] for x in rx if x[0] > t_b]
            stop = next((x for x, y in zip(after, after[1:]) if y - x > 0.15), after[-1] if after else None)
            itr = next((e for e in events if e["type"] == "tts.interrupt" and e["turn"] == srv_turn), None)
            print(f"   barge-in: tts.interrupt={'yes' if itr else 'NO'}; bot audio stopped {int((stop - t_b) * 1000) if stop else 0} ms after client started talking"
                  f" (clip lead-in {(len(utts[0]) and voiced_start(utts[0])) / 16:.0f} ms)")
            srv_turn += 1; t_end = t_b_end
        await wait_turn_done(srv_turn, t_end)
        n_audio = sum(n for t, n in rx[n0:]) / 16000
        results.append(lat)
        print(f"   turn {i}: end-of-voice -> first audio frame {lat} ms (clip tail silence {(len(pcm) - voiced_end(pcm)) / 16:.0f} ms), bot audio {n_audio:.1f}s")
        if hangup["t"]: break

    if hangup["t"] is None:
        t0 = time.perf_counter()
        while hangup["t"] is None and time.perf_counter() - t0 < 15: await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    out_txt = await asyncio.to_thread(http, "GET", f"{a.http}/api/telephony/calls/{uid}/outcome")
    out_json = await asyncio.to_thread(http, "GET", f"{a.http}/api/telephony/calls/{uid}/outcome", None, "application/json")
    hang = await asyncio.to_thread(http, "POST", f"{a.http}/api/telephony/calls/{uid}/hangup", {"cause": "16"})
    stask.cancel(); rtask.cancel()
    try: wr.close()
    except Exception: pass
    await asyncio.sleep(0.5); sup_task.cancel(); await sup.close()

    # --- проверки
    fin = {e["turn"]: e["text"] for e in events if e["type"] == "bot.text.final"}
    greet = fin.get(0, "")
    ok = {
        "greeting": bool(greet) if a.unregistered else any(n in greet for n in a.name.split(",")),
        "caller id": (not any(e["type"] == "session.ready" and e.get("client") for e in events)) if a.unregistered else
            any(e["type"] == "session.ready" and (e.get("client") or {}).get("identified_by") == "caller_id" for e in events),
        "turn 1 answered": bool(fin.get(1)),
        f"outcome {a.expect}": out_txt.startswith("transfer:") if a.expect == "transfer" else out_txt == "hangup",
        "0x00 hangup received": hangup["t"] is not None,
        "frames are 320 bytes": rx_sizes == {320},
        f"session.end reason {'handoff' if a.expect == 'transfer' else 'goodbye'}":
            any(e["type"] == "session.end" and e.get("reason") == ("handoff" if a.expect == "transfer" else "goodbye") for e in events),
        "events match ws-events schema": V is None or not bad,
    }
    print("\n=== RESULT")
    print(f"outcome text/plain: {out_txt!r}; json: {out_json}; hangup endpoint: {hang!r}")
    if hangup["t"] and rx: print(f"0x00 sent {int((hangup['t'] - rx[-1][0]) * 1000)} ms after last audio frame")
    wall = (hangup["t"] or time.perf_counter()) - t_conn
    print(f"frames received: {rx_all[0]} total ({rx_all[0] / wall:.1f}/s incl. silence keepalive), {len(rx)} with speech")
    if bad: print("schema errors:", bad[:5])
    print("latency end-of-voice -> first frame (ms):", results)
    tr = [e["trace"]["latency_ms"] for e in events if e["type"] == "turn.trace"]
    print("server traces latency_ms:", tr)
    for k, v in ok.items(): print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    return all(ok.values())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*"); ap.add_argument("--caller", default="+77010000004"); ap.add_argument("--lang", default="ru")
    ap.add_argument("--http", default="http://127.0.0.1:8013"); ap.add_argument("--as", dest="audiosocket", default="127.0.0.1:9093")
    ap.add_argument("--barge", action="store_true", help="перебить бота во время ответа на 1-ю реплику")
    ap.add_argument("--unregistered", action="store_true", help="не регистрировать звонок через REST (случайный UUID)")
    ap.add_argument("--name", default="Natalia,Наталья,Наталия", help="ожидаемое имя в приветствии (через запятую)")
    ap.add_argument("--expect", choices=["transfer", "hangup"], default="transfer", help="ожидаемый исход (hangup для прощания)")
    sys.exit(0 if asyncio.run(main(ap.parse_args())) else 1)
