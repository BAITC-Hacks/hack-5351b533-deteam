# Телефония: Asterisk + ARI → Voice Router

Для того, кто поднимает Asterisk. Звонок с софтфона попадает в того же голосового агента, что и веб: тот же `Call` (VAD → STT → роутер → TTS), те же события в панели супервизора. Агент узнаёт клиента по номеру звонящего, здоровается по имени и умеет перевести звонок на оператора с резюме.

**Основная схема:** управление звонком через ARI, звук через канал External Media в формате AudioSocket по TCP. AMI не нужен.
**Запасная схема** (если ARI недоступен): диалплан сам регистрирует звонок через `CURL` и вызывает `AudioSocket()`. См. раздел «Запасной режим без ARI».

Код: [`backend/app/ari.py`](../../backend/app/ari.py) (ARI‑контроллер), [`backend/app/telephony.py`](../../backend/app/telephony.py) (AudioSocket‑сервер, реестр звонков, запасной REST). Конфиги: [/contracts/asterisk](../../contracts/asterisk): `extensions.conf`, `ari.conf`, `http.conf`, `pjsip.conf` (`manager.conf` только для запасной схемы с AMI).

## Как идёт звонок (ARI)

```text
софтфон ──SIP──► Asterisk ── from-inbound: Answer, Stasis(voice-ai)
                    │
                    │  ARI WebSocket  ws://ASTERISK:8088/ari/events?app=voice-ai   ◄── бэкенд подключён как приложение voice-ai
                    │  1. StasisStart(канал звонящего C, caller.number = номер звонящего)
                    │  2. бэкенд: регистрирует UUID с номером, answer C, создаёт mixing-бридж B, кладёт в него C
                    │  3. бэкенд: POST /channels/externalMedia (encapsulation=audiosocket, transport=tcp, format=slin,
                    │     external_host=BACKEND_IP:9092, data=<UUID>) -> канал E, кладёт E в бридж B
                    │  4. Asterisk сам подключается к BACKEND_IP:9092 и шлёт UUID, дальше звук slin 8 kHz в обе стороны
                    │
                    │  Конец разговора (бот сначала договаривает последнюю фразу):
                    │  5a. клиент попрощался: бэкенд DELETE /channels/C (положить трубку)
                    │  5b. перевод: бэкенд ставит переменную SAQTA_SUMMARY и делает
                    │      POST /channels/C/continue?context=saqta-transfer&extension=<queue>&priority=1
                    │      -> диалплан saqta-transfer -> Dial(PJSIP/operator)
                    │  5c. клиент положил трубку: StasisEnd/ChannelDestroyed -> бэкенд закрывает сессию, удаляет E и B
```

Всё управление делает бэкенд. От Asterisk нужен только диалплан с `Stasis(voice-ai)`, пользователь ARI и сеть. StasisStart для своих каналов External Media бэкенд игнорирует (их id начинаются с `vr-em-`, бриджи `vr-br-`).

Что бэкенд делает при сбоях (клиента не бросаем):

| Ситуация | Что происходит |
|---|---|
| бэкенд не подключён к ARI (выключен, неверный пароль) | `Stasis()` возвращает `STASISSTATUS=FAILED`, диалплан уходит в запасной контекст `saqta-audiosocket` (CURL + AudioSocket). Если и HTTP бэкенда недоступен, звонок идёт на оператора |
| `externalMedia` не создан (Asterisk не достучался до AudioSocket, нет `chan_audiosocket`) | ошибка в логе `[ari] ERROR: externalMedia ...`, звонок `continue` в `saqta-transfer,operator_general`, `SAQTA_SUMMARY=Сбой голосового агента: ...` |
| AudioSocket с UUID не пришёл за `ARI_MEDIA_TIMEOUT` (6 с) | то же, на оператора |
| AudioSocket оборвался посреди разговора | через 1,5 с: если перевод уже решён, `continue`, иначе на оператора |
| клиент нажал `0` | Asterisk пересылает DTMF в AudioSocket (`0x03`) → «Соедините меня с оператором» → перевод (проверено на 22.10) |

