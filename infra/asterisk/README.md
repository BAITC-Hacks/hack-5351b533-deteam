# Asterisk для входящего голосового ИИ

Стек поднимает Asterisk с SIP/PJSIP, AMI и ARI. Входящие вызовы от разрешённого локального SIP-сервера попадают в приложение ARI `voice-ai`. AMI отдаёт события и позволяет управлять вызовами. Для будущей передачи аудио ИИ используйте ARI External Media (RTP) и сервис обработки речи.

## Запуск в Windows PowerShell

1. Запустите Docker Desktop в режиме Linux containers.
2. Создайте пароли и локальный конфиг транка:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
   ```

3. Запустите Asterisk:

   ```powershell
   docker compose up -d --build
   docker compose ps
   docker compose logs -f asterisk
   ```

`setup.ps1` не заменяет уже созданные `.env` и `config/pjsip_trunk.conf`. Эти файлы не попадают в Git. В Linux/macOS скопируйте `.env.example` в `.env`, замените три значения `CHANGE_ME` стойкими паролями, затем скопируйте `config/pjsip_trunk.conf.example` в `config/pjsip_trunk.conf`.

Для другого локального SIP-сервера укажите **его точный IPv4-адрес** в `.env` как `LOCAL_SIP_PEER_IP`, а затем выполните `docker compose up -d --force-recreate`. На том сервере направьте входящие вызовы на IP Docker-хоста, UDP 5060. Asterisk узнаёт этот транк по IP без SIP-регистрации. Ограничьте доступ к UDP 5060 на сетевом экране адресом этого сервера. Если он работает на том же хосте, в `LOCAL_SIP_PEER_IP` нужен IP, который Asterisk видит внутри контейнера; его можно выяснить по SIP-логам.

## Подключение

| Интерфейс | Адрес с хоста | Учётные данные |
| --- | --- | --- |
| SIP UDP | `localhost:5060` | `test-inbound` / `SIP_TEST_SECRET` |
| AMI TCP | `127.0.0.1:5038` | `voice-ai` / `AMI_SECRET` |
| ARI HTTP и WebSocket | `http://127.0.0.1:8088/ari` | `voice-ai` / `ARI_SECRET` |
| RTP | UDP 10000–10100 | — |

AMI и ARI публикуются только на loopback хоста. Для ИИ-сервиса в том же Compose-проекте используйте `asterisk:5038` и `http://asterisk:8088/ari` внутри Docker-сети. Не публикуйте эти интерфейсы напрямую в интернет. Для удалённого сервиса используйте VPN или другой защищённый канал.

Быстрая проверка:

```powershell
docker compose exec asterisk asterisk -rx 'manager show users'
docker compose exec asterisk asterisk -rx 'pjsip show endpoints'
docker compose exec asterisk asterisk -rx 'dialplan show from-inbound'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-ami.ps1
```

## Входящий звонок

Для локального теста зарегистрируйте программный SIP-телефон на адресе Docker-хоста, порт 5060 UDP, пользователь `test-inbound`, пароль из `.env`. Позвоните на любой номер, например `1000`. Вызов перейдёт в `Stasis(voice-ai)`. ARI-приложение должно заранее открыть WebSocket:

```text
ws://127.0.0.1:8088/ari/events?app=voice-ai&api_key=voice-ai:ARI_SECRET
```

Если приложение ARI не подключено, Asterisk не сможет передать ему вызов и завершит его. Это ожидаемо: голосовой агент в данный репозиторий пока не входит.

Для SIP-провайдера есть необязательный образец в `config/pjsip_trunk.conf`. После правки выполните `docker compose restart asterisk` и проверьте `pjsip show registrations` и `pjsip show endpoints`. Когда RTP пересекает Docker NAT, укажите достижимый для звонящего адрес Docker-хоста в `.env` как `ASTERISK_PUBLIC_IP`, затем пересоздайте контейнер командой `docker compose up -d --force-recreate`. Для локальной сети это LAN-адрес хоста. При изменении сети Docker при необходимости поправьте `ASTERISK_LOCAL_NET`.

## Передача аудио агенту

ARI-приложение получает событие `StasisStart` для входящего канала. Оно создаёт mixing bridge, добавляет в него канал звонящего, затем создаёт External Media канал и добавляет его в тот же bridge. Например, запрос для RTP с кодеком `ulaw`:

```text
POST /ari/channels/externalMedia?app=voice-ai&external_host=agent:60000&format=ulaw
```

Сервис `agent` принимает RTP и отправляет RTP обратно на адрес и порт, указанные в переменных `UNICASTRTP_LOCAL_ADDRESS` и `UNICASTRTP_LOCAL_PORT` внешнего канала. Для распознавания и синтеза речи нужен отдельный сервис. AMI можно параллельно использовать для событий, перевода и завершения звонка. Пароли из `.env` не следует передавать в URL, доступный посторонним; URL выше служит лишь схемой подключения.

Документация Asterisk: [AMI](https://docs.asterisk.org/Configuration/Interfaces/Asterisk-Manager-Interface-AMI/The-Asterisk-Manager-TCP-IP-API/), [ARI](https://docs.asterisk.org/Configuration/Interfaces/Asterisk-REST-Interface-ARI/Getting-Started-with-ARI/), [External Media](https://docs.asterisk.org/Development/Reference-Information/Asterisk-Framework-and-API-Examples/External-Media-and-ARI/), [PJSIP trunk](https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/res_pjsip-Configuration-Examples/).
