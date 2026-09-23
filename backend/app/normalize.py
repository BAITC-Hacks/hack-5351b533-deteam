"""Детерминированная нормализация и валидация слотов по slots.json."""
import re
from . import kit

CYR2LAT = str.maketrans("АВЕКМНОРСТУХавекмнорстух", "ABEKMHOPCTYXABEKMHOPCTYX")
KK_LETTERS = re.compile(r"[әіңғүұқөһ]", re.I)
# названия продуктов и префиксов, произнесённые/распознанные кириллицей: «СК ОГПО 104501», «си эль 500287»
CYR_IDS = [(r"\b(?:ЭС\s*КЬЮ|СКЬЮ|СК)\b", "SQ"), (r"ОГПО", "OGPO"), (r"КАСКО", "CASCO"), (r"ДМС", "DMS"), (r"ТРВЛ", "TRVL"), (r"ПРОП", "PROP"),
           (r"\bНС\b", "NS"), (r"\b(?:СИ\s*ЭЛЬ|КЛ)\b", "CL")]

def latin_ids(t: str) -> str:
    t = (t or "").upper()
    for p, r in CYR_IDS: t = re.sub(p, r, t)
    return t.translate(CYR2LAT)

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
        v = re.sub(r"\s", "", latin_ids(str(v)))
        m = re.search(r"(\d{6})", v)
        if name == "claim_number" and m and not re.fullmatch(r"CL-\d{6}", v): v = "CL-" + m.group(1)
        if name == "policy_number" and not re.fullmatch(r"SQ-(OGPO|CASCO|TRVL|PROP|NS|DMS)-\d{6}", v):
            m2 = re.search(r"(?:SQ|S)?-?(OGPO|CASCO|TRVL|PROP|NS|DMS)-?(\d{6})", v)
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
        b = str(v).strip().lower()
        if b in ("true", "yes", "да", "1", "иә", "есть", "бар"): return True
        if b in ("false", "no", "нет", "0", "жоқ"): return False
        return None                      # «unknown» и прочее — слот не заполнен
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
OPERATOR = re.compile(r"^(соедините\s+(меня\s+)?(с\s+)?)?(оператор|человек|живой\s+человек|специалист|оператормен|адаммен)\w*\W*$", re.I)

def _clean(t: str) -> str:
    return re.sub(r"[^\w\s,ӘәІіҢңҒғҮүҰұҚқӨөҺһ-]", "", t.lower()).strip(" ,.!")

def yes_no(text: str):
    c = _clean(text)
    c2 = re.sub(r"^(да|иә|нет|жоқ)[,\s]+", lambda m: m.group(1) + " ", c)
    if c in YES or c2.split(" ")[0] in ("да", "иә") and len(c.split()) <= 4 and not re.search(r"\d|но |а |и ещё|тоже|ещё|бірақ", c): return "yes"
    if c in NO or c.split(" ")[0] in ("нет", "жоқ") and len(c.split()) <= 3: return "no"
    return None

GREET = re.compile(r"^(алло|ало|здравствуйте|здрасте|добрый (день|вечер|утро)|доброе утро|привет|сәлеметсіз бе|сәлеметсіздер ме|сәлем|салем|саламатсыз ба|ассалаумағалейкум|салам)$", re.I)