## Что нужно от Asterisk

1. **Asterisk 20+** с модулями `res_ari`, `res_stasis`, `res_http_websocket`, `res_pjsip` и AudioSocket (`res_audiosocket`, `chan_audiosocket`). Проверка:
   ```bash
   docker compose exec asterisk asterisk -rx "core show version"
   docker compose exec asterisk asterisk -rx "module show like audiosocket"
   docker compose exec asterisk asterisk -rx "ari show apps"
   ```
   Пакет `asterisk` из Ubuntu 24.04 (20.6.0, образ `infra/asterisk`) содержит `chan_audiosocket.so`, `res_audiosocket.so`, а `res_ari_channels.so` умеет `external_media_audiosocket_tcp` (проверено по бинарникам пакета; живой звонок гоняли на 22.10).
2. **Диалплан.** Контекст `from-inbound` отправляет звонок в `Stasis(voice-ai)`. Это уже так в `infra/asterisk`. Добавить контекст `saqta-transfer` из `contracts/asterisk/extensions.conf` (а лучше подключить файл целиком через `#include`, тогда появится и запасной `saqta-audiosocket`).
3. **ARI.** Пользователь `voice-ai` с паролем, HTTP на порту 8088 (`ari.conf`, `http.conf`). Пароль передаётся нам в `.env` бэкенда.
4. **Два софтфона.** Клиент `1001` и оператор `operator`, см. `pjsip.conf`. Подойдут Linphone, Zoiper, MicroSIP.
5. **Демо‑персоны.** Набор `7575NN` звонит от имени клиента `CNN` из mock_backend: диалплан ставит `CALLERID(num)=+770100000NN` до `Stasis`, бэкенд берёт номер из `StasisStart.channel.caller.number`. `757507` это Сергей, `757504` это Наталья (проверено: «Здравствуйте, Наталья!»). Так с одного софтфона можно звонить разными клиентами.

## Сеть: звонок с одного ПК на другой

**Рекомендуем:** контейнер Asterisk запускается на демо‑ноутбуке рядом с бэкендом. Софтфон клиента на другом ПК регистрируется на IP ноутбука.

| Что | Куда | Порт |
|---|---|---|
| Софтфон → Asterisk | IP ноутбука | 5060/udp, RTP 10000–10100/udp |
| Бэкенд → ARI | `localhost` или IP Asterisk | 8088/tcp |
| Asterisk → бэкенд, AudioSocket | IP ноутбука, видимый из контейнера | 9092/tcp |
| Asterisk → бэкенд, HTTP (только запасная схема) | IP ноутбука | 8000/tcp |

- Сейчас `infra/asterisk/compose.yaml` публикует ARI только на `127.0.0.1`. Если Asterisk на том же ноутбуке, это подходит. Если на другом ПК, нужен `0.0.0.0:8088:8088` и фаервол, пускающий только IP ноутбука.
- Адрес бэкенда для AudioSocket (`AUDIOSOCKET_HOST`) должен быть доступен **изнутри контейнера** Asterisk. Проще всего LAN‑IP ноутбука. Либо в compose добавить `extra_hosts: ["host.docker.internal:host-gateway"]` и указать `host.docker.internal`. Бэкенд слушает AudioSocket на `0.0.0.0` (`AUDIOSOCKET_BIND`), так что подойдёт любой адрес ноутбука.
- Файрвол ноутбука: `sudo ufw allow from <IP Asterisk> to any port 9092 proto tcp` (для Docker на этом же ноутбуке обычно не нужно).
- В `.env` Asterisk указать `ASTERISK_PUBLIC_IP` = LAN‑IP ноутбука, чтобы RTP до софтфона шёл через Docker NAT.

## Настройки бэкенда

