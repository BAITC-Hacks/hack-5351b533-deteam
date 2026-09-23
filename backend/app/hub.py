"""Реестр сессий, журнал событий, поток супервизора, статистика."""
import asyncio, json, time, datetime, statistics
from collections import Counter
from . import config

LOG_DIR = config.BACKEND / "logs" / "sessions"; LOG_DIR.mkdir(parents=True, exist_ok=True)

def now_iso(): return datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

class Hub:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.supervisors: set = set()

    def register(self, sess, caller_phone=None):
        self.sessions[sess.id] = {"session": sess, "events": [], "started_at": now_iso(), "ended_at": None, "end_reason": None, "caller_phone": caller_phone}

    async def publish(self, ev: dict):
        rec = self.sessions.get(ev.get("session_id"))
        if rec is not None:
            rec["events"].append(ev)
            if ev["type"] == "session.end" and not rec["ended_at"]:
                rec["ended_at"], rec["end_reason"] = now_iso(), ev.get("reason")
            try:
                with open(LOG_DIR / f"{ev['session_id']}.jsonl", "a", encoding="utf-8") as f: f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except Exception: pass
        await self.broadcast(ev)

    async def broadcast(self, ev):
        dead = []
        for ws in list(self.supervisors):
            try: await ws.send_text(json.dumps(ev, ensure_ascii=False))
            except Exception: dead.append(ws)
        for ws in dead: self.supervisors.discard(ws)

    def traces(self, sid=None):
        out = []
        for k, rec in self.sessions.items():
            if sid and k != sid: continue
            out += [e["trace"] | {"session_id": k} for e in rec["events"] if e["type"] == "turn.trace"]
        return out

    def summary(self, sid):
        rec = self.sessions[sid]; s = rec["session"]; tr = self.traces(sid)
        tot = [t["latency_ms"]["total"] for t in tr if t["latency_ms"]["total"]]
        return {"session_id": sid, "channel": s.channel, "caller_phone": rec["caller_phone"], "client_id": (s.client or {}).get("client_id"),
                "started_at": rec["started_at"], "ended_at": rec["ended_at"], "end_reason": rec["end_reason"], "turns": len(tr),
                "languages": sorted({t["language"] for t in tr}), "scenarios": list(dict.fromkeys(x["scenario_id"] for t in tr for x in t["scenarios"][:1])),
                "handoff_queue": (s.handoff or {}).get("queue"), "latency_total_p50": int(statistics.median(tot)) if tot else None,
                "catalog_version": s.catalog_version, "flagged": any(_flag(t) for t in tr)}

    def stats(self):
        tr = self.traces()
        pct = lambda xs, q: int(sorted(xs)[min(len(xs) - 1, int(len(xs) * q))]) if xs else None
        lat = {k: {"p50": pct([t["latency_ms"][k] for t in tr], 0.5), "p95": pct([t["latency_ms"][k] for t in tr], 0.95)}
               for k in ("stt", "triage", "router", "response", "tts_first_audio", "total")}
        conf = [t["scenarios"][0]["confidence"] for t in tr if t["scenarios"] and not t["fast_path"]]
        hist = Counter("0.9+" if c >= 0.9 else "0.75-0.9" if c >= 0.75 else "0.45-0.75" if c >= 0.45 else "<0.45" for c in conf)
        n = len(tr) or 1
        return {"sessions": len(self.sessions), "turns": len(tr),
                "by_scenario": [{"scenario_id": k, "count": v} for k, v in Counter(t["scenarios"][0]["scenario_id"] for t in tr if t["scenarios"]).most_common()],
                "by_language": dict(Counter(t["language"] for t in tr)), "by_decision": dict(Counter(t["decision"] for t in tr)),
                "confidence_histogram": [{"bucket": b, "count": hist.get(b, 0)} for b in ("0.9+", "0.75-0.9", "0.45-0.75", "<0.45")],
                "latency_ms": lat, "speculative_hit_rate": round(sum(t.get("speculative_hit", False) for t in tr) / n, 3),
                "template_rate": round(sum(t["response_source"] == "template" for t in tr) / n, 3),
                "handoff_rate": round(sum(t["decision"] == "handoff" for t in tr) / n, 3), "unclear_rate": round(sum(t["decision"] == "clarify" for t in tr) / n, 3),
                "flagged_turns": [{"session_id": t["session_id"], "turn": t["turn"], "transcript": t["transcript"], "scenario_id": t["scenarios"][0]["scenario_id"] if t["scenarios"] else None,
                                   "confidence": t["scenarios"][0]["confidence"] if t["scenarios"] else 0, "decision": t["decision"]} for t in tr if _flag(t)]}

def _flag(t):
    return t["decision"] in ("clarify", "handoff") or (not t["fast_path"] and t["scenarios"] and t["scenarios"][0]["confidence"] < config.CONF_RUN)

HUB = Hub()
