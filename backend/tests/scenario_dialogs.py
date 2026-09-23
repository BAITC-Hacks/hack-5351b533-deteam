"""Сценарные многоходовые диалоги в процессе (реальные роутер и респондер, без сервера).

Запуск из backend/:
  ../.venv/bin/python -m tests.scenario_dialogs                # все диалоги
  ../.venv/bin/python -m tests.scenario_dialogs SC01 E05       # по префиксу имени
  ../.venv/bin/python -m tests.scenario_dialogs -v --tag edge  # с транскриптами, только edge
Печатает таблицу диалог -> PASS/FAIL, для упавших транскрипт и причины. JSON-отчёт: --out path.
"""
import asyncio, json, re, sys, time, argparse
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import kit                      # noqa: E402
from app.session import Session          # noqa: E402
from app.dialog import process_turn      # noqa: E402

IRREV = {n for n, a in kit.actions().items() if a["irreversible"]}
try:   # контракт событий WS (как в tools/validate_contracts.py)
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    _SCH = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in (Path(__file__).resolve().parents[2] / "contracts").glob("*.schema.json")}
    _REG = Registry().with_resources([(x["$id"], Resource.from_contents(x)) for x in _SCH.values()])
    EV_VALIDATOR = Draft202012Validator({"$ref": _SCH["ws-events.schema.json"]["$id"]}, registry=_REG)
except Exception:
    EV_VALIDATOR = None
KK_CHARS = re.compile(r"[әіңғүұқөһӘІҢҒҮҰҚӨҺ]")
EMAILS = {c["email"] for c in kit.mock_backend()["clients"]}
# идентификаторы, которые можно (и нужно) произносить как есть
ALLOWED_DIGITS = [r"SQ-[A-Z]+-\d{6}", r"\bCL-\d{6}", r"\b[A-Z]{1,2}-\d{5,6}", r"\b\d{3}\s?[A-Z]{2,3}\s?\d{2}\b", r"\+7[\d\s\-()]{10,16}\d", r"\b\d{12}\b",
                  r"\S+@\S+", r"\b\d\*+\d{4}\b", r"\d{2}\*+\d{4}"]
YES_RE = re.compile(r"^(да|иә|ия|верно|дұрыс|подтверждаю|растаймын|оформляйте|давайте|ок)", re.I)


@dataclass
class T:
    text: str
    sc: str | None = None          # ожидаемый первый сценарий хода
    dec: str | None = None         # ожидаемое решение: run/continue/confirm/cancel/clarify/out_of_scope/goodbye/handoff
    lang: str | None = None        # ожидаемый язык ответа (по умолчанию язык диалога)
    says: str | None = None        # regex, обязателен в ответе (без учёта регистра)
    not_says: str | None = None    # regex, запрещён в ответе
    kind: str | None = None        # хотя бы один item этого вида (ask/preview/offer/done/handoff/deferred/offer_return/...)
    slot: str | None = None        # бот спрашивает этот слот


@dataclass
class D:
    name: str
    turns: list
    phone: str | None = None       # caller_phone (АОН)
    lang: str = "ru"
    answers: dict = field(default_factory=dict)   # автоответы после сценария: слот -> реплика; confirm / offer
    auto: bool = True
    acts: list = field(default_factory=list)      # должны выполниться успешно
    no_acts: list = field(default_factory=list)   # не должны выполниться
    facts: dict = field(default_factory=dict)     # key -> value где-то в facts
    handoff: str | None = None                    # ожидаемая очередь перевода
    completed: list = field(default_factory=list) # сценарии в completed
    client: str | None = None                     # ожидаемый client_id в конце
    stack: list = field(default_factory=list)     # сценарии, которые должны лежать в стеке (отложены)
    summary_has: str | None = None                # regex: резюме для оператора (handoff_summary) содержит
    tag: str = "happy"
    note: str = ""


def D_(name, *turns, **kw): return D(name, list(turns), **kw)


