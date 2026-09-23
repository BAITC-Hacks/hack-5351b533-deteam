"""Auto-bench с эволюцией каталога (docs/concept/EVOLUTION.md).
Кейсы (regression/cases.jsonl) -> патч каталога от gpt-6-sol (строгая схема + валидатор + анти-хардкод) -> регрессия до/после
на временной версии каталога -> применение супервизором (новая версия, hot reload роутера) / отклонение.
REST: /api/cases, /api/cases/generate, /api/bench, /api/bench/runs, /api/patches.
События /ws/supervisor: case.created, bench.progress, bench.finished, patch.progress, patch.ready, catalog.changed."""
import asyncio, json, os, re, time, difflib, random, copy
from collections import Counter
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from openai import AsyncOpenAI
from . import kit, config, bench
from .bench import now_iso, is_ok
from .catalog import CATALOG, field_get, visible_examples, _hash
from .hub import HUB
from . import router as rt

router = APIRouter()
client = AsyncOpenAI()

REG = config.REGRESSION_DIR; RUNS_DIR = REG / "runs"; PATCH_DIR = REG / "patches"
for _d in (REG, RUNS_DIR, PATCH_DIR): _d.mkdir(parents=True, exist_ok=True)
SC_IDS = [s["scenario_id"] for s in kit.base_catalog()["scenarios"]]
ALL_IDS = SC_IDS + kit.SYS_IDS
FIELDS = ["description", "not_this_if", "examples.ru", "examples.kk"]
SIM_MAX = 0.85          # анти-хардкод: пример не может быть похож на текст кейса/dev сильнее
MAX_OPS, MAX_SCEN = 4, 2
BENCH_CONC = int(os.getenv("EVO_BENCH_CONC", "20"))              # прогон базовой версии (идёт параллельно с патчером)
BENCH_CONC_AFTER = int(os.getenv("EVO_BENCH_CONC_AFTER", "32"))  # прогон временной версии (к этому моменту базовый почти готов)
KK_LETTERS = set("әғқңөұүһі")
TASKS: set = set()

def _err(code, msg, status): return JSONResponse({"error": {"code": code, "message": msg}}, status)

async def emit(ev: dict):
    """Глобальное событие супервизора: конверт без сессии."""
    await HUB.broadcast({"t": 0, "session_id": "", "turn": None, **ev})

def _bg(coro):
    t = asyncio.create_task(coro); TASKS.add(t); t.add_done_callback(TASKS.discard)
    return t

def _next(prefix, keys) -> str:
    n = max([int(k.split("-")[1]) for k in keys if k.startswith(prefix + "-") and k.split("-")[1].isdigit()] or [0])
    return f"{prefix}-{n + 1:04d}"

async def _body(r: Request) -> dict:
    raw = await r.body()
    try: return json.loads(raw) if raw else {}
    except Exception: return {}

# ------------------------------------------------------------------ хранилище
CASES: list[dict] = bench.read_cases()
RUNS: dict[str, dict] = {p.stem: json.loads(p.read_text()) for p in sorted(RUNS_DIR.glob("BR-*.json"))}
PATCHES: dict[str, dict] = {p.stem: json.loads(p.read_text()) for p in sorted(PATCH_DIR.glob("PT-*.json"))}
for _p in PATCHES.values():
    if _p["status"] in ("proposing", "validating", "regression"):
        _p.update(status="failed", error="прервано перезапуском сервера")

def save_cases():
    bench.CASES_FILE.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in CASES), encoding="utf-8")

