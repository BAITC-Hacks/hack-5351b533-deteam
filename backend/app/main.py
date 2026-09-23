"""Voice Router API: REST + WS /ws/voice + WS /ws/supervisor (+ AudioSocket сервер для телефонии)."""
import asyncio, json, os, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
from . import kit, config, speech
from .catalog import CATALOG
from .hub import HUB
from .call import Call
from .router import route_full, warm
from .policy import decide
from .responder import PHRASES

@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(warm())
    async def pre():
        n = await speech.prewarm(speech.template_phrases())
        print(f"[tts] prewarmed {n} phrases")
    if os.getenv("TTS_PREWARM", "1") == "1": asyncio.create_task(pre())
    if os.getenv("AUDIOSOCKET", "1") == "1":
        from .telephony import start_audiosocket
        app.state.as_server = await start_audiosocket()
    if os.getenv("ARI_URL"):   # ARI-контроллер (Stasis voice-ai -> External Media в наш AudioSocket), см. app/ari.py
        from .ari import start_ari
        app.state.ari = start_ari()
    yield

app = FastAPI(title="Voice Router", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Фичи в отдельных модулях со своими APIRouter
from .evolution import router as evolution_router
from .telephony import router as telephony_router
app.include_router(evolution_router)
app.include_router(telephony_router)

@app.get("/api/health")
def health():
    return {"status": "ok", "catalog_version": CATALOG.version, "frozen": CATALOG.frozen,
            "models": {"router": config.ROUTER_MODEL, "second_opinion": config.SECOND_OPINION_MODEL, "response": config.RESPONSE_MODEL,
                       "stt": config.STT_MODEL, "tts": config.TTS_MODEL, "patcher": config.PATCHER_MODEL}}

@app.get("/api/clients")
def clients():
    m = kit.mock_backend()
    return [{"client_id": c["client_id"], "full_name": c["full_name"], "phone": c["phone"], "city": c["city"], "preferred_language": c["preferred_language"],
             "products": [p["product"] for p in m["policies"] if p["client_id"] == c["client_id"]]} for c in m["clients"]]

def brief(s):
    p = PHRASES.get(s["scenario_id"], {})
    return {k: s[k] for k in ("scenario_id", "name", "domain", "category", "priority", "fast_path_eligible", "requires_identification", "requires_confirmation")} | {"name_ru": p.get("name_ru"), "name_kk": p.get("name_kk")}

@app.get("/api/catalog")
def catalog(): return {"version": {**CATALOG.current, "frozen": CATALOG.frozen}, "scenarios": [brief(s) for s in CATALOG.get()["scenarios"]]}

@app.get("/api/catalog/versions")
def versions(): return [{**v, "frozen": CATALOG.frozen and v["current"]} for v in CATALOG.versions]

@app.get("/api/catalog/versions/{version}")
def version(version: str):
    if version not in CATALOG.data: raise HTTPException(404, detail={"error": {"code": "not_found", "message": version}})
    return CATALOG.get(version)

@app.post("/api/catalog/freeze")
async def freeze(r: Request):
    b = await r.json(); v = CATALOG.freeze(bool(b.get("frozen")))
    await HUB.broadcast({"type": "catalog.changed", "t": 0, "session_id": "", "turn": None, "version": CATALOG.version, "parent": v.get("parent"),
                         "frozen": CATALOG.frozen, "reason": "freeze" if CATALOG.frozen else "unfreeze"})
    return v

@app.post("/api/catalog/rollback")
async def rollback(r: Request):
    b = await r.json()
    try: v = CATALOG.rollback(b["version"])
    except PermissionError: return JSONResponse({"error": {"code": "frozen", "message": "catalog is frozen"}}, 409)
    asyncio.create_task(warm())
    await HUB.broadcast({"type": "catalog.changed", "t": 0, "session_id": "", "turn": None, "version": v["version"], "parent": v["parent"], "frozen": False, "reason": "rollback"})
    return v

@app.post("/api/route")
async def route_once(r: Request):
    b = await r.json(); t = time.perf_counter()
    out = await route_full(b["text"], state=b.get("state"))
    return {"router": out, "decision": decide(out), "latency_ms": int((time.perf_counter() - t) * 1000), "model": out["_meta"]["model"]}

@app.get("/api/stats")
def stats(): return HUB.stats()

@app.get("/api/sessions")
def sessions(limit: int = 50, channel: str | None = None):
    out = [HUB.summary(k) for k in reversed(list(HUB.sessions))]
    return [x for x in out if not channel or x["channel"] == channel][:limit]

@app.get("/api/sessions/{sid}")
def session(sid: str):
    if sid not in HUB.sessions: raise HTTPException(404)
    rec = HUB.sessions[sid]; s = rec["session"]
    tr = []
    for e in rec["events"]:
        if e["type"] == "stt.final": tr.append({"turn": e["turn"], "role": "client", "text": e["text"], "lang": e["lang"]})
        if e["type"] == "bot.text.final": tr.append({"turn": e["turn"], "role": "bot", "text": e["text"], "lang": e["lang"]})
    from .hub import recording_obj
    return {**HUB.summary(sid), "transcript": tr, "traces": HUB.traces(sid), "final_state": s.state(),
            "recording": recording_obj(sid, s.channel, rec["ended_at"])}

@app.get("/api/sessions/{sid}/events")
def session_events(sid: str):
    if sid not in HUB.sessions: raise HTTPException(404)
    return PlainTextResponse("\n".join(json.dumps(e, ensure_ascii=False) for e in HUB.sessions[sid]["events"]), media_type="application/x-ndjson")

@app.websocket("/ws/supervisor")
async def ws_supervisor(ws: WebSocket):
    await ws.accept(); HUB.supervisors.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        HUB.supervisors.discard(ws)

@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    """Протокол в contracts/ws-events.schema.json. Бинарные кадры: PCM16 16 kHz (вход), 24 kHz (выход)."""
    await ws.accept()
    async def send_event(e): await ws.send_text(json.dumps(e, ensure_ascii=False))
    async def send_audio(b): await ws.send_bytes(b)
    qp = ws.query_params
    call = Call(qp.get("channel", "web"), send_event, send_audio)
    started = False
    async def start(ev=None):
        nonlocal started
        ev = ev or {}
        call.channel = ev.get("channel") or qp.get("channel", "web")
        await call.start(caller_phone=ev.get("caller_phone") or qp.get("caller_phone"), lang_hint=ev.get("lang_hint") or qp.get("lang_hint"))
        started = True
    def client_ev(ev, tp):
        return {"type": tp, "t": int((time.perf_counter() - call.t_start) * 1000), "session_id": call.sess.id, "turn": ev.get("turn"), "t_client_ms": ev.get("t_client_ms")}
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect": break
            if m.get("bytes") is not None:
                if not started: await start()
                await call.on_audio(m["bytes"]); continue
            try:
                ev = json.loads(m.get("text") or "{}"); t = ev.get("type")
                if not isinstance(t, str): raise ValueError("no type")
            except Exception:
                if started: await call.emit({"type": "error", "code": "protocol", "message": "bad JSON frame", "recoverable": True})
                continue
            if not started:
                await start(ev if t == "session.start" else None)
            if t == "text.input" and (ev.get("text") or "").strip():
                call._spawn(call.on_text(ev["text"].strip()))
            elif t == "input.commit": await call.commit()
            elif t == "control.interrupt": await call.player.interrupt(call.active_turn)
            elif t == "control.mute": call.set_muted(ev.get("muted"))
            elif t == "session.end": break
            elif t in ("playback.started", "playback.finished"): await HUB.publish(client_ev(ev, t))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback; traceback.print_exc()
        if started: await call.emit({"type": "error", "code": "internal", "message": f"{type(e).__name__}: {e}"[:200], "recoverable": False})
    finally:
        await call.close("client")
