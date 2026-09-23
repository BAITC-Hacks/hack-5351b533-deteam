"""Ответ бота: подтверждение понимания из шаблона (мгновенно, аудио из кэша) + содержательная часть
шаблоном или потоковой генерацией gpt-6-luna, ограниченной фактами из плана."""
import json, re
from functools import lru_cache
from pathlib import Path
import os, httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from . import kit, config
from .catalog import CATALOG

client = AsyncOpenAI(http_client=DefaultAsyncHttpxClient(limits=httpx2.Limits(max_connections=200, max_keepalive_connections=50,
                                                                               keepalive_expiry=float(os.getenv("OPENAI_KEEPALIVE_S", "120")))))
PHRASES = json.loads((Path(__file__).parent / "assets" / "scenario_phrases.json").read_text(encoding="utf-8"))

def sys_resp(sid, lang):
    return next(x for x in CATALOG.get()["system_intents"] if x["id"] == sid)["response"][lang]

def first_sentence(t):
    m = re.match(r"^(.+?[.!])(\s|$)", t.strip())
    return m.group(1) if m else None

ACK_OVERRIDE = {"SC11": {"ru": "Сейчас помогу.", "kk": "Қазір көмектесемін."}}   # opening начинается с вопроса

def ack_for(sid, lang):
    if sid in ACK_OVERRIDE: return ACK_OVERRIDE[sid].get(lang)
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

# честный ответ на «Вы робот?»: [без активного вопроса, с возвратом к вопросу]
ROBOT = {"ru": ("Да, я виртуальный ассистент Saqta Insurance на основе искусственного интеллекта. Чем могу помочь?",
                "Да, я виртуальный ассистент Saqta Insurance, при желании соединю с оператором. {}"),
         "kk": ("Иә, мен Saqta Insurance компаниясының жасанды интеллектке негізделген виртуалды көмекшісімін. Немен көмектесейін?",
                "Иә, мен Saqta Insurance виртуалды көмекшісімін, қаласаңыз операторға қосамын. {}")}
OOS_BACK = {"ru": "С этим, к сожалению, не помогу. Вернёмся к вопросу: {}", "kk": "Өкінішке қарай, бұған көмектесе алмаймын. Сұрағыма оралайық: {}"}

REASK = {"again_phone": {"ru": "Не нашла вас по этому номеру. Повторите, пожалуйста, номер телефона.", "kk": "Бұл нөмір бойынша сізді таппадым. Телефон нөміріңізді қайталап айтыңызшы."},
         "again_iin": {"ru": "Не нашла клиента по этому ИИН. Повторите, пожалуйста, ИИН.", "kk": "Бұл ЖСН бойынша клиент табылмады. ЖСН-ді қайталап айтыңызшы."},
         "again_any": {"ru": "Не нашла такой номер. {p}", "kk": "Мұндай нөмір табылмады. {p}"},
         "bad_phone": {"ru": "Не расслышала номер. Повторите, пожалуйста, номер телефона.", "kk": "Нөмірді анық естімедім. Телефон нөміріңізді қайталап айтыңызшы."},
         "bad_iin": {"ru": "Не расслышала ИИН, в нём двенадцать цифр. Повторите, пожалуйста.", "kk": "ЖСН-ді анық естімедім, онда он екі сан бар. Қайталап айтыңызшы."},
         "bad_any": {"ru": "Не расслышала. {p}", "kk": "Анық естімедім. {p}"},
         "other_iin": {"ru": "По этому номеру снова не нашла. Назовите, пожалуйста, ИИН, или соединю с оператором.",
                       "kk": "Бұл нөмір бойынша тағы табылмады. ЖСН-іңізді айтыңызшы, немесе операторға қосамын."},
         "other_phone": {"ru": "По ИИН не нашла. Назовите, пожалуйста, номер телефона, или соединю с оператором.",
                         "kk": "ЖСН бойынша табылмады. Телефон нөміріңізді айтыңызшы, немесе операторға қосамын."}}

