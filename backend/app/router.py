"""LLM-слой выбора сценария: стриминговый вызов со строгой JSON-схемой и кэшированным каталогом.

Задержка:
- стрим + ранний колбэк: on_early({language, response_language, is_continuation, confirmation, scenario_id, confidence})
  срабатывает, как только в частичном JSON появились id и confidence первого сценария (порядок полей в схеме важен);
- хеджирование: ROUTER_HEDGE (по умолчанию 2) одинаковых стримов параллельно, побеждает первый выдавший текстовый
  токен, остальные отменяются и закрываются;
- второе мнение для зоны сомнения стартует спекулятивно по раннему результату, не дожидаясь конца первого вызова.
"""
import json, time, asyncio, os, re, inspect, logging
from functools import lru_cache
import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from . import kit, config
from .catalog import CATALOG

# keepalive дольше паузы между репликами (по умолчанию httpx 5 с): не платим TCP+TLS на каждом ходе
client = AsyncOpenAI(http_client=DefaultAsyncHttpxClient(limits=httpx2.Limits(max_connections=1000, max_keepalive_connections=100,
                                                                               keepalive_expiry=float(os.getenv("ROUTER_KEEPALIVE_S", "120")))))
log = logging.getLogger("router")
HEDGE = max(1, int(os.getenv("ROUTER_HEDGE", "2")))
REFILL = os.getenv("ROUTER_REFILL", "1") == "1"
HEDGE_KEYS = os.getenv("ROUTER_HEDGE_KEYS", "0") == "1"
LATE_MS = int(os.getenv("ROUTER_LATE_HEDGE_MS", "1800"))   # нет первого токена за это время -> ещё один запрос на запасную модель

def schema(ids: tuple, slot_names: tuple) -> dict:
    sref = {"type": "string", "enum": list(ids)}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["language", "response_language", "is_continuation", "confirmation", "scenarios", "alternatives", "slots", "urgency", "emotion"],
        "properties": {
            "language": {"type": "string", "enum": ["ru", "kk", "mixed"]},
            "response_language": {"type": "string", "enum": ["ru", "kk"]},
            "is_continuation": {"type": "boolean"},
            "confirmation": {"type": ["string", "null"], "enum": ["yes", "no", None]},
            "scenarios": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["scenario_id", "confidence", "segment", "reason", "boundary_rule"],
                "properties": {"scenario_id": sref, "confidence": {"type": "number"}, "segment": {"type": "string"},
                               "reason": {"type": "string"}, "boundary_rule": {"type": ["string", "null"]}}}},
            "alternatives": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["scenario_id", "confidence"], "properties": {"scenario_id": sref, "confidence": {"type": "number"}}}},
            "slots": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "value"],
                "properties": {"name": {"type": "string", "enum": list(slot_names)}, "value": {"type": "string"}}}},
            "urgency": {"type": "string", "enum": ["normal", "high", "urgent"]},
            "emotion": {"type": "string", "enum": ["neutral", "upset", "anxious", "happy"]},
        },
    }

