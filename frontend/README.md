# Voice Router — фронтенд

Веб-интерфейс симуляции голосового робота: клиент говорит в микрофон, робот отвечает голосом,
супервизор видит, какой сценарий выбран, почему и за сколько.

Стек: Vite + React 19 + TypeScript, react-router. Node 24 (см. `/.nvmrc`).

## Запуск

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

По умолчанию фронт работает **на моках** (`src/api/mock.ts`) и не требует бэкенда — в шапке горит «MOCK-режим».
Чтобы подключить настоящий бэкенд:

```bash
cp .env.example .env.local
# VITE_USE_MOCK=false
# VITE_BACKEND_URL=http://localhost:8000   ← dev-сервер проксирует туда все /api/*
```

## Экраны

| Путь | Экран | Для кого | Что показывает |
|---|---|---|---|
| `/` | Симулятор звонка | жюри / клиент | Слева диалог: микрофон (удерживать, пока говоришь), текстовый ввод как резерв, выбор тестового клиента. Справа — трассировка выбранной реплики |
| `/supervisor` | Супервизор | супервизор | Сводные метрики (точность, задержки, доля быстрого пути, переспросы, оператор), журнал реплик с фильтрами, разметка «верно / ошибка → правильный сценарий», таблица путаницы сценариев |
| `/scenarios` | Каталог | все | 40 сценариев: описание, границы с соседями, параметры, действия, примеры RU/KZ |

### Трассировка реплики (must-have из ТЗ)

1. **Транскрипт** — что распознали, язык (RU / KZ / RU+KZ), голос или текст, эмоция.
2. **Решение** — тип (запуск / смена темы / возврат к теме / переспрос / оператор), путь (быстрый или LLM), выбранный сценарий с уверенностью и обоснованием. Отдельно подсвечиваются передача оператору и необратимые действия, ждущие подтверждения клиента.
3. **Альтернативы** — следующие кандидаты с уверенностью и причиной, почему не они.
4. **Параметры из речи** — извлечённые и недостающие.
5. **Контекст** — активная тема, стек прерванных тем, очередь тем из одной реплики.
6. **Задержка по этапам** — STT → выбор сценария → генерация ответа → TTS, с ориентирами 500 мс и 1,5 с.

## Как устроен поток реплики

```
[удержание 🎤] → MediaRecorder → Blob (webm)
      → POST /api/sessions/{id}/turns (multipart: audio)   ← или JSON { text }
      ← Turn { user, bot, trace }
      → реплика в ленту, trace в правую панель
      → озвучка: bot.audio_url, если есть; иначе браузерный speechSynthesis (заглушка)
```

Статусы звонка: `idle → listening → thinking → speaking → idle`. Лимит — 10 реплик на диалог.

## API-контракт

Все типы — в [`src/api/types.ts`](src/api/types.ts), это единственный источник правды для фронта.
HTTP-клиент — [`src/api/http.ts`](src/api/http.ts). Поля в snake_case.

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/api/clients` | — | `Client[]` (тестовые клиенты из `mock_backend.json`) |
| GET | `/api/scenarios` | — | `Scenario[]` (из `scenarios.json`) |
| POST | `/api/sessions` | `{ client_id: string \| null }` | `Session` |
| POST | `/api/sessions/{id}/turns` | multipart `audio` **или** JSON `{ text }` | `Turn` |
| POST | `/api/sessions/{id}/end` | — | 204 |
| GET | `/api/supervisor/stats` | — | `SupervisorStats` |
| GET | `/api/supervisor/turns?filter=` | `all \| low_confidence \| clarify \| handoff \| marked_wrong \| slow` | `Turn[]` (новые сверху) |
| POST | `/api/turns/{id}/feedback` | `{ correct, expected_scenario_id?, comment? }` | 204 |

Пример `Turn`:

```json
{
  "id": "turn_7", "session_id": "session_2", "index": 1, "created_at": "2026-09-23T10:00:00Z",
  "user": { "text": "Я вчера оплатил…", "lang": "ru", "input": "voice", "emotion": "neutral" },
  "bot":  { "text": "Вижу платёж, проверяю статус.", "lang": "ru", "audio_url": "/api/audio/turn_7.mp3" },
  "trace": {
    "route_path": "llm",
    "decision": "run_scenario",
    "selected": { "id": "payment_not_confirmed", "title": "Оплата прошла, полис не подтверждён", "confidence": 0.91, "reason": "…" },
    "alternatives": [{ "id": "claim_status", "title": "Статус выплаты", "confidence": 0.22, "reason": "Клиент платит, а не получает" }],
    "rationale": "Клиент описывает списание без активации договора; вторая тема — смена адреса — поставлена в очередь.",
    "params": { "date": "2026-09-22" },
    "missing_params": ["amount"],
    "context": { "active": { "id": "payment_not_confirmed", "title": "…" }, "interrupted": [], "queued": [{ "id": "change_address", "title": "…" }] },
    "pending_confirmation": null,
    "handoff": null,
    "latency": { "stt_ms": 240, "routing_ms": 410, "response_ms": 300, "tts_ms": 180, "total_ms": 1130 },
    "model": "…"
  }
}
```

Если бэкенду удобнее другая форма — меняем `types.ts`, TypeScript покажет все места, которые надо поправить.

## Структура

```
src/
  api/
    types.ts          контракт с бэкендом
    http.ts           реальный клиент
    mock.ts           фейковый бэкенд для разработки UI (НЕ роутер: примитивный поиск ключевых слов)
    index.ts          выбор mock/http по VITE_USE_MOCK
  hooks/
    useCallSession.ts состояние звонка: сессия, реплики, статус, отправка, озвучка
    useRecorder.ts    push-to-talk запись с микрофона
  lib/                форматирование, воспроизведение ответа
  components/
    ui.tsx            Panel, Badge, Kpi и т.п.
    call/             лента диалога, панель звонка
    trace/            трассировка и таймлайн задержек
  pages/              Simulator / Supervisor / Scenarios
```

## TODO

- [ ] Подключить реальный бэкенд, сверить контракт
- [ ] Загрузить настоящие сценарии и клиентов из стартового кита
- [ ] Стриминг (WebSocket): частичный транскрипт и ранний старт ответа
- [ ] Детектор конца речи (VAD) вместо удержания кнопки
- [ ] Подтверждение необратимых действий кнопками «да / нет» в UI
- [ ] Редактирование каталога сценариев
- [ ] Дизайн