```bash
ARI_URL=http://127.0.0.1:8088        # адрес ARI. Пусто -> ARI выключен, работает только запасная схема
ARI_USER=voice-ai
ARI_PASSWORD=...                     # из .env Asterisk (ARI_SECRET)
ARI_APP=voice-ai
AUDIOSOCKET_PORT=9092                # где бэкенд слушает AudioSocket
AUDIOSOCKET_HOST=192.168.1.50        # как Asterisk достаёт до бэкенда: LAN-IP ноутбука или host.docker.internal
```

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AUDIOSOCKET` | `1` | поднимать AudioSocket‑сервер в lifespan (нужен и для ARI) |
| `AUDIOSOCKET_BIND` | `0.0.0.0` | на каком адресе слушать AudioSocket |
| `ARI_TRANSFER_CONTEXT` | `saqta-transfer` | контекст для `continue` |
| `ARI_FALLBACK_QUEUE` | `operator_general` | куда вести звонок, если External Media не поднялся |
| `ARI_MEDIA_TIMEOUT` | `6` | сколько секунд ждать подключения AudioSocket с UUID |
| `BARGE_IN` | `1` | перебивание бота голосом. На громкой связи без эхоподавления поставьте `0` |

Бэкенд сам подключается к ARI при старте и переподключается (1, 2, 4 … 15 с), если Asterisk перезапустили. В логе:

```text
[ari] connected http://127.0.0.1:8088 app=voice-ai; External Media -> 192.168.1.50:9092 (audiosocket/tcp/slin)
```

Запуск: `cd backend && ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`. `--host 0.0.0.0` нужен только для веб‑клиента с другой машины и для запасной схемы (там Asterisk ходит на HTTP бэкенда); ARI‑схеме достаточно AudioSocket на 9092.

## Формат звука

AudioSocket: сообщение = 1 байт тип, 2 байта длина big‑endian, данные.

| Тип | Направление | Данные |
|---|---|---|
| `0x01` | Asterisk → бэкенд, первое сообщение | UUID, 16 байт. Это значение из `data` при создании External Media (или первый аргумент `AudioSocket()` в запасной схеме) |
| `0x10` | в обе стороны | звук slin: PCM16 LE, 8 kHz, моно, 20 мс = 320 байт |
| `0x03` | Asterisk → бэкенд | DTMF, 1 байт ASCII. `0` = «соедините с оператором» (работает в обеих схемах) |
| `0x00` | в обе стороны | завершение |
| `0xff` | Asterisk → бэкенд | ошибка |

* Бэкенд шлёт **непрерывный** поток кадров по 20 мс в реальном темпе: речь бота из буфера, в паузах тишина. Тишина обязательна: `AudioSocket()` в Asterisk 22 закрывает сокет после 2 с без активности в обе стороны.
* Перебивание: клиент заговорил → буфер исходящего звука очищается мгновенно, дальше идут кадры тишины. Бот замолкает через 0,45–0,47 с после начала речи клиента (почти всё это время VAD подтверждает начало речи).
* Если Asterisk не шлёт кадры (софтфон с DTX/подавлением тишины, Local‑канал), сервер сам досыпает тишину во VAD, чтобы конец фразы определился.
* Внутри: 8 → 16 kHz для VAD/STT, TTS 24 → 8 kHz.

## Полная запись звонка

Asterisk запускает `MixMonitor` на входном канале после `Answer()` и до `Stasis(voice-ai)`. Запись прикреплена к каналу звонящего, поэтому охватывает разговор с агентом и продолжение после ARI-перевода в `saqta-transfer` до окончательного отбоя. Привязанный к каналу hangup handler вызывает `StopMixMonitor`; после закрытия WAV появляется маркер `.ready` в общем Docker-томе. Отдельный `recording-uploader` загружает файл в приватный bucket `call-recordings` локального MinIO и отправляет `POST /api/telephony/calls/{call_id}/recording` бэкенду. `call_id` равен Asterisk `UNIQUEID`/ARI `channel.id`, что позволяет связать запись с телефонной сессией.

В callback есть постоянный `recording_uri` вида `s3://...`, bucket/key и подписанный `recording_url`, действительный 24 часа. Backend должен хранить bucket/key и при необходимости выпускать новую ссылку; повторный callback для одного call_id идемпотентен. Если MinIO или backend недоступен, uploader сохраняет локальный WAV и повторяет отправку. Полный формат см. в [OpenAPI](../../contracts/openapi.yaml). Для разработки на одном ПК MinIO доступен на `127.0.0.1:9000`, консоль — на `127.0.0.1:9001`.