# ============================================================ happy path, 40 сценариев
HAPPY = [
    D_("SC01 OGPO quote", T("Сколько стоит ОГПО на легковую машину, она зарегистрирована в Алматы?", sc="SC01", slot="drivers_iin"),
       T("Буду ездить только я, мой ИИН 780115400457", dec="continue", says="тридцать восемь тысяч"),
       acts=["get_bm_class", "calc_ogpo_price"], facts={"price_kzt": 38000}),
    D_("SC02 OGPO purchase", T("Хочу оформить полис ОГПО на машину 482KMA02", sc="SC02"),
       answers={"drivers_iin": "Водитель один, ИИН 910512300456", "phone": "плюс семь семьсот семь, сто двадцать три, сорок пять, шестьдесят семь"},
       acts=["calc_ogpo_price", "create_policy", "send_sms"], facts={"price_kzt": 38000}),
    D_("SC03 CASCO quote", T("Сколько будет стоить каско на тойоту девятнадцатого года, она стоит примерно двенадцать миллионов, франшиза пятьдесят тысяч", sc="SC03",
                              says="пятьсот сорок тысяч"),
       acts=["calc_casco_price"], facts={"price_kzt": 540000}),
    D_("SC04 add driver", T("Хочу вписать жену в полис ОГПО", sc="SC04"), phone="+77010000001",
       answers={"new_driver_iin": "Её ИИН 920607400233", "policy_number": "SQ-OGPO-104501"},
       acts=["get_bm_class", "update_policy"], facts={"extra_premium_kzt": 3800}),
    D_("SC05 change plate", T("Я поменял машину, нужно заменить госномер в полисе ОГПО на 123ABC01", sc="SC05"), phone="+77010000011",
       answers={"vehicle_plate": "123ABC01", "policy_number": "SQ-OGPO-105120"}, acts=["update_policy"]),
    D_("SC06 travel purchase", T("Нужна страховка в Турцию с десятого по шестнадцатое октября, летим вдвоём", sc="SC06"),
       T("Старшему сорок два года", dec="continue", kind="offer", says="пятнадцать тысяч четыреста"),
       answers={"offer": "Да, оформляйте", "phone": "плюс 7 702 345 67 89"},
       acts=["calc_travel_price", "create_policy", "send_sms"], facts={"price_kzt": 15400}),
    D_("SC07 home quote", T("Сколько стоит застраховать квартиру на десять миллионов?", sc="SC07", says="двадцать пять тысяч"),
       acts=["calc_property_price"], facts={"price_kzt": 25000}),
    D_("SC08 accident quote", T("Сколько стоит страховка от несчастного случая на три миллиона?", sc="SC08", says="пятнадцать тысяч"),
       acts=["calc_accident_price"], facts={"price_kzt": 15000}),
    D_("SC09 DMS individual", T("Хочу купить себе медицинскую страховку, не через работу. Что у вас есть?", sc="SC09"), acts=["kb_lookup"]),
    D_("SC10 corporate", T("Хотим застраховать сотрудников нашей компании по ДМС", sc="SC10"), phone="+77071112233",
       answers={"company_name": "ТОО Альфа Строй", "employees_count": "Около пятидесяти человек", "phone": "+77071112233"},
       acts=["transfer_to_operator"], handoff="corporate_sales"),
    D_("SC11 accident now", T("Я только что попал в аварию, стою на дороге, что делать?", sc="SC11", slot="injured"),
       T("Нет, все целы, только машины помяты", dec="continue"), phone="+77010000001",
       answers={"location": "На Абая, возле Достыка"}, acts=["kb_lookup"], completed=["SC11"]),
    D_("SC12 victim claim", T("Три дня назад в меня въехал другой водитель, он виноват, его страховка у вас", sc="SC12"),
       answers={"culprit_vehicle_plate": "777ABC02", "phone": "8 701 555 12 34", "incident_description": "Въехал в меня сзади на светофоре",
                "incident_date": "три дня назад"},
       acts=["get_policy", "create_claim", "send_sms"]),
    D_("SC13 CASCO claim", T("Хочу подать заявление по каско, вчера мне поцарапали машину на парковке", sc="SC13"), phone="+77010000001",
       answers={"incident_description": "поцарапали дверь на парковке", "incident_date": "вчера"},
       acts=["create_claim", "send_sms"]),
    D_("SC14 property claim", T("Соседи сверху затопили мою квартиру позавчера, хочу заявить страховой случай", sc="SC14"), phone="+77010000004",
       answers={"incident_description": "затопили соседи сверху", "incident_date": "позавчера"}, acts=["create_claim", "send_sms"]),
    D_("SC15 medical abroad", T("Я сейчас в Турции, у меня высокая температура, что делать?", sc="SC15"), phone="+77010000006",
       answers={"location": "Анталья"}, acts=["transfer_to_operator"], handoff="medical_assistance_24_7"),
    D_("SC16 accident injury claim", T("Я сломал руку, упал на улице, у меня страховка от несчастных случаев, хочу выплату", sc="SC16"),
       answers={"phone": "плюс 7 701 000 00 05", "policy_number": "у меня нет номера под рукой", "iin": "950430300569"},
       no_acts=["create_claim"], handoff="operator_general", note="ни у одного клиента в моках нет полиса НС: спросить номер полиса, затем оператор, без create_claim"),
    D_("SC17 claim status", T("Что с моим заявлением CL-500330?", sc="SC17", says="девят"), acts=["get_claim"], facts={"status": "under_review"}),
    D_("SC18 claim documents", T("Какие документы нужны для заявления, если в меня врезались и виновник застрахован по ОГПО?", sc="SC18"),
       answers={"product_type": "ОГПО"}, acts=[]),
    D_("SC19 dispute", T("Мне одобрили выплату, но сумма слишком маленькая, я не согласен с решением", sc="SC19"), phone="+77010000005",
       answers={"complaint_text": "СТО оценило ремонт в восемьсот тридцать тысяч, а одобрили только четыреста двенадцать"},
       acts=["get_claim", "create_dispute"]),
    D_("SC20 inspection", T("Хочу записаться на осмотр машины по заявлению CL-500330 на понедельник пятого октября", sc="SC20"), phone="+77010000007",
       answers={"city": "Павлодар", "preferred_date": "пятого октября"}, acts=["book_inspection"]),
    D_("SC21 doctor appointment", T("Запишите меня к терапевту на завтра", sc="SC21"), phone="+77010000002",
       answers={"doctor_specialty": "терапевт", "preferred_date": "завтра", "city": "Астана"}, acts=["book_appointment"]),
    D_("SC22 DMS coverage", T("МРТ покрывается моей медицинской страховкой?", sc="SC22"), phone="+77010000002",
       acts=["check_coverage"], facts={"covered": True}),
    D_("SC23 clinics", T("Какие клиники-партнёры есть в Шымкенте?", sc="SC23", says="Emdeu|Эмдеу"), acts=["list_clinics"]),
    D_("SC24 DMS e-card", T("Не могу найти электронную карту ДМС, где её взять?", sc="SC24"), phone="+77010000002", acts=["send_sms"]),
    D_("SC25 policy validity", T("Мой полис ещё действует?", sc="SC25", says="четырнадцат|февраля"), phone="+77010000008", acts=["get_policies"]),
    D_("SC26 resend", T("Полис оплатил, а на почту ничего не пришло", sc="SC26", says=r"r\*+@|\*\*\*"), phone="+77010000009",
       acts=["resend_documents"]),
    D_("SC27 renewal", T("Хочу продлить каско", sc="SC27"), phone="+77010000007", acts=["renew_policy", "send_sms"]),
    D_("SC28 cancel", T("Продала машину, хочу расторгнуть каско и вернуть деньги", sc="SC28", kind="preview", says="сто шестьдесят три тысячи восемьсот"),
       phone="+77010000010", answers={"cancel_reason": "продала машину"}, acts=["cancel_policy"], facts={"refund_kzt": 163800}),
    D_("SC29 update email", T("Хочу поменять электронную почту на arman.new@mail.example", sc="SC29"), phone="+77010000001",
       answers={"contact_field": "почту", "new_value": "arman.new@mail.example"}, acts=["update_contact"]),
    D_("SC30 charged no policy", T("С карты списали деньги за полис, а полис так и не оформили", sc="SC30"), phone="+77010000003",
       answers={"payment_date": "вчера"}, acts=["check_payment", "transfer_to_operator"], handoff="operator_general"),
    D_("SC31 payment methods", T("Можно ли оплатить каско в рассрочку?", sc="SC31", says="дв|четыр"), acts=["kb_lookup"]),
    D_("SC32 bonus-malus", T("Почему у меня такая цена на ОГПО, какой у меня класс бонус-малус?", sc="SC32"), phone="+77010000009",
       acts=["get_bm_class"], facts={"bm_class": "10"}),
    D_("SC33 office", T("Где ваш офис в Караганде и до скольки он работает?", sc="SC33"), acts=["get_offices"]),
    D_("SC34 app help", T("Не приходит СМС-код для входа в приложение", sc="SC34"), acts=["kb_lookup"]),
    D_("SC35 complaint", T("Хочу пожаловаться: ваш сотрудник в офисе мне нагрубил", sc="SC35"),
       answers={"complaint_text": "Сотрудник в офисе на Абая нагрубил и отказался принять документы"}, acts=["create_complaint"]),
    D_("SC36 callback", T("Перезвоните мне, пожалуйста, завтра после обеда", sc="SC36"), phone="+77071234567",
       answers={"callback_time": "завтра после обеда", "phone": "+77071234567"}, acts=["create_callback"]),
    D_("SC37 operator", T("Соедините меня с оператором", sc="SC37"), acts=["transfer_to_operator"], handoff="operator_general"),
    D_("SC38 fraud", T("Мне звонили якобы из Saqta и просили назвать код из СМС для переоформления страховки", sc="SC38", kind="offer"),
       T("Нет, не сообщил, сразу положил трубку", kind="done"), acts=["report_fraud"], no_acts=["transfer_to_operator"]),
    D_("SC39 embassy certificate", T("Нужна справка о страховке для посольства на английском", sc="SC39", says=r"\*\*\*|почт"), phone="+77010000006",
       answers={"document_type": "справка для посольства"}, acts=["request_document"]),
    D_("SC40 terms", T("Покрывает ли каско ущерб, если за рулём был водитель, не вписанный в полис?", sc="SC40"), acts=["kb_lookup"]),
]