RULES = """
ROUTING RULES
- Pick the scenario(s) the client wants NOW, in the order they should be handled. Several requests in one utterance -> several scenarios, each with its own short segment.
- Order: urgent scenarios first (accident happening right now SC11, sick/injured abroad SC15, fraud call SC38), then in the order mentioned.
- Use the not_this_if boundaries of every candidate. Typical traps:
  * "авария/ДТП/жол апаты": happening now or client is at the scene -> SC11; happened earlier and the OTHER driver is at fault (client is the victim, culprit insured at Saqta) -> SC12; damage to the client's OWN car with CASCO -> SC13.
  * disagreement with a claim decision or with the approved amount -> SC19, not status SC17. Asking which documents are needed or where to send them -> SC18.
  * money charged but policy not issued -> SC30, not resend SC26. Policy exists but did not arrive by email -> SC26.
  * wants a price only -> quote scenario; wants to buy/issue now -> purchase scenario.
  * explicit request for a human/operator -> SC37. Complaint about service/staff -> SC35.
- SYS_OUT_OF_SCOPE: not about Saqta services (life insurance, pensions, loans, deposits, mortgage, weather, jobs, other companies' products).
- SYS_UNCLEAR: the request is too vague to pick a scenario (e.g. "у меня вопрос по страховке", "проблема с полисом"). Then give SYS_UNCLEAR confidence and put the 2 most likely scenarios into alternatives.
- SYS_GOODBYE: client ends the conversation, only thanks, no new request.
- confidence = your honest probability that this scenario is right. Vague or borderline requests must get < 0.7. Clear requests 0.85-0.99.
- alternatives: up to 2 other plausible scenarios with lower confidence (empty if none).

DIALOG STATE
- If STATE has an active scenario and the utterance answers the bot's last question or adds details for it -> is_continuation=true and scenarios=[that active scenario] (plus any NEW request after it).
- confirmation: only when STATE.awaiting is "confirmation" or "offer": "yes" if the client agrees, "no" if refuses or postpones, otherwise null. Agreement to an offer to buy after a quote starts the purchase.
- While STATE.awaiting is "confirmation", a question about the price, terms or details of the pending action is NOT a new request: is_continuation=true, confirmation=null, scenarios=[active scenario].
- A new request while a scenario is active is a topic switch: return the new scenario, is_continuation=false.

LANGUAGE
- language: dominant language of the utterance; "mixed" when Kazakh and Russian are both used in the utterance.
- response_language: language the bot should answer in: the client's dominant language; for mixed use the language of the main request; if the client asks to switch ("давайте на русском", "қазақша сөйлесейік") use the requested one.

SLOTS
- Extract only slots mentioned in THIS utterance (or clearly answering the last question). Normalize values:
  phone "+7XXXXXXXXXX" (spoken digits or words in ru/kk: "плюс семь семьсот один..." / "жеті жүз бір..."; leading 8 -> +7);
  iin 12 digits; plates uppercase latin like 777ABC02; policy "SQ-OGPO-104501"; claim "CL-500287";
  dates ISO YYYY-MM-DD relative to TODAY (завтра/ертең = TODAY+1, вчера/кеше = TODAY-1, "три дня назад"/"үш күн бұрын" = TODAY-3);
  years 4 digits ("двадцатого года" -> 2020); money and counts as plain integers; lists comma-separated;
  booleans "true"/"false"; enums exactly one of the allowed values; cities/countries/doctor specialties in English as in the allowed values or common English names.
- segment: the part of the utterance for this scenario ONLY when there are several scenarios; otherwise "".
- reason: max 8 words, English. boundary_rule: short name of the not_this_if rule you applied (e.g. "SC17->SC19"), or null.
- urgency: urgent for SC11/SC15/SC38 situations, high for claims/complaints, else normal. emotion of the client.
"""

@lru_cache(maxsize=8)
def _system(version: str) -> str:
    sc = CATALOG.scenarios(version)
    lines = []
    for s in sc.values():
        nt = "; ".join(f"{x['condition']} -> {x['use_instead']}" for x in s["not_this_if"]) or "-"
        ex_ru = " / ".join(s["examples"]["ru"][:3] + s["examples"]["ru"][4:]); ex_kk = " / ".join(s["examples"]["kk"][:2] + s["examples"]["kk"][3:])  # [4:]/[3:] = примеры, добавленные патчами каталога
        lines.append(f"{s['scenario_id']} | {s['name']} | {s['domain']}/{s['category']}/{s['priority']} | {s['description']} | "
                     f"not_this_if: {nt} | ru: {ex_ru} | kk: {ex_kk} | slots: {', '.join(s['slots']['required'] + s['slots']['optional'])}")
    sysi = "\n".join(f"{x['id']} | {x['description']}" for x in CATALOG.get(version)["system_intents"])
    sl = []
    for s in kit.slots().values():
        fmt = s.get("pattern") or (", ".join(map(str, s["values"])) if s.get("values") else s["type"])
        sl.append(f"{s['name']} ({s['type']}): {fmt}")
    return (f"You are the routing layer of the Saqta Insurance voice assistant (Kazakhstan, non-life insurance: auto OGPO/CASCO, "
            f"health DMS, travel, property, accident). Clients speak Russian, Kazakh or mix both. TODAY is {config.TODAY}.\n"
            f"Read the client's utterance in the context of STATE and HISTORY and return the routing decision strictly by the JSON schema.\n\n"
            f"SCENARIO CATALOG (id | name | domain/category/priority | description | boundaries | examples | slots)\n" + "\n".join(lines) +
            f"\n\nSYSTEM INTENTS\n{sysi}\n\nSLOT FORMATS\n" + "\n".join(sl) + "\n" + RULES)

