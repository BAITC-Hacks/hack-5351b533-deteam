import os
"""STT (gpt-transcribe) и TTS (gpt-4o-mini-tts) с дисковым кэшем заготовленных фраз."""
import asyncio, hashlib, os, re, time
from pathlib import Path
import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from . import config
from .audio import wav_bytes

client = AsyncOpenAI(http_client=DefaultAsyncHttpxClient(limits=httpx2.Limits(max_connections=200, max_keepalive_connections=50,
                                                                               keepalive_expiry=float(os.getenv("OPENAI_KEEPALIVE_S", "120")))))
# Замер на 28 фразах dev-набора (синтез): language=kk + этот промпт даёт CER ru 0.000 / kk 0.016 / mixed 0.007 против 0.000 / 0.034 / 0.025
# у авто-языка, и не путает короткие ответы ("Да." в авто-режиме распознавалось как "Duh."). Русскую речь kk-режим не портит.
STT_PROMPT = os.getenv("STT_PROMPT", "Звонок в страховую Saqta Insurance, клиент говорит по-русски или по-казахски. Да. Нет. Иә. Жоқ. Верно. Дұрыс. "
                       "ОГПО, КАСКО, ДМС, полис, заявление, выплата, ИИН, ЖСН, өтініш.")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "kk") or None
CACHE = config.BACKEND / "tts_cache"; CACHE.mkdir(exist_ok=True)
INSTR = {"ru": "Native Russian speaker from Almaty, no foreign accent. Calm, warm contact-center operator. Natural, slightly fast pace.",
         "kk": "Native Kazakh speaker. Calm, warm contact-center operator. Natural, slightly fast pace."}
VOICE = {"ru": os.getenv("TTS_VOICE_RU", config.TTS_VOICE), "kk": os.getenv("TTS_VOICE_KK", config.TTS_VOICE)}

TTS = client.with_options(timeout=float(os.getenv("TTS_TIMEOUT", "15")), max_retries=1)
STT = client.with_options(timeout=float(os.getenv("STT_TIMEOUT", "8")), max_retries=1)

STT_HEDGE = int(os.getenv("STT_HEDGE", "2"))   # параллельных одинаковых запросов, берём первый ответ (режет хвост задержки API)

async def _transcribe_once(wav, language=STT_LANGUAGE):
    kw = {"language": language} if language else {}
    r = await STT.audio.transcriptions.create(model=config.STT_MODEL, file=("turn.wav", wav, "audio/wav"), prompt=STT_PROMPT, **kw)
    return r.text.strip()

async def transcribe(pcm16k: bytes) -> tuple[str, int]:
    t = time.perf_counter(); wav = wav_bytes(pcm16k)
    # хедж с разными языками: kk (лучше для казахского) и авто (короткие русские «алло», «да» с kk иногда дают пустоту)
    langs = [STT_LANGUAGE] + [None] * (max(1, STT_HEDGE) - 1) if STT_LANGUAGE else [None] * max(1, STT_HEDGE)
    tasks = [asyncio.create_task(_transcribe_once(wav, lang)) for lang in langs]
    err = None; empty = False
    try:
        for fut in asyncio.as_completed(tasks):
            try: text = await fut
            except asyncio.CancelledError: raise
            except Exception as e: err = e; continue
            if not text: empty = True; continue          # пустой ответ одного варианта: ждём второй
            return text, int((time.perf_counter() - t) * 1000)
        if empty: return "", int((time.perf_counter() - t) * 1000)
        raise err
    finally:
        for x in tasks: x.cancel()

# Типичные галлюцинации STT на тишине/шуме (титры видео) и эхо промпта. Реальный клиент так не говорит.
_HALLU = re.compile(r"продолжение следует|спасибо за (просмотр|внимание)|субтитр|редактор|корректор|подписывайтесь|подпишитесь|ставьте лайк|"
                    r"amara|dimatorzok|thank(s| you) for watching|subtitles|жазылыңыз|жазылып|келесі (бөлім|видео)|до новых встреч|"
                    r"\bмузыка\b|аплодисменты|\bсмех\b|\[.*\]|♪", re.I)
