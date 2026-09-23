"""Политика решения поверх выхода роутера."""
from . import config
from .catalog import CATALOG

def ordered(scenarios: list, version=None) -> list:
    cat = CATALOG.scenarios(version)
    pr = {"urgent": 0, "high": 1, "normal": 1}
    idx = {s["scenario_id"]: i for i, s in enumerate(scenarios)}
    return sorted(scenarios, key=lambda s: (pr.get(cat.get(s["scenario_id"], {}).get("priority", "normal"), 1), idx[s["scenario_id"]]))

def decide(r: dict, unclear_streak: int = 0, version=None) -> dict:
    """Вернуть {'decision', 'scenarios': [ids], 'clarify': [ids]}. Без состояния: продолжения и подтверждения решает сессия."""
    sc = [s for s in r.get("scenarios", []) if s.get("scenario_id")]
    if not sc:
        return {"decision": "clarify", "scenarios": ["SYS_UNCLEAR"], "clarify": [a["scenario_id"] for a in r.get("alternatives", [])][:2]}
    top = sc[0]
    sid = top["scenario_id"]
    if sid == "SYS_GOODBYE": return {"decision": "goodbye", "scenarios": [sid]}
    if sid == "SYS_OUT_OF_SCOPE" and top["confidence"] >= config.CONF_CLARIFY: return {"decision": "out_of_scope", "scenarios": [sid]}
    real = [s for s in sc if not s["scenario_id"].startswith("SYS_")]
    alts = [a["scenario_id"] for a in r.get("alternatives", []) if not a["scenario_id"].startswith("SYS_")]
    if sid == "SYS_UNCLEAR" or not real or real[0]["confidence"] < config.CONF_RUN:
        opts = ([s["scenario_id"] for s in real] + alts)[:2]
        if (real and real[0]["confidence"] < config.CONF_CLARIFY or not real) and unclear_streak >= 1:
            return {"decision": "handoff", "scenarios": ["SC37"], "clarify": opts}
        return {"decision": "clarify", "scenarios": ["SYS_UNCLEAR"], "clarify": opts}
    keep = [s for s in real if s["confidence"] >= config.CONF_CLARIFY]
    return {"decision": "run", "scenarios": [s["scenario_id"] for s in ordered(keep, version)]}
