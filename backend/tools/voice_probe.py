"""Сквозной голосовой тест без микрофона: синтез фразы клиента -> /ws/voice кадрами по 20 мс -> события и задержка.
python tools/voice_probe.py "текст" [--caller +77010000007] [--lang ru] [--url ws://localhost:8000/ws/voice]"""
import asyncio, json, sys, time, argparse, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websockets
from app import speech
from app.audio import resample

async def client_audio(text, lang):
    buf = b""
    async for c in speech.synth_stream(text, lang, store=True): buf += c
    return resample(buf, 24000, 16000)

async def main(a):
    utts = [await client_audio(t, a.lang) for t in a.text]
    url = a.url + (f"?caller_phone={a.caller.replace('+', '%2B')}" if a.caller else "")
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.start", "t": 0, "session_id": "", "channel": "web", "caller_phone": a.caller}))
        audio_bytes = {}; t_end = {}; first_audio = {}; cur = [0]; done = asyncio.Event(); traces = {}
        async def reader():
            async for m in ws:
                if isinstance(m, bytes):
                    audio_bytes[cur[0]] = audio_bytes.get(cur[0], 0) + len(m)
                    if cur[0] not in first_audio: first_audio[cur[0]] = time.perf_counter()
                    continue
                e = json.loads(m); tp = e["type"]
                if tp == "tts.start": cur[0] = e["turn"]
                if tp in ("stt.final", "bot.text.final", "vad.speech_end", "handoff", "error"):
                    print(f"  [{e['t']:>6}] {tp:16} turn={e.get('turn')} {e.get('text') or e.get('summary') or e.get('message') or ''}")
                if tp == "turn.trace":
                    tr = e["trace"]; traces[e["turn"]] = tr
                    print(f"  [{e['t']:>6}] trace scen={[(s['scenario_id'], s['confidence']) for s in tr['scenarios']]} dec={tr['decision']} acts={tr['actions']} lat={tr['latency_ms']}")
                if tp == "session.end": done.set()
        rt = asyncio.create_task(reader())
        await asyncio.sleep(4.0)
        for i, pcm in enumerate(utts, 1):
            for k in range(0, len(pcm), 640):
                await ws.send(pcm[k:k + 640]); await asyncio.sleep(0.02)
            t_end[i] = time.perf_counter()
            silence = b"\x00" * 640
            for _ in range(int(a.gap / 0.02)):
                await ws.send(silence); await asyncio.sleep(0.02)
        await asyncio.sleep(3); rt.cancel()
        for i in t_end:
            fa = first_audio.get(i)
            print(f"turn {i}: client-side end-of-speech -> first audio byte: {int((fa - t_end[i]) * 1000) if fa else None} ms, audio {audio_bytes.get(i, 0) / 48000:.1f}s")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("text", nargs="+"); ap.add_argument("--caller"); ap.add_argument("--lang", default="ru")
    ap.add_argument("--url", default="ws://localhost:8000/ws/voice"); ap.add_argument("--gap", type=float, default=7.0)
    asyncio.run(main(ap.parse_args()))