def save_run(run):
    RUNS[run["run_id"]] = run
    (RUNS_DIR / f"{run['run_id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1))

def save_patch(p):
    PATCHES[p["patch_id"]] = p
    (PATCH_DIR / f"{p['patch_id']}.json").write_text(json.dumps(p, ensure_ascii=False, indent=1))

def norm(t: str) -> str:
    t = t.lower().replace("ё", "е")
    return " ".join(re.sub(r"[^\w\s]", " ", t).split())

def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()

def guess_lang(text: str) -> str:
    words = re.findall(r"\w+", text.lower())
    if not words: return "ru"
    kk = sum(any(ch in KK_LETTERS for ch in w) for w in words)
    ru = sum(bool(re.fullmatch(r"я|мне|меня|мой|моя|мою|моё|моей|у|не|что|как|хочу|можно|нужно|надо|пожалуйста|полис\w*|страхов\w*|"
                               r"здравствуйте|почему|когда|где|это|вы|вас|или|но|уже|ещё|еще|сколько|скажите|подскажите|мы|нам|был\w*|есть", w))
             for w in words if not any(ch in KK_LETTERS for ch in w))
    if kk == 0: return "ru"
    if ru == 0 or kk / len(words) > 0.6: return "kk"
    return "mixed"

# ------------------------------------------------------------------ кейсы
def add_case(*, source, text, expected, lang=None, observed=None, context=None, session_id=None, turn=None, note=None, router_info=None):
    for c in CASES:
        if norm(c["text"]) == norm(text) and c["expected"] == expected: return c, False
    c = {"case_id": _next("CASE", [x["case_id"] for x in CASES]), "source": source, "text": text,
         "lang": lang if lang in ("ru", "kk", "mixed") else guess_lang(text), "context": context or {}, "expected": expected,
         "observed": observed or [], "session_id": session_id, "turn": turn, "created_at": now_iso()}
    if note: c["note"] = note
    if router_info: c["router"] = router_info
    CASES.append(c); save_cases()
    return c, True

def _from_traces(sid: str, turn: int):
    """Ход сессии из HUB: трасса хода и состояние роутера ДО хода (из трассы предыдущего хода)."""
    tr = {t["turn"]: t for t in HUB.traces(sid)}
    cur, prev = tr.get(turn), tr.get(turn - 1)
    ctx = {}
    if prev:
        a = prev.get("active_scenario")
        ctx = {"client": {"identified": True} if prev.get("client_id") else None,
               "active": {"scenario_id": a, "name": CATALOG.scenarios().get(a, {}).get("name", a)} if a else None,
               "stack": prev.get("stack") or [], "awaiting": None, "last_bot": prev.get("response_text")}
    return cur, ctx

@router.get("/api/cases")
def list_cases(): return CASES

@router.post("/api/cases", status_code=201)
async def create_case(r: Request):
    b = await _body(r)
    exp = b.get("expected") or []
    if not isinstance(exp, list) or not exp or any(x not in ALL_IDS for x in exp):
        return _err("bad_request", "expected: непустой список id сценариев", 422)
    text, observed, ctx, lang = b.get("text"), b.get("observed"), b.get("context"), b.get("lang")
    sid, turn, info = b.get("session_id"), b.get("turn"), None
    if sid and turn is not None:
        cur, pctx = _from_traces(sid, int(turn))
        if cur is None and not text: return _err("not_found", f"ход {turn} сессии {sid} не найден", 404)
        if cur:
            text = text or cur["transcript"]
            observed = observed or [s["scenario_id"] for s in cur["scenarios"]]
            lang = lang or (cur.get("language") if cur.get("language") in ("ru", "kk", "mixed") else None)
            ctx = ctx if ctx is not None else pctx
            top = (cur.get("scenarios") or [{}])[0]
            info = {"reason": top.get("reason"), "boundary_rule": top.get("boundary_rule"), "confidence": top.get("confidence"),
                    "catalog_version": cur.get("catalog_version"), "scenarios": _scen(cur)}
    if not text: return _err("bad_request", "нужен text или session_id+turn", 422)
    c, new = add_case(source=b.get("source") if b.get("source") in ("supervisor", "synthetic") else "supervisor", text=text, expected=exp,
                      lang=lang, observed=observed, context=ctx, session_id=sid, turn=turn, note=b.get("note"), router_info=info)
    if new: await emit({"type": "case.created", "case_id": c["case_id"], "source": c["source"]})
    return c

@router.delete("/api/cases/{case_id}")
def delete_case(case_id: str):
    n = len(CASES)
    CASES[:] = [c for c in CASES if c["case_id"] != case_id]
    if len(CASES) == n: return _err("not_found", case_id, 404)
    save_cases()
    return Response(status_code=204)

# ------------------------------------------------------------------ LLM (gpt-6-sol)
async def llm_json(instructions: str, payload, schema: dict, name: str, effort="medium", max_tokens=12000) -> tuple[dict, dict]:
    kw = dict(model=config.PATCHER_MODEL, instructions=instructions, reasoning={"effort": effort}, max_output_tokens=max_tokens,
              input=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
              text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}})
    if config.SERVICE_TIER: kw["service_tier"] = config.SERVICE_TIER
    t = time.perf_counter()
    try:
        r = await client.responses.create(**kw)
    except Exception:
        kw.pop("service_tier", None)
        r = await client.responses.create(**kw)
    meta = {"model": kw["model"], "latency_ms": int((time.perf_counter() - t) * 1000), "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens}
    return json.loads(r.output_text), meta

def scen_def(s: dict) -> dict:
    """Сценарий так, как его видит роутер (только видимые в промпте примеры)."""
    return {k: s[k] for k in ("scenario_id", "name", "domain", "category", "description", "not_this_if")} | {"examples": visible_examples(s)}

def catalog_index(version=None) -> list:
    return [f"{s['scenario_id']} | {s['name']} | {s['description']}" for s in CATALOG.scenarios(version).values()] + \
           [f"{x['id']} | {x['description']}" for x in CATALOG.get(version)["system_intents"]]

# ------------------------------------------------------------------ генерация трудных реплик
GEN_INSTR = """You write adversarial test utterances for the routing layer of the Saqta Insurance voice assistant (Kazakhstan, non-life insurance).
Clients call by phone and speak Russian, Kazakh or mix both; the router (an LLM that reads the scenario catalog) sees a speech-to-text transcript.
Goal: FIND ROUTING FAILURES. The router is strong: plain boundary cases and utterances that name the intent directly are routed correctly,
so they are useless. For each pair of confusable scenarios write utterances that a real caller could say, that sit right at the boundary,
and that still have ONE clear correct answer for a careful human who reads the scenario descriptions and not_this_if rules.
Techniques (combine them):
- lexical trap: most words and keywords belong to the WRONG scenario of the pair, the real intent is expressed indirectly or in one short clause;
- the real request is buried at the end after a long story, or is phrased as a hint, a complaint or a rhetorical question;
- negation or correction: "не статус мне нужен, а ...", "жоқ, мен басқа нәрсе сұрайын деп едім";
- implicit intent: the caller describes the situation and expects the obvious action without naming it;
- spoken transcript artifacts: no punctuation, fillers (ну, короче, это самое, енді, әлгі), colloquial or slang words, numbers as words;
- Kazakh-Russian code-switching inside one sentence;
- do not copy or closely paraphrase catalog examples; avoid the exact keywords of the correct scenario's examples.
Constraints: languages about 40% Russian, 30% natural Kazakh (not a word-by-word translation), 30% mixed (lang=mixed);
no utterances that genuinely need clarification; mostly single requests; expected is the ordered list of correct scenario ids (usually one);
pair: the two ids of the pair; trap: max 12 English words, what makes it hard.
Write exactly the requested number of utterances, alternating which side of each pair is correct."""

def gen_schema():
    ids = {"type": "string", "enum": ALL_IDS}
    return {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "lang", "expected", "pair", "trap"],
        "properties": {"text": {"type": "string"}, "lang": {"type": "string", "enum": ["ru", "kk", "mixed"]},
                       "expected": {"type": "array", "items": ids}, "pair": {"type": "array", "items": ids}, "trap": {"type": "string"}}}}}}

