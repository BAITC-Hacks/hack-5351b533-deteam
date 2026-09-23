# Runbook: установка, запуск и проверка Voice Router

Пошагово для демо‑ноутбука (Ubuntu 24.04) и для коллег на Windows. В конце есть чек‑лист проверки и таблица «если что‑то не так». Все команды выполняются **из корня репозитория**, если не сказано другое.

## 0. Что должно получиться

```text
 Софтфон (Linphone / MicroSIP) ──SIP 5060/udp, RTP 10000–10100/udp──► Asterisk (Docker, infra/asterisk)
                                                                         │ ARI 127.0.0.1:8088  ◄── бэкенд подключён как приложение voice-ai
                                                                         │ External Media ─────► бэкенд :9092 (AudioSocket)
                                                                         │ запись WAV ─► MinIO :9000 ─► callback в бэкенд :8000
 Браузер ──► фронт http://127.0.0.1:5173 ──/api, /ws──► бэкенд http://127.0.0.1:8000
```

| Компонент | Адрес | Запуск |
|---|---|---|
| Бэкенд: роутер, голос, ARI, AudioSocket | `http://127.0.0.1:8000`, AudioSocket `:9092` | `make run` |
| Фронт | `http://127.0.0.1:5173` | `make web` |
| Asterisk + MinIO + загрузчик записей | SIP `<IP ноутбука>:5060`, ARI `127.0.0.1:8088`, MinIO `127.0.0.1:9000`, консоль `:9001` | `docker compose -f infra/asterisk/compose.yaml up -d --build` |

Для телефонии бэкенд запускаем **на хосте** через `make run`, а не в Docker. ARI у Asterisk опубликован только на `127.0.0.1`, из контейнера бэкенда до него не достучаться. Если раньше поднимали бэкенд в Docker из корня (`docker compose up`), сначала остановите его: `docker rm -f voice-router-api`. Иначе порты 8000 и 9092 будут заняты.

## 1. Что скачать

| Что | Ubuntu 24.04 | Windows |
|---|---|---|
| Git, make, curl, venv | `sudo apt install -y git make curl python3.12-venv` | git-scm.com; make не нужен, см. 2.1 |
| Docker | Docker Engine + compose plugin, затем `sudo usermod -aG docker $USER` и перелогиниться | Docker Desktop, режим Linux containers |
| Python 3.12 | есть в системе | python.org, 3.12 |
| Node.js 24 | через nvm: `nvm install 24` (в корне `.nvmrc` = 24) | nodejs.org, 24 LTS |
| Софтфон | Linphone 6, AppImage без sudo, см. ниже | MicroSIP, microsip.org |
| Ключ OpenAI | от команды | от команды |

**Linphone без sudo.** В Ubuntu 24.04 нет libfuse2, поэтому AppImage распаковываем и делаем короткий запускатель:

```bash
mkdir -p ~/Apps && cd ~/Apps
curl -LO https://download.linphone.org/releases/linux/app/Linphone-6.2.3-x86_64.AppImage
chmod +x Linphone-6.2.3-x86_64.AppImage
./Linphone-6.2.3-x86_64.AppImage --appimage-extract >/dev/null && mv squashfs-root linphone-6.2.3 && rm Linphone-6.2.3-x86_64.AppImage
printf '#!/bin/bash\nexec "$HOME/Apps/linphone-6.2.3/AppRun" "$@"\n' > ~/Apps/linphone && chmod +x ~/Apps/linphone
cd -
```

Запуск: `~/Apps/linphone`.

## 2. Настройка

### 2.1 Код и зависимости

```bash
git clone https://github.com/BAITC-Hacks/hack-5351b533-deteam.git hackalem
cd hackalem
make venv                 # .venv + зависимости бэкенда
npm ci --prefix frontend  # зависимости фронта
```

**Windows (PowerShell), вместо make.** В Windows PowerShell 5.1 нельзя склеивать команды через `&&`, поэтому каждая команда отдельной строкой.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm ci --prefix frontend
```

| Цель make | Команда на Windows |
|---|---|
| `make run` | `cd backend` затем `..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| `make web` | `cd frontend` затем `npm run dev -- --host 127.0.0.1 --port 5173` |
| `make validate` | `.venv\Scripts\python.exe tools\validate_contracts.py` |
| `make replay` | `cd backend` затем `..\.venv\Scripts\python.exe -m app.replay` |
| `make test` | `cd backend` затем `..\.venv\Scripts\python.exe -m tests.scenario_dialogs` |

