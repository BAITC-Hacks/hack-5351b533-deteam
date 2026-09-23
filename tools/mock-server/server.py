"""Мок бэкенда Voice Router: реплей fixtures по контракту /contracts.

  pip install -r requirements.txt && python server.py
  HTTP/WS :8000, AudioSocket :9092

WS /ws/voice?fixture=web-d03 : на каждую реплику клиента (text.input, input.commit или
голос: 0.6 с речи и затем 0.5 с тишины) отдаёт следующий ход из fixture с исходными таймингами.
Аудио бота заменено тоном 440 Гц нужной длительности.
"""
import asyncio, json, math, os, struct, uuid, time
from urllib.parse import parse_qsl
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

FX = Path(__file__).resolve().parents[2] / "fixtures"
J = lambda p: json.loads((FX / p).read_text())
app = FastAPI(title="Voice Router mock")
supervisors: set[WebSocket] = set()
calls: dict[str, dict] = {}

def load(sid):
    return [json.loads(l) for l in (FX / "sessions" / f"{sid}.jsonl").read_text().splitlines() if l]

def tone(ms, sr=24000):
    n = int(sr * ms / 1000)
    return b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / sr))) for i in range(n))

async def broadcast(ev):
    for s in list(supervisors):
        try: await s.send_text(json.dumps(ev, ensure_ascii=False))
        except Exception: supervisors.discard(s)

@app.get("/api/health")
def health(): return {"status": "ok", "catalog_version": "v3-9f2c", "frozen": True, "models": {"router": "mock"}}
@app.get("/api/clients")
def clients(): return J("catalog/clients.min.json")
@app.get("/api/catalog")
def catalog():
    v = [x for x in J("supervisor/catalog_versions.json") if x["current"]][0]
    return {"version": v, "scenarios": J("catalog/scenarios.min.json")}
@app.get("/api/catalog/versions")
def versions(): return J("supervisor/catalog_versions.json")
@app.get("/api/stats")
def stats(): return J("supervisor/stats.json")
@app.get("/api/sessions")
def sessions(): return J("supervisor/sessions.json")
@app.get("/api/sessions/{sid}")
def session(sid: str):
    ev = load(sid); meta = next(s for s in J("supervisor/sessions.json") if s["session_id"] == sid)
    tr = []
    for e in ev:
        if e["type"] == "stt.final": tr.append({"turn": e["turn"], "role": "client", "text": e["text"], "lang": e["lang"]})
        if e["type"] == "bot.text.final": tr.append({"turn": e["turn"], "role": "bot", "text": e["text"], "lang": e["lang"]})
    return {**meta, "transcript": tr, "traces": [e["trace"] for e in ev if e["type"] == "turn.trace"],
            "final_state": [e["state"] for e in ev if e["type"] == "dialog.state"][-1]}
@app.get("/api/sessions/{sid}/events")
def events(sid: str): return PlainTextResponse((FX / "sessions" / f"{sid}.jsonl").read_text(), media_type="application/x-ndjson")
@app.get("/api/cases")
def cases(): return J("supervisor/cases.json")
@app.post("/api/cases", status_code=201)
async def case_create(r: Request):
    b = await r.json(); c = {"case_id": "CASE-0004", "source": "supervisor", "lang": b.get("lang", "ru"), "created_at": "2026-10-01T12:10:00Z", **b}
    await broadcast({"type": "case.created", "t": 0, "session_id": "", "case_id": c["case_id"], "source": "supervisor"}); return c
@app.get("/api/bench/runs")
def runs(): return J("supervisor/bench_runs.json")
@app.get("/api/patches")
def patches(): return J("supervisor/patches.json")
@app.get("/api/patches/{pid}")
def patch(pid: str): return J("supervisor/patches.json")[0]
@app.post("/api/patches", status_code=202)
async def patch_create(r: Request):
    async def flow():
        for st, d in [("proposing", 0), ("validating", 0), ("regression_before", 55), ("regression_before", 107), ("regression_after", 60), ("regression_after", 107), ("ready", 107)]:
            await asyncio.sleep(2); await broadcast({"type": "patch.progress", "t": 0, "session_id": "", "patch_id": "PT-0003", "stage": st, "done": d, "total": 107})
        await broadcast({"type": "patch.ready", "t": 0, "session_id": "", "patch_id": "PT-0003"})
    asyncio.create_task(flow()); return {**J("supervisor/patches.json")[0], "status": "proposing", "proposal": None, "regression": None}
@app.post("/api/patches/{pid}/apply")
async def apply(pid: str):
    v = {"version": "v4-1a7e", "parent": "v3-9f2c", "hash": "1a7e55d0", "applied_patch": pid, "created_at": "2026-10-01T12:05:02Z", "frozen": False, "current": True, "note": "SC17/SC19 boundary", "primary_acc": 0.944}
    await broadcast({"type": "catalog.changed", "t": 0, "session_id": "", "version": v["version"], "parent": "v3-9f2c", "frozen": False, "reason": "patch_applied"}); return v