## Если External Media не принимает audiosocket

Поддержку проверяют первым звонком. Asterisk без поддержки отвечает на `externalMedia` `HTTP 501 The encapsulation and/or transport is not supported`, бэкенд пишет в лог:

```text
[ari] ERROR: Asterisk отверг externalMedia encapsulation=audiosocket (HTTP 501) ...
[ari] call PJSIP/1001-00000001 uuid=...: externalMedia failed -> saqta-transfer,operator_general
```

и переводит звонок на оператора. Тогда переключитесь на запасной режим без ARI (ниже): `context=saqta-audiosocket` у эндпоинта или просто не задавайте `ARI_URL` на бэкенде (Stasis вернёт FAILED и диалплан сам уйдёт в `saqta-audiosocket`). RTP‑вариант External Media (`encapsulation=rtp`, `EXTERNAL_MEDIA=rtp`) **не реализован**.

Если в логе `HTTP 500` через ~2 с: Asterisk не смог подключиться к `AUDIOSOCKET_HOST:AUDIOSOCKET_PORT` (адрес не виден из контейнера, фаервол) или не загружен `chan_audiosocket`.

## Проверено вживую (ARI)

Asterisk **22.10.1** (docker `andrius/asterisk`) в отдельной docker‑сети, конфиги `ari.conf`, `http.conf`, `extensions.conf`, `pjsip.conf` из `/contracts/asterisk`, «клиент» это Local‑канал, проигрывающий WAV. Бэкенд с `ARI_URL=http://<контейнер>:8088`, `AUDIOSOCKET_HOST=<шлюз docker-сети>`.

* `757504` → StasisStart с `caller=+77010000004`, External Media подключился с UUID за 16–63 мс, «Здравствуйте, Наталья! Это Saqta Insurance, чем могу помочь?», `identified_by: caller_id`.
* «Что с моим заявлением?» → ответ по заявлению CL‑500311 (задержка по трассе 2,7 с).
* «Соедините меня с оператором» → бот договорил → `continue` → в Asterisk `Transfer to operator_general: Клиент Natalia Smirnova, +77010000004. Вопросы: Статус страхового случая...`, `CALLERID(name)=Saqta operator_general`, `Dial(...)` оператору.
* `757507` «Спасибо, это всё, до свидания» → «Здравствуйте, Сергей!» … «Спасибо за обращение! Хорошего дня.» → `DELETE /channels` (HTTP 204).
* Клиент положил трубку посреди ответа бота → `session.closed reason=client`, E и B удалены, `bridge show all` пуст.
* `module unload chan_audiosocket.so` → понятная ошибка в логе, звонок ушёл в `saqta-transfer` с `SAQTA_SUMMARY=Сбой голосового агента`.
* `docker restart` Asterisk → бэкенд переподключился к ARI сам.
* Бэкенд без `ARI_URL` → `Stasis app 'voice-ai' doesn't exist` → `saqta-audiosocket` → CURL + `AudioSocket()` → разговор → `AudioSocket SUCCESS, outcome=hangup`.

Лог бэкенда на один звонок:

```text
[ari] StasisStart Local/757504@from-inbound-00000000;2 caller='+77010000004'->+77010000004 exten=757504 args=[] -> uuid=ce3a3e22-...
[tel] audiosocket ('172.23.0.2', 45828) uuid=ce3a3e22-... registered caller=+77010000004
[ari] bridged Local/757504@from-inbound-00000000;2 <-> AudioSocket/172.23.0.1:9097-ce3a3e22-... in vr-br-ce3a3e22-... (63 ms)
[ari] transfer Local/757504@from-inbound-00000000;2 uuid=ce3a3e22-... -> saqta-transfer,operator_general,1: HTTP 204
[tel] finish ce3a3e22-... reason=handoff outcome=transfer:operator_general via=ari
[ari] StasisEnd Local/757504@from-inbound-00000000;2 uuid=ce3a3e22-...: after our continue saqta-transfer,operator_general,1
[ari] cleanup uuid=ce3a3e22-...: external media + bridge deleted (36.8 s call)
```