def _ids(version):
    return tuple(list(CATALOG.scenarios(version).keys()) + kit.SYS_IDS)

# ---------- ранний разбор частичного JSON (strict-схема выдаёт поля в порядке схемы)
EARLY_RE = re.compile(
    r'"language"\s*:\s*"(?P<language>\w+)"\s*,\s*"response_language"\s*:\s*"(?P<response_language>\w+)"\s*,\s*'
    r'"is_continuation"\s*:\s*(?P<is_continuation>true|false)\s*,\s*"confirmation"\s*:\s*(?P<confirmation>null|"\w*")\s*,\s*'
    r'"scenarios"\s*:\s*\[\s*(?:\]|\{\s*"scenario_id"\s*:\s*"(?P<scenario_id>\w+)"\s*,\s*'
    r'"confidence"\s*:\s*(?P<confidence>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*[,}])')

def parse_early(buf: str) -> dict | None:
    """Первые поля решения из частичного JSON; None, пока id и confidence первого сценария не дописаны (или scenarios не закрыт пустым)."""
    m = EARLY_RE.search(buf)
    if not m: return None
    g = m.groupdict()
    return {"language": g["language"], "response_language": g["response_language"], "is_continuation": g["is_continuation"] == "true",
            "confirmation": None if g["confirmation"] == "null" else g["confirmation"].strip('"') or None,
            "scenario_id": g["scenario_id"], "confidence": float(g["confidence"]) if g["confidence"] else 0.0}

# ---------- хеджированный стрим
_BG: set = set()
def _bg(coro):
    t = asyncio.create_task(coro); _BG.add(t); t.add_done_callback(_BG.discard)
    return t

async def _close(stream):
    """Закрыть стрим: финализировать генератор SDK (его finally закрывает HTTP-ответ) и сам ответ. Идемпотентно."""
    it = getattr(stream, "_iterator", None)
    if it is not None:
        try: await it.aclose()
        except Exception: pass
    try: await stream.close()
    except Exception: pass

async def _first_token(kw):
    """Открыть стрим и дочитать до первого текстового токена -> (stream, delta, t_first). При ошибке/отмене стрим закрывается."""
    stream = None
    try:
        stream = await client.responses.create(**{k: v for k, v in kw.items() if not k.startswith("_")}, stream=True)
        while True:
            try: ev = await stream.__anext__()
            except StopAsyncIteration: raise RuntimeError("router stream ended before output")
            if ev.type == "response.output_text.delta": return stream, ev.delta, time.perf_counter()
            if ev.type in ("response.failed", "response.incomplete", "error"): raise RuntimeError(f"router stream {ev.type}")
    except BaseException:
        if stream is not None: await _close(stream)
        raise

async def _reap(tasks):
    """Дождаться отменённых проигравших и закрыть стримы тех, кто успел выиграть одновременно с победителем.
    Закрытый недочитанный HTTP/1.1-стрим убивает соединение, поэтому взамен открываем тёплое дешёвым GET без токенов:
    на следующем ходе все хеджи стартуют на готовых соединениях (холодное соединение ~ +250 мс к TTFT)."""
    for r in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(r, tuple): await _close(r[0])
    if REFILL:
        await asyncio.gather(*[client.models.retrieve(config.ROUTER_MODEL) for _ in tasks], return_exceptions=True)

def _kw_i(kw, i):
    # хеджи идентичны (тот же prompt_cache_key): замер с разными ключами выигрыша не дал; ROUTER_HEDGE_KEYS=1 — разные ключи
    return kw if i == 0 or not HEDGE_KEYS else {**kw, "prompt_cache_key": f"{kw['prompt_cache_key']}-h{i}"}

