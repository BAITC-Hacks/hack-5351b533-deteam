# Роутер: LLM‑слой выбора сценария

Роутер это единственное место, где принимается содержательное решение о маршрутизации. Он работает на `gpt-6-luna` без рассуждений, со структурным выводом и кэшированным каталогом. Никаких энкодерных классификаторов и эмбеддингов на пути выбора сценария нет.

## 1. Вход

Промпт состоит из стабильного префикса (кэшируется) и динамического хвоста.

**Стабильный префикс, system:**

1. Роль и правила: оператор контакт‑центра Saqta, языки ru/kk, дата «сегодня» `2026-10-01`.
2. Компактный каталог 40 сценариев. Для каждого: `scenario_id`, `name`, `domain/category/priority`, `description`, `not_this_if` полностью, 2 примера ru + 1 kk, `slots.required`, `requires_identification`, `requires_confirmation`. Ответы и действия в каталог не кладём, они роутеру не нужны.
3. Системные намерения `SYS_OUT_OF_SCOPE`, `SYS_UNCLEAR`, `SYS_GOODBYE` с их описаниями.
4. Правила разграничения: «авария» это SC11 если сейчас на месте, SC12 если пострадавший по ОГПО виновника, SC13 если КАСКО; несогласие с решением это SC19, а не статус SC17; и все остальные `not_this_if`.
5. Правила вывода: несколько намерений → отдельные сегменты; `urgent` первым; `is_continuation = true`, когда реплика отвечает на последний вопрос бота; уверенность честная, при сомнении две альтернативы.

Размер префикса около 14–16k токенов. Кэш‑чтение у `gpt-6-luna` стоит $0.01/M, так что цена вызова определяется выводом.

**Динамический хвост, user:**

```json
{
  "state": {
    "language": "ru",
    "client": {"client_id": "C007", "identified_by": "caller_id"},
    "active_scenario": {"scenario_id": "SC17", "step": "collect_slots", "expected_slot": "claim_number"},
    "stack": [],
    "slots": {"phone": "+77010000007"},
    "pending_confirmation": null,
    "last_bot_question": "Назовите номер заявления или телефон."
  },
  "history": [
    {"role": "client", "text": "Здравствуйте, хочу узнать, что с моим заявлением по каско."},
    {"role": "bot", "text": "Здравствуйте! Сейчас проверю. Назовите номер заявления или телефон."}
  ],
  "utterance": "Номер не помню, телефон плюс 7 701 000 00 07.",
  "is_partial": false
}
```

История ограничена последними 6 репликами. Поле `is_partial` сообщает роутеру, что текст ещё не завершён (спекулятивный вызов), чтобы он не выдумывал слоты из обрезанных слов.

## 2. Выход

Строгая JSON‑схема, файл [/contracts/router-output.schema.json](../../contracts/router-output.schema.json). Порядок полей важен для стриминга: исполнитель начинает работу по первому `scenario_id`.

```json
{
  "language": "mixed",
  "is_continuation": false,
  "confirmation": null,
  "scenarios": [
    {"scenario_id": "SC13", "confidence": 0.88, "segment": "Кеше аулада көлігімді біреу соғып кетіпті, КАСКО бар", "reason": "damage to own car yesterday, has CASCO", "boundary_rule": "SC12 excluded: client is not the victim of another driver's OGPO"},
    {"scenario_id": "SC20", "confidence": 0.81, "segment": "подскажите, где у вас осмотр делают", "reason": "asks where inspection is done", "boundary_rule": null}
  ],
  "alternatives": [{"scenario_id": "SC12", "confidence": 0.22}],
  "slots": {"incident_date": "2026-09-30", "incident_description": "Someone hit the car in the yard yesterday"},
  "urgency": "high",
  "emotion": "neutral"
}
```

- `confirmation`: `"yes"`, `"no"` или `null`. Заполняется, только если в состоянии есть `pending_confirmation`.
- `reason` не длиннее 12 слов, на английском, для панели переводится не нужно: супервизор видит и `boundary_rule`, и сегмент.
- `slots` только нормализованные значения по форматам slots.json: телефон `+7XXXXXXXXXX`, ИИН 12 цифр, госномер латиницей в верхнем регистре, даты ISO относительно `2026-10-01`. Валидация по `pattern` делается детерминированно после роутера, невалидное значение переспрашивается один раз.

## 3. Политика решения

| Условие | Решение | В трассировке `decision` |
|---|---|---|
| `is_continuation` и активный сценарий | заполнить слоты, шаг автомата | `continue` |
| `confirmation = yes/no` при `pending_confirmation` | execute / отмена | `confirm` / `cancel` |
| top‑1 confidence ≥ 0.75 | запустить сценарии по порядку приоритета | `run` |
| 0.45 ≤ confidence < 0.75 | второе мнение `gpt-6-sol`; если совпало и ≥ 0.75, `run`; иначе `SYS_UNCLEAR` с двумя вариантами | `run` / `clarify` |
| confidence < 0.45 второй раз подряд, либо просьба клиента | `transfer_to_operator` с резюме | `handoff` |
| `SYS_OUT_OF_SCOPE` | шаблонный ответ с перечислением, чем можем помочь | `out_of_scope` |
| `SYS_GOODBYE` | закрыть сессию | `goodbye` |

Порядок при нескольких сценариях: `urgent` первым, дальше как упомянуты. Бот проговаривает, что вторая тема тоже будет обработана, и кладёт её в стек.

**Калибровка порогов.** Уверенность LLM не калибрована. После первого рабочего роутера гоняем dev‑набор, группируем точность по корзинам уверенности (0.9+, 0.8–0.9, 0.7–0.8, …) и выставляем пороги там, где точность падает ниже 0.9 и 0.6 соответственно. Пороги лежат в конфиге, не в коде.