GREET_REPLY = {"ru": "Здравствуйте! Слушаю вас.", "kk": "Сәлеметсіз бе! Тыңдап тұрмын."}
HEARD_REPLY = {"ru": "Да, слышу вас хорошо. Чем могу помочь?", "kk": "Иә, жақсы естіп тұрмын. Қалай көмектесе аламын?"}
CLARIFY_OPEN = {"ru": "Подскажите, по какому вопросу вы звоните: страховка авто, здоровья, жилья или путешествий?",
                "kk": "Қай мәселе бойынша хабарласып тұрсыз: көлік, денсаулық, тұрғын үй немесе сапар сақтандыруы?"}
FILLER = {"calc": {"ru": "Сейчас посчитаю.", "kk": "Қазір есептеймін."}, "check": {"ru": "Секунду, проверяю.", "kk": "Бір сәт, тексеремін."},
          "preview": {"ru": "Так, проверю данные.", "kk": "Деректерді тексерейін."}, "any": {"ru": "Секунду.", "kk": "Бір сәт."}}

def filler_for(items, actions, lang):
    """Короткая естественная фраза, пока LLM формулирует ответ (не пустая пауза на ходах с расчётом/проверкой)."""
    kinds = {i.get("kind") for i in items}
    if "preview" in kinds: key = "preview"
    elif any(a.startswith("calc_") for a in actions): key = "calc"
    elif any(a.split(":")[0].startswith(("get_", "check_", "list_", "find_", "kb_", "resend_", "request_")) for a in actions): key = "check"
    else: key = "any"
    return FILLER[key][lang]

def template_for(items, lang):
    """Ответ без LLM, если он детерминирован. Иначе None."""
    if len(items) != 1: return None
    it = items[0]; k = it["kind"]
    if k == "goodbye": return sys_resp("SYS_GOODBYE", lang)
    if k == "greet":
        base = HEARD_REPLY[lang] if it.get("hearing") else GREET_REPLY[lang]
        if it.get("resume_slot"): return base.split("!")[0].split(".")[0] + ". " + slot_prompt(it["resume_slot"], lang)
        return base
    if k == "out_of_scope":
        rp = it.get("reprompt")
        if it.get("robot"): return ROBOT[lang][1].format(rp) if rp else ROBOT[lang][0]
        if rp: return OOS_BACK[lang].format(rp[:1].lower() + rp[1:])
        return sys_resp("SYS_OUT_OF_SCOPE", lang)
    if k == "clarify":
        o = [PHRASES[x][lang] for x in it.get("options", []) if x in PHRASES]
        if len(o) >= 2: return sys_resp("SYS_UNCLEAR", lang).replace("{option_a}", o[0]).replace("{option_b}", o[1])
        return CLARIFY_OPEN[lang]
    if k == "declined": return {"ru": "Хорошо. Чем ещё могу помочь?", "kk": "Жақсы. Тағы немен көмектесе аламын?"}[lang]
    if k == "cancelled": return {"ru": "Хорошо, отложим. Чем ещё могу помочь?", "kk": "Жақсы, кейінге қалдырайық. Тағы немен көмектесе аламын?"}[lang]
    f = it.get("facts") or {}
    if k == "ask" and (f.get("ask_again") or f.get("try_other_identifier")):     # повторный запрос идентификатора — детерминированно
        bad = (it.get("error") or {}).get("code") == "invalid_input"
        if f.get("try_other_identifier"):
            return REASK["other_" + it["slot"]][lang] if "other_" + it["slot"] in REASK else None
        key = ("bad_" if bad else "again_") + (it["slot"] if it["slot"] in ("phone", "iin") else "any")
        return REASK[key][lang].format(p=slot_prompt(it["slot"], lang))
    if k == "ask" and not it.get("error") and (not it["facts"] or set(it["facts"]) == {"ask_alternative"}):
        alt = (it["facts"] or {}).get("ask_alternative")
        if alt in ALT_PROMPTS: return ALT_PROMPTS[alt][lang]
        if isinstance(it.get("question"), dict): return it["question"].get(lang) or it["question"]["ru"]
        return slot_prompt(it["slot"], lang)
    if k == "handoff" and it["scenario"] == "SC11":     # есть пострадавшие: 112 — обязательно и без LLM
        return {"ru": "Если есть пострадавшие, сразу звоните сто двенадцать. Соединяю вас со специалистом по урегулированию, он уже видит ваше обращение.",
                "kk": "Зардап шеккендер болса, бірден жүз он екіге қоңырау шалыңыз. Сізді шығынды реттеу маманына қосамын, ол өтінішіңізді көріп отыр."}[lang]
    if k == "handoff" and it["scenario"] == "SC38" and (it["facts"] or {}).get("ticket_id"):
        return {"ru": "Обращение {t} передала в службу безопасности, больше никому не сообщайте коды. Соединяю со специалистом, он уже видит ситуацию.",
                "kk": "{t} өтінішін қауіпсіздік қызметіне бердім, кодтарды енді ешкімге айтпаңыз. Сізді маманға қосамын, ол жағдайды көріп отыр."}[lang].format(t=it["facts"]["ticket_id"])
    if k == "handoff" and it["scenario"] == "SC37" and not it["facts"]:
        return CATALOG.scenarios()["SC37"]["responses"][lang]["closing"]
    return None

