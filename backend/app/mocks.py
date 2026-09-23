"""Мок-действия из actions.json поверх mock_backend.json и knowledge_base.json.
Каждое действие возвращает dict результата или {"error": {"code", "message"}}."""
import datetime as dt, re, itertools
from . import kit, config
from .normalize import phone as norm_phone, plate as norm_plate

TODAY = dt.date.fromisoformat(config.TODAY)
PFX = {"ogpo": "OGPO", "casco": "CASCO", "travel": "TRVL", "property": "PROP", "accident": "NS", "dms": "DMS"}
QUEUES = ["operator_general", "claims_team", "medical_assistance_24_7", "corporate_sales", "complaints_team", "security_team"]

def err(code, message): return {"error": {"code": code, "message": message}}
def d(s): return dt.date.fromisoformat(s)

def _counters():
    m = kit.mock_backend()
    pol = {}
    for p in m["policies"]:
        pfx, num = p["policy_number"].split("-")[1], int(p["policy_number"].split("-")[2])
        pol[pfx] = max(pol.get(pfx, 0), num)
    return {"policy": {k: itertools.count(pol.get(k, {"OGPO": 104000, "CASCO": 204000, "TRVL": 304000, "PROP": 404000, "NS": 504000, "DMS": 604000}[k]) + 1) for k in PFX.values()},
            "claim": itertools.count(max(int(c["claim_number"][3:]) for c in m["claims"]) + 1),
            "ticket": itertools.count(700101), "fraud": itertools.count(900001), "callback": itertools.count(100001), "dispute": itertools.count(800001)}
COUNTERS = _counters()

ZONE_A = {"russia", "kyrgyzstan", "uzbekistan", "tajikistan", "armenia", "azerbaijan", "belarus", "moldova", "georgia", "turkmenistan"}
ZONE_B = {"schengen", "germany", "france", "italy", "spain", "portugal", "netherlands", "belgium", "luxembourg", "austria", "switzerland", "czech republic", "czechia",
          "poland", "hungary", "slovakia", "slovenia", "croatia", "greece", "malta", "finland", "sweden", "norway", "denmark", "iceland", "estonia", "latvia",
          "lithuania", "liechtenstein", "united kingdom", "uk", "great britain", "britain", "england", "scotland", "europe"}
ZONE_D = {"usa", "united states", "united states of america", "america", "canada"}
RU_COUNTRY = {"грузия": "georgia", "россия": "russia", "турция": "turkey", "оаэ": "uae", "египет": "egypt", "таиланд": "thailand", "сша": "usa", "канада": "canada",
              "германия": "germany", "франция": "france", "италия": "italy", "испания": "spain", "англия": "united kingdom", "великобритания": "united kingdom",
              "кыргызстан": "kyrgyzstan", "узбекистан": "uzbekistan", "шенген": "schengen", "европа": "europe", "түркия": "turkey", "грузияға": "georgia"}
SPEC = {"терапевт": "therapist", "лор": "ENT", "стоматолог": "dentist", "гинеколог": "gynecologist", "кардиолог": "cardiologist", "педиатр": "pediatrician",
        "узи": "ultrasound", "анализ": "lab", "ent": "ENT", "otolaryngologist": "ENT", "dentist": "dentist", "therapist": "therapist", "gp": "therapist",
        "general practitioner": "therapist", "lab": "lab", "laboratory": "lab", "ultrasound": "ultrasound", "cardiologist": "cardiologist",
        "gynecologist": "gynecologist", "pediatrician": "pediatrician", "тіс": "dentist"}

def spec(s):
    s = (s or "").lower().strip()
    for k, v in SPEC.items():
        if k in s: return v
    return s

def zone_of(country):
    c = RU_COUNTRY.get((country or "").lower().strip(), (country or "").lower().strip())
    return "A" if c in ZONE_A else "B" if c in ZONE_B else "D" if c in ZONE_D else "C"

def status_of(p):
    if p.get("status") in ("cancelled", "pending_payment"): return p["status"]
    s, e = d(p["start_date"]), d(p["end_date"])
    return "active" if s <= TODAY <= e else "expired" if e < TODAY else "not_started"

