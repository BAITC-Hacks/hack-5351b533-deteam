"""Проверка fixtures на соответствие контрактам: python tools/validate_contracts.py"""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
R = Path(__file__).resolve().parents[1]
schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in (R / "contracts").glob("*.schema.json")}
reg = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in schemas.values()])
V = lambda name, ref=None: Draft202012Validator({"$ref": schemas[name]["$id"] + (ref or "")}, registry=reg)
bad = 0
def check(v, obj, where):
    global bad
    for e in v.iter_errors(obj): bad += 1; print("FAIL", where, list(e.absolute_path), e.message[:160])
ev = V("ws-events.schema.json")
for f in sorted((R / "fixtures/sessions").glob("*.jsonl")):
    for i, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1): check(ev, json.loads(l), f"{f.name}:{i}")
evo = "evolution.schema.json"
for file, ref in [("cases.json", "#/$defs/Case"), ("bench_runs.json", "#/$defs/BenchRun"), ("patches.json", "#/$defs/Patch"), ("catalog_versions.json", "#/$defs/CatalogVersion")]:
    for o in json.loads((R / "fixtures/supervisor" / file).read_text(encoding="utf-8")): check(V(evo, ref), o, file)
print("OK" if not bad else f"{bad} errors"); sys.exit(1 if bad else 0)
