# Voice Router — Saqta Insurance (DeTeam)

Голосовой AI‑агент контакт‑центра с LLM‑слоем выбора сценария и эволюцией каталога. HackAlem AI, трек Halyk Bank, кейс 2.

## Структура

| Путь | Что внутри |
|---|---|
| [backend/](backend) | бэкенд голосового агента (в работе) |
| [frontend/](frontend) | веб: звонок, трассировка, супервизор |
| [infra/asterisk/](infra/asterisk) | Asterisk в Docker: PJSIP, AMI, ARI |
| [contracts/](contracts) | схемы событий и REST, общие для всех частей |
| [fixtures/](fixtures) | шаблонные диалоги и данные супервизора |
| [tools/](tools) | мок‑сервер, проверка фикстур против схем |
| [data/](data) | стартовый кит кейса и `evaluate.py` |
| [docs/](docs) | условия кейса, концепт, контракты для фронта и телефонии |

## Документы

| Кому | Куда смотреть |
|---|---|
| Команде | [ARCHITECTURE.md](docs/concept/ARCHITECTURE.md), [ROUTER.md](docs/concept/ROUTER.md), [EVOLUTION.md](docs/concept/EVOLUTION.md) |
| Фронтендеру | [docs/frontend/README.md](docs/frontend/README.md), [DIALOG_EXAMPLES.md](docs/frontend/DIALOG_EXAMPLES.md), [frontend/README.md](frontend/README.md) |
| Телефонии | [docs/telephony/README.md](docs/telephony/README.md), [infra/asterisk/README.md](infra/asterisk/README.md) |
| Условия кейса | [docs/case/README.ru.md](docs/case/README.ru.md), [ТЗ](docs/case/TZ_Voice_Router.pdf) |

## Быстрые команды

```bash
python -m venv .venv && .venv/bin/pip install -r tools/mock-server/requirements.txt jsonschema
.venv/bin/python tools/mock-server/server.py        # мок бэкенда: :8000 HTTP/WS, :9092 AudioSocket
.venv/bin/python tools/validate_contracts.py        # фикстуры против схем
cd frontend && npm ci && npm run dev                 # фронт
cd infra/asterisk && docker compose up -d --build    # Asterisk
python data/evaluate.py predictions.json data/dev_utterances.json
```

Для сквозной проверки телефонии на одном компьютере используйте `infra/asterisk/compose.smoke.yaml`: он добавляет к Asterisk мок AudioSocket и временный ARI-контроллер. Настройка `.env`, SIP-аккаунтов и порядок проверки звука описаны в [infra/asterisk/README.md](infra/asterisk/README.md).