RESP_SYS = """You are the voice of the Saqta Insurance contact center (a polite female operator; you are an AI assistant and say so honestly if asked).
Write ONLY the words to say next, in the language given in "language" (ru = Russian, kk = Kazakh). Spoken style for text-to-speech.
Rules:
- Short: at most "max_sentences" sentences (question included), about 30 words in total. Merge facts into one sentence rather than adding sentences. At most ONE question, and put it at the very end.
- If items contain deferred or offer_return: answer the main item in ONE sentence, then the short return/deferred question.
- Lists (documents, covers, steps, clinics): name at most 3 key items; if a *_sms fact is true, say the full list was sent by SMS.
- Never read out internal codes or field names (zone letters, statuses in English, product codes); say them in natural words. Keep clinic and company names as given (e.g. Saulet Medical), but say cities, streets and addresses in the response language ("Astana, Turan Ave 30" -> "Астана, проспект Туран, тридцать"; kk: "Астана, Тұран даңғылы, отыз").
- No filler sentences («Понимаю.», «Хорошо.») on their own: merge empathy into the first sentence.
- "ack_already_spoken" was already said aloud: do not repeat it or greet again.
- Use ONLY facts from "items" and the knowledge in them. Never invent prices, dates, rules or numbers. If a fact is missing, say you will check or offer an operator.
- Dates in 2026 without the year ("до девятого октября", not "...две тысячи двадцать шестого года"); say the year only if it is not 2026.
- Write amounts, dates, years, times and counts as words, never digits ("тридцать восемь тысяч тенге", "второго октября", "четырнадцатого февраля две тысячи двадцать седьмого года", "в девять тридцать"; kk: "отыз сегіз мың теңге", "екінші қазанда", "екі мың жиырма жетінші жылы"). Keep policy/claim/ticket numbers and plates exactly as given. Phone numbers are already given in words: repeat them as is.
- Never read full emails or IIN: emails masked like r***@mail.example.
- items kinds: done = report the result (use style_example as a guide); ask = ask exactly for ask_for; preview = ONE statement sentence with the key details (what, when/amount; not a question), then exactly "Подтверждаете?" (kk: "Растайсыз ба?") as the only question (irreversible action); offer = give the facts and ask the question; handoff = say you are connecting to a specialist who already sees the context; deferred = say briefly you will help with it right after; cancelled = acknowledge the client postponed it; offer_return = ask if they want to return to that topic; error in facts = explain the reason in one sentence and offer the nearest option.
- Empathy first for claims, complaints and accidents; calm and fast for urgent cases.
- Address the client by first name only if client_name is given and it is the first answer about this topic."""

