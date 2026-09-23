# Фронтенд Voice Router: что строим и какие данные придут

Для фронтендера. Бэкенд пишется параллельно, поэтому работаем от контракта и мок‑сервера, который уже отдаёт данные в финальном формате.

- Схемы событий: [/contracts/ws-events.schema.json](../../contracts/ws-events.schema.json), трассировка [trace.schema.json](../../contracts/trace.schema.json), состояние [dialog-state.schema.json](../../contracts/dialog-state.schema.json), эволюция [evolution.schema.json](../../contracts/evolution.schema.json)
- REST: [/contracts/openapi.yaml](../../contracts/openapi.yaml)
- Живые примеры: [/fixtures](../../fixtures), разбор диалогов по ходам: [DIALOG_EXAMPLES.md](DIALOG_EXAMPLES.md)
- Концепт продукта: [/docs/concept/ARCHITECTURE.md](../concept/ARCHITECTURE.md), фича эволюции: [EVOLUTION.md](../concept/EVOLUTION.md)

## Быстрый старт с моком

```bash
python -m venv .venv && .venv/bin/pip install -r fixtures/mock-server/requirements.txt
.venv/bin/python fixtures/mock-server/server.py      # http/ws :8000
```

`ws://localhost:8000/ws/voice?fixture=web-d03` проигрывает диалог по ходам. Следующий ход запускается текстом (`text.input`), кнопкой push‑to‑talk (`input.commit`) или голосом: 0.6 с речи и 0.5 с тишины. Доступные фикстуры: `web-d03`, `web-d04`, `phone-p01`. Аудио бота в моке это тон 440 Гц нужной длины. REST отвечает данными из `fixtures/supervisor`. `POST /api/patches` запускает сценарий эволюции: 7 событий `patch.progress` раз в 2 секунды, затем `patch.ready`.

## Экраны

| Экран | Для кого | Главное |
|---|---|---|
| **Звонок** | жюри, клиент | микрофон, транскрипт, ответ бота, выбор «звоню с номера», текстовый ввод, трассировка каждого хода |
| **Супервизор** | мы на демо, супервизор | список сессий, статистика, спорные ходы, пометка ошибки |
| **Эволюция** | киллер‑фича | кейсы, прогоны bench, карточка патча с диффом и метриками до/после, версии каталога |
| **Оператор** | демо перевода | карточка передачи: очередь, резюме, клиент, слоты, хвост транскрипта |

### Звонок

- **Шапка.** Версия каталога и замок «заморожен» из `session.ready.catalog_version` / `frozen`, обновлять по `catalog.changed`. Индикатор канала.
- **«Звоню с номера».** Выпадающий список из `GET /api/clients`, значение уходит в `session.start.caller_phone`. Вариант «неизвестный номер» = `null`.
- **Микрофон.** Кнопка старт/стоп, режим «открытый микрофон» и режим push‑to‑talk (зажать пробел, по отпусканию `input.commit`). Переключатель «перебивание»: если выключено, во время речи бота кадры не отправляем.
- **Транскрипт.** Партиалы `stt.partial` серым в текущем пузыре, `stt.final` фиксирует текст и язык. Ответ бота из `bot.text.delta` / `bot.text.final`. Бейдж языка у каждой реплики.
- **Живая гипотеза.** Пока клиент говорит, над пузырём чипы сценариев из `router.hypothesis` с уверенностью. Смена кандидата посреди фразы должна быть заметна, это наш вау‑момент.
- **Трассировка хода** из `turn.trace`, раскрывающаяся карточка под репликой:
  - сценарии с уверенностью (бар), сегмент реплики, `reason`, `boundary_rule`;
  - альтернативы серым;
  - `decision` бейджем (`run`, `continue`, `confirm`, `clarify`, `handoff`, `out_of_scope`, `goodbye`);
  - слоты таблицей;
  - действия: обычные, `:preview` жёлтым, `:error` красным;
  - водопад задержек `stt → triage → router → response → tts_first_audio`, итог `total` крупно, зелёный ≤ 1500, жёлтый ≤ 3000;
  - метки `speculative_hit`, `fast_path`, `response_source = template`, `router_model`.
- **Действия.** `action.preview` показывает карточку «ждём подтверждения» с входными данными, `action.executed` меняет её на выполнено или ошибку.
- **Состояние диалога** из `dialog.state`: клиент, активный сценарий и шаг, стек отложенных тем, слоты, ожидаемое подтверждение.
- **Перевод.** `handoff` показывает баннер «Переводим в очередь …» с резюме.
- **Медиана задержки** по сессии внизу экрана, её сверяют с секундомером.

### Супервизор

