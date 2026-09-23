# Шаблонные данные и мок‑сервер

Данные в финальном формате контрактов из `/contracts`, чтобы фронт и телефония работали до готовности бэкенда. Разбор диалогов по ходам: [docs/frontend/DIALOG_EXAMPLES.md](../docs/frontend/DIALOG_EXAMPLES.md).

| Путь | Что внутри |
|---|---|
| `sessions/*.jsonl` | полные потоки событий сессий, по одному JSON на строку, как их шлёт `/ws/voice` |
| `sessions/index.json` | список сессий с тегами |
| `supervisor/` | ответы REST супервизора и эволюции: stats, sessions, cases, bench_runs, patches, catalog_versions |
| `catalog/` | сценарии с русскими названиями, действия, персоны с телефонами |
| мок‑сервер | лежит в [tools/mock-server](../tools/mock-server) |

```bash
python -m venv .venv && .venv/bin/pip install -r tools/mock-server/requirements.txt
.venv/bin/python tools/mock-server/server.py
```