ACTION_NAMES = {"create_policy": {"ru": "оформление полиса", "kk": "полисті рәсімдеу"}, "renew_policy": {"ru": "продление полиса", "kk": "полисті ұзарту"},
                "update_policy": {"ru": "изменение полиса", "kk": "полисті өзгерту"}, "cancel_policy": {"ru": "расторжение полиса", "kk": "полисті бұзу"},
                "create_claim": {"ru": "регистрация заявления о страховом случае", "kk": "сақтандыру жағдайы бойынша өтінішті тіркеу"},
                "create_dispute": {"ru": "регистрация несогласия с решением", "kk": "шешіммен келіспеуді тіркеу"},
                "book_inspection": {"ru": "запись на осмотр автомобиля", "kk": "көлікті тексеруге жазу"},
                "book_appointment": {"ru": "запись к врачу", "kk": "дәрігерге жазу"}, "update_contact": {"ru": "изменение контактных данных", "kk": "байланыс деректерін өзгерту"}}

def llm_payload(sess, text, items, ack):
    lang = sess.language
    out = []
    other_q = any(i["kind"] in ("ask", "offer", "preview", "offer_return", "deferred", "clarify") for i in items)   # один вопрос за ход
    for it in items:
        k = it["kind"]; sid = it.get("scenario")
        d = {"kind": k}
        if sid: d["topic"] = scen_name(sid, lang)
        if it.get("facts"): d["facts"] = speakable_facts(it["facts"], lang)
        if it.get("error"): d["error"] = it["error"]
        if k == "ask":
            q = it.get("question")
            d["ask_for"] = (q.get(lang) or q.get("ru")) if isinstance(q, dict) else q or slot_prompt(it["slot"], lang)
            if (it.get("facts") or {}).get("nearest_options"): d["ask_for"] = "which of nearest_options suits the client (name them, one short question)"
            if (it.get("facts") or {}).get("client_policies"): d["ask_for"] = "which of client_policies (name the products, e.g. ОГПО or КАСКО; one short question)"
        if k in ("done", "handoff") and sid in CATALOG.scenarios():
            d["style_example"] = CATALOG.scenarios()[sid]["responses"][lang]["closing"]
            if it.get("question") and not other_q: d["then_ask"] = it["question"]
        if k == "offer": d["ask"] = it.get("question")
        if k == "preview": d["irreversible_action"] = ACTION_NAMES.get(it.get("action"), {}).get(lang, it.get("action"))
        if k == "clarify": d["options"] = [PHRASES[x][lang] for x in it.get("options", []) if x in PHRASES]
        out.append(d)
    main = [x for x in out if x["kind"] not in ("deferred", "offer_return")]
    side = len(main) < len(out)          # есть вопрос «вернёмся к…» / «потом займёмся…»
    mx = min(3, 2 + (len(main) > 1 or side)) if not ack else 2
    return {"language": lang, "client_name": first_name(sess.client["full_name"], lang) if sess.client else None,
            "ack_already_spoken": ack, "max_sentences": mx, "client_said": text, "items": out}

