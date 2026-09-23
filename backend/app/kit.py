"""Данные стартового кита: сценарии, слоты, действия, база знаний, мок-бэкенд."""
import json, copy
from functools import lru_cache
from .config import DATA_DIR

def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))

@lru_cache
def slots() -> dict:
    return {s["name"]: s for s in _load("slots.json")["slots"]}

@lru_cache
def actions() -> dict:
    return {a["name"]: a for a in _load("actions.json")["actions"]}

@lru_cache
def kb() -> dict:
    return _load("knowledge_base.json")

@lru_cache
def base_catalog() -> dict:
    return _load("scenarios.json")

def mock_backend() -> dict:
    """Свежая копия на каждую сессию моков: создание полисов не портит исходник."""
    return copy.deepcopy(_mock_raw())

@lru_cache
def _mock_raw():
    return _load("mock_backend.json")

@lru_cache
def dev_utterances() -> list:
    return _load("dev_utterances.json")["utterances"]

@lru_cache
def sample_dialogs() -> list:
    return _load("dialogs_sample.json")["dialogs"]

SYS_IDS = ["SYS_OUT_OF_SCOPE", "SYS_UNCLEAR", "SYS_GOODBYE"]
