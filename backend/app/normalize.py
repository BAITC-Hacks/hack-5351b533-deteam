"""Детерминированная нормализация и валидация слотов по slots.json."""
import re
from . import kit

CYR2LAT = str.maketrans("АВЕКМНОРСТУХавекмнорстух", "ABEKMHOPCTYXABEKMHOPCTYX")

def phone(v: str) -> str | None:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d[0] in "78": d = "7" + d[1:]
    elif len(d) == 10: d = "7" + d
    return "+" + d if len(d) == 11 and d.startswith("7") else None

def plate(v: str) -> str:
    return re.sub(r"[\s\-]", "", (v or "").translate(CYR2LAT)).upper()

def _int(v):
    d = re.sub(r"[^\d]", "", str(v))
    return int(d) if d else None

def slot(name: str, value):
    """Вернуть нормализованное значение или None, если не проходит формат."""
    spec = kit.slots().get(name)
    if spec is None or value in (None, "", []):
        return None
    t, pat = spec["type"], spec.get("pattern")
    v = value
    if name == "phone" or (name == "new_value" and isinstance(v, str) and re.fullmatch(r"[\d\s+()\-]{10,}", v)):
        v = phone(str(v)) or (None if name == "phone" else v)
    elif name in ("vehicle_plate", "culprit_vehicle_plate"):
        v = plate(str(v))
    elif name in ("policy_number", "claim_number"):
        v = re.sub(r"\s", "", str(v)).upper()
        m = re.search(r"(\d{6})", v)
        if name == "claim_number" and m and not v.startswith("CL-"): v = "CL-" + m.group(1)
        if name == "policy_number" and not re.match(r"^SQ-", v):
            m2 = re.match(r"^SQ?-?(OGPO|CASCO|TRVL|PROP|NS|DMS)-?(\d{6})$", v)
            if m2: v = f"SQ-{m2.group(1)}-{m2.group(2)}"
    elif t == "integer":
        v = _int(v)
    elif t == "enum":
        vals = spec["values"]
        if all(isinstance(x, int) for x in vals):
            v = _int(v)
        else:
            low = {str(x).lower(): x for x in vals}
            v = low.get(str(v).strip().lower())
        if v not in vals: return None
    elif t == "list":
        items = value if isinstance(value, list) else re.split(r"[,;\s]+", str(value))
        items = [re.sub(r"\D", "", x) for x in items if x]
        v = [x for x in items if not pat or re.fullmatch(pat, x)]
        return v or None
    elif t == "boolean":
        v = str(v).strip().lower() in ("true", "yes", "да", "1", "иә")
        return v
    elif t == "date":
        v = str(v).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): return None
    else:
        v = str(v).strip()
    if v is None: return None
    if pat and t != "list" and not re.fullmatch(pat, str(v)): return None
    return v

def slots(pairs) -> dict:
    out = {}
    for p in pairs or []:
        n, v = (p["name"], p["value"]) if isinstance(p, dict) else p
        nv = slot(n, v)
        if nv is not None: out[n] = nv
    return out

# --- детерминированные продолжения (fast path) ---
YES = {"да", "ага", "верно", "всё верно", "все верно", "правильно", "оформляйте", "оформляем", "давайте", "подтверждаю", "согласен", "согласна",
       "иә", "ия", "дұрыс", "растаймын", "жарайды", "болады", "келісемін", "ок", "окей", "хорошо", "конечно", "да, давайте", "да верно"}
NO = {"нет", "не надо", "не нужно", "отмена", "не", "жоқ", "керек емес", "қажет емес", "не хочу", "потом", "давайте потом"}
BYE = {"спасибо", "спасибо, до свидания", "до свидания", "всего доброго", "рақмет", "рахмет", "сау болыңыз", "всё, спасибо", "все, спасибо",
       "нет, спасибо", "жоқ, рақмет", "отлично, спасибо", "хорошо, спасибо", "спасибо большое", "благодарю"}
OPERATOR = re.compile(r"^(соедините\s+(меня\s+)?(с\s+)?)?(оператор|человек|живой\s+человек|специалист|оператормен|адаммен)[а-яә-ү]*\W*$", re.I)

def _clean(t: str) -> str:
    return re.sub(r"[^\w\s,ӘәІіҢңҒғҮүҰұҚқӨөҺһ-]", "", t.lower()).strip(" ,.!")

def yes_no(text: str):
    c = _clean(text)
    c2 = re.sub(r"^(да|иә|нет|жоқ)[,\s]+", lambda m: m.group(1) + " ", c)
    if c in YES or c2.split(" ")[0] in ("да", "иә") and len(c.split()) <= 4 and not re.search(r"\d|но |а |и ещё|тоже|ещё|бірақ", c): return "yes"
    if c in NO or c.split(" ")[0] in ("нет", "жоқ") and len(c.split()) <= 3: return "no"
    return None

def is_bye(text: str) -> bool:
    return _clean(text) in BYE

def is_operator(text: str) -> bool:
    return bool(OPERATOR.match(_clean(text)))

def extract_pattern(slot_name: str, text: str):
    """Ожидаемый слот из реплики без LLM: телефон, ИИН, госномер, номер заявления или полиса."""
    t = text.translate(CYR2LAT).upper()
    if slot_name == "phone":
        m = re.search(r"(\+?\s*[78][\d\s\-()]{9,16}\d)", text)
        return phone(m.group(1)) if m else None
    if slot_name in ("iin", "new_driver_iin"):
        m = re.search(r"\b(\d{12})\b", re.sub(r"(?<=\d)[\s-](?=\d)", "", text)); return m.group(1) if m else None
    if slot_name in ("vehicle_plate", "culprit_vehicle_plate"):
        m = re.search(r"\b(\d{3}\s?[A-Z]{2,3}\s?\d{2})\b", t); return plate(m.group(1)) if m else None
    if slot_name == "claim_number":
        m = re.search(r"CL[\s-]?(\d{6})", t); return f"CL-{m.group(1)}" if m else None
    if slot_name == "policy_number":
        m = re.search(r"SQ[\s-]?(OGPO|CASCO|TRVL|PROP|NS|DMS)[\s-]?(\d{6})", t); return f"SQ-{m.group(1)}-{m.group(2)}" if m else None
    return None