`GET /api/stats`, `GET /api/sessions`, `GET /api/sessions/{id}`, живой поток `WS /ws/supervisor`. Показываем распределение сценариев, гистограмму уверенности, p50/p95 по этапам, доли шаблонов, спекулятивных попаданий, переводов и уточнений, разбивку по языкам. Очередь спорных ходов из `stats.flagged_turns`. У любого хода кнопка «неверно»: мультивыбор правильных сценариев с сохранением порядка, затем `POST /api/cases`.

### Эволюция

1. Список кейсов `GET /api/cases` с источником, ожиданием и наблюдением.
2. Кнопка «предложить патч» по выбранным кейсам: `POST /api/patches`, прогресс из `patch.progress` (стадии `proposing → validating → regression_before → regression_after → ready`), по `patch.ready` загрузить `GET /api/patches/{id}`.
3. Карточка патча: `rationale`, дифф из `diff[]` (поле, было, стало), таблица метрик `regression.before/after` с дельтой, списки `fixed` и `broken`, `validator_log`, кнопки «применить» (`POST /api/patches/{id}/apply`) и «отклонить».
4. История версий `GET /api/catalog/versions`: хэш, родитель, патч, `primary_acc`, откат `POST /api/catalog/rollback`. Тренд точности по версиям графиком.
5. Прогоны `GET /api/bench/runs`: метрики по языкам и типам, корзины уверенности, ошибки.
6. Переключатель заморозки `POST /api/catalog/freeze`. При заморозке «применить» и «откат» заблокированы.

### Оператор

Слушает `/ws/supervisor`, показывает события `handoff` карточкой по очереди: резюме, клиент, сценарии, слоты, эмоция, последние реплики. На демо это экран «живого специалиста», которому не нужно переспрашивать.

## Протокол WebSocket `/ws/voice`

Подключение: `ws://host:8000/ws/voice`. Первый кадр клиента `session.start`. Каждое событие несёт конверт `type`, `t` (мс от старта сессии по часам сервера), `session_id`, `turn`. Ход 0 это приветствие бота.

**Аудио от клиента.** Бинарные кадры PCM16 little‑endian, 16 kHz, моно, по 20 мс, то есть 640 байт. Захват через `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}})`, ресемпл 48 → 16 kHz в AudioWorklet.

**Аудио от сервера.** Бинарные кадры PCM16 LE, 24 kHz, моно, произвольной длины. Идут между `tts.start` и `tts.end` своего хода. Проигрывать через очередь буферов без пауз. По `tts.interrupt` очередь сбросить немедленно. Когда реально пошёл звук первого чанка хода, отправить `playback.started`, после окончания `playback.finished`.

**События клиента:** `session.start`, `text.input`, `input.commit`, `playback.started`, `playback.finished`, `control.interrupt`, `control.mute`, `session.end`.

**Порядок событий хода:**

```text
vad.speech_start
stt.partial ×N          router.hypothesis ×M    (вперемешку)
vad.speech_end          ← t0 замера задержки
stt.final
action.executed ×K / action.preview
bot.text.delta ×… → bot.text.final
tts.start → [аудио] → turn.trace → dialog.state → tts.end
[handoff] [session.end]
```

`turn.trace` приходит после первого аудио‑чанка, потому что содержит `tts_first_audio` и `total`. Не полагаться на строгий порядок между аудио и трассировкой, связывать по `turn`. Для текстового ввода нет `vad.*` и `stt.partial`, `stt = 0`.

## Шаблонные данные

| Файл | Что внутри |
|---|---|
| `fixtures/sessions/web-d03.jsonl` | статус КАСКО → смена темы на продление с рассрочкой → возврат к заявлению, стек тем, инструменты, спекулятивные попадания |
| `fixtures/sessions/web-d04.jsonl` | caller ID, смешанная речь kk/ru, `book_appointment:preview` → подтверждение + второй вопрос в той же реплике |
| `fixtures/sessions/phone-p01.jsonl` | телефон, неясный запрос → уточнение → жалоба с эмоцией → просьба о человеке → `handoff` |
| `fixtures/supervisor/*.json` | статистика, сессии, кейсы, прогоны bench, патч с диффом, версии каталога |
| `fixtures/catalog/*.json` | 40 сценариев с русскими названиями и флагами, действия, персоны с телефонами |

Проверка, что фикстуры и схемы согласованы: `python contracts/tools/validate.py`.

## Что важно не забыть

- Все надписи на русском, язык реплик бейджем `ru` / `kk` / `mixed`.
- Числа задержек моноширинным шрифтом, без анимации пересчёта: жюри сверяет цифры.
- Текстовый ввод доступен всегда, но второстепенен, микрофон главный.
- Экран звонка должен читаться с проектора: крупный транскрипт, трассировка сворачивается.