При первом `make run` Windows Defender спросит про сеть для python.exe: разрешите в частных сетях.

### 2.2 `infra/asterisk/.env`

- **Windows:** `powershell -NoProfile -ExecutionPolicy Bypass -File .\infra\asterisk\scripts\setup.ps1`. Скрипт создаст `.env` с паролями и `config/pjsip_trunk.conf`.
- **Linux:**
  ```bash
  cp infra/asterisk/.env.example infra/asterisk/.env
  cp infra/asterisk/config/pjsip_trunk.conf.example infra/asterisk/config/pjsip_trunk.conf
  nano infra/asterisk/.env   # заменить все CHANGE_ME
  ```
  В паролях только латиница, цифры и `._~:/-`, иначе контейнер Asterisk упадёт с `Invalid characters in …`. `MINIO_ROOT_PASSWORD` не короче 8 символов. Сгенерировать пароль: `openssl rand -hex 16`.
- **`ASTERISK_PUBLIC_IP`** = LAN‑адрес компьютера, через который софтфон ходит к Asterisk. Этот же адрес потом указывается в софтфоне, даже если всё на одном ПК.
  - Linux: `ip route get 1.1.1.1`, поле `src`.
  - Windows: `(Get-NetIPConfiguration | Where-Object IPv4DefaultGateway).IPv4Address.IPAddress`, либо IPv4 адаптера Wi‑Fi или Ethernet в `ipconfig`. Не адаптеры `vEthernet (WSL)` и `Default Switch`.
  - **На площадке IP будет другой**, см. раздел 5.
- Остальное по умолчанию: `RECORDING_CALLBACK_BASE_URL=http://host.docker.internal:8000`, `RECORDING_CALLBACK_TOKEN` пустой.

### 2.3 `backend/.env`

```bash
OPENAI_API_KEY=sk-...
# Asterisk на этом же компьютере
ARI_URL=http://127.0.0.1:8088
ARI_USER=voice-ai
ARI_PASSWORD=<ARI_SECRET из infra/asterisk/.env>
ARI_APP=voice-ai
AUDIOSOCKET_HOST=host.docker.internal    # как Asterisk из контейнера достаёт до бэкенда
AUDIOSOCKET_PORT=9092
```

- Если `ARI_URL` пустой, бэкенд работает без телефонии, веб и текст продолжают работать.
- `RECORDING_CALLBACK_TOKEN` задаётся только если он задан в `infra/asterisk/.env`, значения должны совпадать.
- **Windows:** создавать файл через `notepad backend\.env`. Команда `echo ... > backend\.env` в PowerShell 5.1 пишет UTF‑16, и бэкенд не прочитает ключ.

### 2.4 Софтфон

| Поле | Значение |
|---|---|
| Пользователь / логин | `1001` |
| Пароль | `SIP_TEST_SECRET` из `infra/asterisk/.env` |
| Домен / SIP‑сервер | `ASTERISK_PUBLIC_IP`, порт 5060 |
| Транспорт | UDP |
| Прокси | пусто |
| Локальный SIP‑порт | `5062`, потому что 5060 занят Docker |

**Linphone.** В интерфейсе Linphone 6 нет поля локального порта. Порт задаём в конфиге до первого запуска или закрыв Linphone:

```bash
f=~/.config/linphone/linphonerc; mkdir -p ~/.config/linphone; touch $f
if grep -q '^sip_port=' $f; then sed -i 's/^sip_port=.*/sip_port=5062/' $f
elif grep -q '^\[sip\]' $f; then sed -i '/^\[sip\]/a sip_port=5062' $f
else printf '[sip]\nsip_port=5062\n' >> $f; fi
```

Затем запустить Linphone, выбрать вход по SIP‑аккаунту и заполнить поля из таблицы.

**MicroSIP.** Account → Add, те же поля. Локальный порт `5062`: Settings → Source port.

**Звук.** В настройках аудио софтфона проверьте устройства. Linphone при первом запуске может выбрать выход HDMI на монитор, и тогда ничего не слышно. Нужны микрофон и динамики гарнитуры или ноутбука. **Гарнитура обязательна:** звук из динамиков попадает в микрофон, и бот перебивает сам себя.

