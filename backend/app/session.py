"""Сессия диалога: состояние, понимание реплики (fast path + роутер), исполнение сценариев, план ответа, трассировка."""
import json, time, uuid, re
from dataclasses import dataclass, field
from . import kit, config, normalize
from .catalog import CATALOG
from .mocks import Backend, QUEUES
from .plans import PLANS, Step, ask as ask_step, handoff as handoff_step
from .router import route_full
from .policy import decide

GLOBAL_SLOTS = ("phone", "iin")
CARRY_SLOTS = ("claim_number", "policy_number")
RU_SWITCH = re.compile(r"на русском|по-русски|по русски|орысша", re.I)
KK_SWITCH = re.compile(r"қазақша|на казахском|по-казахски", re.I)

@dataclass
class Frame:
    sid: str
    slots: dict = field(default_factory=dict)
    memo: dict = field(default_factory=dict)
    confirmed: set = field(default_factory=set)
    gates: dict = field(default_factory=dict)
    asked: dict = field(default_factory=dict)
    status: str = "active"          # active | done | handoff | deferred | cancelled
    awaiting: str | None = None     # slot | confirmation | offer
    expected_slot: str | None = None
    pending_action: str | None = None
    pending_inputs: dict | None = None
    pending_gate: str | None = None
    defer_reason: str | None = None
    new: bool = True
    errors: dict = field(default_factory=dict)
    options: list | None = None     # предложенные ближайшие слоты (no_availability): «да» = первый из них