async def stream_llm(payload):
    kw = dict(model=config.RESPONSE_MODEL, instructions=RESP_SYS, input=json.dumps(payload, ensure_ascii=False),
              reasoning={"effort": "none"}, max_output_tokens=300, stream=True, prompt_cache_key="responder-v1")
    if config.SERVICE_TIER: kw["service_tier"] = config.SERVICE_TIER
    try:
        stream = await client.responses.create(**kw)
    except Exception:
        kw["model"] = config.ROUTER_FALLBACK_MODEL
        stream = await client.responses.create(**kw)
    lang = payload.get("language", "ru"); buf = ""
    async for ev in stream:
        if ev.type == "response.output_text.delta":
            buf += ev.delta
            # отдаём до последнего пробела, перед которым не цифра: «38 000 тенге» конвертируется целиком
            cut = max((m.end() for m in re.finditer(r"[^\d\s]\s+(?=\S)", buf)), default=0)
            if cut:
                yield words_for_digits(buf[:cut], lang); buf = buf[cut:]
    if buf: yield words_for_digits(buf, lang)

def handoff_summary(sess, text):
    c = sess.client
    parts = []
    if c: parts.append(f"Клиент {c['full_name']}, {c['phone']}.")
    topics = [scen_name(s, "ru") for s in sess.completed + [f.sid for f in sess.stack] if s != "SC37"]
    if sess.handoff and sess.handoff["scenario"] != "SC37": topics.append(scen_name(sess.handoff["scenario"], "ru"))
    if topics: parts.append("Вопросы: " + ", ".join(dict.fromkeys(topics)) + ".")
    facts = dict((sess.handoff or {}).get("facts") or {})
    for f in ([sess.active] if sess.active else []) + list(sess.stack):     # что клиент уже назвал по отложенным темам
        for k, v in f.slots.items():
            if k not in ("phone", "iin") and v not in (None, "", []) and k not in facts: facts[k] = v
    if facts: parts.append("Данные: " + ", ".join(f"{k}={v}" for k, v in list(facts.items())[:6]) + ".")
    parts.append(f"Последняя реплика: «{text}».")
    return " ".join(parts)


# ---------------------------------------------------------------- речь: имена, продукты, числа словами
NAMES = {"Arman": ("Арман", "Арман"), "Aigerim": ("Айгерим", "Айгерім"), "Yerlan": ("Ерлан", "Ерлан"), "Natalia": ("Наталья", "Наталья"),
         "Daniyar": ("Данияр", "Данияр"), "Madina": ("Мадина", "Мадина"), "Sergey": ("Сергей", "Сергей"), "Alibek": ("Алибек", "Әлібек"),
         "Rustem": ("Рустем", "Рүстем"), "Kamila": ("Камила", "Камила"), "Nurlan": ("Нурлан", "Нұрлан")}
LAT2CYR = [("zh", "ж"), ("kh", "х"), ("sh", "ш"), ("ch", "ч"), ("ya", "я"), ("yu", "ю"), ("ye", "е"), ("a", "а"), ("b", "б"), ("c", "к"), ("d", "д"),
           ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"), ("i", "и"), ("j", "дж"), ("k", "к"), ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"),
           ("q", "к"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("v", "в"), ("w", "у"), ("x", "кс"), ("y", "ы"), ("z", "з")]

def first_name(full, lang):
    n = (full or "").split()[0] if full else None
    if not n: return None
    if n in NAMES: return NAMES[n][lang == "kk"]
    low = n.lower()
    for a, b in LAT2CYR: low = low.replace(a, b)
    return low.capitalize()

PRODUCTS = {"ogpo": ("ОГПО", "ОГПО"), "casco": ("КАСКО", "КАСКО"), "travel": ("страховка для путешествий", "сапар сақтандыруы"),
            "property": ("страхование жилья", "тұрғын үй сақтандыруы"), "accident": ("страхование от несчастных случаев", "жазатайым оқиғалардан сақтандыру"),
            "dms": ("ДМС", "ДМС"), "ogpo_victim": ("ОГПО, обращение пострадавшего", "ОГПО, зардап шеккеннің өтініші")}
PHONE_RE = re.compile(r"^\+7\d{10}$")