**Оператор, по желанию.** Аккаунт `operator` с паролем `SIP_OPERATOR_SECRET` регистрируется на **втором устройстве**. Например, MicroSIP на ноутбуке коллеги, сервер = IP демо‑ноутбука. Второй Linphone на том же ноутбуке не запустится, Linphone 6 однокопийный. MicroSIP держит один активный аккаунт. Запасной вариант: добавить `operator` вторым аккаунтом в тот же Linphone, тогда перевод зазвонит в нём же. На этот аккаунт уходит перевод «соедините с оператором».

## 3. Запуск, строго по порядку

1. **Asterisk, MinIO, загрузчик записей:**
   ```bash
   docker compose -f infra/asterisk/compose.yaml up -d --build
   docker compose -f infra/asterisk/compose.yaml ps
   ```
2. **Бэкенд**, отдельный терминал: `make run`. Слушает `0.0.0.0:8000` и `:9092`.
3. **Фронт**, ещё один терминал: `make web`. Зависимости уже стоят с шага 2.1.
4. **Софтфон:** `~/Apps/linphone` или MicroSIP.

Бэкенд готов, когда в его логе есть обе строки:

```text
[ari] connected http://127.0.0.1:8088 app=voice-ai; External Media -> host.docker.internal:9092 (audiosocket/tcp/slin)
[tts] prewarmed N phrases
```

При первом запуске прогрев синтезирует около 180 фраз, это минута‑две. Потом фразы берутся из кэша `backend/tts_cache`, и в логе будет `[tts] prewarmed 0 phrases`, это норма.

## 4. Проверка

### 4.1 Без телефона, пара минут

| Команда | Ожидаем |
|---|---|
| `curl -s http://127.0.0.1:8000/api/health`, на Windows `curl.exe -s ...` | `"status":"ok"` и список моделей |
| `make validate` | `OK` |
| `make replay` | `primary per turn: 40/40` |
| `make test` | около `78–80/80 passed`, провалы только «long: 4 sentences» |
| `make bench` | primary_acc 0.99–1.00. Не запускать во время живого звонка, съедает лимит OpenAI |

### 4.2 Голос в браузере

1. Открыть `http://127.0.0.1:5173/call`.
2. В списке номера звонящего выбрать персону, например «+77010000007 · Sergey Popov». Либо «Звоню с неизвестного номера».
3. Начать звонок, разрешить микрофон. Бот здоровается, у известного номера по имени.
4. Сказать «Здравствуйте, хочу узнать, что с моим заявлением по каско». Можно открытым микрофоном или зажав пробел.
5. Ожидаем: транскрипт реплики и ответ про заявление CL‑500330. В трассировке SC17, уверенность, причина, действие `get_claim`, водопад задержек, total 2–3 с.
6. Во вкладке «Супервизор» появилась сессия со всеми ходами.

### 4.3 Телефон

| Шаг | Ожидаем |
|---|---|
| `docker compose -f infra/asterisk/compose.yaml exec asterisk asterisk -rx "ari show apps"` | в списке `voice-ai` |
| Статус аккаунта в софтфоне | зарегистрирован, зелёный |
| Позвонить на `9999` | слышно свой голос с задержкой: SIP и RTP работают |
| Позвонить на `757507` | «Здравствуйте, Сергей!» без вопроса про телефон |
| Сказать «Что с моим заявлением по каско?» | ответ про CL‑500330 и срок решения |
| Сказать «Соедините меня с оператором» или нажать `0` | «Соединяю…», звонит аккаунт `operator`. Если он не зарегистрирован, звонок после «Соединяю…» завершается, это нормально |
| Положить трубку, подождать 10–20 с | в консоли MinIO `http://127.0.0.1:9001` лежит `call-recordings/calls/<id>.wav`, у сессии во фронте появилась запись. Логин консоли `MINIO_ROOT_USER`, по умолчанию `voicerouter`, пароль `MINIO_ROOT_PASSWORD` из `infra/asterisk/.env` |

**Номера для звонка:**
- `757507` звонит от имени Сергея, `757504` от имени Натальи.
- `7575NN` звонит от имени клиента `C0NN` из `data/mock_backend.json`, NN от 01 до 11.
- Любой другой номер, кроме `9999`, попадёт к агенту с caller ID аккаунта `1001`. Это `+77010000007`, то есть тоже Сергей.

### 4.4 Эволюция каталога, демо за минуту

