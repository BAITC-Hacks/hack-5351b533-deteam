"""Прогон размеченных диалогов кита в текстовом режиме: python -m app.replay [D01 D03 ...]"""
import asyncio, sys, json, time
from . import kit
from .session import Session
from .dialog import process_turn

async def replay(d, verbose=True):
    s = Session(channel="text")
    ok = tot = 0; lat = []
    for t in d["turns"]:
        if t["role"] != "client": continue
        tr = await process_turn(s, t["text"])
        got = [x["scenario_id"] for x in tr["scenarios"] if x["confidence"] >= 0.45] if tr["decision"] != "clarify" else ["SYS_UNCLEAR"]
        if tr["decision"] in ("continue", "confirm", "cancel") and tr["fast_path"]: got = got[:1]
        exp = t["scenarios"]; hit = got[:1] == exp[:1]; ok += hit; tot += 1; lat.append(tr["latency_ms"]["total"])
        if verbose:
            print(f"  {'OK ' if hit else 'XX '} C[{t['lang']}] {t['text']}\n      exp={exp} got={got} dec={tr['decision']} fp={tr['fast_path']} acts={tr['actions']} lat={tr['latency_ms']}\n      B[{tr['response_language']}] {tr['response_text']}")
        if s.ended: break
    return ok, tot, lat

async def main(ids):
    ds = [d for d in kit.sample_dialogs() if not ids or d["dialog_id"] in ids]
    T = O = 0; L = []
    for d in ds:
        print(f"=== {d['dialog_id']} {d['title']}")
        o, t, l = await replay(d); O += o; T += t; L += l
    L.sort()
    print(f"\nprimary per turn: {O}/{T}  latency_total p50={L[len(L)//2]} max={L[-1]}")

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
