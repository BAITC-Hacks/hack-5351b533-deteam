"""Планы действий для 40 сценариев. Каждый план идемпотентен: вызывается на каждом ходу, чтения мемоизируются,
необратимые действия выполняются только после явного подтверждения (ctx.preview -> ctx.confirmed).
План возвращает Step: ask / offer / preview / done / handoff."""
from dataclasses import dataclass, field

@dataclass
class Step:
    kind: str                     # ask | offer | preview | done | handoff
    facts: dict = field(default_factory=dict)
    slot: str | None = None       # для ask
    question: str | None = None   # для offer: что спрашиваем
    gate: str | None = None       # для offer / preview
    action: str | None = None     # для preview
    inputs: dict | None = None
    queue: str | None = None      # для handoff
    error: dict | None = None
    offer_next: tuple | None = None   # (scenario_id, slots) предложить следом
    note: str | None = None

def ask(slot, **facts): return Step("ask", facts=facts, slot=slot)
def done(**facts): return Step("done", facts=facts)
def handoff(queue, **facts): return Step("handoff", facts=facts, queue=queue)
def fail(e, **facts): return Step("done", facts=facts, error=e["error"])

DOCS_KEY = {"ogpo": "ogpo_victim", "ogpo_victim": "ogpo_victim", "casco": "casco", "property": "property", "accident": "accident", "travel": "travel"}

def _claim(ctx):
    """Заявление по номеру или по клиенту. Возвращает (claim|None, Step|None)."""
    if ctx.s("claim_number"):
        c = ctx.call("get_claim", claim_number=ctx.s("claim_number"))
        if "error" in c: return None, ctx.on_error(c, "claim_number")
        return c, None
    if not ctx.client:
        st = ctx.identify(ask_note="claim")
        if st: return None, st
    c = ctx.call("get_claim", client_id=ctx.client["client_id"])
    if "error" in c: return None, ask("claim_number", not_found="у клиента не найдено заявлений по телефону")
    ctx.set("claim_number", c["claim_number"])
    return c, None

def SC01(ctx):
    if not ctx.s("region") and ctx.s("vehicle_plate"): ctx.set("region", ctx.region_of_plate(ctx.s("vehicle_plate")))
    ctx.default("vehicle_type", "car")
    if (st := ctx.need("region", "drivers_iin")): return st
    for i in ctx.s("drivers_iin"): ctx.call("get_bm_class", iin=i)
    q = ctx.call("calc_ogpo_price", region=ctx.s("region"), vehicle_type=ctx.s("vehicle_type"), drivers_iin=ctx.s("drivers_iin"))
    if "error" in q: return ctx.on_error(q)
    carry = {k: ctx.s(k) for k in ("region", "vehicle_type", "drivers_iin", "vehicle_plate") if ctx.s(k)}
    return Step("done", facts={"price_kzt": q["price"], "term": "12 months", "bm_classes": q["bm_classes"]}, offer_next=("SC02", carry), question="Оформить полис?")