1. Вкладка «Эволюция»: кейсы, на которых роутер ошибается. Если кейсов нет, пометить ход кнопкой «Неверно» в Супервизоре. Либо сгенерировать, это тратит лимит OpenAI:
   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/cases/generate -H 'Content-Type: application/json' -d '{"n":10}'
   ```
2. «Предложить патч» по кейсу. Через 30–40 с появятся дифф каталога и метрики до и после, сломанных кейсов нет.
3. «Применить», повторить фразу кейса голосом или текстом: сценарий теперь верный.
4. Откатить на `v1` и **заморозить каталог перед зачётным прогоном**.

## 5. Если что‑то не так

| Симптом | Что проверить |
|---|---|
| Софтфон не регистрируется | IP и порт 5060, пароль `SIP_TEST_SECRET`, локальный порт не 5060. `docker compose -f infra/asterisk/compose.yaml exec asterisk asterisk -rx "pjsip show contacts"` |
| На `9999` ничего не слышно | Сначала устройства аудио в софтфоне: Linphone любит выбрать HDMI. Затем `ASTERISK_PUBLIC_IP` = текущий IP и что UDP 10000–10100 не закрыт фаерволом |
| На `757507` звонок сразу сбрасывается | Бэкенд не подключён к ARI: в логе нет `[ari] connected`. `ARI_PASSWORD` должен совпадать с `ARI_SECRET`. Бэкенд запускать до звонка |
| Звонок сразу уходит к оператору без приветствия бота | Asterisk не достучался до `:9092`: в логе бэкенда `[ari] ERROR: … externalMedia …`. Бэкенд должен слушать `0.0.0.0`, `AUDIOSOCKET_HOST=host.docker.internal`. Linux с ufw: `sudo ufw allow from 172.16.0.0/12 to any port 8000,9092 proto tcp` |
| Агент отвечает поздно, больше 4 с | Упёрлись в лимит OpenAI: не гонять `make bench` и патчи эволюции параллельно со звонком |
| Бот перебивает сам себя | Гарнитура вместо динамиков, либо `BARGE_IN=0` в `backend/.env` |
| Запись не появилась во фронте | `docker compose -f infra/asterisk/compose.yaml logs --tail 50 recording-uploader`, нужна строка `Backend acknowledged recording`. `URLError`: бэкенд не запущен или не слушает `0.0.0.0:8000`. `HTTP 401`: `RECORDING_CALLBACK_TOKEN` в двух `.env` не совпадает, выровнять и выполнить `docker compose -f infra/asterisk/compose.yaml up -d recording-uploader` |
| Фронт пишет «Ошибка соединения с /ws/voice» | Бэкенд не запущен на `127.0.0.1:8000` |
| `make run`: address already in use, на Windows WinError 10048 | Работает контейнер `voice-router-api` из корневого compose: `docker rm -f voice-router-api` |
| Бэкенд на Windows падает с `UnicodeDecodeError` | Устаревший код. В актуальном main файлы читаются в UTF‑8. Временный обход: `$env:PYTHONUTF8=1` перед запуском |
| Docker Desktop: `ports are not available … 5060` или `10000` | Порт в зарезервированном диапазоне Windows: `netsh interface ipv4 show excludedportrange protocol=udp`. Помогает, в PowerShell от администратора: `net stop winnat`, затем `docker compose -f infra/asterisk/compose.yaml up -d`, затем `net start winnat` |
| Сменилась сеть, например на площадке | Поменять `ASTERISK_PUBLIC_IP` в `infra/asterisk/.env`, затем `docker compose -f infra/asterisk/compose.yaml up -d --force-recreate asterisk`. В софтфоне поменять адрес сервера |
| Звонок с другого ПК | Софтфон на том ПК регистрируется на IP ноутбука. На ноутбуке открыть 5060/udp и 10000–10100/udp |

## 6. Остановка

- Бэкенд и фронт: Ctrl+C в их терминалах.
- Asterisk и MinIO: `docker compose -f infra/asterisk/compose.yaml down`. Записи и данные MinIO остаются в Docker‑томах.

Подробнее: [телефония](telephony/README.md), [Asterisk напарника](../infra/asterisk/README.md), [бэкенд](../backend/README.md), [фронт: запуск](../frontend/README.md), [фронт: контракт](frontend/README.md), [архитектура](concept/ARCHITECTURE.md).
