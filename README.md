# Voice Router — Saqta Insurance (DeTeam)

Голосовой AI‑агент контакт‑центра вымышленной страховой Saqta Insurance. Вместо классификатора интентов сценарий выбирает LLM‑роутер: он читает каталог из 40 сценариев как данные, возвращает строгий JSON (сценарии, уверенность, слоты, язык, нужен ли переспрос) и работает на русском, казахском и их смеси. Дальше исполнитель ведёт сценарий по плану: собирает слоты, вызывает мок‑системы, спрашивает подтверждение перед необратимыми действиями, держит стек тем. Ответ озвучивается голосом, по вебу или по телефону через Asterisk. Каждый ход виден в панели трассировки. Каталог умеет улучшать себя сам: неудачный кейс превращается в патч, патч проходит валидатор и регрессию и только после этого применяется. HackAlem AI, трек Halyk Bank, кейс 2.

## Запуск одной командой

Нужны Docker и ключ OpenAI в `backend/.env`:

```bash
echo "OPENAI_API_KEY=sk-..." > backend/.env
docker compose up --build            # API и WS на :8000, AudioSocket на :9092
curl localhost:8000/api/health
```

Фронт (звонок из браузера, трассировка, супервизор, каталог):

```bash
cd frontend && npm ci && npm run dev  # http://localhost:5173, /api проксируется на :8000
```

Телефония: у Asterisk свой compose в [infra/asterisk](infra/asterisk/README.md) (`cd infra/asterisk && docker compose up -d --build`). Бэкенду при этом нужен `ARI_URL` (см. закомментированные строки в [docker-compose.yml](docker-compose.yml)).

Без Docker: `make venv && make run`. Все команды: `make help`.

## Как устроен один ход

```
 микрофон (веб, PCM16 16 kHz)  /  телефон (Asterisk -> AudioSocket, slin 8 kHz)
        │
        ▼
 VAD Silero (onnx) + endpointing ── перебивание: tts.interrupt, остановка аудио
        │ конец реплики
        ▼
 STT gpt-transcribe (2 параллельных запроса, берём первый)
        │ текст
        ▼
 ┌─ fast paths без LLM: да/нет на подтверждение, ожидаемый слот по шаблону,
 │  «оператор», прощание, «вы робот?» ── ~1 с до звука
 │
 └─ LLM-роутер gpt-5.6-luna
      · 2 хедж-стрима, строгая JSON-схема, закэшированный промпт с каталогом 40 сценариев
      · раннее решение по первым полям стрима -> сразу играем подтверждение из аудиокэша
      │
      ▼  уверенность в зоне 0.45–0.75?
    второе мнение gpt-6-sol (только в серой зоне)
      │
      ▼
 политика решения: run / clarify / confirm / transfer (пороги CONF_RUN, CONF_CLARIFY)
        │
        ▼
 исполнитель сценариев
      · планы для 40 сценариев, сбор и нормализация слотов (slots.json)
      · мок-действия (31 action поверх mock_backend.json и knowledge_base.json)
      · confirmation gate перед необратимыми действиями
      · стек тем: отвлёкся на другой вопрос -> вернулись к прерванному сценарию
        │ факты
        ▼
 ответ: шаблон или gpt-6-luna (стриминг по предложениям, только по фактам исполнителя)
        │
        ▼
 TTS gpt-4o-mini-tts, голос marin, кэш заготовленных фраз на диске
        │
        ▼
 аудио клиенту

 параллельно: каждое событие хода -> панель трассировки (/ws/voice) и супервизор (/ws/supervisor)
```

Подробно: [ARCHITECTURE.md](docs/concept/ARCHITECTURE.md), [ROUTER.md](docs/concept/ROUTER.md).

## Главная фича: auto‑bench с эволюцией каталога

```
 кейс (ошибка роутера из звонка или сгенерированный)
   -> gpt-6-sol пишет патч каталога (описания, примеры, disambiguation)
   -> валидатор: схема, ссылки на слоты/действия, анти-хардкод (фразы кейса нельзя вписать дословно)
   -> регрессия до/после на dev-наборе и накопленных кейсах
   -> применить новую версию каталога или откатить
```

Каталог версионирован (`catalog/`), любую версию можно откатить. На время оцениваемого прогона каталог замораживается (`POST /api/catalog/freeze`), эволюция в этот момент ничего не применяет. Подробно: [EVOLUTION.md](docs/concept/EVOLUTION.md).

## Телефония

Основная схема: Asterisk отдаёт звонок в Stasis‑приложение `voice-ai`, бэкенд управляет им через ARI и поднимает External Media в свой AudioSocket‑сервер (:9092). Запасная схема: dialplan сам вызывает `AudioSocket()` без ARI. Перевод на оператора, прощание и завершение звонка идут через тот же контроллер. Подробно: [docs/telephony/README.md](docs/telephony/README.md), конфиги в [contracts/asterisk](contracts/asterisk).

## Модели

