# Backend

Голосовой агент: транспорт WS и AudioSocket, VAD, STT, роутер, исполнитель сценариев, TTS, bench и эволюция каталога.
Контракты в [/contracts](../contracts), концепт в [/docs/concept](../docs/concept/ARCHITECTURE.md), данные кита в [/data](../data).

## Запуск

```bash
# из корня репозитория
make venv                  # .venv + requirements.txt + websockets jsonschema referencing
echo "OPENAI_API_KEY=sk-..." > backend/.env
make run                   # uvicorn app.main:app на :8000, AudioSocket на :9092
make up                    # то же в Docker (docker compose up --build)
```

## Проверки

| Команда | Что делает |
|---|---|
| `make bench` | `python -m app.bench` на `data/dev_utterances.json`, затем `data/evaluate.py` по `regression/predictions.json` |
| `make replay` | `python -m app.replay [D01 ...]`: диалоги кита в текстовом режиме, точность и задержки |
| `make test` | `python -m tests.scenario_dialogs [SC01 E05] [-v] [--tag edge] [-j 10] [--out f.json]`: 80 сценарных диалогов в процессе, без сервера |
| `make probe-voice` | `tools/voice_probe.py`: синтез фраз клиента -> `/ws/voice` кадрами 20 мс, задержки, проверка событий по схеме (`--mode voice/commit/text/barge`, `--noise`, `--vad-selftest`) |
| `make probe-phone` | `tools/phone_probe.py`: притворяется Asterisk-ом, регистрирует звонок и шлёт slin 8 kHz по AudioSocket (`--barge`, `--expect transfer/hangup`, `--unregistered`) |
| `make validate` | `tools/validate_contracts.py`: фикстуры против JSON-схем |

Probe-скрипты требуют запущенного сервера. У `phone_probe.py` порты по умолчанию 8013/9093, Makefile передаёт 8000/9092.

## Модули `app/`

| Файл | Что внутри |
|---|---|
| `main.py` | FastAPI: lifespan (прогрев роутера, прогрев TTS, AudioSocket, ARI), REST, `/ws/voice`, `/ws/supervisor` |
| `config.py` | конфигурация из окружения и `.env`, id моделей, пороги |
| `kit.py` | загрузка кита из `data/`: сценарии, слоты, действия, база знаний, мок-бэкенд |
| `catalog.py` | версии каталога сценариев (v1 = `data/scenarios.json`), патчи, freeze, откат |
| `router.py` | LLM-роутер: стриминг, строгая JSON-схема, кэшированный промпт с каталогом, хедж-стримы, второе мнение |
| `policy.py` | политика решения поверх выхода роутера (run / clarify / confirm / handoff) |
| `session.py` | сессия диалога: состояние, fast paths + роутер, исполнение сценариев, стек тем, трассировка |
| `dialog.py` | один ход: понимание -> исполнение -> ответ (ack + шаблон/LLM, стриминг по предложениям) |
| `plans.py` | идемпотентные планы действий для 40 сценариев |
| `normalize.py` | детерминированная нормализация и валидация слотов по `slots.json` |
| `mocks.py` | мок-действия из `actions.json` поверх `mock_backend.json` и `knowledge_base.json` |
| `responder.py` | ответ бота: мгновенное подтверждение из шаблона (аудио из кэша) + содержательная часть |
| `speech.py` | STT (`gpt-transcribe`, хедж) и TTS (`gpt-4o-mini-tts`) с дисковым кэшем фраз |
| `audio.py` | Silero VAD (onnx), сегментация речи, ресемплинг, WAV |
| `call.py` | звонок, не зависящий от транспорта: VAD -> STT -> ход -> TTS-плеер, перебивание |
| `hub.py` | реестр сессий, журнал событий, поток супервизора, статистика |
| `telephony.py` | AudioSocket-сервер для Asterisk + REST `/api/telephony/*` |
| `ari.py` | ARI-контроллер: Stasis `voice-ai`, External Media в наш AudioSocket, перевод и завершение |
| `evolution.py` | auto-bench с эволюцией: кейсы, прогоны, патчи, валидатор, применение/отклонение |
| `bench.py` | регрессионный прогон роутера, метрики как в `data/evaluate.py` |
| `replay.py` | прогон размеченных диалогов кита в текстовом режиме |
| `assets/` | `silero_vad.onnx`, `scenario_phrases.json` |

## Переменные окружения

Обязательна только `OPENAI_API_KEY` (в `backend/.env` или корневом `.env`).

