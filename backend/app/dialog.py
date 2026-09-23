"""Один ход диалога: понимание -> исполнение -> ответ (ack + шаблон/LLM, стриминг по предложениям) -> трассировка.
Ранний ack: как только в потоке роутера виден новый уверенный сценарий, его подтверждение («Сейчас рассчитаю…»)
озвучивается до конца ответа роутера."""
import asyncio, re, time
from . import config
from .catalog import CATALOG
from .responder import filler_for, template_for, ack_for, llm_payload, stream_llm, handoff_summary
from .session import RU_SWITCH, KK_SWITCH

SENT_END = re.compile(r"([.!?…])(\s|$)")
DEC_MAP = {"offer_yes": "run", "resume": "run", "offer_no": "cancel", "greet": "clarify"}

async def _noop(*a, **k): return None

def _early_lang(sess, text, e):
    """Зеркало выбора языка в Session.understand: mixed -> lang_lock (с учётом просьбы в этой реплике) или kk."""
    lock = "ru" if RU_SWITCH.search(text) else "kk" if KK_SWITCH.search(text) else sess.lang_lock
    if e.get("language") == "mixed": return lock or sess.preferred_language() or e.get("response_language") or "kk"
    return e.get("response_language") or sess.language

def _early_ok(sess, e):
    """Ранний ack только для нового реального сценария с уверенностью запуска вне подтверждений/офферов."""
    if e.get("scenario_id") in getattr(sess, "done_frames", {}): return False
    sid = e.get("scenario_id")
    if not sid or sid.startswith("SYS_") or e.get("confidence", 0) < config.CONF_RUN: return False
    if e.get("is_continuation") or e.get("confirmation") is not None: return False
    a = sess.active if sess.active and sess.active.status == "active" else None
    if a and a.sid == sid: return False
    if sess.offer_next or sess.offer_return: return False
    return not any(f.sid == sid for f in sess.stack)

async def process_turn(sess, text, *, t0=None, stt_ms=0, emit=None, speak=None, first_audio: asyncio.Future | None = None):
    """emit(event) шлёт события клиенту; speak(text, template) ставит фразу в очередь TTS; first_audio резолвится при первом аудиочанке.

    latency_ms, мс (t0 = конец речи клиента / получение текста):
      stt             — распознавание (передаётся снаружи);
      triage          — understand без роутера (fast path, нормализация);
      router          — полное время роутера, включая второе мнение;
      response        — от готового решения роутера до первого произнесённого текста; 0, если ранний ack прозвучал раньше;
      first_text      — от t0 до первого произнесённого текста (ранний ack, шаблон или первое предложение LLM);
      tts_first_audio — от первого текста до первого аудиочанка (0 без аудио);
      total           — от t0 до первого аудиочанка (без аудио — до первого текста).
    trace.early_ack = {spoken, scenario_id, matched_final}: был ли ранний ack, по какому сценарию, совпал ли с итоговым."""
    emit = emit or _noop; speak = speak or _noop
    t0 = t0 or time.perf_counter()
    sess.turn_no += 1; sess._turn_actions = []; ev_start = len(sess.events)
    sess.history.append({"role": "client", "text": text})
    t_resp = None; parts = []
    async def say(chunk, template):
        nonlocal t_resp
        chunk = chunk.strip()
        if not chunk: return
        if t_resp is None: t_resp = time.perf_counter()
        parts.append(chunk)
        await emit({"type": "bot.text.delta", "turn": sess.turn_no, "delta": chunk + " "})
        await speak(chunk, template)
    early = {"spoken": False, "scenario_id": None, "ack": None}
    async def on_early(e):
        early["scenario_id"] = e.get("scenario_id")
        if not _early_ok(sess, e): return
        lang_e = _early_lang(sess, text, e)
        ack_e = ack_for(e["scenario_id"], lang_e)
        if not ack_e: return
        sess.language = lang_e          # язык TTS для ack; understand выставит то же значение после роутера
        early.update(spoken=True, ack=ack_e)
        await say(ack_e, True)
    t_u0 = time.perf_counter()
    u = await sess.understand(text, on_early=on_early)
    t_u1 = time.perf_counter()
    r = u.get("router")
    meta = (r or {}).get("_meta") or {}
    router_ms = meta.get("total_ms") or (meta.get("latency_ms", 0) + ((r or {}).get("second_opinion") or {}).get("latency_ms", 0))
    triage_ms = max(0, int((t_u1 - t_u0) * 1000) - router_ms)
    prev_done = set(sess.done_frames)          # тема уже пройдена в этом звонке — вводное «Расскажу.» не повторяем
    items = sess.execute(u)
    for e in sess.events[ev_start:]:
        await emit({**e})
    lang = sess.language
    # --- ответ
    new_first = next((it for it in items if it.get("new") and it.get("scenario") and it["kind"] not in ("deferred",)), None)
    runs = new_first is not None and u["decision"] in ("run", "offer_yes") and new_first["scenario"] not in prev_done
    early_ack = {"spoken": early["spoken"], "scenario_id": early["scenario_id"], "matched_final": bool(runs and new_first["scenario"] == early["scenario_id"])}
    if early["spoken"]:
        ack = early["ack"]              # уже произнесён; второй раз не говорим, LLM получает его как ack_already_spoken
    else:
        ack = ack_for(new_first["scenario"], lang) if runs else None
        if ack: await say(ack, True)
    tpl = template_for(items, lang)
    source = "template"
    if not tpl and not ack and parts == []:
        ack = filler_for(items, sess._turn_actions, lang)
        await say(ack, True)
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
           "first_text": max(0, int((t_resp - t0) * 1000)),
           "tts_first_audio": max(0, int(((t_first or t_resp) - t_resp) * 1000))}
    lat["total"] = int(((t_first or t_resp) - t0) * 1000)
    trace = build_trace(sess, text, u, items, lat, reply, source)
    trace["early_ack"] = early_ack
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
    # версия каталога, которой реально пользовался роутер в этом ходе (после применения патча меняется и в живой сессии)
    sess.catalog_version = (r.get("_meta") or {}).get("catalog_version") or CATALOG.version
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