Звонок с настоящего SIP‑софтфона на этом стенде не делали (тест шёл через Local‑канал).

### Как повторить без софтфона (Local‑канал)

```ini
; extensions.conf на время теста; contracts/asterisk/extensions.conf подключён как extensions_saqta.conf; WAV 8 kHz 16 bit mono в /snd
[general]
static=yes
#include extensions_saqta.conf

[probe-client]
exten => s,1,Answer()
 same => n,Wait(8)
 same => n,Playback(/snd/q1)        ; «Что с моим заявлением?»
 same => n,Wait(18)
 same => n,Playback(/snd/op)        ; «Соедините меня с оператором»
 same => n,Wait(40)

[probe-operator]
exten => s,1,Answer()
 same => n,NoOp(PROBE OPERATOR ANSWERED name=${CALLERID(name)})
 same => n,Wait(2)
```

```bash
docker network create ari-test
docker run -d --name ast --network ari-test \
  -v $PWD/test-extensions.conf:/etc/asterisk/extensions.conf:ro \
  -v $PWD/contracts/asterisk/extensions.conf:/etc/asterisk/extensions_saqta.conf:ro \
  -v $PWD/contracts/asterisk/ari.conf:/etc/asterisk/ari.conf:ro \
  -v $PWD/contracts/asterisk/http.conf:/etc/asterisk/http.conf:ro \
  -v $PWD/contracts/asterisk/pjsip.conf:/etc/asterisk/pjsip.conf:ro \
  -v $PWD/snd:/snd:ro andrius/asterisk:latest
# бэкенд: ARI_URL=http://<IP контейнера>:8088 ARI_PASSWORD=change-me AUDIOSOCKET_HOST=<gateway сети ari-test>
docker exec ast asterisk -rx "dialplan set global OPERATOR Local/s@probe-operator"
docker exec ast asterisk -rx "channel originate Local/757504@from-inbound extension s@probe-client"
docker exec ast asterisk -rx "bridge show all"      # после звонка пусто
```

## Запасной режим без ARI

Диалплан сам ведёт звонок, бэкенд только отвечает по HTTP и AudioSocket. Контекст `[saqta-audiosocket]` в `contracts/asterisk/extensions.conf`. Включается:

* **сам**, если бэкенд не подключён к ARI: `Stasis()` в `from-inbound` возвращает `FAILED` → `Goto(saqta-audiosocket,7575,1)`;
* **вручную**: `context=saqta-audiosocket` у эндпоинта в `pjsip.conf` (или `Goto(saqta-audiosocket,7575,1)` вместо `Stasis`).

Нужны ещё модули `app_audiosocket`, `func_curl`, `res_curl`, `func_uri`, `func_cut`, `func_env` (проверка: `module show like curl`). Проверено на Asterisk 22.10: регистрация через `CURL`, `AudioSocket()`, приветствие по имени, ответ на вопрос, перевод через AMI Redirect, перевод через диалплан по `/outcome`, прощание с отбоем, экстеншен `h`, запасной путь на оператора при выключенном HTTP и при недоступном AudioSocket, адреса из окружения (`VR_API`/`VR_AS`), автоматический уход из `from-inbound` при `STASISSTATUS=FAILED`.

```text
софтфон 1001 ──SIP/RTP──► Asterisk ── saqta-audiosocket, 7575 (или from-inbound, если Stasis FAILED)
                                        │ 1. CURL POST {API}/api/telephony/calls  caller, channel  → UUID (text/plain)
                                        │ 2. TryExec(AudioSocket(UUID, {AS}))  ◄── slin 8 kHz, кадры 20 мс ──►  бэкенд :9092
                                        │ 3a. прощание: бэкенд договорил → 0x00 → AudioSocket вернулся → /outcome = hangup → Hangup
                                        │ 3b. перевод: бэкенд договорил → AMI Setvar SAQTA_SUMMARY + Redirect saqta-transfer,<queue>,1
                                        │     (без AMI: 0x00 → /outcome = transfer:<queue> → Goto saqta-transfer) → Dial(PJSIP/operator)
                                        │ 4. экстеншен h: CURL POST {API}/api/telephony/calls/UUID/hangup
```

