"""Ответ бота: подтверждение понимания из шаблона (мгновенно, аудио из кэша) + содержательная часть
шаблоном или потоковой генерацией gpt-6-luna, ограниченной фактами из плана."""
import json, re
from functools import lru_cache
from pathlib import Path
from openai import AsyncOpenAI
from . import kit, config
from .catalog import CATALOG

client = AsyncOpenAI()
PHRASES = json.loads((Path(__file__).parent / "assets" / "scenario_phrases.json").read_text())

def sys_resp(sid, lang):
    return next(x for x in CATALOG.get()["system_intents"] if x["id"] == sid)["response"][lang]

def first_sentence(t):
    m = re.match(r"^(.+?[.!])(\s|$)", t.strip())
    return m.group(1) if m else None

def ack_for(sid, lang):
    s = CATALOG.scenarios().get(sid)
    if not s or sid == "SC37": return None
    op = s["responses"].get(lang, s["responses"]["ru"])["opening"]
    fs = first_sentence(op)
    if not fs or "?" in fs or len(fs) > 60: return None
    return fs

def slot_prompt(slot, lang):
    return kit.slots()[slot]["prompt"][lang]

def scen_name(sid, lang):
    p = PHRASES.get(sid, {})
    return p.get("name_kk" if lang == "kk" else "name_ru") or CATALOG.scenarios().get(sid, {}).get("name", sid)

ALT_PROMPTS = {"claim": {"ru": "Назовите, пожалуйста, номер заявления или номер телефона.", "kk": "Өтініш нөмірін немесе телефон нөміріңізді айтып жіберіңізші."},
               "policy": {"ru": "Назовите, пожалуйста, номер полиса или номер телефона.", "kk": "Полис нөмірін немесе телефон нөміріңізді айтып жіберіңізші."}}

def template_for(items, lang):
    """Ответ без LLM, если он детерминирован. Иначе None."""
    if len(items) != 1: return None
    it = items[0]; k = it["kind"]
    if k == "goodbye": return sys_resp("SYS_GOODBYE", lang)
    if k == "out_of_scope": return sys_resp("SYS_OUT_OF_SCOPE", lang)
    if k == "clarify":
        o = [PHRASES[x][lang] for x in it.get("options", []) if x in PHRASES]
        if len(o) >= 2: return sys_resp("SYS_UNCLEAR", lang).replace("{option_a}", o[0]).replace("{option_b}", o[1])
        return {"ru": "Уточните, пожалуйста, что именно вас интересует?", "kk": "Нақты не қызықтыратынын айтып жіберіңізші."}[lang]
    if k == "declined": return {"ru": "Хорошо. Чем ещё могу помочь?", "kk": "Жақсы. Тағы немен көмектесе аламын?"}[lang]
    if k == "cancelled": return {"ru": "Хорошо, отложим. Чем ещё могу помочь?", "kk": "Жақсы, кейінге қалдырайық. Тағы немен көмектесе аламын?"}[lang]
    if k == "ask" and not it.get("error") and (not it["facts"] or set(it["facts"]) == {"ask_alternative"}):
        alt = (it["facts"] or {}).get("ask_alternative")
        if alt in ALT_PROMPTS: return ALT_PROMPTS[alt][lang]
        return slot_prompt(it["slot"], lang)
    if k == "handoff" and it["scenario"] == "SC37" and not it["facts"]:
        return CATALOG.scenarios()["SC37"]["responses"][lang]["closing"]
    return None

RESP_SYS = """You are the voice of the Saqta Insurance contact center (a polite female operator; you are an AI assistant and say so honestly if asked).
Write ONLY the words to say next, in the language given in "language" (ru = Russian, kk = Kazakh). Spoken style for text-to-speech.
Rules:
- 1-2 short sentences (max 3 if you must answer two topics). At most ONE question, and put it at the very end.
- "ack_already_spoken" was already said aloud: do not repeat it or greet again.
- Use ONLY facts from "items" and the knowledge in them. Never invent prices, dates, rules or numbers. If a fact is missing, say you will check or offer an operator.
- Dates in 2026 without the year ("до девятого октября", not "...две тысячи двадцать шестого года"); say the year only if it is not 2026.
- Write amounts, dates, times and counts as words ("тридцать восемь тысяч тенге", "второго октября", "в девять тридцать"; kk: "отыз сегіз мың теңге", "екінші қазанда"). Keep policy/claim/ticket numbers exactly as given.
- Never read full emails or IIN: emails masked like r***@mail.example.
- items kinds: done = report the result (use style_example as a guide); ask = ask exactly for ask_for; preview = read back the key details and ask for an explicit "yes" (irreversible action); offer = give the facts and ask the question; handoff = say you are connecting to a specialist who already sees the context; deferred = say briefly you will help with it right after; cancelled = acknowledge the client postponed it; offer_return = ask if they want to return to that topic; error in facts = explain the reason in one sentence and offer the nearest option.
- Empathy first for claims, complaints and accidents; calm and fast for urgent cases.
- Address the client by first name only if client_name is given and it is the first answer about this topic."""

def llm_payload(sess, text, items, ack):
    lang = sess.language
    out = []
    for it in items:
        k = it["kind"]; sid = it.get("scenario")
        d = {"kind": k}
        if sid: d["topic"] = scen_name(sid, lang)
        if it.get("facts"): d["facts"] = it["facts"]
        if it.get("error"): d["error"] = it["error"]
        if k == "ask": d["ask_for"] = slot_prompt(it["slot"], lang)
        if k in ("done", "handoff") and sid in CATALOG.scenarios():
            d["style_example"] = CATALOG.scenarios()[sid]["responses"][lang]["closing"]
            if it.get("question"): d["then_ask"] = it["question"]
        if k == "offer": d["ask"] = it.get("question")
        if k == "preview": d["irreversible_action"] = it.get("action")
        if k == "clarify": d["options"] = [PHRASES[x][lang] for x in it.get("options", []) if x in PHRASES]
        out.append(d)
    return {"language": lang, "client_name": sess.client["full_name"].split()[0] if sess.client else None,
            "ack_already_spoken": ack, "client_said": text, "items": out}

async def stream_llm(payload):
    kw = dict(model=config.RESPONSE_MODEL, instructions=RESP_SYS, input=json.dumps(payload, ensure_ascii=False),
              reasoning={"effort": "none"}, max_output_tokens=300, stream=True, prompt_cache_key="responder-v1")
    if config.SERVICE_TIER: kw["service_tier"] = config.SERVICE_TIER
    try:
        stream = await client.responses.create(**kw)
    except Exception:
        kw["model"] = config.ROUTER_FALLBACK_MODEL
        stream = await client.responses.create(**kw)
    async for ev in stream:
        if ev.type == "response.output_text.delta":
            yield ev.delta

def handoff_summary(sess, text):
    c = sess.client
    parts = []
    if c: parts.append(f"Клиент {c['full_name']}, {c['phone']}.")
    topics = [scen_name(s, "ru") for s in sess.completed + [f.sid for f in sess.stack] if s != "SC37"]
    if sess.handoff and sess.handoff["scenario"] != "SC37": topics.append(scen_name(sess.handoff["scenario"], "ru"))
    if topics: parts.append("Вопросы: " + ", ".join(dict.fromkeys(topics)) + ".")
    facts = (sess.handoff or {}).get("facts") or {}
    if facts: parts.append("Данные: " + ", ".join(f"{k}={v}" for k, v in list(facts.items())[:5]) + ".")
    parts.append(f"Последняя реплика: «{text}».")
    return " ".join(parts)