_norm = lambda t: " ".join(re.findall(r"\w+", t.lower()))
_PROMPT_NORM = _norm(STT_PROMPT)

def is_hallucination(text: str, voiced_ms: int | None = None) -> bool:
    t = (text or "").strip()
    words = re.findall(r"\w+", t.lower())
    if not words or len("".join(words)) < 2: return True
    if _HALLU.search(t): return True
    if len(" ".join(words)) >= 15 and " ".join(words) in _PROMPT_NORM: return True                  # эхо STT_PROMPT
    if voiced_ms is not None and voiced_ms < 400 and len(words) > 6: return True                     # длинный текст из короткого звука
    return False

SHORT_FINAL = {"да", "нет", "иә", "ия", "жоқ", "верно", "дұрыс", "хорошо", "жақсы", "спасибо", "рахмет", "рақмет", "ага", "угу", "конечно",
               "согласен", "согласна", "подтверждаю", "келісемін", "не надо", "не нужно", "керек емес", "да верно", "иә дұрыс", "да конечно", "до свидания", "сау болыңыз"}

def looks_final(text: str) -> bool:
    """Транскрипт похож на законченную реплику: можно закрывать её, не дожидаясь полной тишины VAD.
    Одно-два слова ("Подождите.", "Здравствуйте.") часто продолжаются после паузы, поэтому коротко закрываем только ответы из SHORT_FINAL."""
    t = (text or "").strip(); words = re.findall(r"\w+", t.lower())
    if not re.search(r"[^.][.!?]$", t) or t.endswith(("...", "…")): return False
    if re.search(r"\b(и|а|но|или|что|ну|мен|және|бірақ|немесе)[.!?]$", t.lower()): return False
    return len(words) >= 3 or " ".join(words) in SHORT_FINAL

def _key(text, lang):
    return hashlib.sha1(f"{config.TTS_MODEL}|{VOICE[lang]}|{lang}|{text}".encode()).hexdigest()

def _path(text, lang): return CACHE / f"{_key(text, lang if lang in VOICE else 'ru')}.pcm"

def is_cached(text, lang) -> bool: return _path(text, lang).exists()

def cached(text, lang) -> bytes | None:
    p = _path(text, lang)
    return p.read_bytes() if p.exists() else None

async def synth_stream(text: str, lang: str, store: bool = False):
    """Асинхронный генератор PCM16 24 kHz. Из кэша отдаёт мгновенно."""
    lang = lang if lang in VOICE else "ru"
    c = cached(text, lang)
    if c is not None:
        for i in range(0, len(c), 9600): yield c[i:i + 9600]
        return
    buf = bytearray()
    async with TTS.audio.speech.with_streaming_response.create(model=config.TTS_MODEL, voice=VOICE[lang], input=text,
                                                                 response_format="pcm", instructions=INSTR[lang]) as resp:
        async for chunk in resp.iter_bytes(4800):
            buf += chunk; yield chunk
    if store and buf:
        tmp = _path(text, lang).with_suffix(f".{os.getpid()}.tmp"); tmp.write_bytes(bytes(buf)); tmp.replace(_path(text, lang))  # атомарно: кэш общий

async def prewarm(phrases: list[tuple[str, str]], concurrency=6):
    sem = asyncio.Semaphore(concurrency); todo = [(t, l) for t, l in dict.fromkeys(phrases) if t and not is_cached(t, l)]
    async def one(t, l):
        async with sem:
            try:
                async for _ in synth_stream(t, l, store=True): pass
            except Exception as e:
                print("prewarm fail", t[:40], e)
    await asyncio.gather(*[one(t, l) for t, l in todo])
    return len(todo)