Только OpenAI, все id переопределяются через переменные окружения:

| Роль | Модель |
|---|---|
| Роутер | `gpt-5.6-luna` (запасной `gpt-6-luna`) |
| Второе мнение | `gpt-6-sol` |
| Ответы | `gpt-6-luna` |
| Патчи каталога | `gpt-6-sol` |
| STT | `gpt-transcribe` |
| TTS | `gpt-4o-mini-tts-2025-12-15`, голос `marin` |
| VAD | Silero VAD (onnx, локально, MIT) |

## Измеренные результаты

| Что | Результат | Как проверить |
|---|---|---|
| Dev‑набор, primary accuracy | 0.99–1.00 | `make bench` |
| Dev‑набор, full match | 0.99–1.00 | `make bench` |
| Диалоги кита | 40/40 ходов маршрутизированы верно | `make replay` |
| Сценарные диалоги (40 happy + 40 edge) | 79–80/80 | `make test` |
| Задержка хода в тексте, p50 | ~1.4–1.6 с | `make replay` |
| Голос, конец речи -> первый звук (веб) | 2.0–3.5 с | `make probe-voice` |
| Телефон, конец речи -> первый звук | 2.2–3.3 с на ходах через роутер, ~1 с на fast paths | `make probe-phone` |
| Эволюция: патч от кейса до результата регрессии | 27–38 с | панель «Эволюция», `POST /api/patches` |
| Эволюция: применение патча | ~2 с | `POST /api/patches/{id}/apply` |

## Как проверить самому

```bash
make venv          # .venv с зависимостями бэкенда
make bench         # роутер на data/dev_utterances.json + data/evaluate.py
make replay        # диалоги кита в текстовом режиме
make test          # 80 сценарных диалогов, без сервера
make validate      # фикстуры против JSON-схем контрактов
make run           # сервер, затем в другом терминале:
make probe-voice   # голосовой звонок без микрофона через /ws/voice
make probe-phone   # звонок «как Asterisk» по AudioSocket
```

## Структура репозитория

| Путь | Что внутри |
|---|---|
| [backend/](backend/README.md) | голосовой агент: VAD, STT, роутер, исполнитель, TTS, bench, эволюция, телефония |
| [frontend/](frontend/README.md) | веб: звонок, трассировка, супервизор, каталог и эволюция |
| [infra/asterisk/](infra/asterisk/README.md) | Asterisk в Docker (PJSIP, ARI), MinIO и загрузчик полных записей звонков |
| [contracts/](contracts) | JSON‑схемы событий WS, выхода роутера, REST (OpenAPI), конфиги Asterisk |
| [fixtures/](fixtures) | шаблонные сессии и данные супервизора для фронта |
| [tools/](tools) | мок‑сервер, проверка фикстур против схем |
| [data/](data) | стартовый кит кейса: сценарии, слоты, действия, база знаний, `evaluate.py` |
| [docs/](docs) | условия кейса, концепт, контракты для фронта и телефонии |

## Ограничения

- Задержка зависит от OpenAI API: сети, нагрузки и rate limits. По умолчанию запросы идут с `service_tier=priority` (`SERVICE_TIER`).
- Казахский TTS звучит хуже русского: ударения и отдельные слова.
- Вместо реальных систем страховой стоят моки по `mock_backend.json`.
- SC16 не может завершиться: в мок‑данных нет полиса, покрывающего ДТП, поэтому такой звонок уходит оператору.
- Роутер недетерминирован: между прогонами метрики плавают примерно на 1 п.п.
- Bench и эволюция нагружают тот же ключ API. Во время живого звонка их лучше не запускать.

## Документы

| Кому | Куда смотреть |
|---|---|
| Жюри и команде | [ARCHITECTURE.md](docs/concept/ARCHITECTURE.md), [ROUTER.md](docs/concept/ROUTER.md), [EVOLUTION.md](docs/concept/EVOLUTION.md) |
| Бэкенд | [backend/README.md](backend/README.md) |
| Фронтенд | [docs/frontend/README.md](docs/frontend/README.md), [DIALOG_EXAMPLES.md](docs/frontend/DIALOG_EXAMPLES.md), [frontend/README.md](frontend/README.md) |
| Телефония | [docs/telephony/README.md](docs/telephony/README.md), [infra/asterisk/README.md](infra/asterisk/README.md) |
| Контракты | [contracts/](contracts) |
| Условия кейса | [docs/case/README.ru.md](docs/case/README.ru.md), [ТЗ](docs/case/TZ_Voice_Router.pdf) |
Для сквозной проверки телефонии на одном компьютере есть `infra/asterisk/compose.smoke.yaml`: он добавляет к Asterisk мок AudioSocket и временный ARI-контроллер. Основной Compose сохраняет полные записи звонков в MinIO и уведомляет backend (`POST /api/telephony/calls/{call_id}/recording`). Настройка `.env`, SIP-аккаунтов и порядок проверки описаны в [infra/asterisk/README.md](infra/asterisk/README.md).