### Адреса бэкенда

Диалплан берёт их из `[globals]` `VR_API`/`VR_AS`, переменные окружения процесса Asterisk `VR_API`/`VR_AS` их перекрывают (файл можно не править):

* Asterisk в Docker (bridge) на этом же ноутбуке: `http://host.docker.internal:8000`, `host.docker.internal:9092` (compose: `extra_hosts: ["host.docker.internal:host-gateway"]`).
* Asterisk с `network_mode: host` на этом же Linux‑ноутбуке: `VR_API=http://127.0.0.1:8000`, `VR_AS=127.0.0.1:9092`.
* Asterisk на другом ПК: `VR_API=http://192.168.1.50:8000`, `VR_AS=192.168.1.50:9092`, где 192.168.1.50 это IP ноутбука. Бэкенд с `--host 0.0.0.0`, файрвол: `sudo ufw allow from <IP ПК с Asterisk> to any port 8000,9092 proto tcp`. Проверка с ПК Asterisk: `curl http://192.168.1.50:8000/api/health`, в PowerShell `Test-NetConnection 192.168.1.50 -Port 9092`.
* Docker Desktop с пробросом портов: в `pjsip.conf` нужны `external_media_address`/`external_signaling_address` = LAN‑IP ПК с Asterisk, иначе в SDP уйдёт 172.x и не будет звука. В infra/asterisk это `ASTERISK_PUBLIC_IP` в `.env`.

Переменные бэкенда для этого режима:

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `AMI_HOST`, `AMI_PORT`, `AMI_USER`, `AMI_SECRET` | пусто, 5038 | перевод через AMI (`manager.conf`). Пусто → перевод делает диалплан по `/outcome`. При заданном `ARI_URL` звонки ARI‑схемы переводятся через ARI, AMI не трогается |
| `AMI_TRANSFER_CONTEXT` | `saqta-transfer` | контекст для Redirect |

### Регистрация звонка

```text
POST {API}/api/telephony/calls        Content-Type: application/x-www-form-urlencoded
caller=%2B77010000004&dnid=7575&channel=PJSIP%2F1001-00000001&uniqueid=1727780000.1

200 text/plain
3f6c1a2e-9b7d-4c1e-8a55-2d0c7e9b1f40
```

`caller` в любом формате (`87010000004`, `+7 701 000 00 04`, без URIENCODE «+» станет пробелом, это тоже переживём): бэкенд нормализует к `+7XXXXXXXXXX`, находит клиента (`identified_by: caller_id`), агент здоровается по имени на предпочитаемом языке клиента и не спрашивает телефон. `channel` нужен для AMI Redirect. Диалплан проверяет, что ответ длиной 36 символов, иначе уводит звонок на оператора. AudioSocket с незарегистрированным UUID тоже работает: звонок идёт без caller id, приветствие двуязычное.

### Завершение и перевод

Бэкенд дожидается, пока бот договорит (синтез закончен, очередь плеера пуста, буфер выдан, плюс 0,3 с на джиттер‑буфер телефона), затем:

* **прощание** (`session.end` reason `goodbye`): шлёт `0x00`. `AudioSocket()` возвращает управление (TRYSTATUS=SUCCESS), `/outcome` = `hangup`, `Hangup()`.
* **перевод** (`handoff`): исход `transfer:<queue>` фиксируется сразу по событию `handoff`, потом:
  * с AMI: `Setvar SAQTA_SUMMARY=<резюме>` и `Redirect Channel=<channel> Context=saqta-transfer Exten=<queue> Priority=1`, затем `0x00`;
  * без AMI (или AMI не ответил): `0x00`, диалплан спрашивает `GET /api/telephony/calls/{uuid}/outcome` → `transfer:operator_general` → `Goto(saqta-transfer,operator_general,1)`.
