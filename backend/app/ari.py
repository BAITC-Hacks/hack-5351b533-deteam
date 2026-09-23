"""ARI-контроллер звонков: бэкенд = Stasis-приложение voice-ai (docs/telephony/README.md, основная схема).

StasisStart(канал звонящего C) -> регистрируем UUID с caller.number в telephony.CALLS -> answer C -> mixing-бридж B
-> C в B -> POST /channels/externalMedia (encapsulation=audiosocket, transport=tcp, format=slin, data=UUID) = канал E
-> E в B. Asterisk сам подключается к нашему AudioSocket-серверу (telephony.py) и шлёт UUID; дальше обычный PhoneLeg.
Конец: telephony.PhoneLeg.finish зовёт ARI.hangup (DELETE C) или ARI.transfer (SAQTA_SUMMARY + continue в
saqta-transfer,<queue>,1). StasisEnd/ChannelDestroyed C -> закрываем сессию, удаляем E и B.

Env: ARI_URL (http://127.0.0.1:8088), ARI_USER, ARI_PASSWORD, ARI_APP (voice-ai),
     AUDIOSOCKET_HOST (адрес бэкенда, видимый из Asterisk), AUDIOSOCKET_PORT (9092).
"""
import asyncio, json, os, time, uuid as uuidlib
from urllib.parse import quote, urlsplit, urlunsplit

import websockets
try:
    import httpx2 as httpx          # ставится вместе с openai
except ImportError:                  # pragma: no cover
    import httpx

from . import telephony
from .normalize import phone as norm_phone

ARI_URL = os.getenv("ARI_URL", "").rstrip("/")
ARI_USER = os.getenv("ARI_USER", "voice-ai")
ARI_PASSWORD = os.getenv("ARI_PASSWORD", "")
ARI_APP = os.getenv("ARI_APP", "voice-ai")
TRANSFER_CONTEXT = os.getenv("ARI_TRANSFER_CONTEXT", telephony.TRANSFER_CONTEXT)
FALLBACK_QUEUE = os.getenv("ARI_FALLBACK_QUEUE", "operator_general")   # куда вести звонок, если External Media не поднялся
MEDIA_TIMEOUT = float(os.getenv("ARI_MEDIA_TIMEOUT", "6"))             # сколько ждать подключения AudioSocket с UUID
EM_PREFIX, BR_PREFIX = "vr-em-", "vr-br-"


def log(msg): print(f"[ari] {msg}", flush=True)


class AriCall:
    def __init__(self, uid, chan):
        self.uid, self.chan_id, self.chan_name = uid, chan["id"], chan.get("name")
        self.bridge_id = BR_PREFIX + uid
        self.em_id = EM_PREFIX + uid
        self.gone = False          # канал звонящего вышел из Stasis (положил трубку или мы его увели)
        self.acted = False         # мы уже сделали hangup/continue
        self.cleaned = False
        self.t0 = time.perf_counter()