async def _hedged(kw, n):
    """N одинаковых стримов; победитель = первый выдавший текстовый токен. -> (index, stream, first_delta, t_first).
    Поздний хедж (живой диалог, n > 1): если за LATE_MS нет первого токена — ещё один стрим на ROUTER_FALLBACK_MODEL
    (режет хвосты API в 5–8 с). Его индекс = n; модель победителя кладём в kw["_winner_model"]."""
    tasks = [asyncio.create_task(_first_token(_kw_i(kw, i))) for i in range(n)]
    win = None; err = None; t_start = time.perf_counter()
    late_ok = n > 1 and LATE_MS > 0 and kw["model"] != config.ROUTER_FALLBACK_MODEL
    try:
        pending = set(tasks)
        while pending and win is None:
            timeout = max(0.0, LATE_MS / 1000 - (time.perf_counter() - t_start)) if late_ok else None
            done, pending = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done and late_ok:
                late_ok = False
                lt = asyncio.create_task(_first_token({**kw, "model": config.ROUTER_FALLBACK_MODEL}))
                tasks.append(lt); pending.add(lt)
                log.warning("router: no first token in %d ms, late hedge -> %s", LATE_MS, config.ROUTER_FALLBACK_MODEL)
                continue
            for i, t in enumerate(tasks):
                if t not in done or t.cancelled(): continue
                if t.exception() is not None: err = t.exception()
                elif win is None: win = (i, *t.result())
    finally:
        losers = [t for i, t in enumerate(tasks) if win is None or i != win[0]]
        for t in losers:
            if not t.done(): t.cancel()
        if losers: _bg(_reap(losers))
    if win is None: raise err or RuntimeError("router: no stream")
    kw["_winner_model"] = config.ROUTER_FALLBACK_MODEL if win[0] >= n else kw["model"]
    return win

async def _stream_once(kw, n, t0, check):
    """Один хеджированный вызов до конца потока -> (out, meta_part)."""
    i, stream, buf, t_first = await _hedged(kw, n)
    final = None
    try:
        check(buf)
        while True:
            try: ev = await stream.__anext__()
            except StopAsyncIteration: break
            if ev.type == "response.output_text.delta":
                buf += ev.delta; check(buf)
            elif ev.type == "response.completed": final = ev.response
            elif ev.type in ("response.failed", "response.incomplete", "error"): raise RuntimeError(f"router stream {ev.type}")
    finally:
        await _close(stream)
    out = json.loads(buf)
    u = getattr(final, "usage", None)
    return out, {"ttft_ms": int((t_first - t0) * 1000), "hedge_winner": i,
                 "input_tokens": getattr(u, "input_tokens", None),
                 "cached_tokens": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", None),
                 "output_tokens": getattr(u, "output_tokens", None), "service_tier": getattr(final, "service_tier", None)}

def _kw(utterance, state=None, history=None, version=None, model=None, is_partial=False, effort="none"):
    version = version or CATALOG.version
    payload = {"STATE": state or {}, "HISTORY": (history or [])[-6:], "UTTERANCE": utterance, "is_partial": is_partial}
    kw = dict(model=model or config.ROUTER_MODEL, instructions=_system(version), input=json.dumps(payload, ensure_ascii=False),
              reasoning={"effort": effort}, max_output_tokens=500, prompt_cache_key=f"router-{version}",
              text={"format": {"type": "json_schema", "name": "route", "strict": True,
                               "schema": schema(_ids(version), tuple(kit.slots().keys()))}})
    if config.SERVICE_TIER: kw["service_tier"] = config.SERVICE_TIER
    return kw

async def route(utterance: str, state: dict | None = None, history: list | None = None, version: str | None = None,
                model: str | None = None, is_partial: bool = False, effort: str = "none", on_early=None, hedge: int | None = None) -> dict:
    """Решение роутера. on_early(dict) вызывается не более одного раза (sync или async; корутина выполняется параллельно
    с дочиткой потока и дожидается до возврата). _meta: latency_ms полное время, ttft_ms до первого токена, early_ms до
    раннего колбэка (None, если не было), hedge_winner индекс победившего хеджа, n_hedge число хеджей."""
    version = version or CATALOG.version
    model = model or config.ROUTER_MODEL
    n = max(1, hedge or HEDGE)
    kw = _kw(utterance, state, history, version, model, is_partial, effort)
    t = time.perf_counter()
    early = {"ms": None, "task": None}

    def check(buf):
        if on_early is None or early["ms"] is not None: return
        e = parse_early(buf)
        if e is None: return
        early["ms"] = int((time.perf_counter() - t) * 1000)
        try:
            res = on_early(e)
            if inspect.isawaitable(res): early["task"] = asyncio.ensure_future(res)
        except Exception as ex:
            log.warning("on_early failed: %r", ex)

    try:
        try:
            out, m = await _stream_once(kw, n, t, check)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            if model == config.ROUTER_FALLBACK_MODEL: raise
            log.warning("router %s failed (%r), fallback %s", model, ex, config.ROUTER_FALLBACK_MODEL)
            kw["model"] = config.ROUTER_FALLBACK_MODEL
            out, m = await _stream_once(kw, n, t, check)
    except BaseException as ex:
        if early["task"] is not None and not early["task"].done():
            if isinstance(ex, asyncio.CancelledError): early["task"].cancel()
            else: await asyncio.gather(early["task"], return_exceptions=True)
        raise
    if early["task"] is not None:
        try: await early["task"]
        except Exception as ex: log.warning("on_early task failed: %r", ex)
    out["_meta"] = {"model": kw.pop("_winner_model", None) or kw["model"], "latency_ms": int((time.perf_counter() - t) * 1000), "ttft_ms": m.pop("ttft_ms"),
                    "early_ms": early["ms"], "hedge_winner": m.pop("hedge_winner"), "n_hedge": n, "catalog_version": version, **m}
    return out

