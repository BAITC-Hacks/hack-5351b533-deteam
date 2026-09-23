# Asterisk для локального голосового агента

Этот каталог поднимает Asterisk с SIP, ARI и AudioSocket по [телефонному контракту](../../docs/telephony/README.md). Звонок попадает в `Stasis(voice-ai)`; ARI-приложение управляет каналами, а звук передаётся ему через External Media (`tcp/audiosocket`, `slin`, 8 кГц). AMI оставлен на `127.0.0.1:5038` для совместимости и диагностики, но основной маршрут его не использует.

## Запуск на одном компьютере (Windows)

Из каталога `infra/asterisk`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Скрипт создаёт `.env` с паролями и пустой `config/pjsip_trunk.conf`; существующие пароли сохраняет. В `.env` задайте `ASTERISK_PUBLIC_IP` равным IPv4-адресу Windows-интерфейса, через который MicroSIP обращается к Asterisk. Даже когда оба приложения на одном ПК, этот адрес нужен для RTP через Docker Desktop. `ASTERISK_LOCAL_NET=auto` вычисляет IPv4 контейнера с маской `/32`, чтобы SIP от Docker gateway получил правильный адрес в SDP. При смене сети обновите `ASTERISK_PUBLIC_IP` и пересоздайте контейнер.

Для запуска только Asterisk:

```powershell
docker compose up -d --build
docker compose ps
```

Пока ARI-приложение не подключено, вызовы на агентские номера после `Stasis(voice-ai)` завершаются. Номер `9999` всегда запускает `Echo()` и проверяет SIP/RTP без бэкенда.

## Сквозной тест без готового бэкенда

Дополнительный Compose-файл запускает мок AudioSocket и временный ARI-контроллер. Они работают на этом же ПК в Docker-сети. Контроллер создаёт mixing bridge, добавляет звонящего и External Media канал, закрывает ресурсы при отбое и переводит по DTMF `0` на оператора. Мок возвращает голос звонящего с задержкой около секунды.

Основной Compose также запускает локальный MinIO и `recording-uploader`. Asterisk пишет один WAV на входной канал, включая этап после перевода на оператора. После окончательного отбоя uploader загружает WAV в приватный bucket `call-recordings` и отправляет ссылку в backend по [контракту](../../contracts/openapi.yaml). При недоступности MinIO или backend файл остаётся в томе `call_recordings`, доставка повторяется. Консоль MinIO: `http://127.0.0.1:9001`, логин `MINIO_ROOT_USER` и пароль `MINIO_ROOT_PASSWORD` из `.env`.

```powershell
docker compose -f compose.yaml -f compose.smoke.yaml up -d --build
docker compose -f compose.yaml -f compose.smoke.yaml ps
docker compose -f compose.yaml -f compose.smoke.yaml logs -f ari-smoke mock
docker compose -f compose.yaml -f compose.smoke.yaml logs -f recording-uploader
```

Для проверки конфигурации:

```powershell
docker compose exec asterisk asterisk -rx 'pjsip show endpoints'
docker compose exec asterisk asterisk -rx 'dialplan show from-inbound'
docker compose exec asterisk asterisk -rx 'dialplan show saqta-transfer'
docker compose exec asterisk asterisk -rx 'ari show apps'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-ami.ps1
```

`ari show apps` должен показывать `voice-ai`, когда запущен `ari-smoke` или настоящий бэкенд. Одновременно подключать два ARI-контроллера с этим именем приложения нельзя: перед запуском настоящего бэкенда остановите smoke-сервисы.

## Софтфоны на том же ПК

| Роль | SIP-пользователь | Пароль из `.env` | Что набрать |
| --- | --- | --- | --- |
| Клиент | `1001` | `SIP_TEST_SECRET` | `9999` для RTP, затем `757504` или любой номер для ARI |
| Оператор | `operator` | `SIP_OPERATOR_SECRET` | Принимает перевод по DTMF `0` |