class AriController:
    def __init__(self):
        self.base = ARI_URL + "/ari"
        self.http = httpx.AsyncClient(base_url=self.base, auth=(ARI_USER, ARI_PASSWORD), timeout=8)
        self.calls: dict[str, AriCall] = {}          # channel id звонящего -> AriCall
        self.by_uid: dict[str, AriCall] = {}
        self.em_ids: set[str] = set()                # наши External Media каналы (их StasisStart игнорируем)
        self.connected = False
        self.task = None
        host = os.getenv("AUDIOSOCKET_HOST", "").strip()
        if not host or host in ("0.0.0.0", "::"):
            log("WARNING: AUDIOSOCKET_HOST не задан (или 0.0.0.0) — Asterisk не узнает, куда подключаться. "
                "Задайте LAN-IP ноутбука или host.docker.internal. Пока пробую 127.0.0.1")
            host = "127.0.0.1"
        self.external_host = f"{host}:{telephony.AUDIOSOCKET_PORT}"

    # ------------------------------------------------------------ REST
    async def req(self, method, path, ok_missing=False, **params):
        params = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self.http.request(method, path, params=params)
        except Exception as e:
            log(f"{method} {path} failed: {type(e).__name__}: {e}"); return None, None
        if r.status_code >= 400:
            if not (ok_missing and r.status_code == 404):
                try: detail = r.json().get("message") or r.text
                except Exception: detail = r.text
                log(f"{method} {path} -> HTTP {r.status_code}: {' '.join(str(detail).split())[:300]}")
            return r.status_code, None
        try: body = r.json() if r.content else {}
        except Exception: body = {}
        return r.status_code, body

    # ------------------------------------------------------------ события
    def ws_url(self):
        u = urlsplit(self.base)
        scheme = "wss" if u.scheme == "https" else "ws"
        q = f"app={quote(ARI_APP)}&api_key={quote(ARI_USER, safe='')}:{quote(ARI_PASSWORD, safe='')}"
        return urlunsplit((scheme, u.netloc, u.path + "/events", q, ""))

    async def run(self):
        delay = 1.0
        while True:
            try:
                async with websockets.connect(self.ws_url(), open_timeout=5, ping_interval=20, ping_timeout=20,
                                              max_size=2**22) as ws:
                    self.connected = True; delay = 1.0
                    log(f"connected {ARI_URL} app={ARI_APP}; External Media -> {self.external_host} (audiosocket/tcp/slin)")
                    async for msg in ws:
                        try: ev = json.loads(msg)
                        except Exception: continue
                        try: await self.on_event(ev)
                        except Exception as e:
                            import traceback; traceback.print_exc()
                            log(f"event {ev.get('type')} handler error: {e}")
                    log("websocket closed by Asterisk")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = str(e)
                if "401" in msg: msg += " (проверьте ARI_USER/ARI_PASSWORD)"
                log(f"connect/read failed: {type(e).__name__}: {msg[:200]}; retry in {delay:.0f}s")
            self.connected = False
            await asyncio.sleep(delay); delay = min(delay * 2, 15)

    async def on_event(self, ev):
        tp = ev.get("type"); ch = ev.get("channel") or {}; cid = ch.get("id")
        if tp == "StasisStart":
            if cid in self.em_ids or cid.startswith(EM_PREFIX) or (ch.get("name") or "").startswith(("AudioSocket/", "UnicastRTP/")):
                return                                           # наш же External Media канал
            if cid in self.calls: return
            asyncio.create_task(self.setup(ch, ev.get("args") or []))
        elif tp in ("StasisEnd", "ChannelDestroyed"):
            if cid in self.calls:
                c = self.calls[cid]
                if tp == "ChannelDestroyed": telephony.CALLS.get(c.uid, {}).update(hangup_cause=ev.get("cause"))
                if not c.gone:
                    c.gone = True
                    rec = telephony.CALLS.get(c.uid) or {}
                    who = "after our " + rec.get("ari_action", "?") if c.acted else f"caller hung up (cause={ev.get('cause')})"
                    log(f"{tp} {c.chan_name} uuid={c.uid}: {who}")
                    asyncio.create_task(self.caller_gone(c))
            elif cid and (cid in self.em_ids or cid.startswith(EM_PREFIX)):
                if tp == "ChannelDestroyed": self.em_ids.discard(cid)
                c = self.by_uid.get(cid[len(EM_PREFIX):])
                if c and not c.gone and tp == "StasisEnd":
                    log(f"external media {cid} ended while caller {c.chan_name} still in Stasis")
                    asyncio.create_task(self.media_lost(c))
        elif tp == "ChannelHangupRequest":
            if cid in self.calls: log(f"hangup request {ch.get('name')} cause={ev.get('cause')}")
        # DTMF отдельно не обрабатываем: chan_audiosocket сам пересылает цифры в AudioSocket (0x03), «0» ловит telephony.py

    # ------------------------------------------------------------ звонок
    async def setup(self, ch, args):
        uid = str(uuidlib.uuid4()); c = AriCall(uid, ch)
        self.calls[c.chan_id] = c; self.by_uid[uid] = c; self.em_ids.add(c.em_id)
        caller = (ch.get("caller") or {}).get("number") or ""
        rec = telephony._rec(uid)
        rec.update(caller_raw=caller, caller=norm_phone(caller) if caller else None, dnid=(ch.get("dialplan") or {}).get("exten"),
                   channel=ch.get("name"), channel_id=c.chan_id, uniqueid=c.chan_id, registered=True, ari=True)
        log(f"StasisStart {ch.get('name')} caller={caller!r}->{rec['caller']} exten={rec['dnid']} args={args} -> uuid={uid}")
        await self.req("POST", f"/channels/{c.chan_id}/answer")          # диалплан обычно уже сделал Answer()
        if c.gone: return await self.cleanup(c)
        st, br = await self.req("POST", "/bridges", type="mixing", bridgeId=c.bridge_id, name=f"saqta-{uid[:8]}")
        if br is None: return await self.fail(c, "bridge create failed")
        st, _ = await self.req("POST", f"/bridges/{c.bridge_id}/addChannel", channel=c.chan_id)
        if st is None or st >= 400: return await self.fail(c, "add caller to bridge failed")
        st, em = await self.req("POST", "/channels/externalMedia", app=ARI_APP, channelId=c.em_id, external_host=self.external_host,
                                format="slin", encapsulation="audiosocket", transport="tcp", connection_type="client",
                                direction="both", data=uid)
        if em is None:
            if st in (400, 422, 501):
                log(f"ERROR: Asterisk отверг externalMedia encapsulation=audiosocket (HTTP {st}). Нужен Asterisk с "
                    f"chan_audiosocket/res_audiosocket (18.x+; проверено на 22.10). Запасной путь без ARI: контекст "
                    f"saqta-audiosocket в contracts/asterisk/extensions.conf. RTP-режим (EXTERNAL_MEDIA=rtp) не реализован.")
            else:
                log(f"ERROR: externalMedia не создан (HTTP {st}): Asterisk не смог подключиться к AudioSocket "
                    f"{self.external_host} (проверьте AUDIOSOCKET_HOST/AUDIOSOCKET_PORT/фаервол: адрес должен быть виден "
                    f"изнутри Asterisk) или не загружен chan_audiosocket (module show like audiosocket).")
            return await self.fail(c, "externalMedia failed")
        if c.gone: return await self.cleanup(c)
        st, _ = await self.req("POST", f"/bridges/{c.bridge_id}/addChannel", channel=c.em_id)
        if st is None or st >= 400: return await self.fail(c, "add external media to bridge failed")
        log(f"bridged {c.chan_name} <-> {em.get('name')} in {c.bridge_id} ({(time.perf_counter() - c.t0) * 1000:.0f} ms)")
        asyncio.create_task(self.media_watchdog(c))

    async def media_watchdog(self, c):
        """Asterisk создал E, но AudioSocket с нашим UUID так и не пришёл — не держим клиента в тишине."""
        await asyncio.sleep(MEDIA_TIMEOUT)
        rec = telephony.CALLS.get(c.uid) or {}
        if not c.gone and not c.acted and rec.get("call") is None:
            await self.fail(c, f"AudioSocket с uuid={c.uid} не подключился за {MEDIA_TIMEOUT:.0f} с")

    async def fail(self, c, why):
        """Не бросаем клиента: ведём на оператора (как запасной путь в диалплане)."""
        log(f"call {c.chan_name} uuid={c.uid}: {why} -> {TRANSFER_CONTEXT},{FALLBACK_QUEUE}")
        rec = telephony.CALLS.get(c.uid) or {}
        rec.update(action="transfer", queue=FALLBACK_QUEUE, transferred_by="ari-fallback")
        if not c.gone and not c.acted:
            c.acted = True; rec["ari_action"] = "fallback-continue"
            await self.req("POST", f"/channels/{c.chan_id}/variable", variable="SAQTA_SUMMARY", value=f"Сбой голосового агента: {why}"[:300])
            await self.req("POST", f"/channels/{c.chan_id}/continue", context=TRANSFER_CONTEXT, extension=FALLBACK_QUEUE, priority=1)
        await self.cleanup(c)

    async def caller_gone(self, c):
        rec = telephony.CALLS.get(c.uid) or {}
        if rec.get("action") == "pending": rec["action"] = "hangup"
        call = rec.get("call")
        await self.cleanup(c)                         # DELETE E -> Asterisk закрывает AudioSocket -> PhoneLeg завершится
        if call and not call.closed and not c.acted:
            await call.close("client")

    async def media_lost(self, c):
        """E завершился раньше звонящего (AudioSocket оборвался). Даём PhoneLeg.finish 1.5 с на свой hangup/continue."""
        await asyncio.sleep(1.5)
        if c.gone or c.acted: return
        rec = telephony.CALLS.get(c.uid) or {}
        if rec.get("action") == "transfer": await self.transfer(rec, rec.get("queue") or FALLBACK_QUEUE, rec.get("summary") or "")
        else: await self.fail(c, "AudioSocket оборвался")

    async def cleanup(self, c):
        if c.cleaned: return
        c.cleaned = True
        await self.req("DELETE", f"/channels/{c.em_id}", ok_missing=True)
        await self.req("DELETE", f"/bridges/{c.bridge_id}", ok_missing=True)
        self.calls.pop(c.chan_id, None)
        log(f"cleanup uuid={c.uid}: external media + bridge deleted ({time.perf_counter() - c.t0:.1f} s call)")
        asyncio.get_running_loop().call_later(60, self.forget, c)

    def forget(self, c):
        self.by_uid.pop(c.uid, None); self.em_ids.discard(c.em_id)

    # ------------------------------------------------------------ хуки из telephony.PhoneLeg
    def owns(self, rec) -> bool:
        return bool(rec and rec.get("ari") and rec.get("uuid") in self.by_uid)

    async def hangup(self, rec) -> bool:
        c = self.by_uid.get(rec["uuid"])
        if not c or c.gone or c.acted: return False
        c.acted = True; rec["ari_action"] = "hangup"
        st, _ = await self.req("DELETE", f"/channels/{c.chan_id}", ok_missing=True)
        log(f"hangup {c.chan_name} uuid={rec['uuid']}: HTTP {st}")
        return st is not None and st < 400

    async def transfer(self, rec, queue, summary) -> bool:
        c = self.by_uid.get(rec["uuid"])
        if not c or c.gone or c.acted: return False
        c.acted = True; rec["ari_action"] = f"continue {TRANSFER_CONTEXT},{queue},1"
        val = " ".join((summary or "").split())[:300]
        await self.req("POST", f"/channels/{c.chan_id}/variable", variable="SAQTA_SUMMARY", value=val)
        st, _ = await self.req("POST", f"/channels/{c.chan_id}/continue", context=TRANSFER_CONTEXT, extension=queue, priority=1)
        log(f"transfer {c.chan_name} uuid={rec['uuid']} -> {TRANSFER_CONTEXT},{queue},1: HTTP {st}")
        return st is not None and st < 400

    async def leg_closed(self, rec, reason):
        """AudioSocket-сессия закончилась. Если исход ещё не применён (сбой, Asterisk закрыл сокет) — применяем сами."""
        c = self.by_uid.get(rec.get("uuid"))
        if not c or c.gone or c.acted: return
        if rec.get("action") == "transfer": await self.transfer(rec, rec.get("queue") or FALLBACK_QUEUE, rec.get("summary") or "")
        elif reason == "error": await self.fail(c, "ошибка в сессии агента")
        else: await self.hangup(rec)


def start_ari():
    if not ARI_URL: return None
    ctl = AriController()
    telephony.ARI = ctl
    ctl.task = asyncio.create_task(ctl.run())
    return ctl
