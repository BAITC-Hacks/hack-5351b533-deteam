"""Регрессионный прогон роутера: dev_utterances + кейсы, метрики как в data/evaluate.py."""
import asyncio, json, time, statistics, datetime
from collections import defaultdict
from . import kit, config
from .router import route_full
from .policy import decide

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
    m = lambda x: {"n": x["n"], "primary_acc": round(x["primary"] / x["n"], 3), "full_match": round(x["full"] / x["n"], 3)}
    return {"all": m(g["all"]),
            "by_lang": {k[5:]: m(v) for k, v in g.items() if k.startswith("lang=")},
            "by_type": {k[5:]: m(v) for k, v in g.items() if k.startswith("type=")},
            "intent_recall": round(rh / rt, 3) if rt else None,
            "confidence_buckets": [{"bucket": b, "n": v[0], "primary_acc": round(v[1] / v[0], 3)} for b, v in sorted(buckets.items()) if v[0]]}, errs

async def run(dataset=None, version=None, model=None, concurrency=16, on_progress=None):
    items = dataset or [{"id": u["id"], "text": u["text"], "lang": u["lang"], "type": u["type"], "expected": u["expected"]} for u in kit.dev_utterances()]
    sem = asyncio.Semaphore(concurrency); done = 0; lat = []
    async def one(u):
        nonlocal done
        async with sem:
            for attempt in range(2):
                try:
                    r = await asyncio.wait_for(route_full(u["text"], state=u.get("context"), version=version, model=model), 40)
                    break
                except Exception as e:
                    r = None; err = str(e)
            if r is None:
                u2 = {**u, "got": [], "confidence": 0, "error": err}
            else:
                d = decide(r, version=version)
                lat.append(r["_meta"]["latency_ms"])
                u2 = {**u, "got": d["scenarios"], "confidence": (r["scenarios"][0]["confidence"] if r["scenarios"] else 0), "router": r}
            done += 1
            if on_progress: await on_progress(done, len(items))
            return u2
    t = time.time()
    res = await asyncio.gather(*[one(u) for u in items])
    m, errs = metrics(res)
    return {"catalog_version": version, "model": model or config.ROUTER_MODEL, "status": "finished",
            "started_at": datetime.datetime.fromtimestamp(t, datetime.UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "finished_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "dataset": {"n": len(items)}, "metrics": m, "errors": errs,
            "latency_ms": {"router_p50": int(statistics.median(lat)) if lat else None,
                           "router_p95": int(sorted(lat)[int(len(lat) * 0.95) - 1]) if lat else None},
            "items": res}

if __name__ == "__main__":
    import sys, argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--model"); ap.add_argument("--out", default=str(config.REGRESSION_DIR / "predictions.json"))
    a = ap.parse_args()
    rep = asyncio.run(run(model=a.model))
    config.REGRESSION_DIR.mkdir(parents=True, exist_ok=True)
    json.dump({u["id"]: u["got"] for u in rep["items"]}, open(a.out, "w"), ensure_ascii=False, indent=1)
    slim = {k: v for k, v in rep.items() if k != "items"}
    json.dump({**slim, "items": [{k: v for k, v in u.items() if k != "router"} | {"reason": (u.get("router") or {}).get("scenarios", [{}])[0].get("reason") if (u.get("router") or {}).get("scenarios") else None,
               "scen": [(s["scenario_id"], s["confidence"]) for s in (u.get("router") or {}).get("scenarios", [])], "second": (u.get("router") or {}).get("second_opinion")} for u in rep["items"]]},
              open(config.REGRESSION_DIR / f"bench_{rep['model']}.json", "w"), ensure_ascii=False, indent=1)
    print("model", rep["model"], "latency", rep["latency_ms"], "cache sample", rep["items"][0].get("router", {}).get("_meta"))