class Backend:
    """Состояние моков на одну сессию: копия mock_backend.json, изменения не переживают сессию."""
    def __init__(self):
        self.db = kit.mock_backend(); self.kb = kit.kb(); self.log = []

    # --- клиенты и полисы
    def find_client(self, phone=None, iin=None, **_):
        if phone:
            ph = norm_phone(phone)
            if not ph: return err("invalid_input", f"Bad phone {phone}")
            c = next((c for c in self.db["clients"] if c["phone"] == ph), None)
            return {"client_id": c["client_id"], "full_name": c["full_name"]} if c else err("not_found", f"Client with phone {ph} not found")
        if iin:
            if not re.fullmatch(r"\d{12}", str(iin)): return err("invalid_input", f"Bad IIN {iin}")
            c = next((c for c in self.db["clients"] if c["iin"] == iin), None)
            return {"client_id": c["client_id"], "full_name": c["full_name"]} if c else err("not_found", f"Client with IIN {iin} not found")
        return err("invalid_input", "phone or iin required")

    def client(self, client_id):
        return next((c for c in self.db["clients"] if c["client_id"] == client_id), None)

    def _pol(self, p):
        return {"policy_number": p["policy_number"], "product": p["product"], "status": status_of(p), "start_date": p["start_date"],
                "end_date": p["end_date"], "premium": p.get("premium"), "details": p.get("details", {}), "client_id": p["client_id"]}

    def get_policies(self, client_id=None, **_):
        if not self.client(client_id): return err("not_found", f"Client {client_id} not found")
        return {"policies": [self._pol(p) for p in self.db["policies"] if p["client_id"] == client_id]}

    def get_policy(self, policy_number=None, vehicle_plate=None, product=None, **_):
        if policy_number:
            p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
            return self._pol(p) if p else err("not_found", f"Policy {policy_number} not found")
        if vehicle_plate:
            pl = norm_plate(vehicle_plate)
            ps = [p for p in self.db["policies"] if p.get("details", {}).get("vehicle_plate") == pl and (not product or p["product"] == product)]
            ps.sort(key=lambda p: p["product"] != "ogpo")
            return self._pol(ps[0]) if ps else err("not_found", f"No policy for plate {pl}")
        return err("invalid_input", "policy_number or vehicle_plate required")

    def get_bm_class(self, iin=None, **_):
        if not re.fullmatch(r"\d{12}", str(iin or "")): return err("invalid_input", f"Bad IIN {iin}")
        c = next((c for c in self.db["clients"] if c["iin"] == iin), None)
        return {"iin": iin, "bm_class": c["bm_class"] if c else self.db["defaults"]["unknown_iin_bm_class"]}

    # --- расчёты
    def calc_ogpo_price(self, region=None, vehicle_type="car", drivers_iin=None, term_months=12, **_):
        pr = self.kb["products"]["ogpo"]["pricing"]
        if region not in pr["base_by_region_kzt"]: return err("invalid_input", f"Bad region {region}")
        if vehicle_type not in pr["vehicle_type_coef"]: return err("invalid_input", f"Bad vehicle_type {vehicle_type}")
        drivers = drivers_iin or []
        classes = {i: self.get_bm_class(i).get("bm_class", "3") for i in drivers} or {"-": "3"}
        coef = max(pr["bm_coef"][c] for c in classes.values())
        price = round(pr["base_by_region_kzt"][region] * pr["vehicle_type_coef"][vehicle_type] * coef * pr["term_coef"][str(term_months)])
        return {"price": price, "bm_classes": classes, "region": region, "vehicle_type": vehicle_type, "term_months": term_months}

    @staticmethod
    def region_of_plate(plate):
        code = norm_plate(plate)[-2:]
        return kit.kb()["products"]["ogpo"]["pricing"]["region_by_plate_code"].get(code, "other")

    def calc_casco_price(self, car_value=None, car_year=None, franchise=0, package="Standard", **_):
        pr = self.kb["products"]["casco"]["pricing"]
        if not car_value or not car_year: return err("invalid_input", "car_value and car_year required")
        age = TODAY.year - int(car_year)
        if age > pr["max_car_age"][package]:
            alt = "Lite" if package == "Standard" and age <= pr["max_car_age"]["Lite"] else None
            return err("not_eligible", f"Car is {age} years old, {package} allows up to {pr['max_car_age'][package]}" + (f"; Lite package is possible" if alt else ""))
        rate = next(v for k, v in pr["rate_by_car_age"].items() if int(k.split("-")[0]) <= max(age, 0) <= int(k.split("-")[1]))
        price = round(int(car_value) * rate * pr["franchise_coef"][str(int(franchise or 0))] * pr["package_coef"][package])
        return {"price": price, "package": package, "franchise": int(franchise or 0), "car_age": age}

    def calc_travel_price(self, trip_country=None, trip_start=None, trip_end=None, travelers_count=1, traveler_max_age=30, **_):
        t = self.kb["products"]["travel"]
        try: days = (d(trip_end) - d(trip_start)).days + 1
        except Exception: return err("invalid_input", "Bad trip dates")
        if days <= 0: return err("invalid_input", "trip_end before trip_start")
        age = int(traveler_max_age)
        if age > 75: return err("not_eligible", "Traveler over 75: only via operator")
        coef = 2.0 if age >= 65 else 1.0
        z = zone_of(trip_country); zi = t["zones"][z]
        return {"price": round(zi["rate_per_day_kzt"] * days * int(travelers_count) * coef), "zone": z, "coverage": zi["coverage"], "days": days}

    def calc_property_price(self, property_type=None, sum_insured=None, **_):
        p = self.kb["products"]["property"]
        base = p["price_per_year_kzt"].get(str(sum_insured))
        if base is None: return err("invalid_input", f"Sum insured must be one of {list(p['price_per_year_kzt'])}")
        return {"price": round(base * (p["house_coef"] if property_type == "house" else 1))}

    def calc_accident_price(self, sum_insured=None, **_):
        base = self.kb["products"]["accident"]["price_per_year_kzt"].get(str(sum_insured))
        return {"price": base} if base else err("invalid_input", "Sum insured must be 1, 3 or 5 million")

    # --- необратимые
    def create_policy(self, product_type=None, phone=None, **details):
        if product_type not in PFX: return err("invalid_input", f"Bad product {product_type}")
        num = f"SQ-{PFX[product_type]}-{next(COUNTERS['policy'][PFX[product_type]])}"
        self.db["policies"].append({"policy_number": num, "client_id": details.pop("client_id", None), "product": product_type, "status": "pending_payment",
                                    "start_date": config.TODAY, "end_date": config.TODAY, "premium": details.get("price"), "details": details})
        return {"policy_number": num, "payment_link_sent_to": norm_phone(phone or "")}

    def renew_policy(self, policy_number=None, **_):
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p: return err("not_found", f"Policy {policy_number} not found")
        if p["product"] == "dms" and p.get("details", {}).get("type") == "corporate": return err("not_eligible", "Corporate DMS is renewed by the employer")
        num = f"SQ-{PFX[p['product']]}-{next(COUNTERS['policy'][PFX[p['product']]])}"
        start = d(p["end_date"]) + dt.timedelta(days=1)
        return {"policy_number": num, "price": p.get("premium"), "start_date": start.isoformat()}

    def update_policy(self, policy_number=None, new_driver_iin=None, vehicle_plate=None, **_):
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p: return err("not_found", f"Policy {policy_number} not found")
        if status_of(p) != "active": return err("policy_inactive", f"Policy {policy_number} is {status_of(p)}")
        rem = max(0, (d(p["end_date"]).year - TODAY.year) * 12 + d(p["end_date"]).month - TODAY.month + 1)
        extra = 0
        if p["product"] == "ogpo":
            det = p["details"]
            drivers = det.get("drivers_iin", []) + ([new_driver_iin] if new_driver_iin else [])
            region = self.region_of_plate(vehicle_plate or det["vehicle_plate"])
            new = self.calc_ogpo_price(region, det.get("vehicle_type", "car"), drivers)["price"]
            extra = max(0, round((new - (p["premium"] or 0)) * rem / 12))
            if new_driver_iin: det["drivers_iin"] = drivers
            if vehicle_plate: det["vehicle_plate"] = norm_plate(vehicle_plate)
        elif vehicle_plate:
            p["details"]["vehicle_plate"] = norm_plate(vehicle_plate)
        return {"policy_number": policy_number, "extra_premium": extra}

    def cancel_policy(self, policy_number=None, cancel_reason=None, **_):
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p: return err("not_found", f"Policy {policy_number} not found")
        if p.get("status") == "cancelled": return err("already_done", "Policy already cancelled")
        if status_of(p) != "active": return err("policy_inactive", f"Policy {policy_number} is {status_of(p)}")
        if p.get("premium") is None: return err("not_eligible", "Corporate policy is cancelled by the employer")
        end1 = d(p["end_date"]) + dt.timedelta(days=1)
        months = (end1.year - TODAY.year) * 12 + end1.month - TODAY.month - (1 if end1.day < TODAY.day else 0)
        paid = any(c["policy_number"] == policy_number and c["status"] == "paid" for c in self.db["claims"])
        refund = 0 if paid else round(p["premium"] * months / 12 * 0.9)
        p["status"] = "cancelled"
        return {"policy_number": policy_number, "refund_amount": refund, "unused_months": months,
                "note": "No refund: a claim was paid under this policy" if paid else "Refund to the card within 10 working days"}

    def cancel_quote(self, policy_number):
        """Расчёт возврата без расторжения, для озвучивания перед подтверждением."""
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p: return err("not_found", f"Policy {policy_number} not found")
        if p.get("status") == "cancelled": return err("already_done", "Policy already cancelled")
        if status_of(p) != "active": return err("policy_inactive", f"Policy {policy_number} is {status_of(p)}")
        if p.get("premium") is None: return err("not_eligible", "Corporate policy is cancelled by the employer")
        end1 = d(p["end_date"]) + dt.timedelta(days=1)
        months = (end1.year - TODAY.year) * 12 + end1.month - TODAY.month - (1 if end1.day < TODAY.day else 0)
        paid = any(c["policy_number"] == policy_number and c["status"] == "paid" for c in self.db["claims"])
        return {"refund_amount": 0 if paid else round(p["premium"] * months / 12 * 0.9), "unused_months": months,
                "note": "No refund: a claim was paid under this policy" if paid else "Refund to the card within 10 working days"}

    def create_claim(self, product_type=None, incident_date=None, incident_description=None, policy_number=None, culprit_vehicle_plate=None, **_):
        if not incident_date: return err("invalid_input", "incident_date required")
        if policy_number:
            p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
            if not p: return err("not_found", f"Policy {policy_number} not found")
            if not (d(p["start_date"]) <= d(incident_date) <= d(p["end_date"])): return err("policy_inactive", f"Policy was not active on {incident_date}")
        num = f"CL-{next(COUNTERS['claim'])}"
        self.db["claims"].append({"claim_number": num, "policy_number": policy_number, "claim_type": product_type, "incident_date": incident_date,
                                  "status": "registered", "next_step": "Upload documents from the SMS list."})
        return {"claim_number": num}

    def get_claim(self, claim_number=None, client_id=None, **_):
        cs = [c for c in self.db["claims"] if (claim_number and c["claim_number"] == claim_number) or (not claim_number and client_id and c.get("client_id") == client_id)]
        if not cs: return err("not_found", f"Claim {claim_number or 'for ' + str(client_id)} not found")
        cs.sort(key=lambda c: c["incident_date"], reverse=True)
        return dict(cs[0])

    def create_dispute(self, claim_number=None, complaint_text=None, **_):
        if not any(c["claim_number"] == claim_number for c in self.db["claims"]): return err("not_found", f"Claim {claim_number} not found")
        return {"ticket_id": f"D-{next(COUNTERS['dispute'])}", "review": "15 working days, written answer"}

    def book_inspection(self, claim_number=None, city=None, preferred_date=None, **_):
        if not any(c["claim_number"] == claim_number for c in self.db["claims"]): return err("not_found", f"Claim {claim_number} not found")
        pts = {p["city"]: p for p in self.kb["inspection_points"]}
        pt = pts.get(city) or pts["other"]
        day = d(preferred_date); last = 5 if pt["city"] in ("Almaty", "Astana") else 4
        if day.weekday() > last:
            alts = [(day + dt.timedelta(days=i)) for i in range(1, 4) if (day + dt.timedelta(days=i)).weekday() <= last][:2]
            return err("no_availability", f"No slots on {preferred_date}; nearest: {', '.join(a.isoformat() + ' 10:00' for a in alts)}")
        addr = pt["address"] if pt["city"] != "other" else f"{city}, " + next((o["address"] for o in self.kb["offices"] if o["city"] == city), "") + " (office parking)"
        return {"slot_datetime": f"{preferred_date} 10:00", "address": addr}

    def _dms(self, policy_number):
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p or p["product"] != "dms": return None, err("not_found", f"DMS policy {policy_number} not found")
        if status_of(p) != "active": return None, err("policy_inactive", f"Policy {policy_number} is {status_of(p)}")
        return p, None

    def book_appointment(self, policy_number=None, doctor_specialty=None, city=None, preferred_date=None, **_):
        p, e = self._dms(policy_number)
        if e: return e
        sp = spec(doctor_specialty); pkg = p["details"].get("package", "Basic")
        if sp == "dentist" and pkg == "Basic": return err("not_covered", "Dentistry is not covered by the Basic package")
        cl = [c for c in self.kb["clinics"] if c["city"] == city and sp in c["specialties"]]
        if not cl: return err("no_availability", f"No partner clinic with {sp} in {city}")
        day = d(preferred_date)
        if day.weekday() == 6:
            return err("no_availability", f"No slots on Sunday {preferred_date}; nearest: {(day + dt.timedelta(days=1)).isoformat()} 09:30")
        return {"clinic_name": cl[0]["name"], "address": f"{city}, {cl[0]['address']}", "slot_datetime": f"{preferred_date} 09:30", "specialty": sp}

    COVER = [("lab", ["lab", "анализ", "талдау"]), ("MRI and CT", ["mri", "ct", "мрт", "кт"]), ("Dental", ["dent", "стомат", "зуб", "тіс"]),
             ("Ultrasound", ["ultrasound", "узи"]), ("medications", ["medic", "лекарств", "дәрі"]), ("hospitalization", ["hospital", "госпитал", "стационар"]),
             ("Specialists", ["specialist", "ent", "лор", "кардиолог", "гинеколог", "cardio", "gyne"]), ("Therapist", ["therapist", "терапевт"]),
             ("Cosmetology", ["cosmet", "космет"]), ("prosthetics", ["prosthe", "протез", "implant", "имплант"])]

    def check_coverage(self, policy_number=None, service_name=None, **_):
        p, e = self._dms(policy_number)
        if e: return e
        pkg = p["details"].get("package", "Basic"); info = self.kb["products"]["dms"]["packages"][pkg]
        s = (service_name or "").lower()
        key = next((k for k, words in self.COVER if any(w in s for w in words)), None)
        covered = None; note = None
        if key:
            hit = [x for x in info["covered"] if key.lower() in x.lower()]
            miss = [x for x in info["not_covered"] if key.lower() in x.lower()]
            if miss and not (key == "Dental" and hit and "prosthe" not in s): covered, note = False, miss[0]
            elif hit: covered, note = True, hit[0]
        return {"covered": covered, "note": note, "package": pkg, "covered_list": info["covered"], "not_covered_list": info["not_covered"]}

    def list_clinics(self, city=None, doctor_specialty=None, **_):
        sp = spec(doctor_specialty) if doctor_specialty else None
        cl = [c for c in self.kb["clinics"] if c["city"] == city and (not sp or sp in c["specialties"])]
        return {"clinics": cl} if cl else err("not_found", f"No partner clinics in {city}")

    def resend_documents(self, policy_number=None, email=None, **_):
        p = next((p for p in self.db["policies"] if p["policy_number"] == policy_number), None)
        if not p: return err("not_found", f"Policy {policy_number} not found")
        if status_of(p) in ("expired", "cancelled"): return err("policy_inactive", f"Policy {policy_number} is {status_of(p)}")
        c = self.client(p["client_id"])
        return {"policy_number": policy_number, "sent_to": email or (c or {}).get("email")}

    def check_payment(self, client_id=None, payment_date=None, **_):
        ps = [p for p in self.db["payments"] if p["client_id"] == client_id and (not payment_date or p["date"] == payment_date)]
        if not ps: ps = [p for p in self.db["payments"] if p["client_id"] == client_id]
        if not ps: return err("not_found", "Payment not found")
        p = sorted(ps, key=lambda x: x["date"], reverse=True)[0]
        return {"payment_id": p["payment_id"], "payment_status": p["status"], "amount": p["amount"], "date": p["date"], "product": p["product"], "note": p.get("note")}

    def update_contact(self, client_id=None, contact_field=None, new_value=None, **_):
        c = self.client(client_id)
        if not c: return err("not_found", f"Client {client_id} not found")
        if contact_field == "phone":
            v = norm_phone(new_value or "")
            if not v: return err("invalid_input", "Bad phone")
        elif contact_field == "email":
            v = new_value
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v or ""): return err("invalid_input", "Bad email")
        elif contact_field == "address": v = new_value
        else: return err("invalid_input", "contact_field must be phone, email or address")
        c[contact_field] = v
        return {"updated": contact_field}

    def request_document(self, policy_number=None, document_type=None, email=None, **_):
        if not any(p["policy_number"] == policy_number for p in self.db["policies"]): return err("not_found", f"Policy {policy_number} not found")
        t = self.kb["documents_available"].get(document_type)
        if not t: return err("invalid_input", f"Unknown document {document_type}")
        return {"sent_to": email, "delivery": t}

    def get_offices(self, city=None, **_):
        o = next((o for o in self.kb["offices"] if o["city"].lower() == (city or "").lower()), None)
        return {"city": o["city"], "address": o["address"], "hours": o["hours"]} if o else err("not_found", f"No office in {city}")

    def kb_lookup(self, topic=None, **_):
        node = self.kb
        for part in (topic or "").split("."):
            if isinstance(node, dict) and part in node: node = node[part]
            else: return err("not_found", f"Topic {topic} not found")
        return {"topic": topic, "answer": node}

    def send_sms(self, phone=None, text=None, **_):
        ph = norm_phone(phone or "")
        return {"sent_to": ph} if ph else err("invalid_input", "phone required")

    def create_callback(self, phone=None, callback_time=None, **_):
        ph = norm_phone(phone or "")
        return {"callback_id": f"CB-{next(COUNTERS['callback'])}", "phone": ph, "callback_time": callback_time} if ph else err("invalid_input", "phone required")

    def create_complaint(self, complaint_text=None, **_):
        return {"ticket_id": f"T-{next(COUNTERS['ticket'])}"}

    def report_fraud(self, fraud_details=None, **_):
        return {"ticket_id": f"F-{next(COUNTERS['fraud'])}"}

    def transfer_to_operator(self, queue="operator_general", summary=None, **_):
        return {"queue": queue if queue in QUEUES else "operator_general"}

    def call(self, name, **inputs):
        fn = getattr(self, name, None)
        if fn is None or name not in kit.actions(): return err("invalid_input", f"Unknown action {name}")
        try: res = fn(**inputs)
        except Exception as e: res = err("invalid_input", f"{type(e).__name__}: {e}")
        self.log.append({"action": name, "inputs": inputs, "result": None if "error" in res else res, "error": res.get("error")})
        return res
