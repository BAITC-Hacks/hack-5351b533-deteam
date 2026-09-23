# Контракты Voice Router

Единственный источник правды для бэкенда, фронта и телефонии. Меняем только через правку этих файлов и сообщение в чат команды.

| Файл | Что описывает |
|---|---|
| `ws-events.schema.json` | все события `WS /ws/voice` и `WS /ws/supervisor`, форматы аудио |
| `trace.schema.json` | трассировка хода (формат README кейса + наши поля) |
| `dialog-state.schema.json` | состояние диалога: клиент, активный сценарий, стек, слоты, подтверждение |
| `router-output.schema.json` | вывод роутера, strict‑схема для structured outputs `gpt-6-luna` |
| `evolution.schema.json` | кейсы, прогоны bench, патчи каталога, версии каталога |
| `openapi.yaml` | REST: сессии, статистика, каталог, эволюция, телефония |
| `telephony/` | `extensions.conf`, `pjsip.conf`, `manager.conf` для Asterisk |

Проверка фикстур против схем:

```bash
.venv/bin/pip install jsonschema && .venv/bin/python contracts/tools/validate.py
```
