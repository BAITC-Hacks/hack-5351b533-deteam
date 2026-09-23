"""Один ход диалога: понимание -> исполнение -> ответ (ack + шаблон/LLM, стриминг по предложениям) -> трассировка."""
import asyncio, re, time
from . import config
from .catalog import CATALOG
from .responder import template_for, ack_for, llm_payload, stream_llm, handoff_summary

SENT_END = re.compile(r"([.!?…])(\s|$)")
DEC_MAP = {"offer_yes": "run", "resume": "run", "offer_no": "cancel"}

async def _noop(*a, **k): return None

async def process_turn(sess, text, *, t0=None, stt_ms=0, emit=None, speak=None, first_audio: asyncio.Future | None = None):
    """emit(event) шлёт события клиенту; speak(text, template) ставит фразу в очередь TTS; first_audio резолвится при первом аудиочанке."""
    emit = emit or _noop; speak = speak or _noop
    t0 = t0 or time.perf_counter()
    sess.turn_no += 1; sess._turn_actions = []; ev_start = len(sess.events)
    sess.history.append({"role": "client", "text": text})
    t_u0 = time.perf_counter()
    u = await sess.understand(text)
    t_u1 = time.perf_counter()
    r = u.get("router")
    router_ms = (r or {}).get("_meta", {}).get("latency_ms", 0) + ((r or {}).get("second_opinion") or {}).get("latency_ms", 0)
    triage_ms = max(0, int((t_u1 - t_u0) * 1000) - router_ms)
    items = sess.execute(u)
    for e in sess.events[ev_start:]:
        await emit({**e})
    lang = sess.language
    # --- ответ
    new_first = next((it for it in items if it.get("new") and it.get("scenario") and it["kind"] not in ("deferred",)), None)
    ack = ack_for(new_first["scenario"], lang) if new_first and u["decision"] in ("run", "offer_yes") else None
    tpl = template_for(items, lang)
    t_resp = None; parts = []
    async def say(chunk, template):
        nonlocal t_resp
        chunk = chunk.strip()
        if not chunk: return
        if t_resp is None: t_resp = time.perf_counter()
        parts.append(chunk)
        await emit({"type": "bot.text.delta", "turn": sess.turn_no, "delta": chunk + " "})
        await speak(chunk, template)
    if ack: await say(ack, True)
    source = "template"
    if tpl:
        await say(tpl, True)
    else:
        source = "llm"
        buf = ""
        async for d in stream_llm(llm_payload(sess, text, items, ack)):
            buf += d
            while (m := SENT_END.search(buf)):
                await say(buf[:m.end()], False); buf = buf[m.end():]
        await say(buf, False)
    reply = " ".join(parts)
    sess.history.append({"role": "bot", "text": reply})
    await emit({"type": "bot.text.final", "turn": sess.turn_no, "text": reply, "lang": lang, "template": source == "template"})
    t_first = None
    if first_audio is not None:
        try: t_first = await asyncio.wait_for(asyncio.shield(first_audio), 10)
        except Exception: t_first = None
    t_resp = t_resp or time.perf_counter()
    lat = {"stt": int(stt_ms), "triage": triage_ms, "router": router_ms,
           "response": max(0, int((t_resp - t_u1) * 1000)),
           "tts_first_audio": max(0, int(((t_first or t_resp) - t_resp) * 1000))}
    lat["total"] = int(((t_first or t_resp) - t0) * 1000)
    trace = build_trace(sess, text, u, items, lat, reply, source)
    await emit({"type": "turn.trace", "turn": sess.turn_no, "trace": trace})
    await emit({"type": "dialog.state", "state": sess.state()})
    if sess.handoff and not sess.ended:
        h = sess.handoff
        await emit({"type": "handoff", "turn": sess.turn_no, "queue": h["queue"], "summary": handoff_summary(sess, text),
                    "context": {"client": sess.client, "scenarios": sess.completed + [h["scenario"]], "slots": sess.state()["slots"], "language": lang,
                                "emotion": trace["emotion"], "transcript_tail": sess.history[-4:]}})
        sess.ended = True
        await emit({"type": "session.end", "reason": "handoff"})
    elif sess.ended:
        await emit({"type": "session.end", "reason": "goodbye"})
    return trace

def build_trace(sess, text, u, items, lat, reply, source):
    r = u.get("router") or {}
    cat = CATALOG.scenarios()
    if r:
        scen = [{"scenario_id": s["scenario_id"], "confidence": round(s["confidence"], 3), "name": cat.get(s["scenario_id"], {}).get("name", s["scenario_id"]),
                 "segment": s.get("segment"), "reason": s.get("reason"), "boundary_rule": s.get("boundary_rule")} for s in r.get("scenarios", [])]
        alts = [{"scenario_id": a["scenario_id"], "confidence": round(a["confidence"], 3)} for a in r.get("alternatives", [])]
        reason = "; ".join(s.get("reason") or "" for s in r.get("scenarios", []))
    else:
        scen = [{"scenario_id": s, "confidence": 1.0, "name": cat.get(s, {}).get("name", s), "reason": f"fast path: {u.get('fast_path')}"} for s in u.get("scenarios", [])]
        alts, reason = [], f"deterministic {u.get('fast_path')}"
    if u["decision"] == "clarify":
        scen = [{"scenario_id": "SYS_UNCLEAR", "confidence": (r.get("scenarios") or [{"confidence": 0}])[0]["confidence"], "reason": reason}] + [x for x in scen if x["scenario_id"] != "SYS_UNCLEAR"][:0]
        alts = [{"scenario_id": x, "confidence": next((s["confidence"] for s in (r.get("scenarios") or []) + (r.get("alternatives") or []) if s["scenario_id"] == x), 0)} for x in u.get("clarify", [])]
    return {"turn": sess.turn_no, "transcript": text, "language": r.get("language") or ("kk" if sess.language == "kk" else "ru"),
            "scenarios": scen, "alternatives": alts, "reason": reason, "slots": u.get("slots", {}), "actions": list(sess._turn_actions),
            "latency_ms": lat, "decision": DEC_MAP.get(u["decision"], u["decision"]), "response_language": sess.language, "response_text": reply,
            "response_source": source, "fast_path": u.get("fast_path"), "speculative_hit": False, "second_opinion": r.get("second_opinion"),
            "router_model": (r.get("_meta") or {}).get("model"), "catalog_version": sess.catalog_version,
            "active_scenario": sess.active.sid if sess.active else None, "stack": [f.sid for f in sess.stack],
            "emotion": r.get("emotion", "neutral"), "urgency": r.get("urgency", "normal"), "channel": sess.channel, "interrupted": False,
            "client_id": sess.client["client_id"] if sess.client else None}
