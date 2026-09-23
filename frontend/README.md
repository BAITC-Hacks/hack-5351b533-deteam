# Voice Router — фронтенд

Веб Voice Router по [docs/frontend](../docs/frontend/README.md): звонок, супервизор, эволюция каталога, оператор, каталог сценариев.

Стек: Vite + React 19 + TypeScript, react-router. Node 24 (`/.nvmrc`).

**Контракты не здесь.** Источник правды — [/contracts](../contracts), что строим и какие данные придут — [docs/frontend](../docs/frontend/README.md). Типы фронта в [`src/api/types.ts`](src/api/types.ts) повторяют схемы оттуда.

## Запуск

Фронт работает поверх бэкенда или мок‑сервера команды (он отдаёт данные из `/fixtures` в финальном формате).

```bash
# 1. мок-сервер (из корня репозитория)
python -m venv .venv
.venv/bin/pip install -r tools/mock-server/requirements.txt          # Windows: .venv\Scripts\pip ...
PYTHONUTF8=1 .venv/bin/python tools/mock-server/server.py             # :8000

# 2. фронт
cd frontend
npm install
npm run dev                                                           # http://127.0.0.1:5173
```

На Windows в PowerShell: `$env:PYTHONUTF8=1; .venv\Scripts\python tools\mock-server\server.py`. Без `PYTHONUTF8` мок падает на кириллице в фикстурах.

Dev‑сервер проксирует `/api` и `/ws` на `http://127.0.0.1:8000`. Другой адрес — `VITE_BACKEND_URL` в `.env.local` (см. `.env.example`).

## Экраны

| Путь | Что показывает | Данные |
|---|---|---|
| `/call` | Звонок для жюри: «звоню с номера», микрофон (открытый или push‑to‑talk пробелом), перебивание, «стоп», транскрипт по мере речи, живая гипотеза роутера, карточки действий «ждём да» → «выполнено», баннер перевода, состояние диалога, трассировка каждого хода, медиана задержки по звонку | `WS /ws/voice`: PCM16 16 kHz с микрофона ([worklet](public/pcm-capture-worklet.js)), ответ PCM16 24 kHz |
| `/supervisor` | Ключевые цифры, журнал **всех** звонков, очередь спорных ходов, распределения | `GET /api/stats`, `GET /api/sessions`, `WS /ws/supervisor` |
| `/supervisor/sessions/:id?turn=N` | Лента тем, весь диалог, трассировка хода, состояние в конце, «неверно» → кейс | `GET /api/sessions/{id}`, `GET/POST /api/cases` |
| `/evolution` | Кейсы → «предложить патч» → стадии → дифф каталога и метрики до/после → применить/отклонить; версии с трендом точности и откатом; заморозка; прогоны bench | `/api/cases`, `/api/patches*`, `/api/catalog/*`, `/api/bench*`, события `patch.*`, `bench.*`, `catalog.changed` |
| `/operator` | Очередь переводов: резюме, клиент, темы, слоты, эмоция, хвост разговора | события `handoff` из `WS /ws/supervisor`, прошлые — из `/api/sessions/{id}/events` |
| `/scenarios` | Каталог текущей версии по доменам | `GET /api/catalog` |

В шапке всегда: версия каталога, хэш, замок «заморожен» (обновляется по `catalog.changed`) и статус живого потока.

### Трассировка хода (`trace.schema.json`)

Реплика и язык, эмоция/срочность → решение (`run`, `continue`, `clarify`, `handoff`…), быстрый путь без роутера, спекулятивное попадание, источник ответа → сценарии хода (их может быть несколько: сегмент реплики, `reason`, `boundary_rule`) и альтернативы, предупреждение «на грани», если отрыв < 15 п.п. → второе мнение → слоты и действия (`:preview` жёлтым, `:error` красным) → активный сценарий и стек → ответ бота → водопад задержек `stt → triage → router → response → tts_first_audio`, итог крупно: зелёный ≤ 1500 мс, жёлтый ≤ 3000.

### Живое обновление

Одно подключение к `WS /ws/supervisor` на всё приложение ([`lib/live.tsx`](src/lib/live.tsx)), переподключение раз в 3 с. По событиям `session.created`, `session.closed`, `turn.trace`, `case.created` журнал и статистика перезапрашиваются, карточка звонка — по событиям своей сессии, каталог — по `catalog.changed`.

## Структура

```
src/
  api/            типы по контрактам, REST-клиент
  lib/            живой поток WS, каталог и клиенты (названия вместо SC17/C007), подписи
  hooks/          загрузка данных, голосовая сессия /ws/voice
  components/
    ui.tsx        Panel, Badge, ScenarioChip, BarList, TotalLatency…
    trace/        трассировка хода, водопад задержек
    supervisor/   сводка, спорные ходы, итог звонка, форма «неверно»
    evolution/    кейсы, прогресс и карточка патча, версии, bench
    call/         живой транскрипт, состояние диалога
  audio/          захват микрофона и воспроизведение PCM
  pages/          Call / Supervisor / Session / Evolution / Operator / Scenarios
```

## Мок‑сервер: что учесть

- `/ws/voice?fixture=web-d03|web-d04|phone-p01` проигрывает записанный диалог: ход запускает текст, отпускание push‑to‑talk или 0,6 с голоса + 0,5 с тишины. Распознанный текст берётся из фикстуры, а не из сказанного. Звук бота — тон 440 Гц.
- Данные статичны: новые звонки не попадают в журнал, кейсы и патчи не сохраняются. Применение патча, заморозку и откат фронт отражает по ответам и событиям `catalog.changed`.
- Не реализованы: отклонение патча, запуск bench, удаление кейса, откат — фронт покажет ошибку запроса.

## TODO

- [ ] Проверить звонок на реальном бэкенде: VAD, партиалы, перебивание
- [ ] Дизайн