def SC02(ctx):
    if (st := ctx.need("vehicle_plate", "drivers_iin")): return st
    ctx.contact_phone()
    if (st := ctx.need("phone")): return st
    region = ctx.s("region") or ctx.region_of_plate(ctx.s("vehicle_plate"))
    q = ctx.call("calc_ogpo_price", region=region, vehicle_type=ctx.s("vehicle_type") or "car", drivers_iin=ctx.s("drivers_iin"))
    if "error" in q: return ctx.on_error(q)
    inputs = {"product_type": "ogpo", "phone": ctx.s("phone"), "vehicle_plate": ctx.s("vehicle_plate"), "drivers_iin": ctx.s("drivers_iin"), "price": q["price"],
              "client_id": (ctx.client or {}).get("client_id")}
    if not ctx.confirmed("create_policy"):
        return ctx.preview("create_policy", inputs, summary={"product": "ОГПО", "vehicle_plate": ctx.s("vehicle_plate"), "drivers": len(ctx.s("drivers_iin")), "price_kzt": q["price"], "sms_phone": ctx.s("phone")})
    r = ctx.call("create_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    ctx.call("send_sms", phone=ctx.s("phone"))
    return done(policy_number=r["policy_number"], payment_link="sent by SMS", starts="right after payment")

def SC03(ctx):
    if (st := ctx.need("car_year", "car_value")): return st
    q = ctx.call("calc_casco_price", car_value=ctx.s("car_value"), car_year=ctx.s("car_year"), franchise=ctx.s("franchise") or 0)
    if "error" in q:
        if q["error"]["code"] == "not_eligible" and "Lite" in q["error"]["message"]:
            lite = ctx.call("calc_casco_price", car_value=ctx.s("car_value"), car_year=ctx.s("car_year"), franchise=ctx.s("franchise") or 0, package="Lite")
            return fail(q, lite_price_kzt=lite.get("price"), lite_covers=ctx.kb("products.casco.packages.Lite"))
        return ctx.on_error(q)
    return done(package="Standard", price_kzt=q["price"], franchise_kzt=q["franchise"], covers=ctx.kb("products.casco.packages.Standard"),
                other_franchise_options="0, 50000, 100000 KZT")

def SC04(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(["ogpo", "casco"])): return st
    if (st := ctx.need("new_driver_iin")): return st
    bm = ctx.call("get_bm_class", iin=ctx.s("new_driver_iin"))
    inputs = {"policy_number": ctx.s("policy_number"), "new_driver_iin": ctx.s("new_driver_iin")}
    if not ctx.confirmed("update_policy"):
        return ctx.preview("update_policy", inputs, summary={"policy_number": ctx.s("policy_number"), "add_driver_iin": ctx.mask_iin(ctx.s("new_driver_iin")), "driver_bm_class": bm.get("bm_class")})
    r = ctx.call("update_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    return done(driver_added=True, extra_premium_kzt=r["extra_premium"], payment_link="by SMS" if r["extra_premium"] else None)

def SC05(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(["ogpo", "casco"])): return st
    if (st := ctx.need("vehicle_plate")): return st
    inputs = {"policy_number": ctx.s("policy_number"), "vehicle_plate": ctx.s("vehicle_plate")}
    if not ctx.confirmed("update_policy"):
        return ctx.preview("update_policy", inputs, summary={"policy_number": ctx.s("policy_number"), "new_vehicle_plate": ctx.s("vehicle_plate")})
    r = ctx.call("update_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    return done(new_vehicle_plate=ctx.s("vehicle_plate"), extra_premium_kzt=r["extra_premium"])

def SC06(ctx):
    if (st := ctx.need("trip_country", "trip_start", "trip_end", "travelers_count", "traveler_max_age")): return st
    q = ctx.call("calc_travel_price", trip_country=ctx.s("trip_country"), trip_start=ctx.s("trip_start"), trip_end=ctx.s("trip_end"),
                 travelers_count=ctx.s("travelers_count"), traveler_max_age=ctx.s("traveler_max_age"))
    if "error" in q:
        if q["error"]["code"] == "not_eligible": return handoff("operator_general", reason="traveler over 75, only via operator")
        return ctx.on_error(q)
    facts = {"price_kzt": q["price"], "coverage": q["coverage"], "zone": q["zone"], "days": q["days"], "travelers": ctx.s("travelers_count"), "assistance": "24/7"}
    if not ctx.gate("buy"): return Step("offer", facts=facts, question="Оформляем?", gate="buy")
    ctx.contact_phone()
    if (st := ctx.need("phone")): return st
    inputs = {"product_type": "travel", "phone": ctx.s("phone"), "trip_country": ctx.s("trip_country"), "trip_start": ctx.s("trip_start"),
              "trip_end": ctx.s("trip_end"), "travelers_count": ctx.s("travelers_count"), "price": q["price"], "client_id": (ctx.client or {}).get("client_id")}
    if not ctx.confirmed("create_policy"):
        return ctx.preview("create_policy", inputs, summary={"country": ctx.s("trip_country"), "from": ctx.s("trip_start"), "to": ctx.s("trip_end"),
                                                             "travelers": ctx.s("travelers_count"), "price_kzt": q["price"], "sms_phone": ctx.s("phone")})
    r = ctx.call("create_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    ctx.call("send_sms", phone=ctx.s("phone"))
    return done(policy_number=r["policy_number"], payment_link="sent by SMS")

def SC07(ctx):
    if (st := ctx.need("property_type", "sum_insured")): return st
    q = ctx.call("calc_property_price", property_type=ctx.s("property_type"), sum_insured=ctx.s("sum_insured"))
    if "error" in q: return fail(q, allowed_sums_kzt=[5000000, 10000000, 20000000])
    return done(price_kzt=q["price"], covers=ctx.kb("products.property.covers"))

def SC08(ctx):
    if (st := ctx.need("sum_insured")): return st
    q = ctx.call("calc_accident_price", sum_insured=ctx.s("sum_insured"))
    if "error" in q: return fail(q, allowed_sums_kzt=[1000000, 3000000, 5000000])
    return done(price_kzt=q["price"], covers=ctx.kb("products.accident.covers"))

def SC09(ctx):
    return done(dms=ctx.call("kb_lookup", topic="products.dms").get("answer"), installments=ctx.kb("payments.installments.dms_individual"))

def SC10(ctx):
    ctx.contact_phone()
    if (st := ctx.need("company_name", "employees_count", "phone")): return st
    ctx.call("transfer_to_operator", queue="corporate_sales")
    return handoff("corporate_sales", company=ctx.s("company_name"), employees=ctx.s("employees_count"), callback="within a working day")

def SC11(ctx):
    steps = ctx.call("kb_lookup", topic="claims.road_accident_now").get("answer")
    if ctx.s("injured") is None: return ask("injured", instructions=steps[:2])
    if ctx.s("injured"):
        ctx.call("transfer_to_operator", queue="claims_team")
        return handoff("claims_team", injured=True, call_112=True)
    ctx.contact_phone()
    if ctx.s("phone"): ctx.call("send_sms", phone=ctx.s("phone"))
    return done(injured=False, instructions=steps[1:5], instructions_sms=bool(ctx.s("phone")), next="file the claim when ready")

def SC12(ctx):
    if (st := ctx.need("culprit_vehicle_plate")): return st
    p = ctx.call("get_policy", vehicle_plate=ctx.s("culprit_vehicle_plate"), product="ogpo")
    if "error" in p: return ctx.on_error(p, "culprit_vehicle_plate")
    if (st := ctx.need("incident_date", "incident_description")): return st
    ctx.contact_phone()
    if (st := ctx.need("phone")): return st
    inputs = {"product_type": "ogpo_victim", "incident_date": ctx.s("incident_date"), "incident_description": ctx.s("incident_description"),
              "policy_number": p["policy_number"], "culprit_vehicle_plate": ctx.s("culprit_vehicle_plate")}
    if not ctx.confirmed("create_claim"):
        return ctx.preview("create_claim", inputs, summary={"incident_date": ctx.s("incident_date"), "culprit_plate": ctx.s("culprit_vehicle_plate"),
                                                            "culprit_policy_found": True, "contact_phone": ctx.s("phone")})
    r = ctx.call("create_claim", **inputs)
    if "error" in r: return ctx.on_error(r)
    ctx.call("send_sms", phone=ctx.s("phone"))
    return done(claim_number=r["claim_number"], documents_sms=True, documents=ctx.kb("claims.documents.ogpo_victim"))

def _claim_flow(ctx, product, docs_key, queue_if=None):
    if (st := ctx.identify()): return st
    if (st := ctx.policy([product])): return st
    if (st := ctx.need("incident_date", "incident_description")): return st
    inputs = {"product_type": product, "incident_date": ctx.s("incident_date"), "incident_description": ctx.s("incident_description"), "policy_number": ctx.s("policy_number")}
    if not ctx.confirmed("create_claim"):
        return ctx.preview("create_claim", inputs, summary={"policy_number": ctx.s("policy_number"), "incident_date": ctx.s("incident_date"), "what": ctx.s("incident_description")})
    r = ctx.call("create_claim", **inputs)
    if "error" in r: return ctx.on_error(r)
    ctx.call("send_sms", phone=ctx.client["phone"])
    facts = {"claim_number": r["claim_number"], "documents": ctx.kb(f"claims.documents.{docs_key}"), "documents_sms": True, "decision_time": ctx.kb("claims.decision_time")}
    if queue_if and queue_if(ctx.s("incident_description") or ""):
        ctx.call("transfer_to_operator", queue="claims_team")
        return handoff("claims_team", **facts)
    return facts

def SC13(ctx):
    r = _claim_flow(ctx, "casco", "casco", queue_if=lambda t: any(w in t.lower() for w in ("theft", "stolen", "угна", "угон", "total", "тотал", "ұрла")))
    if isinstance(r, Step): return r
    return Step("done", facts={**r, "next": "inspection of the car"}, offer_next=("SC20", {"claim_number": r["claim_number"]}), question="Записать на осмотр?")

def SC14(ctx):
    r = _claim_flow(ctx, "property", "property")
    return r if isinstance(r, Step) else done(**r)

def SC15(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(["travel"])): return st
    ctx.call("get_policy", policy_number=ctx.s("policy_number"))
    ctx.call("transfer_to_operator", queue="medical_assistance_24_7")
    return handoff("medical_assistance_24_7", rule=ctx.kb("products.travel.notes")[1], location=ctx.s("location"))

def SC16(ctx):
    r = _claim_flow(ctx, "accident", "accident")
    return r if isinstance(r, Step) else done(**r)

def SC17(ctx):
    c, st = _claim(ctx)
    if st: return st
    return done(claim_number=c["claim_number"], status=c["status"], next_step=c.get("next_step"), approved_amount_kzt=c.get("approved_amount"),
                decision_due=c.get("decision_due"), missing_documents=c.get("missing_documents"))

def SC18(ctx):
    c = None
    if ctx.s("claim_number"):
        c = ctx.call("get_claim", claim_number=ctx.s("claim_number"))
        if "error" not in c: ctx.default("product_type", {"ogpo_victim": "ogpo"}.get(c["claim_type"], c["claim_type"]))
        else: c = None
    elif ctx.client and not ctx.s("product_type"):
        c = ctx.call("get_claim", client_id=ctx.client["client_id"])
        if "error" not in c: ctx.default("product_type", {"ogpo_victim": "ogpo"}.get(c["claim_type"], c["claim_type"]))
        else: c = None
    if (st := ctx.need("product_type")): return st
    facts = {"documents": ctx.kb(f"claims.documents.{DOCS_KEY.get(ctx.s('product_type'), 'casco')}"), "how_to_submit": ctx.kb("claims.submission")}
    if c: facts.update(claim_number=c["claim_number"], claim_status=c["status"], missing_documents=c.get("missing_documents", []), claim_next_step=c.get("next_step"))
    return done(**facts)

def SC19(ctx):
    c, st = _claim(ctx)
    if st: return st
    if (st := ctx.need("complaint_text")): return st
    inputs = {"claim_number": c["claim_number"], "complaint_text": ctx.s("complaint_text")}
    if not ctx.confirmed("create_dispute"):
        return ctx.preview("create_dispute", inputs, summary={"claim_number": c["claim_number"], "decision": c.get("status"), "approved_kzt": c.get("approved_amount"),
                                                              "assessor_estimate_kzt": c.get("assessor_estimate"), "disagreement": ctx.s("complaint_text")})
    r = ctx.call("create_dispute", **inputs)
    if "error" in r: return ctx.on_error(r)
    return done(ticket_id=r["ticket_id"], review=ctx.kb("claims.dispute"))

def SC20(ctx):
    c, st = _claim(ctx)
    if st: return st
    if ctx.client: ctx.default("city", ctx.client.get("city"))
    if (st := ctx.need("city", "preferred_date")): return st
    inputs = {"claim_number": c["claim_number"], "city": ctx.s("city"), "preferred_date": ctx.s("preferred_date")}
    if not ctx.confirmed("book_inspection"):
        return ctx.preview("book_inspection", inputs)
    r = ctx.call("book_inspection", **inputs)
    if "error" in r: return ctx.on_error(r, "preferred_date")
    return done(slot=r["slot_datetime"], address=r["address"], bring="vehicle registration certificate")

def SC21(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(["dms"])): return st
    ctx.default("city", ctx.client.get("city"))
    if (st := ctx.need("doctor_specialty", "preferred_date")): return st
    inputs = {"policy_number": ctx.s("policy_number"), "doctor_specialty": ctx.s("doctor_specialty"), "city": ctx.s("city"), "preferred_date": ctx.s("preferred_date")}
    if not ctx.confirmed("book_appointment"):
        return ctx.preview("book_appointment", inputs)
    r = ctx.call("book_appointment", **inputs)
    if "error" in r: return ctx.on_error(r, "preferred_date")
    return done(clinic=r["clinic_name"], address=r["address"], slot=r["slot_datetime"], bring="ID card")

def SC22(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(["dms"])): return st
    if (st := ctx.need("service_name")): return st
    r = ctx.call("check_coverage", policy_number=ctx.s("policy_number"), service_name=ctx.s("service_name"))
    if "error" in r: return ctx.on_error(r)
    return done(service=ctx.s("service_name"), covered=r["covered"], note=r["note"], package=r["package"],
                covered_list=r["covered_list"] if r["covered"] is None else None, not_covered_list=r["not_covered_list"] if r["covered"] is None else None)

def SC23(ctx):
    if ctx.client: ctx.default("city", ctx.client.get("city"))
    if (st := ctx.need("city")): return st
    r = ctx.call("list_clinics", city=ctx.s("city"), doctor_specialty=ctx.s("doctor_specialty"))
    if "error" in r: return fail(r, cities_with_clinics=sorted({c["city"] for c in ctx.kb("clinics")}))
    return done(clinics=[f"{c['name']}, {c['address']}" for c in r["clinics"]], sms_offer=True)

def SC24(ctx):
    if (st := ctx.identify()): return st
    ctx.call("kb_lookup", topic="products.dms.e_card")
    ctx.call("send_sms", phone=ctx.client["phone"])
    return done(e_card=ctx.kb("products.dms.e_card"), sent_to_phone=ctx.client["phone"])

def SC25(ctx):
    if ctx.s("policy_number"):
        p = ctx.call("get_policy", policy_number=ctx.s("policy_number"))
        if "error" in p: return ctx.on_error(p, "policy_number")
        return done(policy_number=p["policy_number"], product=p["product"], status=p["status"], end_date=p["end_date"])
    if (st := ctx.identify(ask_note="policy")): return st
    ps = ctx.call("get_policies", client_id=ctx.client["client_id"]).get("policies", [])
    if ctx.s("product_type"): ps = [p for p in ps if p["product"] == ctx.s("product_type")] or ps
    return done(policies=[{"policy_number": p["policy_number"], "product": p["product"], "status": p["status"], "end_date": p["end_date"]} for p in ps])

def SC26(ctx):
    if (st := ctx.identify()): return st
    if not ctx.s("policy_number"):
        ps = [p for p in ctx.call("get_policies", client_id=ctx.client["client_id"]).get("policies", []) if p["status"] in ("active", "not_started")]
        if not ps: return done(no_active_policies=True)
        ctx.set("policy_number", ps[0]["policy_number"])
    r = ctx.call("resend_documents", policy_number=ctx.s("policy_number"), email=ctx.s("email"))
    if "error" in r: return ctx.on_error(r, "policy_number")
    return done(policy_number=r["policy_number"], sent_to=ctx.mask_email(r["sent_to"]), hint="check spam folder")

def SC27(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(None, prefer_expiring=True)): return st
    p = ctx.call("get_policy", policy_number=ctx.s("policy_number"))
    inputs = {"policy_number": ctx.s("policy_number")}
    if not ctx.confirmed("renew_policy"):
        return ctx.preview("renew_policy", inputs, summary={"policy_number": p["policy_number"], "product": p["product"], "current_policy_ends": p["end_date"], "new_term": "12 months after the current one",
                                                            "price_kzt": p.get("premium"), "installments": ctx.kb(f"payments.installments.{p['product']}") if p["product"] in ("casco", "ogpo", "travel") else None})
    r = ctx.call("renew_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    ctx.call("send_sms", phone=ctx.client["phone"])
    return done(new_policy_number=r["policy_number"], price_kzt=r["price"], starts=r["start_date"], payment_link="SMS")

def SC28(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(None)): return st
    if (st := ctx.need("cancel_reason")): return st
    q = ctx.backend.cancel_quote(ctx.s("policy_number"))
    if "error" in q: return ctx.on_error(q)
    inputs = {"policy_number": ctx.s("policy_number"), "cancel_reason": ctx.s("cancel_reason")}
    if not ctx.confirmed("cancel_policy"):
        return ctx.preview("cancel_policy", inputs, summary={"policy_number": ctx.s("policy_number"), "refund_kzt": q["refund_amount"], "irreversible": True, "note": q["note"]})
    r = ctx.call("cancel_policy", **inputs)
    if "error" in r: return ctx.on_error(r)
    return done(cancelled=True, refund_kzt=r["refund_amount"], refund_time=ctx.kb("cancellation.refund_time"))

def SC29(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.need("contact_field", "new_value")): return st
    inputs = {"client_id": ctx.client["client_id"], "contact_field": ctx.s("contact_field"), "new_value": ctx.s("new_value")}
    if not ctx.confirmed("update_contact"):
        return ctx.preview("update_contact", inputs, summary={"field": ctx.s("contact_field"), "new_value": ctx.s("new_value")})
    r = ctx.call("update_contact", **inputs)
    if "error" in r: return ctx.on_error(r, "new_value")
    return done(updated=ctx.s("contact_field"))

def SC30(ctx):
    if (st := ctx.identify()): return st
    r = ctx.call("check_payment", client_id=ctx.client["client_id"], payment_date=ctx.s("payment_date"))
    if "error" in r:
        if (st := ctx.need("payment_date")): return st
        return fail(r)
    if r["payment_status"] == "charged_policy_not_issued":
        ctx.call("transfer_to_operator", queue="operator_general")
        return handoff("operator_general", amount_kzt=r["amount"], date=r["date"], product=r["product"], issue="payment charged, policy not issued; specialist will issue the policy or refund")
    return done(payment_status=r["payment_status"], amount_kzt=r["amount"], date=r["date"])

def SC31(ctx):
    return done(payments=ctx.call("kb_lookup", topic="payments").get("answer"), product=ctx.s("product_type"))

def SC32(ctx):
    if ctx.client: ctx.default("iin", ctx.backend.client(ctx.client["client_id"])["iin"])
    if (st := ctx.need("iin")): return st
    r = ctx.call("get_bm_class", iin=ctx.s("iin"))
    if "error" in r: return ctx.on_error(r, "iin")
    return done(bm_class=r["bm_class"], rules=ctx.call("kb_lookup", topic="bonus_malus").get("answer")["rules"], ogpo_coef=ctx.kb(f"products.ogpo.pricing.bm_coef.{r['bm_class']}"))

def SC33(ctx):
    if ctx.client: ctx.default("city", ctx.client.get("city"))
    if (st := ctx.need("city")): return st
    r = ctx.call("get_offices", city=ctx.s("city"))
    if "error" in r: return fail(r, cities=[o["city"] for o in ctx.kb("offices")])
    return done(address=f"{r['city']}, {r['address']}", hours=r["hours"], contact_center_hours=ctx.kb("company.contact_center"))

def SC34(ctx):
    return done(app_help=ctx.call("kb_lookup", topic="app_help").get("answer"))

def SC35(ctx):
    if (st := ctx.need("complaint_text")): return st
    r = ctx.call("create_complaint", complaint_text=ctx.s("complaint_text"))
    return done(ticket_id=r["ticket_id"], review=ctx.kb("complaints.review_time"))

def SC36(ctx):
    ctx.contact_phone()
    if (st := ctx.need("phone", "callback_time")): return st
    r = ctx.call("create_callback", phone=ctx.s("phone"), callback_time=ctx.s("callback_time"))
    if "error" in r: return ctx.on_error(r, "phone")
    return done(callback_time=ctx.s("callback_time"), phone=ctx.s("phone"))

def SC37(ctx):
    if not ctx.client and ctx.s("phone"): ctx.identify()
    q = ctx.handoff_queue()
    ctx.call("transfer_to_operator", queue=q)
    return handoff(q)

def SC38(ctx):
    rules = ctx.call("kb_lookup", topic="fraud_policy").get("answer")
    if (st := ctx.need("fraud_details")): return Step("ask", facts={"fraud_policy": rules[:2]}, slot="fraud_details")
    if not ctx.gate("shared_codes_answered"):
        return Step("offer", facts={"fraud_policy": rules[:2]}, question="Вы уже сообщили им код или данные карты?", gate="shared_codes_answered")
    r = ctx.call("report_fraud", fraud_details=ctx.s("fraud_details"))
    if ctx.gate_value("shared_codes_answered") == "yes":
        ctx.call("transfer_to_operator", queue="security_team")
        return handoff("security_team", ticket_id=r["ticket_id"], shared_codes=True)
    return Step("done", facts={"ticket_id": r["ticket_id"], "advice": "never share codes"}, offer_next=("SC25", {}), question="Проверить, что с вашим полисом всё в порядке?")

def SC39(ctx):
    if (st := ctx.identify()): return st
    if (st := ctx.policy(None)): return st
    if ctx.client: ctx.default("email", ctx.backend.client(ctx.client["client_id"]).get("email"))
    if (st := ctx.need("document_type", "email")): return st
    r = ctx.call("request_document", policy_number=ctx.s("policy_number"), document_type=ctx.s("document_type"), email=ctx.s("email"))
    if "error" in r: return ctx.on_error(r, "document_type")
    return done(sent_to=ctx.mask_email(r["sent_to"]), delivery=r["delivery"], document=ctx.s("document_type"))

def SC40(ctx):
    pt = ctx.s("product_type")
    info = ctx.call("kb_lookup", topic=f"products.{pt}").get("answer") if pt else {k: {kk: vv for kk, vv in v.items() if kk in ("name", "covers", "exclusions", "packages")} for k, v in ctx.kb("products").items()}
    return done(topic=ctx.s("topic"), product_terms=info, claims_rules={k: ctx.kb(f"claims.{k}") for k in ("notify_deadline", "decision_time", "payout_time")},
                cancellation=ctx.kb("cancellation"))

PLANS = {f"SC{i:02d}": globals()[f"SC{i:02d}"] for i in range(1, 41)}