def speakable_facts(o, lang, key=None):
    """Факты для LLM: коды продуктов -> названия, телефоны -> слова (LLM часто ошибается в цифрах телефона)."""
    if isinstance(o, dict): return {k: speakable_facts(v, lang, k) for k, v in o.items()}
    if isinstance(o, list): return [speakable_facts(v, lang, key) for v in o]
    if isinstance(o, str):
        if PHONE_RE.match(o): return phone_words(o, lang)
        if key and key.endswith("_ends_with") and o.isdigit(): return " ".join(num_words(int(c), lang) for c in o)
        if key in ("product", "product_type", "claim_type", "expected") and o in PRODUCTS: return PRODUCTS[o][lang == "kk"]
    return o

RU_U = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
RU_UF = ["ноль", "одна", "две"] + RU_U[3:]
RU_TEEN = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
RU_T = ["", "десять", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
RU_H = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
RU_ORD = ["", "первого", "второго", "третьего", "четвёртого", "пятого", "шестого", "седьмого", "восьмого", "девятого", "десятого", "одиннадцатого",
          "двенадцатого", "тринадцатого", "четырнадцатого", "пятнадцатого", "шестнадцатого", "семнадцатого", "восемнадцатого", "девятнадцатого"]
RU_ORD_T = ["", "", "двадцатого", "тридцатого", "сорокового", "пятидесятого", "шестидесятого", "семидесятого", "восьмидесятого", "девяностого"]
KK_U = ["нөл", "бір", "екі", "үш", "төрт", "бес", "алты", "жеті", "сегіз", "тоғыз"]
KK_T = ["", "он", "жиырма", "отыз", "қырық", "елу", "алпыс", "жетпіс", "сексен", "тоқсан"]
RU_MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"
KK_MONTHS = "қаңтар|ақпан|наурыз|сәуір|мамыр|маусым|шілде|тамыз|қыркүйек|қазан|қараша|желтоқсан"

def _ru999(n, fem=False):
    w = []; h, r = divmod(n, 100)
    if h: w.append(RU_H[h])
    if 10 <= r < 20: w.append(RU_TEEN[r - 10])
    else:
        t, u = divmod(r, 10)
        if t: w.append(RU_T[t])
        if u: w.append((RU_UF if fem else RU_U)[u])
    return w

def _plural(n, forms):
    n %= 100
    if 11 <= n <= 19: return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]

def ru_num(n):
    if n == 0: return "ноль"
    w = []
    for div, forms, fem in ((10**9, ("миллиард", "миллиарда", "миллиардов"), False), (10**6, ("миллион", "миллиона", "миллионов"), False), (1000, ("тысяча", "тысячи", "тысяч"), True)):
        q, n = divmod(n, div)
        if q: w += _ru999(q, fem) + [_plural(q, forms)]
    if n: w += _ru999(n)
    return " ".join(w)

def _kk999(n):
    w = []; h, r = divmod(n, 100)
    if h: w += ([KK_U[h]] if h > 1 else []) + ["жүз"]
    t, u = divmod(r, 10)
    if t: w.append(KK_T[t])
    if u: w.append(KK_U[u])
    return w

def kk_num(n):
    if n == 0: return "нөл"
    w = []
    for div, name in ((10**9, "миллиард"), (10**6, "миллион"), (1000, "мың")):
        q, n = divmod(n, div)
        if q: w += ([] if q == 1 and div == 1000 else _kk999(q)) + [name]
    if n: w += _kk999(n)
    return " ".join(w)

def num_words(n, lang): return kk_num(n) if lang == "kk" else ru_num(n)

def ru_ord_gen(n):
    """Порядковое в родительном: 14 -> «четырнадцатого», 2027 -> «две тысячи двадцать седьмого»."""
    if n >= 1000:
        th, r = divmod(n, 1000)
        return (ru_num(th * 1000) + " " + ru_ord_gen(r)) if r else ru_num(th * 1000)[:-1] + "ного"
    if n < 20: return RU_ORD[n]
    t, u = divmod(n, 10)
    return RU_ORD_T[t] if not u else f"{RU_T[t]} {RU_ORD[u]}"