## 4. Детерминированные продолжения (fast path)

Не классификаторы, а проверки формата в контексте ожидаемого слота. Они экономят вызов LLM и не могут ошибиться в выборе сценария, потому что сценарий уже активен.

| Ситуация | Проверка | Действие |
|---|---|---|
| Ожидается `phone`, `iin`, `vehicle_plate`, `claim_number`, `policy_number` | текст после нормализации цифр совпал с `pattern` | заполнить слот без LLM |
| Есть `pending_confirmation` | реплика из словаря «да/верно/оформляйте/иә/дұрыс» либо «нет/не надо/жоқ» без другого содержания | execute / отмена |
| Реплика содержит только «оператор/человек/адам/оператормен» | | `handoff` |
| Активного сценария нет, реплика из словаря прощаний | | `SYS_GOODBYE` |

Всё, что не попало в таблицу, идёт в роутер. Сценарии с `fast_path_eligible` в каталоге мы обслуживаем тем же роутером, но без генерации: ответ шаблонный или прямая выдача раздела базы знаний. Выигрыш по времени измеряем и показываем.

## 5. Спекулятивный роутинг

```text
on stt.partial(text):
    if len(text.split()) < 3: return
    if text == last_sent: return
    if now - last_call_started < 350ms: return        # не чаще, чем раз в 350 мс
    launch router(text, is_partial=true) → store as hypothesis(text)
    emit router.hypothesis to UI

on vad.speech_end → stt.final(text):
    if hypothesis(text) exists and is complete:      # финал совпал с партиалом
        result = hypothesis; speculative_hit = true
    elif hypothesis for prefix exists with same scenarios and final differs only in trailing filler:
        result = hypothesis; speculative_hit = true
    else:
        result = await router(text, is_partial=false); speculative_hit = false
    cancel all pending speculative calls
```

Слоты из спекулятивного результата перепроверяются валидаторами, чтобы обрезанное слово не стало значением. Гипотезы транслируются на фронт событием `router.hypothesis`: жюри видит, как кандидат меняется посреди фразы.

## 6. Второе мнение

Для зоны 0.45–0.75 тот же промпт уходит в `gpt-6-sol` с `effort: "low"`. Результаты объединяются просто: если top‑1 совпал, уверенность берём максимальную; если нет, формируем `SYS_UNCLEAR` из двух разных top‑1. Задержка второго мнения около секунды, но она платится редко и только там, где иначе был бы переспрос.

## 7. Каркас промпта

```text
You are the routing layer of Saqta Insurance voice assistant (Kazakhstan, non‑life insurance).
Today is 2026-10-01. Clients speak Russian, Kazakh or mix both in one sentence.

Task: read the client's utterance in the context of the dialog state and decide which scenarios
from the catalog the client wants, in order. Split the utterance into segments if it contains
several requests. Extract slots mentioned in this utterance only, normalized.

Catalog (id | name | domain/category/priority | description | boundaries | examples | required slots):
SC01 | OGPO price quote | auto/sales/normal | Client wants to know the price of mandatory motor insurance ... 
  not_this_if: wants to buy right away → SC02; asks about CASCO → SC03
  ru: "Скажите, почём сейчас ОГПО на машину в Астане?" ; "..." ; kk: "..."
  required: region, vehicle_type, drivers_iin
...
System intents: SYS_OUT_OF_SCOPE (...), SYS_UNCLEAR (...), SYS_GOODBYE (...)

Rules:
- Urgent situations (accident right now, sick abroad, fraud) always come first.
- A request that continues the active scenario (answers the last bot question) is a continuation, do not re‑route.
- Disagreement with a decision is SC19, not SC17. Money charged but no policy is SC30, not SC26.
- Be honest about confidence. If two scenarios fit, put the second one in alternatives.
- Language: the dominant language of the utterance; "mixed" when both are present in the same sentence.
- Output strictly by the schema. Keep reason under 12 words.
```

Реальный текст промпта живёт в `/api/router/prompt.py` и собирается из scenarios.json автоматически, чтобы патчи каталога попадали в него без правок кода.

## 8. Примеры на dev‑наборе

| id | Реплика | Ожидание | Что должен увидеть роутер |
|---|---|---|---|
| U083 | «Кеше аулада көлігімді біреу соғып кетіпті, КАСКО бар, и ещё подскажите, где у вас осмотр делают» | SC13, SC20 | два сегмента, `mixed`, `incident_date = 2026-09-30` |
| U087 | «Отказали в выплате, и ваш оператор ещё и нагрубил» | SC19, SC35 | несогласие первым, жалоба второй, `emotion = upset` |
| U095 | «Кеше аварияға түстім, но я не виноват, виновник у вас застрахован» | SC12 | не SC11 (не сейчас) и не SC13 (не своё КАСКО) |
| U102 | «Алло, я по поводу страховки» | SYS_UNCLEAR | уверенность низкая, две альтернативы для уточнения |
| U098 | «Можно у вас взять кредит на машину?» | SYS_OUT_OF_SCOPE | кредиты не предоставляются |
| U089 | «Полис на почту не пришёл, и заодно скажите, до какого числа он действует» | SC26, SC25 | оба сценария требуют идентификации, слот `phone` спросим один раз |

## 9. Оценка

```bash
make bench            # роутер по dev_utterances.json → regression/predictions.json → docs/evaluate.py
```

Раннер гоняет 104 реплики в 20 параллельных запросов, пишет метрики `primary_acc`, `full_match`, `intent_recall`, разбивку по языку и типу, p50/p95 задержки роутера. Тот же раннер используется эволюцией каталога для регрессии, см. [EVOLUTION.md](EVOLUTION.md). Реплики dev‑набора в промпт не попадают ни в каком виде, иначе замер теряет смысл.
