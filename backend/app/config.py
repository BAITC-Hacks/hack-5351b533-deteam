"""Конфигурация из окружения. Все модели только OpenAI."""
import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
load_dotenv(BACKEND / ".env")
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
CATALOG_DIR = Path(os.getenv("CATALOG_DIR", ROOT / "catalog"))
REGRESSION_DIR = Path(os.getenv("REGRESSION_DIR", ROOT / "regression"))
DB_PATH = Path(os.getenv("DB_PATH", BACKEND / "voicerouter.sqlite"))

ROUTER_MODEL = os.getenv("ROUTER_MODEL", "gpt-5.6-luna")
ROUTER_FALLBACK_MODEL = os.getenv("ROUTER_FALLBACK_MODEL", "gpt-6-luna")
SECOND_OPINION_MODEL = os.getenv("SECOND_OPINION_MODEL", "gpt-6-sol")
RESPONSE_MODEL = os.getenv("RESPONSE_MODEL", "gpt-6-luna")
PATCHER_MODEL = os.getenv("PATCHER_MODEL", "gpt-6-sol")
STT_MODEL = os.getenv("STT_MODEL", "gpt-transcribe")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts-2025-12-15")
TTS_VOICE = os.getenv("TTS_VOICE", "marin")
SERVICE_TIER = os.getenv("SERVICE_TIER", "priority") or None

TODAY = os.getenv("TODAY", "2026-10-01")
CONF_RUN = float(os.getenv("CONF_RUN", "0.75"))
CONF_CLARIFY = float(os.getenv("CONF_CLARIFY", "0.45"))
