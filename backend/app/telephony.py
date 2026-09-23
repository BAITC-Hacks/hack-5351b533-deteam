"""Телефония: AudioSocket-сервер для Asterisk + REST регистрации звонков /api/telephony/*.

Основная схема (ARI, app/ari.py): Stasis(voice-ai) -> ARI регистрирует UUID с caller.number, создаёт External Media
(audiosocket/tcp, data=UUID) -> Asterisk подключается сюда. Исход: ARI DELETE канала (прощание) или ARI continue в
saqta-transfer,<queue>,1 (перевод). Запасная схема без ARI: Asterisk CURL POST /api/telephony/calls -> UUID;
AudioSocket(UUID, host:9092) -> тот же Call, что и веб (VAD -> STT -> роутер -> TTS). Исход звонка: 0x00 (hangup)
либо перевод (AMI Setvar+Redirect, запасной путь /outcome).
Протокол AudioSocket: 1 байт тип, 2 байта длина big-endian, данные.
"""
import asyncio, os, struct, time, uuid as uuidlib
from urllib.parse import parse_qsl
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from .call import Call
from .normalize import phone as norm_phone

router = APIRouter()
# AUDIOSOCKET_HOST — адрес бэкенда, видимый из Asterisk (для ARI externalMedia); слушаем на AUDIOSOCKET_BIND
AUDIOSOCKET_BIND = os.getenv("AUDIOSOCKET_BIND", "0.0.0.0")
AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9092"))
ARI = None   # app.ari.AriController, если задан ARI_URL: тогда исход звонка применяет ARI, а не AMI/диалплан
AMI = {k: os.getenv(f"AMI_{k}") for k in ("HOST", "PORT", "USER", "SECRET")}
TRANSFER_CONTEXT = os.getenv("AMI_TRANSFER_CONTEXT", "saqta-transfer")

K_HANGUP, K_UUID, K_DTMF, K_AUDIO, K_ERROR = 0x00, 0x01, 0x03, 0x10, 0xFF
FRAME = 320            # 20 мс slin 8 kHz 16-bit
TICK = 0.02

CALLS: dict[str, dict] = {}   # uuid -> {caller, dnid, channel, uniqueid, action, queue, summary, session_id, ...}


def _rec(uid: str) -> dict:
    if uid not in CALLS:
        if len(CALLS) > 500:
            for k in list(CALLS)[:100]: CALLS.pop(k, None)
        CALLS[uid] = {"uuid": uid, "caller": None, "caller_raw": None, "dnid": None, "channel": None, "uniqueid": None,
                      "action": "pending", "queue": None, "summary": None, "session_id": None, "registered": False,
                      "created": time.time(), "hangup_cause": None, "call": None}
    return CALLS[uid]


async def _form(r: Request) -> dict:
    body = (await r.body()).decode("utf-8", "replace")
    # CURL() шлёт x-www-form-urlencoded; «+» в caller без URIENCODE превратится в пробел — normalize.phone это переживёт
    return dict(parse_qsl(body, keep_blank_values=True)) | dict(r.query_params)


# ---------------------------------------------------------------- REST
@router.post("/api/telephony/calls")
async def register_call(r: Request):
    f = await _form(r)
    uid = str(uuidlib.uuid4()); rec = _rec(uid)
    raw = (f.get("caller") or "").strip()
    rec.update(caller_raw=raw, caller=norm_phone(raw) if raw else None, dnid=f.get("dnid"), channel=f.get("channel") or None,
               uniqueid=f.get("uniqueid"), registered=True)
    print(f"[tel] register {uid} caller={raw!r}->{rec['caller']} channel={rec['channel']} dnid={rec['dnid']}", flush=True)
    return PlainTextResponse(uid)


@router.get("/api/telephony/calls/{call_uuid}/outcome")
def call_outcome(call_uuid: str, r: Request):
    rec = CALLS.get(call_uuid) or {"action": "hangup", "queue": None, "summary": None, "session_id": None}
    if "application/json" in r.headers.get("accept", ""):
        return JSONResponse({"action": rec["action"], "queue": rec["queue"], "summary": rec["summary"], "session_id": rec["session_id"] or ""})
    return PlainTextResponse(f"transfer:{rec['queue']}" if rec["action"] == "transfer" and rec["queue"] else "hangup")