def confusable_pairs(version=None) -> list:
    return [(s["scenario_id"], x["use_instead"], x["condition"]) for s in CATALOG.scenarios(version).values() for x in s["not_this_if"]]

def uncovered_pairs(version=None) -> list:
    """Пары сценариев одной категории, между которыми в каталоге ещё нет правила границы: здесь роутер ошибается чаще."""
    sc = list(CATALOG.scenarios(version).values()); ruled = {frozenset((a, c)) for a, c, _ in confusable_pairs(version)}
    return [(a["scenario_id"], b["scenario_id"], "") for i, a in enumerate(sc) for b in sc[i + 1:]
            if a["category"] == b["category"] and (a["domain"] == b["domain"] or "general" in (a["domain"], b["domain"]))
            and frozenset((a["scenario_id"], b["scenario_id"])) not in ruled]

@router.post("/api/cases/generate")
async def generate_cases(r: Request):
    """gpt-6-sol пишет пограничные реплики для пар сценариев из not_this_if, роутер их размечает; неверно смаршрутизированные
    (дважды подряд) сохраняются кейсами source=synthetic."""
    b = await _body(r); t0 = time.perf_counter()
    n = max(2, min(int(b.get("n") or 10), 40)); ver = CATALOG.version
    allp = confusable_pairs(ver)
    if b.get("pairs"):
        want = {tuple(p) for p in b["pairs"]}
        pairs = [p for p in allp if (p[0], p[1]) in want or (p[1], p[0]) in want] or [(a, c, "") for a, c in want]
    else:   # половина пар из not_this_if, половина «непокрытых» пар одной категории/домена
        k = (n + 1) // 2; unc = uncovered_pairs(ver) if b.get("uncovered", True) else []
        pairs = random.sample(allp, min(len(allp), k - min(len(unc), k // 2))) + random.sample(unc, min(len(unc), k // 2))
    random.shuffle(pairs)
    sc = CATALOG.scenarios(ver); sysi = {x["id"]: x for x in CATALOG.get(ver)["system_intents"]}
    # параллельные пачки по ~8 реплик (4 пары) — быстрее одного длинного вызова
    nb = max(1, (n + 7) // 8); chunks = [pairs[i::nb] for i in range(nb)]; sizes = [n // nb + (i < n % nb) for i in range(nb)]
    def payload(ps, m):
        ids = sorted({x for p in ps for x in p[:2]})
        return {"n": m, "pairs": [{"a": a, "b": c, "boundary": cond or "no explicit rule in the catalog yet"} for a, c, cond in ps],
                "scenarios": [scen_def(sc[i]) if i in sc else {"id": i, "description": sysi.get(i, {}).get("description")} for i in ids],
                "catalog_index": catalog_index(ver)}
    outs = await asyncio.gather(*[llm_json(GEN_INSTR, payload(ps, m), gen_schema(), "utterances", effort=b.get("effort") or "medium")
                                  for ps, m in zip(chunks, sizes) if ps and m], return_exceptions=True)
    ok_outs = [o for o in outs if not isinstance(o, Exception)]
    if not ok_outs: return _err("llm_error", str(outs[0])[:300], 502)
    out = {"items": [x for o, _ in ok_outs for x in o["items"]]}
    meta = {"model": config.PATCHER_MODEL, "batches": len(outs), "failed_batches": len(outs) - len(ok_outs),
            "latency_ms": max(m["latency_ms"] for _, m in ok_outs), "output_tokens": sum(m["output_tokens"] for _, m in ok_outs)}
    dev_texts = [u["text"] for u in kit.dev_utterances()]
    items = [{"id": f"G{i + 1:02d}", "text": x["text"], "lang": x["lang"], "type": bench.case_type(x["expected"]), "expected": x["expected"],
              "pair": x["pair"], "trap": x["trap"], "source": "synthetic"}
             for i, x in enumerate(out["items"]) if x["expected"] and all(e in ALL_IDS for e in x["expected"])]
    items = [x for x in items if max((similarity(x["text"], d) for d in dev_texts), default=0) <= 0.9]
    res = await asyncio.gather(*[bench.route_one(x, ver) for x in items])
    miss = [x for x in res if not x["ok"]]
    conf = {x["id"]: x for x in await asyncio.gather(*[bench.route_one({k: v for k, v in m.items() if k not in ("router",)}, ver) for m in miss])}
    saved = []
    for x in res:
        scen = _scen(x.pop("router", None))
        if x["ok"]: continue
        x["confirm_got"] = conf[x["id"]]["got"]; x["stable"] = not conf[x["id"]]["ok"]
        if not x["stable"]: continue
        c, new = add_case(source="synthetic", text=x["text"], expected=x["expected"], lang=x["lang"], observed=x["got"],
                          note=f"generated {'/'.join(x.get('pair') or [])}: {x.get('trap', '')}",
                          router_info={"reason": x.get("reason"), "boundary_rule": x.get("boundary_rule"), "confidence": x.get("confidence"),
                                       "catalog_version": ver, "scenarios": scen})
        x["case_id"] = c["case_id"]
        if new:
            saved.append(c); await emit({"type": "case.created", "case_id": c["case_id"], "source": "synthetic"})
    return {"catalog_version": ver, "n": len(res), "misrouted": len(miss), "saved": saved, "items": res,
            "pairs": [[a, c] for a, c, _ in pairs], "generator": meta, "latency_ms": int((time.perf_counter() - t0) * 1000)}

# ------------------------------------------------------------------ прогоны
def new_run(version, items, purpose) -> dict:
    run = {"run_id": _next("BR", RUNS), "catalog_version": version, "status": "running", "purpose": purpose,
           "dataset": {"dev": sum(x["source"] == "dev" for x in items), "cases": sum(x["source"] != "dev" for x in items), "n": len(items)},
           "started_at": now_iso(), "finished_at": None, "metrics": {"all": {"n": len(items), "primary_acc": 0.0, "full_match": 0.0}}}
    save_run(run)
    return run

def finish_run(run, rep):
    run.update({k: rep[k] for k in ("status", "finished_at", "duration_ms", "metrics", "errors", "latency_ms", "model")})
    run["items"] = [bench.slim_item(u) for u in rep["items"]]
    save_run(run)
    return run

def _slim_run(run): return {k: v for k, v in run.items() if k != "items"}

async def bench_task(run, items):
    ver = run["catalog_version"]
    async def prog(d, n):
        if d % 5 == 0 or d == n: await emit({"type": "bench.progress", "run_id": run["run_id"], "done": d, "total": n})
    try:
        rep = await bench.run(items, version=ver, concurrency=20, on_progress=prog)
        finish_run(run, rep)
        if ver in CATALOG.data and ver not in CATALOG.temp: CATALOG.set_accuracy(ver, rep["metrics"]["all"]["primary_acc"])
        await emit({"type": "bench.finished", "run_id": run["run_id"], "status": "finished", "catalog_version": ver, "metrics": rep["metrics"]})
    except Exception as e:
        run.update(status="failed", finished_at=now_iso(), error=str(e)[:300]); save_run(run)
        await emit({"type": "bench.finished", "run_id": run["run_id"], "status": "failed", "metrics": {}, "error": str(e)[:300]})

@router.post("/api/bench", status_code=202)
async def start_bench(r: Request):
    b = await _body(r)
    ver = b.get("catalog_version") or CATALOG.version
    if ver not in CATALOG.data: return _err("not_found", f"версия {ver} не найдена", 404)
    items = bench.dataset(include_cases=b.get("include_cases", True))
    run = new_run(ver, items, "manual")
    _bg(bench_task(run, items))
    return {"run_id": run["run_id"], "catalog_version": ver, "dataset": run["dataset"]}

@router.get("/api/bench/runs")
def list_runs(): return [_slim_run(x) for x in RUNS.values()]

@router.get("/api/bench/runs/{run_id}")
def get_run(run_id: str):
    return RUNS[run_id] if run_id in RUNS else _err("not_found", run_id, 404)

# ------------------------------------------------------------------ патчер
PATCH_INSTR = """You maintain the scenario catalog of an insurance voice assistant (Saqta Insurance, Kazakhstan; clients speak Russian, Kazakh or mix both).
An LLM router reads the catalog (per scenario: description, all not_this_if boundary rules, a few ru/kk examples) and picks the scenario(s)
for a client utterance. It misrouted the utterances below (expected = correct routing, observed = what the router chose).
Propose the SMALLEST catalog change that fixes the routing without breaking neighbouring scenarios.
Rules:
- Allowed edits only: not_this_if (add a boundary rule, replace or remove an existing one), description (replace the whole text),
  examples.ru / examples.kk (append a new example, replace or remove an existing one). Ids, slots, actions, priorities and flags are never touched.
- At most 4 ops and at most 2 scenarios in total.
- Prefer boundary rules: ADD a not_this_if rule to the WRONGLY chosen scenario pointing to the correct one (use_instead). Add an example to the
  correct scenario only in addition to a rule, or when the boundary is already described but the router still fails.
- Existing rules and examples protect other utterances: do not replace or remove them unless they are clearly wrong; never narrow an existing rule.
- A not_this_if condition is one short English sentence describing a GENERAL class of requests (not the concrete utterance, no names or numbers).
  use_instead must be an existing scenario id (or SYS_OUT_OF_SCOPE / SYS_UNCLEAR / SYS_GOODBYE) different from the scenario that owns the rule.
- Never copy the utterance into examples: a validator rejects any example whose similarity to a test utterance is above 0.85.
  Generalize: other words and details, same intent, natural spoken style, in the language of the field (examples.kk in Kazakh).
- Description replacements keep the original meaning and only sharpen the boundary.
- If the expected label itself looks wrong, still propose the best boundary fix and say so in the rationale.
Op fields: text = the new rule condition / new description / example text (for remove: the exact existing item or rule condition);
old_text = for replace of a list item the exact existing item or rule condition, otherwise null; use_instead = only for not_this_if add/replace, otherwise null.
rationale: ONE sentence in Russian: the root cause of the misrouting and what the change does."""

def patch_schema():
    return {"type": "object", "additionalProperties": False, "required": ["rationale", "ops"], "properties": {
        "rationale": {"type": "string"},
        "ops": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                "required": ["op", "scenario_id", "field", "text", "old_text", "use_instead"],
                "properties": {"op": {"type": "string", "enum": ["add", "append", "replace", "remove"]},
                               "scenario_id": {"type": "string", "enum": SC_IDS}, "field": {"type": "string", "enum": FIELDS},
                               "text": {"type": "string"}, "old_text": {"type": ["string", "null"]},
                               "use_instead": {"type": ["string", "null"], "enum": ALL_IDS + [None]}}}}}}

async def propose(cases: list, base: str, feedback: dict | None):
    sc = CATALOG.scenarios(base)
    ids = [i for i in dict.fromkeys(x for c in cases for x in c["expected"] + (c.get("observed") or [])) if i in sc]
    near = [c for c in CASES if c["case_id"] not in {x["case_id"] for x in cases} and set(c["expected"] + c.get("observed", [])) & set(ids)][:3]
    payload = {"cases": [{"text": c["text"], "lang": c["lang"], "context": c.get("context") or {}, "expected": c["expected"],
                          "observed": c.get("observed") or [], "router_reason": (c.get("router") or {}).get("reason"),
                          "router_boundary_rule": (c.get("router") or {}).get("boundary_rule"),
                          "router_output": (c.get("router") or {}).get("scenarios")} for c in cases],
               "scenarios": [scen_def(sc[i]) for i in ids],
               "similar_past_cases": [{"text": c["text"], "expected": c["expected"], "observed": c.get("observed")} for c in near],
               "catalog_index": catalog_index(base)}
    if feedback:
        payload["previous_attempt"] = feedback
        payload["instruction"] = "Your previous proposal was REJECTED by the validator for the reasons listed. Fix every reason; generalize the examples."
    return await llm_json(PATCH_INSTR, payload, patch_schema(), "catalog_patch", effort="medium")

def validate(prop: dict, base: str) -> tuple[bool, list, list]:
    """Схема + ограничения + анти-хардкод. Возвращает (ok, лог, ops в формате контракта PatchOp)."""
    sc = CATALOG.scenarios(base); log, bad, ops = [], [], []
    raw = prop.get("ops") or []
    if not raw: bad.append("патч пустой")
    if len(raw) > MAX_OPS: bad.append(f"{len(raw)} ops > {MAX_OPS}")
    touched = {o["scenario_id"] for o in raw}
    if len(touched) > MAX_SCEN: bad.append(f"затронуто сценариев {len(touched)} > {MAX_SCEN}")
    tests = [c["text"] for c in CASES] + [u["text"] for u in kit.dev_utterances()]
    for o in raw:
        sid, f, kind, text = o["scenario_id"], o["field"], o["op"], (o.get("text") or "").strip()
        tag = f"{kind} {sid}.{f}"
        if sid not in sc or f not in FIELDS: bad.append(f"{tag}: неизвестный сценарий или поле"); continue
        if not text: bad.append(f"{tag}: пустое значение"); continue
        cur = field_get(sc[sid], f); old = None
        if f == "description":
            if kind != "replace": bad.append(f"{tag}: description допускает только replace"); continue
            ops.append({"op": "replace", "scenario_id": sid, "field": f, "value": text}); log.append(f"{tag}: ok"); continue
        if kind in ("remove", "replace"):
            key = text if kind == "remove" else (o.get("old_text") or "")
            pool = visible_examples(sc[sid])[f.split(".")[1]] if f.startswith("examples.") else cur
            old = next((x for x in pool if norm(x["condition"] if isinstance(x, dict) else x) == norm(key)), None)
            if old is None: bad.append(f"{tag}: элемент для {kind} не найден среди видимых роутеру: {key[:60]!r}"); continue
            if kind == "remove":
                ops.append({"op": "remove", "scenario_id": sid, "field": f, "value": old}); log.append(f"{tag}: элемент найден: ok"); continue
        if f == "not_this_if":
            ui = o.get("use_instead")
            if ui not in ALL_IDS: bad.append(f"{tag}: use_instead {ui} не существует"); continue
            if ui == sid: bad.append(f"{tag}: use_instead указывает на свой же сценарий"); continue
            if any(norm(x["condition"]) == norm(text) for x in cur): bad.append(f"{tag}: такое правило уже есть"); continue
            log.append(f"use_instead {ui} exists: ok")
            val = {"condition": text, "use_instead": ui}
        else:
            sim, near = max(((similarity(text, t), t) for t in tests), default=(0.0, ""))
            if sim > SIM_MAX:
                bad.append(f"анти-хардкод: пример {text!r} похож на тестовую реплику {near!r} ({sim:.2f} > {SIM_MAX}), нужно обобщить"); continue
            log.append(f"example similarity to case text {sim:.2f} < {SIM_MAX}: ok")
            if f == "examples.kk" and not any(ch in KK_LETTERS for ch in text.lower()): log.append(f"{tag}: warning: пример без казахских букв")
            val = text
        ops.append({"op": kind, "scenario_id": sid, "field": f, "value": val, **({"old": old} if kind == "replace" else {})})
    if not bad and _hash(CATALOG.apply_ops(ops, base)["scenarios"]) == _hash(CATALOG.get(base)["scenarios"]):
        bad.append("патч не меняет каталог")
    return not bad, log + [f"REJECT: {x}" for x in bad], ops

def make_diff(base: str, tmp: str, ops: list) -> list:
    B, A = CATALOG.scenarios(base), CATALOG.scenarios(tmp)
    keys = list(dict.fromkeys((o["scenario_id"], o["field"]) for o in ops))
    return [{"scenario_id": s, "field": f, "before": copy.deepcopy(field_get(B[s], f)), "after": copy.deepcopy(field_get(A[s], f)),
             "ops": [o["op"] for o in ops if (o["scenario_id"], o["field"]) == (s, f)]} for s, f in keys]

async def stage(p, st, done=None, total=None, message=None, status=None):
    if status: p["status"] = status
    p["progress"] = {"stage": st, "done": done, "total": total, "message": message}
    ev = {"type": "patch.progress", "patch_id": p["patch_id"], "stage": st}
    if done is not None: ev |= {"done": done, "total": total}
    if message: ev["message"] = message
    await emit(ev)

RECHECK = int(os.getenv("EVO_RECHECK", "4"))   # доп. прогонов для расходящихся и целевых реплик: большинство из 1 + RECHECK
GAP = (1 + RECHECK) // 2 + 1                     # fixed/broken: разница верных прогонов не меньше GAP (5 прогонов -> 3)

def _summary(run, voted: list) -> dict:
    """Метрики версии по итогам голосования (расходящиеся реплики перепроверены), сырые метрики прогона — в raw."""
    m, _ = bench.metrics(voted); raw = run["metrics"]["all"]
    out = {"run_id": run["run_id"], "catalog_version": run["catalog_version"], "n": m["all"]["n"], "primary_acc": m["all"]["primary_acc"],
           "full_match": m["all"]["full_match"], "raw": {"primary_acc": raw["primary_acc"], "full_match": raw["full_match"]}}
    for name, pred in (("dev", lambda u: u.get("source", "dev") == "dev"), ("cases", lambda u: u.get("source", "dev") != "dev")):
        sub = [u for u in voted if pred(u)]
        if sub:
            sm, _ = bench.metrics(sub)
            out[name] = {"n": sm["all"]["n"], "primary_acc": sm["all"]["primary_acc"], "full_match": sm["all"]["full_match"]}
    return out

def _vote(rs: list) -> dict:
    oks = sum(r["ok"] for r in rs); ok = oks * 2 > len(rs)
    got = Counter(tuple(r["got"]) for r in rs if r["ok"] == ok).most_common(1)[0][0]
    return {**rs[0], "got": list(got), "ok": ok, "votes": f"{oks}/{len(rs)}", "passes": oks}

def start_before(p, base) -> dict:
    """Прогон базовой версии не зависит от патча: стартует сразу, параллельно с патчером и самопроверкой."""
    items = bench.dataset(include_cases=True)
    rg = {"items": items, "N": len(items), "b": 0, "a": 0, "last": 0, "t0": time.perf_counter(),
          "run": new_run(base, items, f"patch {p['patch_id']} before")}
    async def pb(d, n): rg["b"] = d; await _tick(p, rg)
    rg["task"] = asyncio.create_task(bench.run(items, version=base, concurrency=BENCH_CONC, on_progress=pb))
    return rg

def cancel_before(rg: dict | None, why: str):
    if not rg: return
    if not rg["task"].done(): rg["task"].cancel()
    if rg["run"]["status"] == "running": rg["run"].update(status="failed", finished_at=now_iso(), error=why); save_run(rg["run"])

async def _tick(p, rg, force=False):
    if p["status"] != "regression": return
    d, N = rg["b"] + rg["a"], rg["N"]
    if force or d - rg["last"] >= 5 or d == 2 * N:
        rg["last"] = d
        await stage(p, "regression_before" if rg["b"] < N else "regression_after", d, 2 * N)

async def regression(p, base, tmp, rg):
    items, N, tm = rg["items"], rg["N"], p["timings"]
    async def pa(d, n): rg["a"] = d; await _tick(p, rg)
    await _tick(p, rg, force=True)
    ra_run = new_run(tmp, items, f"patch {p['patch_id']} after")
    t = time.perf_counter()
    ra = await bench.run(items, version=tmp, concurrency=BENCH_CONC_AFTER, on_progress=pa)
    tm["after_run_ms"] = int((time.perf_counter() - t) * 1000)
    rb = await rg["task"]
    tm["before_run_ms"] = rb["duration_ms"]
    finish_run(rg["run"], rb); finish_run(ra_run, ra)
    rb_run = rg["run"]; t = time.perf_counter()
    B = {u["id"]: u for u in rb["items"]}; A = {u["id"]: u for u in ra["items"]}
    diff_ids = [i for i in B if B[i]["ok"] != A[i]["ok"]]
    ids = list(dict.fromkeys(diff_ids + [c for c in p["case_ids"] if c in B]))
    # перепроверка: роутер недетерминирован, fixed/broken считаем по большинству из 1 + RECHECK прогонов на каждой версии
    vb, va = {i: [B[i]] for i in ids}, {i: [A[i]] for i in ids}
    if ids:
        await stage(p, "regression_after", 2 * N, 2 * N, message=f"перепроверка {len(ids)} реплик (большинство из {1 + RECHECK})")
        sub = [x for x in items if x["id"] in ids]
        jobs = [(vb, x, base) for x in sub for _ in range(RECHECK)] + [(va, x, tmp) for x in sub for _ in range(RECHECK)]
        for (dst, _, _), r in zip(jobs, await asyncio.gather(*[bench.route_one(x, v) for _, x, v in jobs])):
            dst[r["id"]].append(r)
    tm["recheck_ms"] = int((time.perf_counter() - t) * 1000)
    VB = {i: _vote(vb[i]) if i in vb else B[i] for i in B}; VA = {i: _vote(va[i]) if i in va else A[i] for i in A}
    gap = lambda i: VA[i].get("passes", VA[i]["ok"]) - VB[i].get("passes", VB[i]["ok"])   # шум недетерминированного роутера не считаем
    fixed = [i for i in ids if not VB[i]["ok"] and VA[i]["ok"] and gap(i) >= GAP]
    broken = [i for i in ids if VB[i]["ok"] and not VA[i]["ok"] and gap(i) <= -GAP]
    flaky = [i for i in diff_ids if i not in fixed and i not in broken]
    targets = {cid: {"expected": B[cid]["expected"], "before": VB[cid]["got"], "after": VA[cid]["got"],
                     "before_ok": VB[cid].get("votes"), "after_ok": VA[cid].get("votes"), "fixed": cid in fixed} for cid in p["case_ids"] if cid in B}
    return {"before": _summary(rb_run, list(VB.values())), "after": _summary(ra_run, list(VA.values())),
            "fixed": fixed, "broken": broken, "flaky": flaky, "targets": targets}

MAX_ATTEMPTS, SELF_CHECK_RUNS = 3, 2
SCEN_KEYS = ("scenario_id", "confidence", "segment", "reason", "boundary_rule")

def _scen(r: dict | None) -> list:
    return [{k: x.get(k) for k in SCEN_KEYS} for x in (r or {}).get("scenarios", [])]

async def self_check(cases: list, version: str) -> dict:
    """Целевые кейсы на временной версии, SELF_CHECK_RUNS раз каждый -> {case_id: [результаты]}."""
    items = bench.dataset(include_dev=False, cases=cases)
    out = {}
    for r in await asyncio.gather(*[bench.route_one(x, version) for x in items for _ in range(SELF_CHECK_RUNS)]):
        out.setdefault(r["id"], []).append(r)
    return out

async def patch_task(p):
    t0 = time.perf_counter(); base = p["base_version"]; tm = p["timings"]; pid = p["patch_id"]
    temps, rg = [], None
    try:
        cases = [c for c in CASES if c["case_id"] in p["case_ids"]]
        rg = start_before(p, base)
        feedback, msg, best, prop = None, None, None, {}
        for attempt in range(MAX_ATTEMPTS):
            await stage(p, "proposing", message=msg, status="proposing")
            t1 = time.perf_counter()
            prop, meta = await propose(cases, base, feedback)
            tm["propose_ms"] = tm.get("propose_ms", 0) + int((time.perf_counter() - t1) * 1000); p["patcher"] = meta
            await stage(p, "validating", status="validating")
            ok, log, ops = validate(prop, base)
            p["validator_log"] += [f"попытка {attempt + 1}:"] + log; p["attempts"] = attempt + 1
            if not ok:
                feedback = {"proposal": prop, "rejected": [x for x in log if x.startswith("REJECT")]}
                msg = "повторная генерация после отказа валидатора"; continue
            # самопроверка: целевые кейсы на временной версии (у каждой попытки своё имя: router._system кэширует по версии)
            tmp = CATALOG.add_temp(f"{base}+{pid}" + (f".{attempt + 1}" if attempt else ""), CATALOG.apply_ops(ops, base)); temps.append(tmp)
            await stage(p, "validating", message="самопроверка на целевых кейсах")
            t1 = time.perf_counter()
            chk = await self_check(cases, tmp)
            tm["self_check_ms"] = tm.get("self_check_ms", 0) + int((time.perf_counter() - t1) * 1000)
            nfix = sum(all(r["ok"] for r in rs) for rs in chk.values())
            p["validator_log"].append(f"самопроверка на временной версии: исправлено {nfix}/{len(chk)} кейсов ({SELF_CHECK_RUNS} прогона на кейс)")
            if best is None or nfix > best[0]: best = (nfix, prop, ops, tmp)
            if nfix == len(chk): break
            feedback = {"proposal": prop,
                        "problem": "The patch passed the validator but did NOT fix the routing. Router output on the PATCHED catalog is below.",
                        "router_after_patch": [{"text": rs[0]["text"], "expected": rs[0]["expected"], "got": [r["got"] for r in rs],
                                                "router": _scen(rs[0].get("router"))} for rs in chk.values() if not all(r["ok"] for r in rs)],
                        "hint": "Make the boundary unambiguous for this class of requests (the rule should name what the router latched onto), "
                                "and consider one generalized example for the correct scenario."}
            msg = "патч не исправил кейс на самопроверке, повторная генерация"
        if best is None:
            p["proposal"] = {"ops": [], "rationale": prop.get("rationale", ""), "raw_ops": prop.get("ops")}
            cancel_before(rg, "patch failed")
            p.update(status="failed", error=f"валидатор отклонил все {MAX_ATTEMPTS} попытки"); tm["total_ms"] = int((time.perf_counter() - t0) * 1000); save_patch(p)
            await stage(p, "failed", message=p["error"]); return
        nfix, prop, ops, tmp = best
        for t in temps:
            if t != tmp: CATALOG.drop_temp(t)
        p["proposal"] = {"ops": ops, "rationale": prop.get("rationale", "")}
        p["temp_version"] = tmp; p["diff"] = make_diff(base, tmp, ops); save_patch(p)
        p["status"] = "regression"
        t2 = time.perf_counter()
        p["regression"] = await regression(p, base, tmp, rg)
        tm["regression_ms"] = int((time.perf_counter() - t2) * 1000); tm["total_ms"] = int((time.perf_counter() - t0) * 1000)
        if p["status"] == "rejected": save_patch(p); return      # отклонён супервизором во время прогона
        p["status"] = "ready"; save_patch(p)
        await stage(p, "ready", message=f"fixed {len(p['regression']['fixed'])}, broken {len(p['regression']['broken'])}")
        await emit({"type": "patch.ready", "patch_id": pid})
    except Exception as e:
        cancel_before(rg, "patch failed")
        for t in temps: CATALOG.drop_temp(t)
        p.pop("temp_version", None)
        p.update(status="failed", error=f"{type(e).__name__}: {e}"[:300]); tm["total_ms"] = int((time.perf_counter() - t0) * 1000); save_patch(p)
        await stage(p, "failed", message=p["error"])

def _public_patch(p): return {k: v for k, v in p.items() if k != "temp_version"}

@router.post("/api/patches", status_code=202)
async def create_patch(r: Request):
    b = await _body(r)
    ids = b.get("case_ids") or []
    known = {c["case_id"] for c in CASES}
    if not ids or any(i not in known for i in ids): return _err("bad_request", f"неизвестные кейсы: {[i for i in ids if i not in known] or 'пусто'}", 422)
    p = {"patch_id": _next("PT", PATCHES), "status": "proposing", "case_ids": ids, "base_version": CATALOG.version, "proposal": None,
         "diff": [], "regression": None, "validator_log": [], "applied_version": None, "created_at": now_iso(), "timings": {},
         "progress": {"stage": "proposing", "done": None, "total": None, "message": None}}
    save_patch(p)
    _bg(patch_task(p))
    return _public_patch(p)

@router.get("/api/patches")
def list_patches(): return [_public_patch(p) for p in PATCHES.values()]

@router.get("/api/patches/{patch_id}")
def get_patch(patch_id: str):
    return _public_patch(PATCHES[patch_id]) if patch_id in PATCHES else _err("not_found", patch_id, 404)

@router.post("/api/patches/{patch_id}/apply")
async def apply_patch(patch_id: str):
    p = PATCHES.get(patch_id)
    if not p: return _err("not_found", patch_id, 404)
    if CATALOG.frozen: return _err("frozen", "catalog is frozen", 409)
    if p["status"] != "ready": return _err("not_ready", f"патч в статусе {p['status']}", 409)
    cur, base, tmp = CATALOG.version, p["base_version"], p.get("temp_version")
    if base == cur and tmp in CATALOG.data: cat = CATALOG.get(tmp)
    else:
        cat = CATALOG.apply_ops(p["proposal"]["ops"], cur)
        if base != cur: p["validator_log"].append(f"патч перенесён с {base} на текущую {cur}")
    touched = "/".join(dict.fromkeys(o["scenario_id"] for o in p["proposal"]["ops"]))
    try: v = CATALOG.commit(cat, patch_id, note=f"{touched}: {p['proposal']['rationale']}"[:160])
    except PermissionError: return _err("frozen", "catalog is frozen", 409)
    if base == cur and p.get("regression"): CATALOG.set_accuracy(v["version"], p["regression"]["after"]["primary_acc"]); v["primary_acc"] = p["regression"]["after"]["primary_acc"]
    if tmp: CATALOG.drop_temp(tmp)
    p.update(status="applied", applied_version=v["version"]); p.pop("temp_version", None); save_patch(p)
    await rt.warm(v["version"])   # hot reload: промпт новой версии собран и кэш префикса прогрет до ответа
    await emit({"type": "catalog.changed", "version": v["version"], "parent": v["parent"], "frozen": CATALOG.frozen, "reason": "patch_applied"})
    return v

@router.post("/api/patches/{patch_id}/reject")
async def reject_patch(patch_id: str):
    p = PATCHES.get(patch_id)
    if not p: return _err("not_found", patch_id, 404)
    if p["status"] == "applied": return _err("already_applied", "патч уже применён, используйте откат версии", 409)
    if p.get("temp_version"): CATALOG.drop_temp(p.pop("temp_version"))
    p["status"] = "rejected"; save_patch(p)
    return _public_patch(p)