@app.post("/api/catalog/freeze")
async def freeze(r: Request):
    f = (await r.json())["frozen"]
    await broadcast({"type": "catalog.changed", "t": 0, "session_id": "", "version": "v3-9f2c", "parent": "v2-77c0", "frozen": f, "reason": "freeze" if f else "unfreeze"})
    return {**J("supervisor/catalog_versions.json")[2], "frozen": f}

# --- telephony contract ---
@app.post("/api/telephony/calls")
async def call_register(r: Request):
    f = dict(parse_qsl((await r.body()).decode())); u = str(uuid.uuid4()); calls[u] = {**f, "outcome": "hangup"}
    print("CALL", u, f); return PlainTextResponse(u)
@app.get("/api/telephony/calls/{u}/outcome")
def outcome(u: str): return PlainTextResponse(calls.get(u, {}).get("outcome", "hangup"))
@app.post("/api/telephony/calls/{u}/hangup")
async def hangup(u: str, r: Request): print("HANGUP", u, dict(parse_qsl((await r.body()).decode()))); return PlainTextResponse("ok")

@app.websocket("/ws/supervisor")
async def ws_sup(ws: WebSocket):
    await ws.accept(); supervisors.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect: supervisors.discard(ws)

@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket, fixture: str = "web-d03"):
    await ws.accept()
    ev = load(fixture)
    groups, cur = [], []
    for e in ev:  # группы: приветствие, затем по ходу (всё до следующего vad.speech_start)
        if e["type"] == "vad.speech_start" and cur: groups.append(cur); cur = []
        cur.append(e)
    groups.append(cur)
    trigger = asyncio.Queue(); t_start = time.monotonic()

    async def play(group):
        base = group[0]["t"]; t0 = time.monotonic()
        for e in group:
            if e["type"] == "vad.speech_start": continue
            await asyncio.sleep(max(0, (e["t"] - base) / 1000 - (time.monotonic() - t0)))
            e = {**e, "t": int((time.monotonic() - t_start) * 1000)}
            await ws.send_text(json.dumps(e, ensure_ascii=False)); await broadcast(e)
            if e["type"] == "tts.start":
                nxt = next((x for x in group if x["type"] == "tts.end" and x["turn"] == e["turn"]), None)
                dur = (nxt["t"] - [x for x in group if x is not None and x["type"] == "tts.start" and x["turn"] == e["turn"]][0]["t"]) if nxt else 1500
                pcm = tone(min(dur, 4000)); asyncio.create_task(send_audio(pcm))

    async def send_audio(pcm):
        for i in range(0, len(pcm), 4800):  # 100 мс чанки
            await ws.send_bytes(pcm[i:i + 4800]); await asyncio.sleep(0.09)

    async def reader():
        voiced = silent = 0
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect": raise WebSocketDisconnect()
            if m.get("bytes"):
                b = m["bytes"]; n = len(b) // 2
                rms = math.sqrt(sum(s * s for s in struct.unpack(f"<{n}h", b[:n * 2])) / max(n, 1)) if n else 0
                if rms > 500: voiced += 20; silent = 0
                elif voiced >= 600:
                    silent += 20
                    if silent >= 500: voiced = silent = 0; await trigger.put(1)
            elif m.get("text"):
                t = json.loads(m["text"]).get("type")
                if t in ("text.input", "input.commit"): await trigger.put(1)

    rt = asyncio.create_task(reader())
    try:
        # ждём session.start
        await play(groups[0])
        for g in groups[1:]:
            await trigger.get(); await play(g)
        await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError): pass
    finally: rt.cancel()

# --- AudioSocket echo: проверка телефонии без бэкенда ---
async def audiosocket(reader, writer):
    """Эхо с задержкой 1 с. В ARI-режиме DTMF обрабатывает ARI-контроллер."""
    uid = None; buf = []
    try:
        while True:
            h = await reader.readexactly(3); kind, ln = h[0], struct.unpack(">H", h[1:])[0]
            p = await reader.readexactly(ln) if ln else b""
            if kind == 0x01: uid = str(uuid.UUID(bytes=p)); print("AUDIOSOCKET uuid", uid)
            elif kind == 0x10:
                buf.append(p)
                if len(buf) > 50: writer.write(b"\x10" + struct.pack(">H", len(buf[0])) + buf.pop(0)); await writer.drain()
            elif kind == 0x03:
                d = p.decode(); print("DTMF", d)
                if os.environ.get("MOCK_TELEPHONY_MODE") == "ari":
                    continue
                if d in "0#":
                    if uid in calls and d == "0": calls[uid]["outcome"] = "transfer:operator_general"
                    writer.write(b"\x00\x00\x00"); await writer.drain(); break
            elif kind == 0x00: break
    except asyncio.IncompleteReadError: pass
    finally: writer.close()

async def main():
    srv = await asyncio.start_server(audiosocket, "0.0.0.0", 9092)
    cfg = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info"))
    async with srv: await cfg.serve()

if __name__ == "__main__":
    asyncio.run(main())
