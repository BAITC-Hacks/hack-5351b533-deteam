"""Регрессионный прогон роутера: dev_utterances + кейсы (regression/cases.jsonl), метрики как в data/evaluate.py.
CLI: python -m app.bench [--model M] [--version V] [--cases] [--out predictions.json]"""
import asyncio, json, time, statistics, datetime
from collections import defaultdict
from . import kit, config
from .router import route_full
from .policy import decide

CASES_FILE = config.REGRESSION_DIR / "cases.jsonl"

def now_iso(ts=None):
    d = datetime.datetime.fromtimestamp(ts, datetime.UTC) if ts else datetime.datetime.now(datetime.UTC)
    return d.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"

def read_cases() -> list:
    if not CASES_FILE.exists(): return []
    return [json.loads(l) for l in CASES_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]

def case_type(expected: list) -> str:
    if expected[0] == "SYS_OUT_OF_SCOPE": return "out_of_scope"
    if expected[0] == "SYS_UNCLEAR": return "unclear"
    return "multi_intent" if len(expected) > 1 else "single"

def dataset(include_dev=True, include_cases=True, cases=None) -> list:
    """Элементы прогона: {id, text, lang, type, expected, context?, source}."""
    items = []
    if include_dev:
        items += [{"id": u["id"], "text": u["text"], "lang": u["lang"], "type": u["type"], "expected": u["expected"], "source": "dev"} for u in kit.dev_utterances()]
    if include_cases:
        for c in (read_cases() if cases is None else cases):
            items.append({"id": c["case_id"], "text": c["text"], "lang": c.get("lang", "ru"), "type": case_type(c["expected"]),
                          "expected": c["expected"], "context": c.get("context") or None, "source": c.get("source", "supervisor")})
    return items

def is_ok(expected, got) -> bool:
    """Ход верный: первый сценарий совпал и множество совпало (primary + full_match)."""
    return bool(got) and got[0] == expected[0] and set(got) == set(expected)

def metrics(items):
    """items: [{id, lang, type, expected, got, confidence}]"""
    g = defaultdict(lambda: {"n": 0, "primary": 0, "full": 0})
    rh = rt = 0; errs = []; buckets = defaultdict(lambda: [0, 0])
    for u in items:
        exp, got = u["expected"], u["got"]
        p = bool(got) and got[0] == exp[0]; f = set(got) == set(exp)
        if u.get("type") == "multi_intent": rh += len(set(exp) & set(got)); rt += len(exp)
        for k in ("all", f"lang={u.get('lang')}", f"type={u.get('type')}"):
            g[k]["n"] += 1; g[k]["primary"] += p; g[k]["full"] += f
        c = u.get("confidence") or 0
        b = "0.9+" if c >= 0.9 else "0.75-0.9" if c >= 0.75 else "0.45-0.75" if c >= 0.45 else "<0.45"
        buckets[b][0] += 1; buckets[b][1] += p
        if not f: errs.append({"id": u["id"], "text": u["text"], "expected": exp, "got": got})
    m = lambda x: {"n": x["n"], "primary_acc": round(x["primary"] / x["n"], 3) if x["n"] else 0.0, "full_match": round(x["full"] / x["n"], 3) if x["n"] else 0.0}
    out = {"all": m(g["all"]),
           "by_lang": {k[5:]: m(v) for k, v in g.items() if k.startswith("lang=")},
           "by_type": {k[5:]: m(v) for k, v in g.items() if k.startswith("type=")},
           "confidence_buckets": [{"bucket": b, "n": v[0], "primary_acc": round(v[1] / v[0], 3)} for b, v in sorted(buckets.items()) if v[0]]}
    if rt: out["intent_recall"] = round(rh / rt, 3)
    return out, errs

async def route_one(u, version=None, model=None, timeout=20):
    """Один элемент через тот же роутер, что в диалоге (route_full + policy.decide). Одна повторная попытка."""
    err = None
    for attempt in range(2):
        try:
            r = await asyncio.wait_for(route_full(u["text"], state=u.get("context"), version=version, model=model, hedge=1), timeout)  # без хеджей: решение то же, нагрузка вдвое меньше
            d = decide(r, version=version)
            top = (r.get("scenarios") or [{}])[0]
            return {**u, "got": d["scenarios"], "decision": d["decision"], "confidence": round(top.get("confidence", 0) or 0, 3),
                    "ok": is_ok(u["expected"], d["scenarios"]), "reason": top.get("reason"), "boundary_rule": top.get("boundary_rule"),
                    "router_ms": r["_meta"]["latency_ms"], "router": r}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
    return {**u, "got": [], "decision": "error", "confidence": 0, "ok": False, "error": err}

async def run(dataset=None, version=None, model=None, concurrency=20, on_progress=None):
    items = dataset if dataset is not None else globals()["dataset"](include_cases=False)
    sem = asyncio.Semaphore(concurrency); done = 0
    async def one(u):
        nonlocal done
        async with sem:
            u2 = await route_one(u, version, model)
        done += 1
        if on_progress:
            try: await on_progress(done, len(items))
            except Exception: pass
        return u2
    t = time.time()
    res = await asyncio.gather(*[one(u) for u in items])
    m, errs = metrics(res)
    lat = sorted(u["router_ms"] for u in res if u.get("router_ms"))
    src = defaultdict(int)
    for u in items: src["dev" if u.get("source", "dev") == "dev" else "cases"] += 1
    return {"catalog_version": version, "model": model or config.ROUTER_MODEL, "status": "finished",
            "started_at": now_iso(t), "finished_at": now_iso(),
            "duration_ms": int((time.time() - t) * 1000),
            "dataset": {"dev": src["dev"], "cases": src["cases"], "n": len(items)}, "metrics": m, "errors": errs,
            "latency_ms": {"router_p50": int(statistics.median(lat)) if lat else None,
                           "router_p95": int(lat[max(0, int(len(lat) * 0.95) - 1)]) if lat else None},
            "items": res}

def slim_item(u: dict) -> dict:
    """Элемент прогона без полного выхода роутера (для хранения и UI)."""
    r = u.get("router") or {}
    return {k: v for k, v in u.items() if k not in ("router", "context")} | {
        "scen": [[s["scenario_id"], s["confidence"]] for s in r.get("scenarios", [])],
        "second": (r.get("second_opinion") or {}).get("scenarios")}

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--model"); ap.add_argument("--version"); ap.add_argument("--cases", action="store_true")
    ap.add_argument("--out", default=str(config.REGRESSION_DIR / "predictions.json"))
    a = ap.parse_args()
    rep = asyncio.run(run(dataset(include_cases=a.cases), version=a.version, model=a.model))
    config.REGRESSION_DIR.mkdir(parents=True, exist_ok=True)
    json.dump({u["id"]: u["got"] for u in rep["items"] if u.get("source", "dev") == "dev"}, open(a.out, "w"), ensure_ascii=False, indent=1)
    slim = {k: v for k, v in rep.items() if k != "items"}
    json.dump({**slim, "items": [slim_item(u) for u in rep["items"]]},
              open(config.REGRESSION_DIR / f"bench_{rep['model']}.json", "w"), ensure_ascii=False, indent=1)
    m = rep["metrics"]["all"]
    print("model", rep["model"], "n", m["n"], "primary", m["primary_acc"], "full", m["full_match"], "latency", rep["latency_ms"], "sec", rep["duration_ms"] / 1000)
    for e in rep["errors"]: print("  ERR", e["id"], e["expected"], "->", e["got"], e["text"])