def template_phrases():
    """Все детерминированные фразы для предсинтеза."""
    from . import kit
    from .catalog import CATALOG
    from .responder import ack_for, ALT_PROMPTS, PHRASES
    out = []
    for lang in ("ru", "kk"):
        for sid in CATALOG.scenarios():
            a = ack_for(sid, lang)
            if a: out.append((a, lang))
        out.append((CATALOG.scenarios()["SC37"]["responses"][lang]["closing"], lang))
        for s in kit.slots().values(): out.append((s["prompt"][lang], lang))
        for x in CATALOG.get()["system_intents"]:
            if "{" not in x["response"][lang]: out.append((x["response"][lang], lang))
        for v in ALT_PROMPTS.values(): out.append((v[lang], lang))
        out += [({"ru": "Хорошо. Чем ещё могу помочь?", "kk": "Жақсы. Тағы немен көмектесе аламын?"}[lang], lang),
                ({"ru": "Хорошо, отложим. Чем ещё могу помочь?", "kk": "Жақсы, кейінге қалдырайық. Тағы немен көмектесе аламын?"}[lang], lang)]
    from .responder import GREET_REPLY, HEARD_REPLY, CLARIFY_OPEN, FILLER as R_FILLER
    out += [(d[l], l) for d in (GREET_REPLY, HEARD_REPLY, CLARIFY_OPEN) for l in ("ru", "kk")]
    out += [(v[l], l) for v in R_FILLER.values() for l in ("ru", "kk")]
    out += [(GREETING["unknown"], "ru"), (FILLER["ru"], "ru"), (FILLER["kk"], "kk")]
    for c in kit.mock_backend()["clients"]:   # персональные приветствия для caller ID: первый звук звонка из кэша
        lang = c.get("preferred_language", "ru"); out.append((greeting(c, lang), lang))
    return out

_TR2 = [("iya", "ия"), ("zh", "ж"), ("kh", "х"), ("sh", "ш"), ("ch", "ч"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"), ("ey", "ей"), ("ay", "ай"),
        ("iy", "ий"), ("oy", "ой"), ("ai", "ай"), ("ia", "ия")]
_TR1 = dict(zip("abvgdezijklmnoprstufhcyw", "абвгдезийклмнопрстуфхцыв"))
_NAME_FIX = {"Natalia": {"ru": "Наталья", "kk": "Наталья"}, "Aigerim": {"kk": "Айгерім"}, "Nurlan": {"kk": "Нұрлан"}, "Kamila": {"kk": "Камила"}}

def first_name(full_name: str, lang: str) -> str:
    """Имя клиента кириллицей для TTS (в моках имена латиницей: 'Sergey' -> 'Сергей')."""
    n = (full_name or "").split()[0] if full_name else ""
    if not re.fullmatch(r"[A-Za-z'-]+", n): return n
    fix = _NAME_FIX.get(n, {}); fix = fix.get(lang) or fix.get("ru")
    if fix: return fix
    w = n.lower(); out = "е" if w.startswith("ye") else ""; i = 2 if out else 0
    while i < len(w):
        d = next(((x, c) for x, c in _TR2 if w.startswith(x, i)), None)
        if d: out += d[1]; i += len(d[0])
        else: out += _TR1.get(w[i], w[i]); i += 1
    return out[:1].upper() + out[1:]

def greeting(client: dict | None, lang: str) -> str:
    if not client: return GREETING["unknown"]
    return GREETING[lang if lang in ("ru", "kk") else "ru"].format(name=first_name(client["full_name"], lang))

FILLER = {"ru": "Секунду.", "kk": "Бір сәт."}   # call.FILLER_MS

GREETING = {"unknown": "Сәлеметсіз бе! Здравствуйте! Это Saqta Insurance, чем могу помочь?",
            "ru": "Здравствуйте, {name}! Это Saqta Insurance, чем могу помочь?",
            "kk": "Сәлеметсіз бе, {name}! Saqta Insurance, қалай көмектесе аламын?"}