class Ctx:
    """Интерфейс для планов сценариев."""
    def __init__(self, sess: "Session", f: Frame):
        self.sess, self.f, self.backend = sess, f, sess.backend

    @property
    def client(self): return self.sess.client

    def s(self, name):
        v = self.f.slots.get(name)
        if v is not None: return v
        if name in GLOBAL_SLOTS: return self.sess.gslots.get(name)
        if name in CARRY_SLOTS: return self.sess.known.get(name)
        return None
    def set(self, name, v): self.f.slots[name] = v
    def default(self, name, v):
        if self.s(name) is None and v is not None: self.f.slots[name] = v

    def need(self, *names):
        for n in names:
            if self.s(n) in (None, "", []):
                return ask_step(n)
        return None

    def said(self):
        """Последняя реплика клиента (для проверок, где LLM-слоту нельзя верить на слово)."""
        return next((h["text"] for h in reversed(self.sess.history) if h["role"] == "client"), "")

    def contact_phone(self):
        self.default("phone", self.sess.gslots.get("phone") or (self.client or {}).get("phone") or self.sess.caller_phone)

    def identify(self, ask_note=None):
        """АОН уже проверен в Session.__init__; здесь только названные клиентом телефон или ИИН."""
        if self.client: return None
        for key in ("phone", "iin"):
            v = self.s(key)
            if not v: continue
            r = self.call("find_client", **{key: v})
            if "error" not in r:
                c = self.backend.client(r["client_id"])
                self.sess.client = {"client_id": c["client_id"], "full_name": c["full_name"], "phone": c["phone"], "city": c["city"], "identified_by": key}
                self.sess.gslots.setdefault("phone", c["phone"])
                return None
            self.f.slots.pop(key, None); self.sess.gslots.pop(key, None)
            return self.on_error(r, key)
        st = ask_step("phone")
        if ask_note: st.facts = {"ask_alternative": ask_note}
        return st

    def policy(self, products, prefer_expiring=False):
        pn = self.s("policy_number")
        if pn:
            p = self.call("get_policy", policy_number=pn)
            carried = "policy_number" not in self.f.slots
            if "error" in p:
                if carried: self.sess.known.pop("policy_number", None)
                else: return self.on_error(p, "policy_number")
            elif not products or p["product"] in products:
                self.set("policy_number", pn); return None
            elif not carried:
                return Step("done", facts={"policy": pn, "product": p["product"], "expected": products}, error={"code": "invalid_input", "message": f"Policy {pn} is {p['product']}, not {'/'.join(products)}"})
        ps = self.call("get_policies", client_id=self.client["client_id"]).get("policies", [])
        if products: ps = [p for p in ps if p["product"] in products]
        pt = self.s("product_type") or normalize.product_in_text(self.said())
        if pt and len(ps) > 1: ps = [p for p in ps if p["product"] == pt] or ps
        live = [p for p in ps if p["status"] in ("active", "not_started")] or ps
        if prefer_expiring and len(live) > 1: live = sorted(live, key=lambda p: p["end_date"])[:1]
        if len(live) == 1:
            self.set("policy_number", live[0]["policy_number"]); return None
        if not live:
            kind = "/".join(products or ["any"])
            if self.f.asked.get("policy_number"):       # номер уже спрашивали и полис так и не нашёлся -> оператор
                return handoff_step("operator_general", reason=f"no {kind} policy found for the client")
            return Step("ask", slot="policy_number", facts={"no_policy_found_for_client": kind, "or_operator": True},
                        error={"code": "not_found", "message": f"Client has no {kind} policy"})
        return Step("ask", slot="policy_number", facts={"client_policies": [f"{p['policy_number']} ({p['product']})" for p in live]})

    def call(self, name, **inputs):
        key = name + json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        if key in self.f.memo: return self.f.memo[key]
        t = time.perf_counter()
        r = self.backend.call(name, **inputs)
        self.f.memo[key] = r
        self.sess._action(name, inputs, r, int((time.perf_counter() - t) * 1000))
        return r

    def preview(self, action, inputs, summary=None):
        facts = summary
        if facts is None:
            r = getattr(self.backend, action)(**inputs)
            if "error" in r: return self.on_error(r, "preferred_date")
            facts = r
        return Step("preview", facts=facts, action=action, inputs=inputs)

    def confirmed(self, action): return action in self.f.confirmed
    def gate(self, name):
        v = self.f.gates.get(name)
        return v is not None and (v == "yes" or name.endswith("_answered"))
    def gate_value(self, name): return self.f.gates.get(name)

    def on_error(self, res, slot=None):
        e = res["error"]; code = e["code"]
        if code in ("not_found", "invalid_input") and slot:
            # идентификация: телефон и ИИН считаются вместе: повтор один раз -> другой идентификатор -> оператор
            ident = slot in GLOBAL_SLOTS
            key = "_identify" if ident else slot
            n = self.f.errors.get(key, 0) + 1; self.f.errors[key] = n
            self.f.slots.pop(slot, None)
            if ident: self.sess.gslots.pop(slot, None)
            if n == 1: return Step("ask", slot=slot, facts={"problem": e["message"], "ask_again": True}, error=e)
            if ident and n == 2:
                other = "iin" if slot == "phone" else "phone"
                return Step("ask", slot=other, facts={"problem": e["message"], "try_other_identifier": True, "or_operator": True}, error=e)
            return handoff_step("operator_general", problem=e["message"], reason="could not identify after retries" if ident else f"{slot} not found twice")
        if code == "no_availability":
            asked_date = self.f.slots.pop("preferred_date", None)
            m = re.search(r"nearest:\s*(.+)$", e["message"])
            if m:   # предлагаем ближайшие слоты одним вопросом «какой удобнее»
                return Step("ask", slot="preferred_date", facts={"requested_date_unavailable": asked_date, "nearest_options": [x.strip() for x in m.group(1).split(",")]}, error=e)
            return Step("ask", slot="preferred_date", facts={"problem": e["message"]}, error=e)
        if code == "service_unavailable":
            return handoff_step("operator_general", problem=e["message"])
        return Step("done", facts={"problem": e["message"]}, error=e)

    def kb(self, path):
        node = kit.kb()
        for p in path.split("."):
            node = node.get(p) if isinstance(node, dict) else None
            if node is None: return None
        return node

    region_of_plate = staticmethod(Backend.region_of_plate)
    @staticmethod
    def mask_email(e):
        if not e or "@" not in e: return e
        u, d = e.split("@", 1); return f"{u[0]}***@{d}"
    @staticmethod
    def mask_iin(i): return f"{i[:2]}******{i[-4:]}" if i else i

    def handoff_queue(self):
        cat = CATALOG.scenarios()
        for sid in [x.sid for x in reversed(self.sess.stack)] + list(reversed(self.sess.completed)):
            h = cat.get(sid, {}).get("handoff")
            if h and h.get("queue") in QUEUES and sid != "SC37": return h["queue"]
        return "operator_general"


