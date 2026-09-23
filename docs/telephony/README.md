# Телефония: Asterisk → Voice Router

Для того, кто поднимает Asterisk. Задача: входящий звонок на софтфон или SIP‑транк попадает в того же голосового агента, что и веб, с идентификацией клиента по номеру звонящего и переводом на оператора. Бэкенд пишется параллельно, проверять можно на мок‑сервере из [/tools/mock-server](../../tools/mock-server).

Готовые конфиги: [/contracts/asterisk](../../contracts/asterisk). REST телефонии: раздел `telephony` в [/contracts/openapi.yaml](../../contracts/openapi.yaml).

## Схема

```text
софтфон 1001 ──SIP──► Asterisk ── dialplan saqta-inbound, 7575
                         │ 1. CURL POST /api/telephony/calls (caller, channel)  → UUID (text/plain)
                         │ 2. AudioSocket(UUID, api:9092)   ◄──── двусторонний звук slin 8 kHz ────► бэкенд
                         │ 3a. клиент попрощался: бэкенд шлёт hangup (0x00), канал завершается
                         │ 3b. перевод: бэкенд через AMI делает Setvar SAQTA_SUMMARY + Redirect
                         │     в saqta-transfer,<queue>,1 → Dial(PJSIP/operator)
                         │ 4. экстеншен h: CURL POST /api/telephony/calls/UUID/hangup
```

## Что нужно поднять

1. Asterisk 20 или 22 LTS в Docker с модулями `app_audiosocket`, `func_curl`, `res_pjsip`, `manager`. Проверка: `asterisk -rx "module show like audiosocket"` и `module show like curl`.
2. Сервис в общем `docker compose` в профиле `telephony`, в одной сети с `api`. Порты наружу 5060/udp и RTP 10000–10100/udp для софтфонов.
3. Конфиги из `/contracts/asterisk`: `pjsip.conf`, `extensions.conf`, `manager.conf`. Пароли заменить.
4. Два софтфона: клиент `1001` и оператор `operator`. Подойдут Linphone, Zoiper, MicroSIP.
5. Опционально SIP‑транк провайдера, входящие маршрутизировать в `saqta-inbound`, `7575`.

## Контракт

### Регистрация звонка

```text
POST http://api:8000/api/telephony/calls
Content-Type: application/x-www-form-urlencoded
caller=+77010000007&dnid=7575&channel=PJSIP/1001-00000001&uniqueid=1727780000.1

200 text/plain
3f6c1a2e-9b7d-4c1e-8a55-2d0c7e9b1f40
```

`caller` в любом формате, бэкенд нормализует к `+7XXXXXXXXXX` и сразу ищет клиента, поэтому агент здоровается по имени и не спрашивает телефон. `channel` обязателен: по нему бэкенд делает перевод через AMI. Пустой ответ означает, что бэкенд недоступен, диалплан уводит звонок на оператора.

### AudioSocket

TCP на `api:9092`. Каждое сообщение: 1 байт тип, 2 байта длина big‑endian, данные.

| Тип | Направление | Данные |
|---|---|---|
| `0x01` | Asterisk → сервер, первое сообщение | UUID, 16 байт |
| `0x10` | в обе стороны | звук slin: PCM16 LE, 8 kHz, моно; 20 мс = 320 байт |
| `0x03` | Asterisk → сервер | DTMF, 1 байт ASCII |
| `0x00` | в обе стороны | завершение |
| `0xff` | Asterisk → сервер | ошибка |

Сервер отправляет звук кадрами по 20 мс в реальном темпе. Перебивание: сервер просто перестаёт слать кадры. Внутри бэкенд ресемплирует 8 → 16 kHz для распознавания и 24 → 8 kHz для синтеза.

### Перевод на оператора

Не полагаемся на то, что `AudioSocket()` вернёт управление в диалплан: в разных версиях Asterisk закрытие сокета сервером может обрывать канал. Поэтому бэкенд переводит звонок через AMI.

```text
Action: Setvar            Channel: PJSIP/1001-00000001   Variable: SAQTA_SUMMARY   Value: <резюме>
Action: Redirect          Channel: PJSIP/1001-00000001   Context: saqta-transfer   Exten: complaints_team   Priority: 1
```

Очереди из кита: `operator_general`, `claims_team`, `medical_assistance_24_7`, `corporate_sales`, `complaints_team`, `security_team`. В демо все ведут на один софтфон `operator`, имя очереди видно в caller name. Полное резюме оператор видит в веб‑экране «Оператор». Пользователь AMI: `voicerouter`, порт 5038, права в `manager.conf`.

Запасной путь в диалплане: после `AudioSocket()` запрос `GET /api/telephony/calls/{uuid}/outcome` возвращает `hangup` или `transfer:<queue>`.

### Завершение

Экстеншен `h` вызывает `POST /api/telephony/calls/{uuid}/hangup` с `cause`. Бэкенд закрывает сессию и пишет итог в панель супервизора.

## Демо‑трюк с персонами

Набор `7575NN` звонит от имени персоны `CNN` из mock_backend: `757507` это Сергей Попов `+77010000007`, `757504` это Наталья Смирнова `+77010000004`. На сцене можно звонить с одного софтфона разными клиентами.

## Проверка без бэкенда

```bash
.venv/bin/pip install -r tools/mock-server/requirements.txt
.venv/bin/python tools/mock-server/server.py
```

Мок на `:8000` принимает регистрацию звонка и отдаёт UUID, на `:9092` работает эхо AudioSocket: вы слышите себя с задержкой около секунды. DTMF `0` помечает исход `transfer:operator_general` и закрывает сокет, `#` закрывает сокет с исходом `hangup`. В логе мока видно регистрацию, UUID, DTMF и завершение.

## Чек‑лист готовности

- [ ] Звонок с `1001` на `7575` регистрируется, в логе бэкенда видны caller и channel
- [ ] Эхо через мок слышно без искажений в обе стороны
- [ ] `757507` меняет caller ID на `+77010000007`
- [ ] AMI Redirect из консоли переводит активный звонок в `saqta-transfer` и звонит оператору
- [ ] `h` вызывает hangup‑эндпоинт
- [ ] При выключенном бэкенде звонок уходит на оператора, а не обрывается
- [ ] Задержка на телефоне замерена секундомером от конца фразы до начала ответа