# ============================================================ edge cases
EDGE = [
    D_("E01 unknown phone -> re-ask -> IIN -> operator", T("Хочу узнать статус моего заявления", sc="SC17"),
       T("Плюс 7 701 999 99 99", dec="continue", slot="phone"), T("Плюс 7 701 888 88 88", dec="continue", slot="iin"),
       T("111122223333", dec="continue", kind="handoff"), handoff="operator_general", auto=False, tag="edge"),
    D_("E02 expired OGPO validity -> renewal", T("Мой полис ОГПО ещё действует?", sc="SC25", says="истёк|закончил|заверш|не действует|недейств|истек|просроч"),
       T("Тогда продлите его", sc="SC27", kind="handoff"), phone="+77010000003", no_acts=["renew_policy"], acts=["check_payment"], handoff="operator_general",
       auto=False, tag="edge", note="C003 SQ-OGPO-102850 истёк 2026-09-29; продление уже оплачено (P-3001), но не выпущено -> специалист, без второй оплаты"),
    D_("E03 CASCO too old -> Lite", T("Сколько стоит каско на машину две тысячи тринадцатого года, стоит пять миллионов?", sc="SC03", says="Lite|Лайт|угон"),
       acts=["calc_casco_price"], facts={"lite_price_kzt": 130000}, tag="edge"),
    D_("E04 traveler over 75 -> operator", T("Нужна страховка в Грузию с пятого по двенадцатое октября, еду с мамой, ей семьдесят восемь лет", sc="SC06"),
       answers={"travelers_count": "двое", "traveler_max_age": "семьдесят восемь"}, no_acts=["create_policy"], handoff="operator_general", tag="edge"),
    D_("E05 DMS not covered", T("Имплантация зубов покрывается по моей страховке ДМС?", sc="SC22"), phone="+77010000002",
       acts=["check_coverage"], facts={"covered": False}, tag="edge"),
    D_("E06 Sunday appointment -> alternatives", T("Запишите меня к лору на воскресенье, четвёртое октября", sc="SC21", slot="preferred_date", says="понедельник|пят"),
       T("Хорошо, давайте в понедельник", dec="continue", kind="preview"), phone="+77010000002",
       acts=["book_appointment"], tag="edge"),
    D_("E07 Saturday inspection Pavlodar -> alternatives", T("Запишите на осмотр машины по заявлению CL-500330 на субботу третьего октября", sc="SC20",
                                                          slot="preferred_date", says="понедельник|пят|шест"),
       phone="+77010000007", answers={"preferred_date": "пятого октября"}, acts=["book_inspection"], tag="edge"),
    D_("E08 multi-intent urgent first", T("Я только что попал в аварию! И ещё хотел узнать, сколько стоит каско", sc="SC11", slot="injured"),
       T("Нет, никто не пострадал", dec="continue", kind="offer_return"), T("Да, давайте про каско", sc="SC03"),
       auto=False, tag="edge", acts=["kb_lookup"]),
    D_("E09 topic switch and return", T("Хочу добавить водителя в полис ОГПО", sc="SC04", slot="new_driver_iin"),
       T("Кстати, а где ваш офис в Алматы?", sc="SC33", kind="offer_return"), T("Да, давайте вернёмся", sc="SC04", slot="new_driver_iin"),
       T("920607400233", dec="continue", kind="preview"), T("Да", dec="confirm"), phone="+77010000001", acts=["get_offices", "update_policy"], tag="edge"),
    D_("E10 language switch kk->ru", T("Сәлеметсіз бе, менің полисім әлі жарамды ма?", sc="SC25", lang="kk"),
       T("Давайте лучше на русском. До какого числа он действует?", lang="ru"), phone="+77010000008", lang="kk", auto=False, tag="edge"),
    D_("E11 mixed -> kk", T("Сәлеметсіз бе, маған КАСКО керек, машина двадцатого года, стоит восемь миллионов, қанша болады?", sc="SC03", lang="kk"),
       lang="kk", facts={"price_kzt": 400000}, tag="edge"),
    D_("E12 are you a robot (idle)", T("Вы робот?", says="виртуальн|искусствен|ИИ|бот|ассистент|помощник", not_says="не помогу|человек[^а-я]"), auto=False, tag="edge"),
    D_("E13 are you a robot (mid-dialog)", T("Сколько стоит ОГПО на машину в Алматы?", sc="SC01", slot="drivers_iin"),
       T("Подождите, а вы робот?", says="виртуальн|искусствен|ИИ|бот|ассистент|помощник", not_says="не помогу"),
       T("Ладно. ИИН 780115400457", says="тридцать восемь тысяч"), acts=["calc_ogpo_price"], auto=False, tag="edge"),
    D_("E14 out of scope", T("Можно у вас взять кредит на машину?", dec="out_of_scope"), no_acts=["create_policy"], auto=False, tag="edge"),
    D_("E15 unclear -> clarify -> resolved", T("Здравствуйте, у меня вопрос по страховке", dec="clarify"), T("Хочу узнать, действует ли мой полис", sc="SC25"),
       phone="+77010000008", acts=["get_policies"], tag="edge"),
    D_("E16 confirmation no -> deferred", T("Продала машину, хочу расторгнуть каско", sc="SC28"), phone="+77010000010",
       answers={"cancel_reason": "продала машину", "confirm": "Нет, пока не надо, я подумаю"}, no_acts=["cancel_policy"], stack=["SC28"], tag="edge"),
    D_("E17 offer_next SC01->SC02", T("Сколько стоит ОГПО на легковушку в Алматы? Водитель один, ИИН 780115400457", sc="SC01", says="тридцать восемь тысяч"),
       T("Да, оформляйте", sc="SC02"),
       answers={"vehicle_plate": "482KMA02", "phone": "плюс 7 707 123 45 67", "drivers_iin": "780115400457"},
       acts=["create_policy"], facts={"price_kzt": 38000}, tag="edge"),
    D_("E18 offer_next SC13->SC20", T("Вчера во дворе мне помяли машину, хочу подать заявление по каско", sc="SC13"), phone="+77010000001",
       answers={"incident_description": "помяли бампер во дворе", "incident_date": "вчера", "next": "Да, запишите на осмотр", "preferred_date": "в понедельник пятого октября",
                "city": "Алматы"},
       acts=["create_claim", "book_inspection"], tag="edge", note="после create_claim ответ 'да' на предложение осмотра запускает SC20"),
    D_("E19 offer_next SC38->SC25", T("Мне сейчас звонили, сказали что из Saqta, просили код из СМС", sc="SC38"),
       T("Нет, ничего не говорил", kind="done"), T("Да, проверьте", sc="SC25"), phone="+77010000008",
       acts=["report_fraud", "get_policies"], auto=False, tag="edge"),
    D_("E20 phone in Kazakh words", T("Полисім жарамды ма екенін білгім келеді", sc="SC25", lang="kk"),
       T("Плюс жеті, жеті жүз бір, нөл нөл нөл, нөл нөл, он бір", lang="kk"), lang="kk", client="C011", acts=["find_client"], auto=False, tag="edge"),
    D_("E21 policy number with Cyrillic letters", T("Проверьте, пожалуйста, действует ли полис СК ОГПО 104501", sc="SC25"), acts=["get_policy"],
       facts={"status": "active"}, tag="edge"),
    D_("E22 culprit plate with Cyrillic letters", T("Позавчера в меня врезался водитель, он виноват, его госномер 777 АВС 02", sc="SC12"),
       answers={"phone": "плюс 7 701 555 12 34", "incident_description": "врезался в бок на перекрёстке", "incident_date": "позавчера",
                "culprit_vehicle_plate": "777 АВС 02"},
       acts=["get_policy", "create_claim"], tag="edge"),
    D_("E23 kk full claim status", T("Сәлеметсіз бе, өтінішімнің жағдайы қандай?", sc="SC17", lang="kk"),
       T("CL-500287", lang="kk"), lang="kk", acts=["get_claim"], auto=False, tag="edge"),
    D_("E24 operator mid-dialog passes context", T("Хочу расторгнуть каско", sc="SC28"), T("Соедините с оператором", sc="SC37", kind="handoff"),
       phone="+77010000010", acts=["transfer_to_operator"], no_acts=["cancel_policy"], summary_has="SQ-CASCO-204350", auto=False, tag="edge"),
    D_("E25 goodbye", T("Сколько стоит квартиру застраховать на пять миллионов?", sc="SC07"), T("Спасибо, до свидания", dec="goodbye"),
       auto=False, tag="edge", facts={"price_kzt": 15000}),
]
EDGE += [
    D_("E26 kk cancel CASCO, phone in words", T("Сәлеметсіз бе, көлігімді саттым, КАСКО шартын бұзып, қалған ақшаны қайтарғым келеді", sc="SC28", lang="kk"),
       T("Телефон: плюс жеті, жеті жүз бір, нөл нөл нөл, нөл нөл, он", lang="kk", kind="preview", says="жүз алпыс үш мың сегіз жүз"),
       T("Иә, растаймын", dec="confirm", lang="kk"), lang="kk", acts=["cancel_policy"], facts={"refund_kzt": 163800}, client="C010", auto=False, tag="edge"),
    D_("E27 kk DMS appointment by caller id", T("Ертең терапевтке жазылғым келеді", sc="SC21", lang="kk"), phone="+77010000002", lang="kk",
       answers={"confirm": "Иә, жазыңыз", "doctor_specialty": "терапевт", "preferred_date": "ертең"}, acts=["book_appointment"], tag="edge"),
    D_("E28 two normal intents", T("Хочу продлить каско и узнать, можно ли оплатить в рассрочку", sc="SC27"), phone="+77010000007",
       acts=["kb_lookup"], no_acts=["renew_policy"], auto=False, tag="edge"),
    D_("E29 kk robot question", T("Сіз роботсыз ба?", lang="kk", says="виртуалды|жасанды"), lang="kk", auto=False, tag="edge"),
    D_("E30 out of scope mid-dialog -> reprompt", T("Сколько стоит ОГПО на машину в Алматы?", sc="SC01", slot="drivers_iin"),
       T("А какая завтра погода в Алматы?", dec="out_of_scope", says="ИИН|за рул"), T("Ладно, мой ИИН 780115400457", says="тридцать восемь тысяч"),
       acts=["calc_ogpo_price"], auto=False, tag="edge"),
    D_("E31 claim number not found twice -> operator", T("Какой статус у заявления CL-999999?", sc="SC17"), T("CL-888888", kind="handoff"),
       handoff="operator_general", auto=False, tag="edge"),
    D_("E32 SC15 unknown caller -> one question -> medical", T("Я в Египте, ребёнку плохо, высокая температура!", sc="SC15", slot="phone"),
       T("Плюс 7 777 123 45 67", kind="handoff"), handoff="medical_assistance_24_7", auto=False, tag="edge"),
    D_("E33 fraud with shared codes -> security", T("Мне звонили из якобы Saqta, я продиктовал им код из СМС", sc="SC38"),
       answers={"offer": "Да, уже сказал код", "fraud_details": "звонили якобы из Saqta, попросили код из смс, я продиктовал"},
       acts=["report_fraud", "transfer_to_operator"], handoff="security_team", tag="edge"),
    D_("E34 accident with injured -> claims team", T("Только что попал в аварию, пассажир ранен!", sc="SC11"), phone="+77010000001",
       answers={"injured": "Да, пассажир ранен"}, handoff="claims_team", acts=["transfer_to_operator"], tag="edge"),
    D_("E35 CASCO theft -> claim then claims team", T("У меня вчера угнали машину, хочу заявить по каско", sc="SC13"), phone="+77010000001",
       answers={"incident_description": "угнали машину со двора", "incident_date": "вчера"}, acts=["create_claim", "transfer_to_operator"], handoff="claims_team",
       tag="edge"),
    D_("E36 update phone spoken in words", T("Хочу поменять номер телефона в вашей базе", sc="SC29"), phone="+77010000004",
       answers={"contact_field": "телефон", "new_value": "плюс семь семьсот семь, сто двадцать три, сорок пять, шестьдесят семь"}, acts=["update_contact"], tag="edge"),
    D_("E37 kk travel quote and purchase", T("Сәлеметсіз бе, Түркияға он бірінші қазаннан он жетінші қазанға дейін екеуміз барамыз, сақтандыру керек", sc="SC06", lang="kk"),
       lang="kk", answers={"traveler_max_age": "Үлкеніміз қырық екі жаста", "offer": "Иә, рәсімдеңіз", "phone": "плюс жеті, жеті жүз бір, бес жүз елу бес, он екі, отыз төрт",
                           "confirm": "Иә, растаймын"},
       acts=["create_policy"], facts={"price_kzt": 15400}, tag="edge"),
]
EDGE += [
    D_("E38 Sunday -> 'да' takes nearest slot", T("Запишите меня к терапевту на воскресенье", sc="SC21", slot="preferred_date"),
       T("Да, подходит", kind="preview", dec="continue"), T("Да", dec="confirm"), phone="+77010000002", acts=["book_appointment"], auto=False, tag="edge"),
    D_("E39 robot question during confirmation", T("Хочу расторгнуть каско, машину продала", sc="SC28", kind="preview"),
       T("Вы робот?", says="виртуальн", dec="out_of_scope"), T("Нет, не надо расторгать", dec="cancel"), phone="+77010000010",
       no_acts=["cancel_policy"], auto=False, tag="edge"),
]
EDGE += [
    D_("E40 robot question after imperative ask -> reprompt", T("Хочу добавить водителя в полис ОГПО", sc="SC04", slot="new_driver_iin"),
       T("Секунду, а я с роботом говорю?", says="виртуальн", dec="out_of_scope"), T("Понял. 920607400233", kind="preview"),
       phone="+77010000001", auto=False, tag="edge"),
]
ALL = HAPPY + EDGE