* Клиент положил трубку (`0x00` от Asterisk, закрытие сокета, `POST /hangup`): сессия закрывается, в панели супервизора `session.closed`.

`GET /outcome` с `Accept: application/json` отдаёт `{"action": "transfer", "queue": "...", "summary": "...", "session_id": "phone-..."}`. Отладка (обе схемы): `GET /api/telephony/calls` показывает последние звонки, у ARI‑звонков `ari: true` и `ari_action`.

Запасные пути: бэкенд недоступен (`CURL` вернул пусто) → `Goto(saqta-transfer,operator_general,1)`; AudioSocket недоступен или сокет оборвался (TRYSTATUS=FAILED) → на оператора. Оба проверены.

## Очереди

`operator_general`, `claims_team`, `medical_assistance_24_7`, `corporate_sales`, `complaints_team`, `security_team`. В демо все ведут на `${OPERATOR}` = `PJSIP/operator`, имя очереди видно в caller name (`Saqta claims_team`), резюме в `SAQTA_SUMMARY` и в веб‑экране оператора.

## Тест без Asterisk: phone_probe

Скрипт притворяется Asterisk в запасной схеме: регистрирует звонок по REST, подключается к AudioSocket с этим UUID, в реальном темпе шлёт синтезированную речь клиента, считает входящие кадры, ловит `0x00` и печатает поток событий из `/ws/supervisor`.

```bash
cd backend
../.venv/bin/python tools/phone_probe.py --http http://127.0.0.1:8000 --as 127.0.0.1:9092
../.venv/bin/python tools/phone_probe.py --barge                       # перебить бота на первом ответе
../.venv/bin/python tools/phone_probe.py --expect hangup "Когда заканчивается мой полис?" "Спасибо, это всё, до свидания"
../.venv/bin/python tools/phone_probe.py --unregistered "Соедините меня с оператором"   # UUID без регистрации
```

Пример прогона: приветствие через 54 мс после подключения; ход 1 от конца речи до первого кадра ответа 2,26 с (STT 1,0 с + роутер 1,5 с); «оператор» по быстрому пути 0,93 с; `0x00` через 0,7 с после последнего кадра.

## Чек‑лист

- [ ] `ari show apps` показывает `voice-ai` после старта бэкенда, в логе бэкенда `[ari] connected`
- [ ] Звонок с `1001` на любой номер: агент здоровается, в логе бэкенда `[ari] StasisStart`, `[tel] audiosocket ... registered`, `[ari] bridged`
- [ ] `757504` здоровается по имени «Наталья» без вопроса про телефон
- [ ] Вопрос → ответ, в панели супервизора ходы с трассой
- [ ] Фраза «соедините с оператором»: бот договаривает, звонит софтфон `operator`, в caller name видно очередь
- [ ] «Спасибо, до свидания»: бот прощается и кладёт трубку
- [ ] Клиент кладёт трубку посреди ответа: сессия на бэкенде закрывается, в Asterisk не остаётся висящих бриджей (`bridge show all`)
- [ ] Бэкенд выключен → звонок уходит на оператора (через запасной контекст), а не обрывается
- [ ] Задержка на телефоне замерена секундомером от конца фразы до начала ответа (ожидаемо 2–3 с, быстрые пути около 1 с)

## Известные ограничения

* Громкая связь без эхоподавления: бот слышит сам себя и перебивает. Используйте гарнитуру или `BARGE_IN=0`.
* `tts.end` для телефона приходит, когда весь звук ушёл в буфер, а не когда доиграл (буфер выдаётся в реальном темпе).
* `session.ready.audio_in.sample_rate` пишет 16000 (внутренняя частота VAD). Для телефона вход 8 kHz, выход `audio_out.sample_rate` = 8000.
* RTP‑вариант External Media не реализован, только `encapsulation=audiosocket`.
