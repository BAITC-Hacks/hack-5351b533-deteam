# Подключение этого Asterisk к Voice Router

Основная схема: ARI. `docker/entrypoint.py` уже генерирует `from-inbound` со `Stasis(voice-ai)`, `ari.conf` (пользователь `voice-ai`, пароль `ARI_SECRET`) и `http.conf` на 8088. Бэкенд подключается к ARI, сам создаёт External Media (AudioSocket по TCP) и сам переводит звонок на оператора через `continue` в `saqta-transfer`. Подробности и проверенные логи: [`/docs/telephony/README.md`](../../docs/telephony/README.md).

Минимальные правки:

1. `compose.yaml`: смонтировать диалплан из контракта и дать контейнеру адрес бэкенда.

   ```yaml
       volumes:
         - ./config/pjsip_trunk.conf:/etc/asterisk/pjsip_trunk.conf:ro
         - ../../contracts/asterisk/extensions.conf:/etc/asterisk/extensions_saqta.conf:ro
       extra_hosts:
         - "host.docker.internal:host-gateway"   # если бэкенд на этой же машине
   ```

   Если бэкенд на другом ПК, ARI нужно опубликовать не только на loopback: `"0.0.0.0:8088:8088/tcp"` и фаервол, пускающий только IP ноутбука с бэкендом.

2. `docker/entrypoint.py`: в блоке `write("extensions.conf", ...)` заменить свой `[from-inbound]` на include (в контракте тот же `from-inbound` со `Stasis(voice-ai)`, плюс `saqta-transfer`, демо‑персоны `7575NN` и запасной `saqta-audiosocket`):

   ```ini
   [general]
   static=yes
   writeprotect=yes

   #include extensions_saqta.conf
   ```

   Если include не хочется: достаточно добавить в свой диалплан контекст `[saqta-transfer]` из контракта.

3. Оператор: перевод звонит в `${OPERATOR}` = `PJSIP/operator`. Эндпоинт `operator` есть в [`/contracts/asterisk/pjsip.conf`](../../contracts/asterisk/pjsip.conf); его можно добавить в генерируемый `pjsip.conf` или переопределить `OPERATOR` в `[globals]` на существующий эндпоинт.

4. `docker compose up -d --force-recreate`, затем проверить:

   ```bash
   docker compose exec asterisk asterisk -rx "module show like audiosocket"   # chan_audiosocket, res_audiosocket
   docker compose exec asterisk asterisk -rx "ari show apps"                  # voice-ai после старта бэкенда
   docker compose exec asterisk asterisk -rx "dialplan show saqta-transfer"
   ```

   Пакет `asterisk` в Ubuntu 24.04 (20.6) содержит `chan_audiosocket` и поддержку `externalMedia encapsulation=audiosocket`. Живой звонок проверяли на образе `andrius/asterisk` (22.10); если на 20.6 бэкенд пишет `externalMedia ... HTTP 501`, используйте `andrius/asterisk`.

Бэкенд (`.env`): `ARI_URL=http://127.0.0.1:8088` (или IP этого ПК), `ARI_USER=voice-ai`, `ARI_PASSWORD=<ARI_SECRET>`, `AUDIOSOCKET_HOST=<адрес бэкенда, видимый из контейнера>` (`host.docker.internal` или LAN‑IP ноутбука), `AUDIOSOCKET_PORT=9092`.

## Запасной режим без ARI

Если бэкенд не подключён к ARI, `Stasis()` возвращает FAILED и контракт сам уводит звонок в `saqta-audiosocket`: диалплан регистрирует звонок через `CURL` и вызывает `AudioSocket()`. Для этого нужны `func_curl`/`res_curl` и адреса бэкенда в `.env` Asterisk (диалплан читает их через `ENV()`):

```env
VR_API=http://192.168.1.50:8000      # IP ноутбука с бэкендом; на той же машине: http://host.docker.internal:8000
VR_AS=192.168.1.50:9092
```

AMI для перевода не обязателен: без него бэкенд шлёт `0x00`, а диалплан сам спрашивает `/outcome` и переводит. Чтобы перевод шёл через AMI, опубликуйте `5038:5038` (сейчас только loopback) и задайте на бэкенде `AMI_HOST`, `AMI_USER=voice-ai`, `AMI_SECRET`.