def is_greeting(text: str) -> bool:
    """Только приветствие без просьбы: «Алло», «Сәлеметсіз бе.», «Здравствуйте!»."""
    t = re.sub(r"[^\w\sәіңғүұқөһ]", " ", (text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(алло|ало)\s+", "", t) if t not in ("алло", "ало") else t
    return bool(GREET.match(t))

HEARING = re.compile(r"слыш|слыхать|есті(п|ле|лі|сің|сіз)|естіліп", re.I)

def is_hearing_check(text: str) -> bool:
    """«Алло, алло, вы меня слышите?», «Мені естіп тұрсыз ба?» — проверка связи без просьбы."""
    t = (text or "").lower(); words = re.findall(r"\w+", t)
    if not HEARING.search(t) or len(words) > 9: return False
    rest = [w for w in words if not re.match(r"(алло|ало|вы|ты|меня|мене|мені|слыш|слыхать|есті|алё|але|да|нет|хорошо|плохо|там|сіз|сен|ба|бе|ма|ме|тұрсыз|тұрсың|нормально|кто|это|питер|привет|здравствуйте|сәлеметсіз)", w)]
    return len(rest) <= 1

def is_bye(text: str) -> bool:
    return _clean(text) in BYE

def is_operator(text: str) -> bool:
    return bool(OPERATOR.match(_clean(text)))

# --- числа словами (ru/kk) -> цифры, для телефона и ИИН без вызова роутера
_U = {"ноль": 0, "нуль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
      "нөл": 0, "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5, "алты": 6, "жеті": 7, "сегіз": 8, "тоғыз": 9}
_TEEN = {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
         "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_T = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
      "он": 10, "жиырма": 20, "отыз": 30, "қырық": 40, "елу": 50, "алпыс": 60, "жетпіс": 70, "сексен": 80, "тоқсан": 90}
_H = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900}
_FILLER = {"плюс", "мой", "моя", "номер", "телефон", "телефона", "иин", "это", "вот", "так", "да", "менің", "нөмірім", "нөмірі", "телефоным", "жиын", "его", "её", "ее"}

def words_to_digits(text: str):
    """«плюс семь семьсот один ноль ноль…» / «жеті жүз бір…» -> ('7701000…', число незнакомых слов)."""
    out, unknown = [], 0
    cur = None; o = 0          # o: 100/10/1 — какой разряд можно добавить дальше; 0 — группа закрыта
    def flush():
        nonlocal cur, o
        if cur is not None: out.append(str(cur))
        cur, o = None, 0
    for w in re.findall(r"\d+|[^\W\d_]+", text.lower()):
        if w.isdigit(): flush(); out.append(w); continue
        if w in _H:
            flush(); cur, o = _H[w], 100
        elif w == "жүз":
            if cur is not None and cur < 10 and o == 1: cur, o = cur * 100, 100
            else: flush(); cur, o = 100, 100
        elif w in _T:
            if cur is not None and o == 100 and cur % 100 == 0: cur, o = cur + _T[w], 10
            else: flush(); cur, o = _T[w], 10
        elif w in _TEEN:
            if cur is not None and o == 100 and cur % 100 == 0: cur += _TEEN[w]; o = 0
            else: flush(); cur, o = _TEEN[w], 0
        elif w in _U:
            v = _U[w]
            if v == 0: flush(); out.append("0"); continue
            if cur is not None and o in (100, 10) and cur % 10 == 0 and (o == 10 or cur % 100 == 0): cur += v; o = 0
            else: flush(); cur, o = v, 1
        elif w not in _FILLER: unknown += 1
    flush()
    return "".join(out), unknown

def extra_words(text: str) -> int:
    """Сколько в реплике слов, кроме чисел (цифрами или словами) и служебных слов."""
    return words_to_digits(text)[1]

def extract_pattern(slot_name: str, text: str):
    """Ожидаемый слот из реплики без LLM: телефон, ИИН, госномер, номер заявления или полиса."""
    t = latin_ids(text)
    if slot_name == "phone":
        m = re.search(r"(\+?\s*[78][\d\s\-()]{9,16}\d)", text)
        if m: return phone(m.group(1))
        dg, unk = words_to_digits(text)
        return phone(dg) if unk <= 2 and len(dg) in (10, 11) else None
    if slot_name in ("iin", "new_driver_iin"):
        m = re.search(r"\b(\d{12})\b", re.sub(r"(?<=\d)[\s-](?=\d)", "", text))
        if m: return m.group(1)
        dg, unk = words_to_digits(text)
        return dg if unk <= 2 and len(dg) == 12 else None
    if slot_name in ("vehicle_plate", "culprit_vehicle_plate"):
        m = re.search(r"\b(\d{3}\s?[A-Z]{2,3}\s?\d{2})\b", t); return plate(m.group(1)) if m else None
    t = re.sub(r"(?<=\d)[\s-](?=\d)", "", t)
    if slot_name == "claim_number":
        m = re.search(r"(?:CL)?[\s-]?\b(5\d{5})\b", t) if not re.search(r"SQ|OGPO|CASCO|DMS", t) else None
        return f"CL-{m.group(1)}" if m else None
    if slot_name == "policy_number":
        m = re.search(r"(?:SQ)?[\s-]?(OGPO|CASCO|TRVL|PROP|NS|DMS)[\s-]?(\d{6})", t); return f"SQ-{m.group(1)}-{m.group(2)}" if m else None
    return None

ROBOT = re.compile(r"\b(вы|ты)\b[\s,—-]*(ведь|же|что|всё-таки|вообще|случайно)?[\s,]*(робот|бот|автоответчик|нейросеть|искусственный интеллект|живой человек|человек|реальный человек|живой)\b"
                   r"|\bэто\s+(робот|бот|автоответчик)\b|\bс\s+кем\s+я\s+(говорю|разговариваю)|\bя\s+(говорю|разговариваю)\s+с\s+(роботом|ботом|человеком|машиной)"
                   r"|\bс\s+(роботом|ботом|автоответчиком|нейросетью|искусственным интеллектом)\s+(я\s+)?(говорю|разговариваю)"
                   r"|\b(сіз|сен)\s+(робот|бот|адам)|\bробот(сыз|сың|пен)|\bробот\s*(па|ба|ма|бе|ме)\b|\bадамсыз\s*ба|\bтірі\s+адам", re.I)

def is_robot_question(text: str) -> bool:
    """«Вы робот?» — короткий вопрос о природе собеседника (без просьбы соединить и без другой темы)."""
    return bool(ROBOT.search(text)) and len(text.split()) <= 8 and not re.search(r"соедин|оператор|переключ|позов|қос|не хочу|надоел|не буду", text, re.I)

PRODUCT_WORDS = [("ogpo", r"огпо|обязательн\w* (авто)?страх|автогражданк|міндетті"), ("casco", r"каско|casco"), ("dms", r"\bдмс\b|медицинск\w* страх|медициналық"),
                 ("travel", r"путешеств|туристическ|выезд\w* за границ|сапар"), ("property", r"квартир|жиль|\bдом[аеу]?\b|пәтер|үй"), ("accident", r"несчастн|жазатайым")]

def product_in_text(text: str):
    """Продукт, явно названный в реплике (для выбора полиса, если роутер не заполнил product_type)."""
    hits = [p for p, rx in PRODUCT_WORDS if re.search(rx, text or "", re.I)]
    return hits[0] if len(hits) == 1 else None