В настройках обоих клиентов: SIP-сервер `<ASTERISK_PUBLIC_IP>:5060`, транспорт UDP, домен `<ASTERISK_PUBLIC_IP>`, SIP-прокси пустой. Если MicroSIP запущен на Windows-хосте с Docker, задайте ему локальный SIP-порт `5062` (у второго клиента другой, например `5064`): порт `5060` уже занят публикацией Docker. Существующий аккаунт `test-inbound` продолжает работать с `SIP_TEST_SECRET`.

1. Позвоните с `1001` на `9999`. Вы должны слышать свой голос. Если тишина, проверьте устройство вывода, `ASTERISK_PUBLIC_IP`, SIP-регистрацию и UDP 10000–10100.
2. Позвоните на `757504`: в Asterisk caller ID станет `+77010000004`; `757507` даёт `+77010000007`. В логе `ari-smoke` должны появиться `StasisStart`/bridge, в логе `mock` — AudioSocket UUID. После произнесённой фразы придёт эхо.
3. Во время звонка нажмите DTMF `0`: должен зазвонить `operator`. DTMF `#` завершает тестовый звонок.
4. Положите трубку и проверьте `docker compose exec asterisk asterisk -rx 'bridge show all'`: тестовый bridge должен исчезнуть.
5. В логе `recording-uploader` проверьте загрузку WAV и подтверждение callback. В MinIO объект появится как `call-recordings/calls/<call_id>.wav`. Если callback не отвечает, запись остаётся локально и будет доставлена после восстановления бэкенда.

## Подключение настоящего бэкенда

Если бэкенд запущен на Windows-хосте, его настройки:

```text
ARI_URL=http://127.0.0.1:8088
ARI_USER=voice-ai
ARI_PASSWORD=<значение ARI_SECRET из infra/asterisk/.env>
ARI_APP=voice-ai
AUDIOSOCKET_HOST=host.docker.internal
AUDIOSOCKET_PORT=9092
```

Для callback записи `RECORDING_CALLBACK_BASE_URL` в `infra/asterisk/.env` указывает на адрес бэкенда, достижимый **из контейнера uploader**. Для бэкенда на Windows-хосте это обычно `http://host.docker.internal:8000`; для сервиса в той же Docker-сети — `http://api:8000`. При необходимости задайте одинаковый bearer-токен в `RECORDING_CALLBACK_TOKEN` у uploader и бэкенда. Подписанный `recording_url` выдаётся для Windows-хоста через `MINIO_PUBLIC_ENDPOINT=http://127.0.0.1:9000` и истекает через 24 часа. Постоянный адрес записи — `recording_uri`/bucket/key; бэкенд может выпустить свежую ссылку через S3 API. `call_id` в callback — `channel.id` из `StasisStart`.

Бэкенду для выпуска новых ссылок нужен S3 endpoint `http://minio:9000` в общей Docker-сети (или `http://127.0.0.1:9000`, если он на Windows-хосте), bucket `call-recordings` и учётные данные `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` из локального `.env`. Bucket приватный. Реализация callback должна идемпотентно сохранять запись по `call_id` и принимать повторную доставку с тем же `recording_uri`/`sha256`, даже если подписанный URL изменился.

Если бэкенд запускается контейнером в той же Compose-сети, используйте `ARI_URL=http://asterisk:8088`, а в `AUDIOSOCKET_HOST` — имя сервиса бэкенда. Asterisk подключается **к** бэкенду по TCP 9092; бэкенд должен слушать `0.0.0.0:9092` внутри своего контейнера. ARI и AMI опубликованы на хосте только на loopback. Не передавайте `.env` или пароли в Git.

Порты Asterisk: SIP 5060/udp, RTP 10000–10100/udp, ARI 127.0.0.1:8088/tcp, AMI 127.0.0.1:5038/tcp. Для внешнего локального SIP-сервера можно указать `LOCAL_SIP_PEER_IP` в `.env`; подробности [в исходной схеме](../../docs/telephony/README.md).
