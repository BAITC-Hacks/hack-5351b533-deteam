# Шаблонные данные и мок‑сервер

Данные в финальном формате контрактов из `/contracts`, чтобы фронт и телефония работали до готовности бэкенда. Разбор диалогов по ходам: [docs/frontend/DIALOG_EXAMPLES.md](../docs/frontend/DIALOG_EXAMPLES.md).

| Путь | Что внутри |
|---|---|
| `sessions/*.jsonl` | полные потоки событий сессий, по одному JSON на строку, как их шлёт `/ws/voice` |
| `sessions/index.json` | список сессий с тегами |
| `supervisor/` | ответы REST супервизора и эволюции: stats, sessions, cases, bench_runs, patches, catalog_versions |
| `catalog/` | сценарии с русскими названиями, действия, персоны с телефонами |
| `mock-server/` | FastAPI мок: REST, `/ws/voice` реплей, `/ws/supervisor`, эхо AudioSocket на `:9092` |

```bash
python -m venv .venv && .venv/bin/pip install -r fixtures/mock-server/requirements.txt
.venv/bin/python fixtures/mock-server/server.py
```