class Session:
    def __init__(self, channel="web", caller_phone=None, lang="ru", session_id=None):
        self.id = session_id or f"{channel}-{uuid.uuid4().hex[:6]}"
        self.channel = channel
        self.caller_phone = normalize.phone(caller_phone) if caller_phone else None
        self.language = lang
        self.backend = Backend()
        self.client = None
        self.gslots: dict = {}
        self.known: dict = {}
        self.done_frames: dict = {}
        self.lang_lock = None
        self.active: Frame | None = None
        self.stack: list[Frame] = []
        self.completed: list[str] = []
        self.unclear_streak = 0
        self.offer_next = None           # (sid, slots, question)
        self.offer_return: Frame | None = None
        self.history: list[dict] = []
        self.turn_no = 0
        self.ended = False
        self.handoff = None
        self.events: list = []
        self.catalog_version = CATALOG.version
        self._turn_actions: list = []
        if self.caller_phone:
            r = self.backend.call("find_client", phone=self.caller_phone)
            if "error" not in r:
                c = self.backend.client(r["client_id"])
                self.client = {"client_id": c["client_id"], "full_name": c["full_name"], "phone": c["phone"], "city": c["city"], "identified_by": "caller_id"}
                self.gslots["phone"] = c["phone"]
                self.language = c.get("preferred_language", lang)

    # ---------- helpers
    def _action(self, name, inputs, res, ms, preview=False):
        tag = name + (":preview" if preview else ":error" if "error" in res else "")
        self._turn_actions.append(tag)
        self.events.append({"type": "action.preview" if preview else "action.executed", "turn": self.turn_no, "action": name, "inputs": _jsonable(inputs),
                            **({"irreversible": kit.actions()[name]["irreversible"]} if preview else
                               {"result": None if "error" in res else _jsonable(res), "error": res.get("error"), "duration_ms": ms})})

    def preferred_language(self):
        if not self.client: return None
        c = self.backend.client(self.client["client_id"])
        return (c or {}).get("preferred_language")

    def router_state(self):
        a = self.active
        st = {"client": {"name": self.client["full_name"].split()[0], "identified": True} if self.client else None,
              "active": None, "stack": [f.sid for f in self.stack], "awaiting": None,
              "last_bot": next((h["text"] for h in reversed(self.history) if h["role"] == "bot"), None)}
        if a and a.status == "active":
            st["active"] = {"scenario_id": a.sid, "name": CATALOG.scenarios()[a.sid]["name"], "expected_slot": a.expected_slot,
                            "known_slots": sorted(k for k, v in a.slots.items() if v not in (None, "", []))}
            st["awaiting"] = a.awaiting
            if a.awaiting == "confirmation": st["active"]["pending_action"] = a.pending_action
        elif self.offer_next or self.offer_return:
            st["awaiting"] = "offer"
            st["offer"] = self.offer_next[0] if self.offer_next else f"return to {self.offer_return.sid}"
        return st

    def _pop_stack(self, sid):
        for i, f in enumerate(self.stack):
            if f.sid == sid:
                return self.stack.pop(i)
        return None

    def _defer(self, f, reason):
        f.status = "deferred"; f.defer_reason = reason
        if f not in self.stack: self.stack.append(f)

    # ---------- understanding
    async def understand(self, text, on_early=None):
        a = self.active if self.active and self.active.status == "active" else None
        yn = normalize.yes_no(text)
        if a and a.awaiting in ("confirmation", "offer") and yn:
            return {"decision": "confirm" if yn == "yes" else "cancel", "fast_path": "yes_no", "confirmation": yn, "scenarios": [a.sid], "slots": {}}
        if not a and self.offer_next and yn:
            return {"decision": "offer_yes" if yn == "yes" else "offer_no", "fast_path": "yes_no", "scenarios": [self.offer_next[0]] if yn == "yes" else [], "slots": {}}
        if not a and self.offer_return and yn:
            return {"decision": "resume" if yn == "yes" else "offer_no", "fast_path": "yes_no", "scenarios": [self.offer_return.sid] if yn == "yes" else [], "slots": {}}
        if a and a.awaiting == "slot" and a.expected_slot == "preferred_date" and a.options and yn == "yes":
            return {"decision": "continue", "fast_path": "yes_no", "scenarios": [a.sid], "slots": {"preferred_date": a.options[0][:10]}}
        if a and a.awaiting == "slot" and a.expected_slot:
            v = normalize.extract_pattern(a.expected_slot, text)
            if v and normalize.extra_words(text) <= 6:
                return {"decision": "continue", "fast_path": "slot_pattern", "scenarios": [a.sid], "slots": {a.expected_slot: v}}
        if normalize.is_operator(text):
            return {"decision": "handoff", "fast_path": "operator_request", "scenarios": ["SC37"], "slots": {}}
        if normalize.is_hearing_check(text):
            if not self.lang_lock: self.language = "kk" if normalize.KK_LETTERS.search(text) else "ru"
            return {"decision": "greet", "fast_path": "greeting", "hearing": True, "scenarios": ["SYS_UNCLEAR"], "slots": {}}
        if normalize.is_greeting(text):
            if not self.lang_lock: self.language = "kk" if normalize.KK_LETTERS.search(text) else "ru"
            return {"decision": "greet", "fast_path": "greeting", "scenarios": ["SYS_UNCLEAR"], "slots": {}}
        if normalize.is_robot_question(text):
            if not self.lang_lock: self.language = "kk" if normalize.KK_LETTERS.search(text) else "ru"
            return {"decision": "out_of_scope", "fast_path": "fast_path_scenario", "robot": True, "scenarios": ["SYS_OUT_OF_SCOPE"], "slots": {}}
        if not a and normalize.is_bye(text):
            return {"decision": "goodbye", "fast_path": "goodbye_word", "scenarios": ["SYS_GOODBYE"], "slots": {}}

        r = await route_full(text, state=self.router_state(), history=self.history[-7:-1], on_early=on_early)
        slots = normalize.slots(r.get("slots"))
        if "injured" in slots and not re.search(r"пострада|ранен|цел[ыа]?\b|жив|травм|112|скор|зардап|аман|жарақат|жарала", text, re.I): slots.pop("injured")
        # однозначные форматы добираем детерминированно, если роутер их пропустил («полис СК ОГПО 104501», «CL 500287»)
        for k in ("policy_number", "claim_number"):
            if k not in slots and (v := normalize.extract_pattern(k, text)) and (k == "policy_number" or "CL" in normalize.latin_ids(text)): slots[k] = v
        if RU_SWITCH.search(text): self.lang_lock = "ru"
        elif KK_SWITCH.search(text): self.lang_lock = "kk"
        if not re.search(r"[а-яёәіңғүұқөһ]", text, re.I): pass       # только номер/цифры/латиница: язык разговора не меняем
        elif r.get("language") == "mixed": self.language = self.lang_lock or self.preferred_language() or r.get("response_language") or "kk"
        else: self.language = r.get("response_language") or self.language
        extra = [s["scenario_id"] for s in r.get("scenarios", []) if not s["scenario_id"].startswith("SYS_") and s["confidence"] >= config.CONF_CLARIFY]
        base = {"router": r, "slots": slots, "fast_path": None}
        if a and a.awaiting in ("confirmation", "offer") and r.get("confirmation"):
            return {**base, "decision": "confirm" if r["confirmation"] == "yes" else "cancel", "confirmation": r["confirmation"],
                    "scenarios": [a.sid] + [x for x in extra if x != a.sid]}
        if not a and self.offer_next and r.get("confirmation") == "yes":
            return {**base, "decision": "offer_yes", "scenarios": [self.offer_next[0]] + [x for x in extra if x != self.offer_next[0]]}
        if not a and self.offer_return and r.get("confirmation") == "yes":
            return {**base, "decision": "resume", "scenarios": [self.offer_return.sid] + [x for x in extra if x != self.offer_return.sid]}
        if a and r.get("is_continuation"):
            return {**base, "decision": "continue", "scenarios": [a.sid] + [x for x in extra if x != a.sid]}
        d = decide(r, self.unclear_streak)
        return {**base, **d}

    def _reprompt(self):
        """Вопрос, на котором остановились (слот, подтверждение, оффер), чтобы вернуть клиента после отвлечения."""
        a = self.active if self.active and self.active.status == "active" else None
        if not (a and a.awaiting) and not self.offer_next and not self.offer_return: return None
        last = next((h["text"] for h in reversed(self.history) if h["role"] == "bot"), "") or ""
        qs = re.findall(r"[^.!?…]*\?", last)
        if qs: return qs[-1].strip()
        sents = [x for x in re.split(r"(?<=[.!?…])\s+", last.strip()) if x]
        return sents[-1] if sents else None          # «Назовите ИИН водителя…» — повелительная просьба без «?»

    # ---------- execution
    def _step(self, f: Frame) -> Step:
        st = PLANS[f.sid](Ctx(self, f))
        if not isinstance(st, Step): st = Step("done", facts=st)
        return st

    def _apply(self, f: Frame, st: Step):
        f.awaiting = f.expected_slot = f.pending_action = f.pending_gate = None
        f.options = (st.facts or {}).get("nearest_options") if st.kind == "ask" else None
        if st.kind == "ask":
            f.awaiting, f.expected_slot = "slot", st.slot
            f.asked[st.slot] = f.asked.get(st.slot, 0) + 1
            if f.asked[st.slot] > 3:
                st.kind, st.queue, st.facts = "handoff", "operator_general", {"reason": f"could not get {st.slot}"}
                return self._apply(f, st)
        elif st.kind == "preview":
            f.awaiting, f.pending_action, f.pending_inputs = "confirmation", st.action, st.inputs
            self._action(st.action, st.inputs, st.facts or {}, 0, preview=True)
        elif st.kind == "offer":
            f.awaiting, f.pending_gate = "offer", st.gate
        elif st.kind == "handoff":
            f.status = "handoff"
            self.handoff = {"queue": st.queue, "scenario": f.sid, "facts": st.facts}
        else:
            f.status = "done"
            self.done_frames[f.sid] = f
            if f.sid not in self.completed: self.completed.append(f.sid)
        for k in CARRY_SLOTS:
            if f.slots.get(k): self.known[k] = f.slots[k]
            if st.offer_next: self.offer_next = (st.offer_next[0], st.offer_next[1], st.question)

    def _item(self, f, st):
        return {"kind": st.kind, "scenario": f.sid, "new": f.new, "facts": _jsonable(st.facts), "slot": st.slot, "question": st.question,
                "action": st.action, "queue": st.queue, "error": st.error, "offer_next": st.offer_next[0] if st.offer_next else None}

    def run(self, sids, slots):
        ids = [s for s in sids if s in PLANS]
        for k in GLOBAL_SLOTS:
            if k in slots: self.gslots[k] = slots[k]
        frames = []
        for sid in dict.fromkeys(ids):
            if self.active and self.active.sid == sid and self.active.status == "active": f = self.active
            elif (f := self._pop_stack(sid)): f.status = "active"; f.new = False
            else:
                f = Frame(sid)
                prev = self.done_frames.get(sid)
                if prev: f.slots = {k: v for k, v in prev.slots.items() if v not in (None, "", [])}
            frames.append(f)
        if self.active and self.active.status == "active" and self.active not in frames:
            self._defer(self.active, "topic_switch")
        self.active = None; self.offer_return = None
        if frames: self.offer_next = None
        cat = CATALOG.scenarios()
        for f in frames:
            f.slots.update({k: v for k, v in slots.items() if k not in GLOBAL_SLOTS or k in cat[f.sid]["slots"]["required"] + cat[f.sid]["slots"]["optional"]})
        items, interactive = [], None
        urgent = frames and cat[frames[0].sid]["priority"] == "urgent"
        for i, f in enumerate(frames):
            if urgent and i > 0:
                self._defer(f, "multi_intent"); items.append({"kind": "deferred", "scenario": f.sid}); continue
            st = self._step(f)
            if st.kind in ("ask", "offer", "preview"):
                if interactive is None:
                    interactive = f; self._apply(f, st); items.append(self._item(f, st))
                else:
                    self._defer(f, "multi_intent"); items.append({"kind": "deferred", "scenario": f.sid})
            elif st.kind == "handoff":
                self._apply(f, st); items.append(self._item(f, st))
                for g in frames[i + 1:]: self._defer(g, "multi_intent")
                break
            else:
                self._apply(f, st); items.append(self._item(f, st))
            f.new = False if f is not interactive else f.new
        self.active = interactive
        if self.active or any(i["kind"] == "deferred" for i in items): self.offer_next = None   # предложение не прозвучит — не ждём на него «да»
        if not self.active and self.stack and not self.handoff and not self.offer_next:
            self.offer_return = self.stack[-1]
            items.append({"kind": "offer_return", "scenario": self.stack[-1].sid})
        items.sort(key=lambda x: x["kind"] in ("ask", "offer", "preview", "offer_return"))
        return items

    def execute(self, u: dict) -> list:
        dec = u["decision"]
        a = self.active if self.active and self.active.status == "active" else None
        if dec == "greet":
            a = self.active if self.active and self.active.status == "active" else None
            return [{"kind": "greet", "hearing": bool(u.get("hearing")), "resume": a.sid if a else None,
                     "resume_slot": a.expected_slot if a and a.awaiting == "slot" else None}]
        if dec == "goodbye" and not u.get("fast_path") and not self.completed and not self.stack and self.turn_no <= 1 and not normalize.is_bye(u.get("text", "")):
            return [{"kind": "greet", "resume": None}]   # роутер принял приветствие/обрывок первой реплики за прощание
        if dec == "goodbye":
            self.ended = True; return [{"kind": "goodbye"}]
        if dec == "out_of_scope":
            return [{"kind": "out_of_scope", "robot": bool(u.get("robot")), "reprompt": self._reprompt()}]
        if dec == "clarify":
            self.unclear_streak += 1
            return [{"kind": "clarify", "options": u.get("clarify", [])[:2]}]
        self.unclear_streak = 0
        if dec == "handoff":
            return self.run(["SC37"], u.get("slots", {}))
        if dec in ("confirm", "cancel") and a:
            yes = dec == "confirm"
            if a.awaiting == "confirmation":
                if yes: a.confirmed.add(a.pending_action)
                else:
                    self._defer(a, "deferred_by_client"); self.active = None
                    rest = [x for x in u["scenarios"] if x != a.sid]
                    items = self.run(rest, u.get("slots", {})) if rest else []
                    if not rest: self.offer_return = None
                    return [{"kind": "cancelled", "scenario": a.sid}] + [i for i in items if i["kind"] != "offer_return"]
            elif a.awaiting == "offer":
                a.gates[a.pending_gate] = "yes" if yes else "no"
                if not yes and not a.pending_gate.endswith("_answered"):
                    a.status = "cancelled"; self.active = None
                    rest = [x for x in u["scenarios"] if x != a.sid]
                    return [{"kind": "cancelled", "scenario": a.sid}] + (self.run(rest, u.get("slots", {})) if rest else [])
            return self.run(u["scenarios"], u.get("slots", {}))
        if dec == "offer_yes" and self.offer_next:
            sid, carry, _ = self.offer_next; self.offer_next = None
            return self.run(u["scenarios"], {**carry, **u.get("slots", {})})
        if dec == "resume" and self.offer_return:
            return self.run(u["scenarios"], u.get("slots", {}))
        if dec == "offer_no":
            self.offer_next = None; self.offer_return = None
            return [{"kind": "declined"}]
        return self.run(u.get("scenarios", []), u.get("slots", {}))

    # ---------- state snapshot for UI
    def state(self):
        a = self.active
        pend = None
        if a and a.awaiting == "confirmation":
            pend = {"action": a.pending_action, "inputs": _jsonable(a.pending_inputs or {})}
        return {"session_id": self.id, "channel": self.channel, "language": self.language,
                "client": {k: self.client[k] for k in ("client_id", "full_name", "phone", "identified_by")} if self.client else None,
                "active": {"scenario_id": a.sid, "name": CATALOG.scenarios()[a.sid]["name"], "step": _step_name(a), "expected_slot": a.expected_slot,
                           "missing_slots": [a.expected_slot] if a.expected_slot else []} if a else None,
                "stack": [{"scenario_id": f.sid, "name": CATALOG.scenarios()[f.sid]["name"], "reason": f.defer_reason or "topic_switch", "slots": _jsonable(f.slots)} for f in reversed(self.stack)],
                "slots": _jsonable({**self.gslots, **(a.slots if a else {})}), "pending_confirmation": pend, "unclear_streak": self.unclear_streak,
                "completed": list(self.completed), "handoff": {"queue": self.handoff["queue"]} if self.handoff else None}

def _step_name(f):
    return {"slot": "identify" if f.expected_slot in ("phone", "iin") and not f.slots.get("phone") else "collect_slots", "confirmation": "await_confirmation",
            "offer": "await_confirmation"}.get(f.awaiting, "closing")

def _jsonable(o):
    return json.loads(json.dumps(o, ensure_ascii=False, default=lambda x: list(x) if isinstance(x, set) else str(x)))
