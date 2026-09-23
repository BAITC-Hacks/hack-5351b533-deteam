"""LLM-слой выбора сценария: один вызов gpt-6-luna со строгой JSON-схемой и кэшированным каталогом."""
import json, time, asyncio
from functools import lru_cache
from openai import AsyncOpenAI
from . import kit, config
from .catalog import CATALOG

client = AsyncOpenAI()

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
        ex_ru = " / ".join(s["examples"]["ru"][:3]); ex_kk = " / ".join(s["examples"]["kk"][:2])
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

async def route(utterance: str, state: dict | None = None, history: list | None = None, version: str | None = None,
                model: str | None = None, is_partial: bool = False, effort: str = "none") -> dict:
    version = version or CATALOG.version
    model = model or config.ROUTER_MODEL
    payload = {"STATE": state or {}, "HISTORY": (history or [])[-6:], "UTTERANCE": utterance, "is_partial": is_partial}
    kw = dict(model=model, instructions=_system(version), input=json.dumps(payload, ensure_ascii=False),
              reasoning={"effort": effort}, max_output_tokens=500, prompt_cache_key=f"router-{version}",
              text={"format": {"type": "json_schema", "name": "route", "strict": True,
                               "schema": schema(_ids(version), tuple(kit.slots().keys()))}})
    if config.SERVICE_TIER: kw["service_tier"] = config.SERVICE_TIER
    t = time.perf_counter()
    try:
        r = await client.responses.create(**kw)
    except Exception:
        if model == config.ROUTER_FALLBACK_MODEL: raise
        kw["model"] = config.ROUTER_FALLBACK_MODEL
        r = await client.responses.create(**kw)
    out = json.loads(r.output_text)
    out["_meta"] = {"model": kw["model"], "latency_ms": int((time.perf_counter() - t) * 1000), "catalog_version": version,
                    "input_tokens": r.usage.input_tokens, "cached_tokens": r.usage.input_tokens_details.cached_tokens,
                    "output_tokens": r.usage.output_tokens, "service_tier": getattr(r, "service_tier", None)}
    return out

async def warm(version=None):
    """Прогрев кэша префикса после старта или смены версии каталога."""
    try: await route("Здравствуйте", version=version)
    except Exception: pass


def needs_second_opinion(r: dict) -> bool:
    """Зона сомнения: верхний кандидат между порогами уточнения и запуска."""
    sc = r.get("scenarios") or []
    if not sc: return False
    c = sc[0]["confidence"]
    if sc[0]["scenario_id"] in ("SYS_GOODBYE", "SYS_OUT_OF_SCOPE") and c >= config.CONF_RUN: return False
    return config.CONF_CLARIFY <= c < config.CONF_RUN

async def route_full(utterance, state=None, history=None, version=None, model=None):
    """Роутер + второе мнение gpt-6-sol для зоны сомнения. Возвращает выход первого вызова с полем second_opinion."""
    r = await route(utterance, state, history, version, model)
    r["second_opinion"] = None
    if needs_second_opinion(r):
        try:
            r2 = await route(utterance, state, history, version, config.SECOND_OPINION_MODEL, effort="low")
            top1 = r["scenarios"][0]["scenario_id"]; top2 = r2["scenarios"][0]["scenario_id"] if r2["scenarios"] else None
            r["second_opinion"] = {"model": config.SECOND_OPINION_MODEL, "scenarios": [{"scenario_id": s["scenario_id"], "confidence": s["confidence"]} for s in r2["scenarios"]],
                                   "agreed": top1 == top2, "latency_ms": r2["_meta"]["latency_ms"]}
            if r2["scenarios"] and r2["scenarios"][0]["confidence"] >= config.CONF_RUN:
                if top1 == top2:
                    r["scenarios"][0]["confidence"] = max(r["scenarios"][0]["confidence"], r2["scenarios"][0]["confidence"])
                else:
                    r["scenarios"], r["alternatives"] = r2["scenarios"], (r2["alternatives"] or []) + [{"scenario_id": top1, "confidence": r["scenarios"][0]["confidence"]}]
                    r["slots"] = r2["slots"] or r["slots"]
        except Exception:
            pass
    return r