| Группа | Переменные (значение по умолчанию) |
|---|---|
| Пути | `DATA_DIR` (`data/`), `CATALOG_DIR` (`catalog/`), `REGRESSION_DIR` (`regression/`), `DB_PATH` (`backend/voicerouter.sqlite`) |
| Модели | `ROUTER_MODEL` (`gpt-5.6-luna`), `ROUTER_FALLBACK_MODEL` (`gpt-6-luna`), `SECOND_OPINION_MODEL` (`gpt-6-sol`), `RESPONSE_MODEL` (`gpt-6-luna`), `PATCHER_MODEL` (`gpt-6-sol`), `STT_MODEL` (`gpt-transcribe`), `TTS_MODEL` (`gpt-4o-mini-tts-2025-12-15`), `SERVICE_TIER` (`priority`, пусто = по умолчанию) |
| Роутер | `CONF_RUN` (0.75), `CONF_CLARIFY` (0.45), `ROUTER_HEDGE` (2), `ROUTER_HEDGE_KEYS` (0), `ROUTER_REFILL` (1), `ROUTER_KEEPALIVE_S` (120), `TODAY` (`2026-10-01`, дата для мок-данных) |
| STT | `STT_LANGUAGE` (`kk`), `STT_PROMPT`, `STT_HEDGE` (2), `STT_TIMEOUT` (8) |
| TTS | `TTS_VOICE` (`marin`), `TTS_VOICE_RU`, `TTS_VOICE_KK`, `TTS_TIMEOUT` (15), `TTS_PREWARM` (1) |
| VAD и перебивание | `VAD_SILENCE_MS` (800), `VAD_EARLY_MS` (128), `VAD_SEM_MIN_MS` (300), `BARGE_IN` (1), `BARGE_MIN_MS` (300), `FILLER_MS` (0) |
| AudioSocket | `AUDIOSOCKET` (1), `AUDIOSOCKET_BIND` (`0.0.0.0`), `AUDIOSOCKET_PORT` (9092), `AUDIOSOCKET_HOST` (адрес для External Media, по умолчанию пусто) |
| ARI / AMI | `ARI_URL` (пусто = ARI выключен), `ARI_USER` (`voice-ai`), `ARI_PASSWORD`, `ARI_APP` (`voice-ai`), `ARI_TRANSFER_CONTEXT`, `ARI_FALLBACK_QUEUE` (`operator_general`), `ARI_MEDIA_TIMEOUT` (6), `AMI_TRANSFER_CONTEXT` (`saqta-transfer`) |
| Эволюция | `EVO_BENCH_CONC` (20), `EVO_BENCH_CONC_AFTER` (32), `EVO_RECHECK` (4) |

## API

Полная схема в [contracts/openapi.yaml](../contracts/openapi.yaml), события WS в [contracts/ws-events.schema.json](../contracts/ws-events.schema.json).

| Метод и путь | Назначение |
|---|---|
| `GET /api/health` | живость сервиса |
| `GET /api/clients` | тестовые клиенты из мок-бэкенда |
| `GET /api/catalog` | текущая версия каталога и краткий список сценариев |
| `GET /api/catalog/versions`, `GET /api/catalog/versions/{version}` | история версий каталога |
| `POST /api/catalog/freeze`, `POST /api/catalog/rollback` | заморозка на оцениваемый прогон, откат версии |
| `POST /api/route` | один вызов роутера по тексту (без исполнения) |
| `GET /api/stats` | статистика звонков |
| `GET /api/sessions`, `GET /api/sessions/{sid}`, `GET /api/sessions/{sid}/events` | журнал сессий и события хода |
| `GET/POST /api/cases`, `DELETE /api/cases/{case_id}`, `POST /api/cases/generate` | кейсы для эволюции |
| `POST /api/bench`, `GET /api/bench/runs`, `GET /api/bench/runs/{run_id}` | прогоны bench |
| `POST /api/patches`, `GET /api/patches`, `GET /api/patches/{patch_id}` | патчи каталога (генерация, валидация, регрессия до/после) |
| `POST /api/patches/{patch_id}/apply`, `POST /api/patches/{patch_id}/reject` | применить или отклонить патч |
| `POST /api/telephony/calls`, `GET /api/telephony/calls` | регистрация звонка от Asterisk, список |
| `GET /api/telephony/calls/{call_uuid}/outcome`, `POST /api/telephony/calls/{call_uuid}/hangup` | исход звонка, завершение |
| `WS /ws/voice` | звонок из браузера: аудио PCM16 16 kHz и `text.input` на вход, аудио ответа и события трассировки на выход |
| `WS /ws/supervisor` | поток событий всех сессий для панели супервизора |
| TCP `:9092` | AudioSocket для Asterisk (slin 8 kHz, кадры 20 мс) |

## Где что лежит

| Путь | Что |
|---|---|
| `backend/logs/sessions/` | журналы сессий (события ходов) |
| `backend/tts_cache/` | дисковый кэш TTS заготовленных фраз (прогревается при старте, `TTS_PREWARM=1`) |
| `catalog/` | версии каталога сценариев (`index.json`, `v*.json`) |
| `regression/` | `cases.jsonl`, прогоны bench (`bench_*.json`), `predictions.json`, артефакты патчей |
| `backend/voicerouter.sqlite` | `DB_PATH` |

Все эти каталоги в `.gitignore`. В Docker они смонтированы томами (см. [docker-compose.yml](../docker-compose.yml)).