async def warm(version=None):
    """Прогрев кэша префикса и пула соединений (по одному на хедж) после старта или смены версии каталога.
    Потоки дочитываются до конца, чтобы соединения вернулись в пул."""
    async def one(i):
        s, _, _ = await _first_token(_kw_i(_kw("Здравствуйте", version=version), i))
        try:
            async for _ in s: pass
        finally: await _close(s)
    await asyncio.gather(*[one(i) for i in range(HEDGE)], return_exceptions=True)


def needs_second_opinion(r: dict) -> bool:
    """Зона сомнения: верхний кандидат между порогами уточнения и запуска."""
    sc = r.get("scenarios") or []
    if not sc: return False
    c = sc[0]["confidence"]
    if sc[0]["scenario_id"] in ("SYS_GOODBYE", "SYS_OUT_OF_SCOPE") and c >= config.CONF_RUN: return False
    return config.CONF_CLARIFY <= c < config.CONF_RUN

async def route_full(utterance, state=None, history=None, version=None, model=None, on_early=None, hedge=None):
    """Роутер + второе мнение gpt-6-sol для зоны сомнения. Возвращает выход первого вызова с полем second_opinion.
    on_early(dict) пробрасывается в первый вызов (для второго мнения не вызывается). Второе мнение стартует уже по раннему
    результату, если первый сценарий попал в зону сомнения. _meta.total_ms — полное время вместе со вторым мнением."""
    t = time.perf_counter()
    so = {"task": None}
    def start_so():
        if so["task"] is None:
            so["task"] = asyncio.ensure_future(route(utterance, state, history, version, config.SECOND_OPINION_MODEL, effort="low", hedge=hedge))
    def early(e):
        if e.get("scenario_id") and needs_second_opinion({"scenarios": [e]}): start_so()
        return on_early(e) if on_early else None
    try:
        r = await route(utterance, state, history, version, model, on_early=early, hedge=hedge)  # hedge=1 в bench (evolution)
        r["second_opinion"] = None
        if needs_second_opinion(r):
            start_so()
            try:
                r2 = await so["task"]
                top1 = r["scenarios"][0]["scenario_id"]; top2 = r2["scenarios"][0]["scenario_id"] if r2["scenarios"] else None
                r["second_opinion"] = {"model": config.SECOND_OPINION_MODEL, "scenarios": [{"scenario_id": s["scenario_id"], "confidence": s["confidence"]} for s in r2["scenarios"]],
                                       "agreed": top1 == top2, "latency_ms": r2["_meta"]["latency_ms"]}
                if r2["scenarios"] and r2["scenarios"][0]["confidence"] >= config.CONF_RUN:
                    if top1 == top2:
                        r["scenarios"][0]["confidence"] = max(r["scenarios"][0]["confidence"], r2["scenarios"][0]["confidence"])
                    else:
                        r["scenarios"], r["alternatives"] = r2["scenarios"], (r2["alternatives"] or []) + [{"scenario_id": top1, "confidence": r["scenarios"][0]["confidence"]}]
                        r["slots"] = r2["slots"] or r["slots"]
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                log.warning("second opinion failed: %r", ex)
        r["_meta"]["total_ms"] = int((time.perf_counter() - t) * 1000)
        return r
    finally:
        tk = so["task"]
        if tk is not None and not tk.done():
            tk.cancel(); _bg(asyncio.gather(tk, return_exceptions=True))
        elif tk is not None and not tk.cancelled():
            tk.exception()  # пометить как прочитанное