@router.post("/api/telephony/calls/{call_uuid}/hangup")
async def call_hangup(call_uuid: str, r: Request):
    f = await _form(r); rec = CALLS.get(call_uuid)
    print(f"[tel] hangup {call_uuid} cause={f.get('cause')}", flush=True)
    if rec:
        rec["hangup_cause"] = f.get("cause")
        if rec["action"] == "pending": rec["action"] = "hangup"
        call: Call | None = rec.get("call")
        if call and not call.closed: await call.close("client")
    return PlainTextResponse("ok")


@router.get("/api/telephony/calls")
def list_calls():
    """Отладка: последние звонки (без объекта Call)."""
    return [{k: v for k, v in rec.items() if k != "call"} | {"active": bool(rec.get("call") and not rec["call"].closed)}
            for rec in list(CALLS.values())[-50:]]


# ---------------------------------------------------------------- AMI (опционально)
async def ami_transfer(channel: str, queue: str, summary: str) -> bool:
    """Setvar SAQTA_SUMMARY + Redirect в saqta-transfer,<queue>,1. Без AMI_HOST — тихо пропускаем."""
    if not (AMI["HOST"] and AMI["USER"] and AMI["SECRET"] and channel): return False
    try:
        rd, wr = await asyncio.wait_for(asyncio.open_connection(AMI["HOST"], int(AMI["PORT"] or 5038)), 3)
    except Exception as e:
        print(f"[tel] AMI connect failed: {e}", flush=True); return False
    async def act(**kv):
        wr.write(("".join(f"{k}: {v}\r\n" for k, v in kv.items()) + "\r\n").encode("utf-8")); await wr.drain()
        while True:   # читаем блоки до ответа с нашим ActionID (события пропускаем)
            block = {}
            while (line := (await asyncio.wait_for(rd.readline(), 3)).decode("utf-8", "replace").strip()):
                if ":" in line: k, v = line.split(":", 1); block[k.strip()] = v.strip()
            if block.get("ActionID") == kv["ActionID"]: return block
            if not block and rd.at_eof(): return {}
    ok = False
    try:
        await asyncio.wait_for(rd.readline(), 3)   # баннер "Asterisk Call Manager/x.y"
        r = await act(Action="Login", Username=AMI["USER"], Secret=AMI["SECRET"], Events="off", ActionID="vr1")
        if r.get("Response") != "Success": print(f"[tel] AMI login failed: {r}", flush=True); return False
        val = " ".join((summary or "").split()).replace('"', "'")[:300]
        await act(Action="Setvar", Channel=channel, Variable="SAQTA_SUMMARY", Value=val, ActionID="vr2")
        r = await act(Action="Redirect", Channel=channel, Context=TRANSFER_CONTEXT, Exten=queue, Priority="1", ActionID="vr3")
        ok = r.get("Response") == "Success"
        print(f"[tel] AMI redirect {channel} -> {TRANSFER_CONTEXT},{queue},1: {r.get('Response')} {r.get('Message', '')}", flush=True)
        await act(Action="Logoff", ActionID="vr4")
    except Exception as e:
        print(f"[tel] AMI error: {type(e).__name__}: {e}", flush=True)
    finally:
        try: wr.close()
        except Exception: pass
    return ok


# ---------------------------------------------------------------- AudioSocket
def pack(kind: int, payload: bytes = b"") -> bytes:
    return struct.pack(">BH", kind, len(payload)) + payload


async def read_msg(rd: asyncio.StreamReader):
    h = await rd.readexactly(3)
    kind, n = h[0], struct.unpack(">H", h[1:])[0]
    return kind, (await rd.readexactly(n) if n else b"")