def kk_ord(n):
    w = kk_num(n).split(); last = w[-1]
    if last == "жиырма": last = "жиырмасыншы"
    elif last[-1] in "аеыіоөұүә": last += "ншы" if re.search(r"[аоұы][^аоұыәөүіе]*$", last) else "нші"
    else: last += "ыншы" if re.search(r"[аоұы][^аоұыәөүіе]*$", last) else "інші"
    return " ".join(w[:-1] + [last])

def phone_words(p, lang):
    d = re.sub(r"\D", "", p)
    if len(d) != 11: return p
    def g(x):
        z = len(x) - len(x.lstrip("0"))
        return " ".join([num_words(0, lang)] * z + ([num_words(int(x), lang)] if x.lstrip("0") else []))
    return "плюс " + ", ".join(g(x) for x in (d[0], d[1:4], d[4:7], d[7:9], d[9:11]))

PROTECT = re.compile(r"SQ-[A-Z]+-\d+|\bCL-\d+|\b[A-Z]{1,2}-\d+|\b\d{3}\s?[A-Z]{2,3}\s?\d{2}\b|\S+@\S+|\d{2}\*+\d{4}|\d+[.,]\d+")

def words_for_digits(text, lang):
    """Страховка поверх промпта: цифры, оставшиеся в ответе LLM, -> слова (идентификаторы, номера и госномера не трогаем)."""
    if not re.search(r"\d", text): return text
    keep = []
    def hold(m): keep.append(m.group(0)); return chr(0xE000 + len(keep) - 1)      # символ из Private Use Area, без цифр
    t = re.sub(r"\+7[\d\s\-()]{9,16}\d", lambda m: phone_words(m.group(0), lang), text)
    t = PROTECT.sub(hold, t)
    if lang == "kk":
        t = re.sub(rf"\b(\d{{1,2}})(?:-?(?:ші|шы|інші|ыншы|нші|ншы))?\s+({KK_MONTHS})", lambda m: f"{kk_ord(int(m.group(1)))} {m.group(2)}", t)
        t = re.sub(r"\b(\d{4})(?:\s*ж\.|\s+жыл)", lambda m: f"{kk_ord(int(m.group(1)))} жыл", t)
    else:
        t = re.sub(rf"\b(\d{{1,2}})(?:-?го)?\s+({RU_MONTHS})", lambda m: f"{ru_ord_gen(int(m.group(1)))} {m.group(2)}", t)
        t = re.sub(r"\b(\d{4})\s+(года|г\.)", lambda m: f"{ru_ord_gen(int(m.group(1)))} года", t)
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: num_words(int(m.group(1)), lang) + " " + (num_words(int(m.group(2)), lang) if m.group(2) != "00" else ("ноль ноль" if lang == "ru" else "нөл нөл")), t)
    t = re.sub(r"\b\d{1,3}(?:[  ]\d{3})+\b|\b\d+\b", lambda m: num_words(int(re.sub(r"\D", "", m.group(0))), lang), t)
    return re.sub("[\ue000-\uf8ff]", lambda m: keep[ord(m.group(0)) - 0xE000], t)

def static_phrases():
    """Фиксированные фразы этого модуля для предсинтеза TTS (speech.template_phrases может добавить их к своим)."""
    out = []
    for lang in ("ru", "kk"):
        out.append((ROBOT[lang][0], lang))
        out.append((template_for([{"kind": "handoff", "scenario": "SC11", "facts": {"injured": True}}], lang), lang))
        out.append(({"ru": "Уточните, пожалуйста, что именно вас интересует?", "kk": "Нақты не қызықтыратынын айтып жіберіңізші."}[lang], lang))
        out.append(({"ru": "Все целы, никто не пострадал?", "kk": "Барлығы аман ба, зардап шеккендер жоқ па?"}[lang], lang))
    return out
