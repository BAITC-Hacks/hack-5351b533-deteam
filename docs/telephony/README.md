# Телефония: Asterisk + ARI → Voice Router

Для того, кто поднимает Asterisk. Звонок с софтфона попадает в того же голосового агента, что и веб: агент узнаёт клиента по номеру звонящего и умеет перевести звонок на оператора.

**Схема:** управление звонком через ARI, звук через канал External Media в формате AudioSocket по TCP. AMI не нужен.

Готовые конфиги: [/contracts/asterisk](../../contracts/asterisk). `extensions.conf`, `ari.conf`, `http.conf`, `pjsip.conf`.

## Как идёт звонок

```text
софтфон ──SIP──► Asterisk ── from-inbound: Answer, Stasis(voice-ai)
                    │
                    │  ARI WebSocket  ws://ASTERISK:8088/ari/events?app=voice-ai   ◄── бэкенд подключён как приложение voice-ai
                    │  1. StasisStart(канал звонящего C, caller.number = номер звонящего)
                    │  2. бэкенд: answer C, создаёт mixing-бридж B, кладёт в него C
                    │  3. бэкенд: POST /channels/externalMedia (encapsulation=audiosocket, transport=tcp, format=slin,
                    │     external_host=BACKEND_IP:9092, data=<UUID>) -> канал E, кладёт E в бридж B
                    │  4. Asterisk сам подключается к BACKEND_IP:9092 и шлёт UUID, дальше звук slin 8 kHz в обе стороны
                    │
                    │  Конец разговора:
                    │  5a. клиент попрощался: бэкенд DELETE /channels/C (положить трубку)
                    │  5b. перевод: бэкенд ставит переменную SAQTA_SUMMARY и делает
                    │      POST /channels/C/continue?context=saqta-transfer&extension=<queue>&priority=1
                    │      -> диалплан saqta-transfer -> Dial(PJSIP/operator)
                    │  5c. клиент положил трубку: StasisEnd/ChannelDestroyed -> бэкенд закрывает сессию, удаляет E и B
```

Всё управление делает бэкенд. От Asterisk нужен только диалплан с `Stasis(voice-ai)`, пользователь ARI и сеть.

## Что нужно от Asterisk

1. **Asterisk 20+** с модулями `res_ari`, `res_stasis`, `res_http_websocket`, `res_pjsip` и AudioSocket (`res_audiosocket`, `chan_audiosocket`). Проверка:
   ```bash
   docker compose exec asterisk asterisk -rx "core show version"
   docker compose exec asterisk asterisk -rx "module show like audiosocket"
   docker compose exec asterisk asterisk -rx "ari show apps"
   ```
2. **Диалплан.** Контекст `from-inbound` отправляет звонок в `Stasis(voice-ai)`. Это уже так в `infra/asterisk`. Добавить контекст `saqta-transfer` из `contracts/asterisk/extensions.conf`.
3. **ARI.** Пользователь `voice-ai` с паролем, HTTP на порту 8088. Пароль передаётся нам в `.env` бэкенда.
4. **Два софтфона.** Клиент `1001` и оператор `operator`, см. `pjsip.conf`. Подойдут Linphone, Zoiper, MicroSIP.
5. **Демо‑персоны.** Набор `7575NN` звонит от имени клиента `CNN` из mock_backend: `757507` это Сергей, `757504` это Наталья. Так с одного софтфона можно звонить разными клиентами.

## Сеть: звонок с одного ПК на другой

**Рекомендуем:** контейнер Asterisk запускается на демо‑ноутбуке рядом с бэкендом. Софтфон клиента на другом ПК регистрируется на IP ноутбука.

| Что | Куда | Порт |
|---|---|---|
| Софтфон → Asterisk | IP ноутбука | 5060/udp, RTP 10000–10100/udp |
| Бэкенд → ARI | `localhost` или IP Asterisk | 8088/tcp |
| Asterisk → бэкенд, AudioSocket | IP ноутбука, видимый из контейнера | 9092/tcp |

- Сейчас `infra/asterisk/compose.yaml` публикует ARI только на `127.0.0.1`. Если Asterisk на том же ноутбуке, это подходит. Если на другом ПК, нужен `0.0.0.0:8088:8088` и фаервол, пускающий только IP ноутбука.
- Адрес бэкенда для AudioSocket должен быть доступен **изнутри контейнера** Asterisk. Проще всего LAN‑IP ноутбука. Либо в compose добавить `extra_hosts: ["host.docker.internal:host-gateway"]` и указать `host.docker.internal`.
- В `.env` Asterisk указать `ASTERISK_PUBLIC_IP` = LAN‑IP ноутбука, чтобы RTP до софтфона шёл через Docker NAT.

## Настройки бэкенда

```bash
ARI_URL=http://127.0.0.1:8088        # адрес ARI
ARI_USER=voice-ai
ARI_PASSWORD=...                     # из .env Asterisk (ARI_SECRET)
ARI_APP=voice-ai
AUDIOSOCKET_PORT=9092                # где бэкенд слушает AudioSocket
AUDIOSOCKET_HOST=192.168.1.50        # как Asterisk достаёт до бэкенда: LAN-IP ноутбука или host.docker.internal
```

Бэкенд сам подключается к ARI при старте и переподключается, если Asterisk перезапустили.

## Формат звука

AudioSocket: сообщение = 1 байт тип, 2 байта длина big‑endian, данные.

| Тип | Направление | Данные |
|---|---|---|
| `0x01` | Asterisk → бэкенд, первое сообщение | UUID, 16 байт. Это значение из `data` при создании External Media |
| `0x10` | в обе стороны | звук slin: PCM16 LE, 8 kHz, моно, 20 мс = 320 байт |
| `0x00` | в обе стороны | завершение |
| `0xff` | Asterisk → бэкенд | ошибка |

Бэкенд отдаёт звук кадрами по 20 мс в реальном темпе. Если клиент перебивает, бэкенд просто перестаёт слать кадры.

## Если External Media не принимает audiosocket

Поддержку проверяют первым звонком. Если бэкенд в логе пишет ошибку `externalMedia` про `encapsulation`, версия Asterisk её не поддерживает. Запасной путь: `encapsulation=rtp`, `transport=udp`, `format=slin16`, бэкенд принимает RTP на порту 9092/udp. Для этого скажите нам, мы переключим режим одной переменной `EXTERNAL_MEDIA=rtp`.

## Чек‑лист

- [ ] `ari show apps` показывает `voice-ai` после старта бэкенда
- [ ] Звонок с `1001` на любой номер: агент здоровается, в логе бэкенда `StasisStart` и подключение AudioSocket с UUID
- [ ] `757504` здоровается по имени «Наталья» без вопроса про телефон
- [ ] Фраза «соедините с оператором»: звонит софтфон `operator`, в caller name видно очередь
- [ ] Клиент кладёт трубку посреди ответа: сессия на бэкенде закрывается, в Asterisk не остаётся висящих бриджей (`bridge show all`)
- [ ] Задержка на телефоне замерена секундомером от конца фразы до начала ответа