class PhoneLeg:
    """Одно соединение AudioSocket <-> Call. Исходящий звук — буфер, отдаётся кадрами по 20 мс в реальном темпе."""

    def __init__(self, rd, wr):
        self.rd, self.wr = rd, wr
        self.out = bytearray()
        self.call: Call | None = None; self.rec: dict | None = None
        self.finishing = False; self.sent_hangup = False; self.done = asyncio.Event()
        self.frames_out = 0; self.t_first_out = None; self.last_in = None

    # --- колбэки Call
    async def send_event(self, e):
        tp = e.get("type")
        if tp == "handoff" and self.rec is not None:   # исход фиксируем сразу: сокет может закрыться раньше, чем бот договорит
            self.rec.update(action="transfer", queue=e.get("queue") or "operator_general", summary=e.get("summary"))
        elif tp == "session.end" and e.get("reason") in ("handoff", "goodbye") and not self.finishing:
            self.finishing = e["reason"]
            asyncio.create_task(self.finish(e["reason"]))

    async def send_audio(self, pcm: bytes):
        if self.sent_hangup: return
        self.out += pcm

    async def clear_audio(self):
        self.out.clear()   # перебивание: буфер пуст мгновенно, pacer перестаёт слать кадры

    # --- исходящий звук
    def _write(self, kind, payload=b""):
        if self.wr.is_closing() or (self.sent_hangup and kind != K_HANGUP): return False
        self.wr.write(pack(kind, payload)); return True

    async def pacer(self):
        """Непрерывный поток 20 мс кадров, как RTP: речь из буфера, иначе тишина.
        Тишина нужна как keepalive: AudioSocket() в Asterisk 22 рвёт сокет после 2 с без активности в обе стороны."""
        loop = asyncio.get_running_loop(); nxt = loop.time(); stall = 0; sil = b"\x00" * FRAME
        while True:
            if len(self.out) >= FRAME or (self.out and stall >= 3):   # неполный кадр ждём до 60 мс, потом дополняем тишиной
                frame = bytes(self.out[:FRAME]); del self.out[:FRAME]; stall = 0
                if len(frame) & 1: frame = frame[:-1]
                if len(frame) < FRAME: frame += b"\x00" * (FRAME - len(frame))
                self.frames_out += 1
                if self.t_first_out is None: self.t_first_out = time.perf_counter()
            else:
                if self.out: stall += 1
                frame = sil
            if not self._write(K_AUDIO, frame): return
            nxt += TICK; now = loop.time()
            if nxt < now - 0.2: nxt = now          # отстали (GC/нагрузка) — не нагоняем пачкой
            await asyncio.sleep(max(0, nxt - now))

    async def filler(self):
        """Asterisk не шлёт кадры, пока от абонента нет RTP (DTX/тишина в софтфоне, Local-канал в Wait()).
        Без кадров VAD не увидит конец фразы, поэтому досыпаем тишину в реальном темпе."""
        while True:
            await asyncio.sleep(0.1)
            if self.finishing or self.last_in is None: continue
            gap = time.perf_counter() - self.last_in
            if gap >= 0.12:
                n = int(gap / TICK); self.last_in += n * TICK
                await self.call.on_audio(b"\x00" * (FRAME * n))

    async def drained(self, timeout=25):
        """Бот договорил: TTS синтезирован, очередь плеера пуста, буфер выдан. Держится >= 400 мс подряд."""
        t0 = time.perf_counter(); stable = 0.0
        while time.perf_counter() - t0 < timeout:
            p = self.call.player
            talking = getattr(p, "busy", None)
            if talking is None: talking = getattr(p, "speaking", False)
            busy = bool(self.out) or talking or not p.q.empty() or any(not x.done() for x in p.producers)
            stable = 0.0 if busy else stable + 0.05
            if stable >= 0.4: return True
            await asyncio.sleep(0.05)
        return False

    async def finish(self, reason):
        await asyncio.sleep(0.2)
        await self.drained()
        await asyncio.sleep(0.3)   # последний кадр доигрывает в джиттер-буфере телефона
        rec = self.rec
        if reason == "handoff":
            queue = rec.get("queue") or (self.call.sess.handoff or {}).get("queue") or "operator_general"
            rec.update(action="transfer", queue=queue)   # уже выставлено по событию handoff; дублируем на всякий случай
            if ARI and ARI.owns(rec):
                rec["transferred_by"] = "ari" if await ARI.transfer(rec, queue, rec.get("summary") or "") else "ari-failed"
            elif await ami_transfer(rec.get("channel"), queue, rec.get("summary") or ""):
                rec["transferred_by"] = "ami"; await asyncio.sleep(0.5)   # Redirect уже увёл канал из AudioSocket()
            else:
                rec["transferred_by"] = "dialplan"   # диалплан после AudioSocket() спросит /outcome
        else:
            rec["action"] = "hangup"
            if ARI and ARI.owns(rec): await ARI.hangup(rec)
        print(f"[tel] finish {rec['uuid']} reason={reason} outcome={rec['action']}:{rec.get('queue')} via={rec.get('transferred_by')}", flush=True)
        self.sent_hangup = True; self.out.clear()
        try:
            self._write(K_HANGUP); await self.wr.drain()
        except Exception: pass
        await self.call.close(reason)
        self.done.set()

    # --- главный цикл соединения
    async def run(self):
        peer = self.wr.get_extra_info("peername")
        try:
            kind, payload = await asyncio.wait_for(read_msg(self.rd), 10)
        except Exception:
            self.wr.close(); return
        if kind != K_UUID or len(payload) != 16:
            print(f"[tel] {peer}: first message kind={kind:#x} len={len(payload)}, ожидали UUID", flush=True)
            self.wr.close(); return
        uid = str(uuidlib.UUID(bytes=payload))
        known = uid in CALLS
        self.rec = rec = _rec(uid)
        print(f"[tel] audiosocket {peer} uuid={uid} {'registered caller=' + str(rec['caller']) if known else 'UNKNOWN uuid (no caller id)'}", flush=True)
        self.call = call = Call("phone", self.send_event, self.send_audio, self.clear_audio, out_rate=8000, in_rate=8000)
        rec["call"] = call
        pacer = asyncio.create_task(self.pacer()); filler = asyncio.create_task(self.filler())
        reason = "client"
        try:
            await call.start(caller_phone=rec.get("caller"), session_id=f"phone-{uid[:8]}")
            rec["session_id"] = call.sess.id
            done_wait = asyncio.create_task(self.done.wait())
            while True:
                rd_task = asyncio.create_task(read_msg(self.rd))
                await asyncio.wait({rd_task, done_wait}, return_when=asyncio.FIRST_COMPLETED)
                if not rd_task.done():
                    rd_task.cancel(); break
                kind, payload = rd_task.result()
                if kind == K_AUDIO:
                    # после прощания/перевода звук клиента не слушаем, чтобы не перебить финальную фразу
                    if payload and not self.finishing:
                        self.last_in = time.perf_counter(); await call.on_audio(payload[: len(payload) & ~1])
                elif kind == K_DTMF:
                    d = payload.decode("ascii", "replace")
                    print(f"[tel] dtmf {uid} {d!r}", flush=True)
                    if d == "0" and not self.finishing:   # запасной выход на оператора с клавиатуры
                        asyncio.create_task(call.on_text("Соедините меня с оператором"))
                elif kind == K_HANGUP:
                    print(f"[tel] remote hangup {uid}", flush=True); break
                elif kind == K_ERROR:
                    print(f"[tel] asterisk error {uid} {payload.hex()}", flush=True); reason = "error"; break
            done_wait.cancel()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            import traceback; traceback.print_exc(); reason = "error"
        finally:
            pacer.cancel(); filler.cancel()
            if not self.finishing and rec["action"] == "pending": rec["action"] = "hangup"
            if not call.closed: await call.close(self.finishing or reason)   # после AMI Redirect сокет закрывает Asterisk
            try: self.wr.close()
            except Exception: pass
            if ARI and ARI.owns(rec):   # сокет закрылся без hangup/continue (сбой, Asterisk закрыл E) — ARI доведёт звонок
                try: await ARI.leg_closed(rec, self.finishing or reason)
                except Exception as e: print(f"[tel] ARI leg_closed error: {e}", flush=True)
            print(f"[tel] closed {uid} frames_out={self.frames_out} outcome={rec['action']}:{rec.get('queue')}", flush=True)


async def _handle(rd, wr):
    await PhoneLeg(rd, wr).run()


async def start_audiosocket():
    srv = await asyncio.start_server(_handle, AUDIOSOCKET_BIND, AUDIOSOCKET_PORT)
    print(f"[tel] AudioSocket listening on {AUDIOSOCKET_BIND}:{AUDIOSOCKET_PORT}"
          f" (AMI {'on ' + AMI['HOST'] if AMI['HOST'] else 'off'})", flush=True)
    return srv
