"""STT (gpt-transcribe) и TTS (gpt-4o-mini-tts) с дисковым кэшем заготовленных фраз."""
import asyncio, hashlib, time
from pathlib import Path
from openai import AsyncOpenAI
from . import config
from .audio import wav_bytes

client = AsyncOpenAI()
STT_PROMPT = "Saqta Insurance. ОГПО, КАСКО, ДМС, полис, заявление, выплата, госномер, ИИН, франшиза. Сақтандыру, полис, өтініш, ЖСН, көлік, емхана."
CACHE = config.BACKEND / "tts_cache"; CACHE.mkdir(exist_ok=True)
INSTR = {"ru": "Native Russian speaker from Almaty, no foreign accent. Calm, warm contact-center operator. Natural, slightly fast pace.",
         "kk": "Native Kazakh speaker. Calm, warm contact-center operator. Natural, slightly fast pace."}
import os
VOICE = {"ru": os.getenv("TTS_VOICE_RU", config.TTS_VOICE), "kk": os.getenv("TTS_VOICE_KK", config.TTS_VOICE)}

async def transcribe(pcm16k: bytes) -> tuple[str, int]:
    t = time.perf_counter()
    r = await client.audio.transcriptions.create(model=config.STT_MODEL, file=("turn.wav", wav_bytes(pcm16k), "audio/wav"), prompt=STT_PROMPT)
    return r.text.strip(), int((time.perf_counter() - t) * 1000)

def _key(text, lang):
    return hashlib.sha1(f"{config.TTS_MODEL}|{VOICE[lang]}|{lang}|{text}".encode()).hexdigest()

def cached(text, lang) -> bytes | None:
    p = CACHE / f"{_key(text, lang)}.pcm"
    return p.read_bytes() if p.exists() else None

async def synth_stream(text: str, lang: str, store: bool = False):
    """Асинхронный генератор PCM16 24 kHz. Из кэша отдаёт мгновенно."""
    lang = lang if lang in VOICE else "ru"
    c = cached(text, lang)
    if c is not None:
        for i in range(0, len(c), 9600): yield c[i:i + 9600]
        return
    buf = bytearray()
    async with client.audio.speech.with_streaming_response.create(model=config.TTS_MODEL, voice=VOICE[lang], input=text,
                                                                 response_format="pcm", instructions=INSTR[lang]) as resp:
        async for chunk in resp.iter_bytes(4800):
            buf += chunk; yield chunk
    if store and buf: (CACHE / f"{_key(text, lang)}.pcm").write_bytes(bytes(buf))

async def prewarm(phrases: list[tuple[str, str]], concurrency=6):
    sem = asyncio.Semaphore(concurrency); todo = [(t, l) for t, l in dict.fromkeys(phrases) if t and cached(t, l) is None]
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
    out += [(GREETING["unknown"], "ru")]
    return out

GREETING = {"unknown": "Сәлеметсіз бе! Здравствуйте! Это Saqta Insurance, чем могу помочь?",
            "ru": "Здравствуйте, {name}! Это Saqta Insurance, чем могу помочь?",
            "kk": "Сәлеметсіз бе, {name}! Saqta Insurance, қалай көмектесе аламын?"}