# ============================================================ проверки
def _facts_iter(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _facts_iter(v)
    elif isinstance(o, list):
        for x in o: yield from _facts_iter(x)


def text_issues(r: str, lang: str, multi: bool = False) -> list:
    out = []
    if not r.strip(): return ["empty response"]
    sents = [x for x in re.split(r"(?<=[.!?…])\s+", r.strip()) if x.strip()]
    if len(sents) > 3: out.append(f"long: {len(sents)} sentences")
    if len(r.split()) > (60 if multi else 45): out.append(f"long: {len(r.split())} words")
    nq = sum(x.rstrip().endswith("?") for x in sents)
    if nq > 1: out.append(f"{nq} questions")
    has_kk = bool(KK_CHARS.search(r))
    if lang == "kk" and not has_kk: out.append("lang: expected kk")
    if lang == "ru" and has_kk: out.append("lang: expected ru")
    s = r
    for p in ALLOWED_DIGITS: s = re.sub(p, " ", s)
    if re.search(r"\d", s): out.append(f"digits: {re.findall(r'[^ ]*\d[^ ]*', s)[:3]}")
    for e in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", r):
        if "*" not in e and "saqta-insurance" not in e: out.append(f"email not masked: {e}")
    return out


@dataclass
class Res:
    name: str
    tag: str
    fails: list
    log: list
    turns: int
    secs: float
    note: str = ""


async def run_dialog(d: D, sem: asyncio.Semaphore) -> Res:
    async with sem:
        t0 = time.perf_counter()
        s = Session(channel="text", caller_phone=d.phone, lang=d.lang)
        items_log = []; orig = s.execute
        def ex(u):
            it = orig(u); items_log.append(it); return it
        s.execute = ex
        fails, log, traces, by_turn = [], [], [], {}
        cur_lang = d.lang
        events = []
        async def collect(e): events.append(e)

        async def say(text, t: T | None = None):
            nonlocal cur_lang
            try:
                tr = await asyncio.wait_for(process_turn(s, text, emit=collect), 90)
            except Exception as e:
                fails.append(f"turn {s.turn_no}: exception {type(e).__name__}: {e}"); return None
            items = items_log[-1] if items_log else []
            traces.append((text, tr, items)); by_turn[tr["turn"]] = (text, tr, items)
            if t and t.lang: cur_lang = t.lang
            r = tr["response_text"]
            top = tr["scenarios"][0]["scenario_id"] if tr["scenarios"] else None
            log.append(f"  C: {text}\n     -> dec={tr["decision"]} top={top} fp={tr["fast_path"]} slots={tr["slots"]} acts={tr["actions"]} items={[(i.get('kind'), i.get('scenario'), i.get('slot')) for i in items]}\n  B[{tr['response_language']}]: {r}")
            multi = len([i for i in items if i.get("kind") not in ("deferred", "offer_return")]) > 1
            for iss in text_issues(r, cur_lang, multi): fails.append(f"turn {tr['turn']}: {iss}")
            if t:
                if t.sc and top != t.sc: fails.append(f"turn {tr['turn']}: scenario {top} != {t.sc}")
                if t.dec and tr["decision"] != t.dec: fails.append(f"turn {tr['turn']}: decision {tr['decision']} != {t.dec}")
                if t.says and not re.search(t.says, r, re.I): fails.append(f"turn {tr['turn']}: response lacks /{t.says}/")
                if t.not_says and re.search(t.not_says, r, re.I): fails.append(f"turn {tr['turn']}: response has forbidden /{t.not_says}/")
                if t.kind and not any(i.get("kind") == t.kind for i in items): fails.append(f"turn {tr['turn']}: no item kind={t.kind}")
                if t.slot and not any(i.get("kind") == "ask" and i.get("slot") == t.slot for i in items):
                    fails.append(f"turn {tr['turn']}: bot did not ask {t.slot}")
            return tr

        for t in d.turns:
            await say(t.text, t)
            if s.ended: break
        n = 0; used_next = False
        while d.auto and not s.ended and n < 8:
            a = s.active if s.active and s.active.status == "active" else None
            if not a:
                if (s.offer_next or s.offer_return) and "next" in d.answers and not used_next:
                    used_next = True; await say(d.answers["next"]); n += 1; continue
                break
            if a.awaiting == "slot":
                ans = d.answers.get(a.expected_slot)
                if ans is None: fails.append(f"unexpected ask {a.sid}.{a.expected_slot}"); break
            elif a.awaiting == "confirmation": ans = d.answers.get("confirm", "Да, подтверждаю")
            elif a.awaiting == "offer": ans = d.answers.get("offer", "Да")
            else: break
            await say(ans); n += 1
        # --- проверки диалога
        ok_acts = [x["action"] for x in s.backend.log if not x["error"]]
        for a in d.acts:
            if a not in ok_acts: fails.append(f"action {a} not executed (ok: {ok_acts})")
        for a in d.no_acts:
            if a in ok_acts: fails.append(f"forbidden action {a} executed")
        if d.handoff and (not s.handoff or s.handoff["queue"] != d.handoff):
            fails.append(f"handoff {s.handoff and s.handoff['queue']} != {d.handoff}")
        for sid in d.completed:
            if sid not in s.completed: fails.append(f"{sid} not completed ({s.completed})")
        if d.client and (s.client or {}).get("client_id") != d.client: fails.append(f"client {(s.client or {}).get('client_id')} != {d.client}")
        if EV_VALIDATOR:
            errs = [f"{e.get('type')}{list(x.absolute_path)}: {x.message[:120]}" for e in events for x in EV_VALIDATOR.iter_errors({"t": 0, "session_id": s.id, **e})]
            for x in errs[:3]: fails.append(f"contract: {x}")
        if d.summary_has:
            from app.responder import handoff_summary
            hs = handoff_summary(s, traces[-1][0] if traces else "")
            if not re.search(d.summary_has, hs): fails.append(f"handoff summary lacks /{d.summary_has}/: {hs}")
            log.append(f"  [handoff summary] {hs}")
        for sid in d.stack:
            if sid not in [f.sid for f in s.stack]: fails.append(f"{sid} not in stack ({[f.sid for f in s.stack]})")
        allf = [kv for _, _, items in traces for it in items for kv in _facts_iter(it.get("facts") or {})]
        for k, v in d.facts.items():
            if not any(fk == k and fv == v for fk, fv in allf):
                seen = [fv for fk, fv in allf if fk == k]
                fails.append(f"fact {k}={v} not found (seen {seen[:3]})")
        # необратимые: только после preview и явного «да»
        previews = {}
        for e in s.events:
            if e["type"] == "action.preview": previews.setdefault(e["action"], e["turn"])
            if e["type"] == "action.executed" and e["action"] in IRREV and not e.get("error"):
                pt = previews.get(e["action"])
                text, tr, _ = by_turn.get(e["turn"], ("?", {"decision": "?"}, None))
                if pt is None or pt >= e["turn"]: fails.append(f"IRREVERSIBLE {e['action']} executed without earlier preview")
                elif tr["decision"] != "confirm" or not YES_RE.search(text.strip()):
                    fails.append(f"IRREVERSIBLE {e['action']} executed on non-yes turn: '{text}' dec={tr['decision']}")
        return Res(d.name, d.tag, fails, log, s.turn_no, round(time.perf_counter() - t0, 1), d.note)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("-v", action="store_true", help="транскрипты всех диалогов")
    ap.add_argument("--tag", default=None)
    ap.add_argument("-j", type=int, default=10, help="параллельных диалогов")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ds = [d for d in ALL if (not a.names or any(d.name.startswith(n) for n in a.names)) and (not a.tag or d.tag == a.tag)]
    sem = asyncio.Semaphore(a.j); t0 = time.perf_counter()
    res = await asyncio.gather(*[run_dialog(d, sem) for d in ds])
    for r in res:
        if a.v or r.fails:
            print(f"\n=== {r.name} [{'PASS' if not r.fails else 'FAIL'}] {r.note}")
            print("\n".join(r.log))
            for f in r.fails: print(f"  !! {f}")
    print(f"\n{'dialog':<52} {'tag':<6} {'res':<5} turns  secs")
    for r in res:
        print(f"{r.name[:52]:<52} {r.tag:<6} {'PASS' if not r.fails else 'FAIL':<5} {r.turns:>5} {r.secs:>5}" + ("" if not r.fails else f"  {r.fails[0][:90]}"))
    npass = sum(not r.fails for r in res)
    print(f"\n{npass}/{len(res)} passed in {time.perf_counter() - t0:.0f}s")
    if a.out:
        Path(a.out).write_text(json.dumps([r.__dict__ for r in res], ensure_ascii=False, indent=1), encoding="utf-8")
    return npass == len(res)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
