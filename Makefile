# Voice Router — быстрые команды. Python-окружение: .venv в корне репозитория.
#   python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt websockets jsonschema referencing
PY      := $(CURDIR)/.venv/bin/python
BACKEND := $(CURDIR)/backend
HTTP    ?= http://127.0.0.1:8000
WS      ?= ws://127.0.0.1:8000/ws/voice
ASOCK   ?= 127.0.0.1:9092

.PHONY: help venv run up down logs bench replay test probe-voice probe-phone validate web

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv: ## создать .venv и поставить зависимости бэкенда
	python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt websockets jsonschema referencing

run: ## бэкенд локально на :8000 (+ AudioSocket :9092)
	cd $(BACKEND) && $(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000

up: ## бэкенд в Docker (нужен backend/.env с OPENAI_API_KEY)
	docker compose up --build

down: ## остановить контейнер
	docker compose down

logs: ## логи контейнера
	docker compose logs -f api

web: ## фронт (Vite dev server на :5173)
	cd frontend && ([ -d node_modules ] || npm ci) && npm run dev -- --host 127.0.0.1 --port 5173

bench: ## роутер на dev-наборе + официальный evaluate.py
	cd $(BACKEND) && $(PY) -m app.bench --out $(CURDIR)/regression/predictions.json
	$(PY) data/evaluate.py regression/predictions.json data/dev_utterances.json

replay: ## размеченные диалоги кита в текстовом режиме
	cd $(BACKEND) && $(PY) -m app.replay

test: ## 80 сценарных диалогов (40 happy + 40 edge), без сервера
	cd $(BACKEND) && $(PY) -m tests.scenario_dialogs

probe-voice: ## сквозной голосовой тест через /ws/voice (сервер должен быть запущен)
	cd $(BACKEND) && $(PY) tools/voice_probe.py --url $(WS) --caller +77010000007 "Что с моим заявлением?" "Соедините с оператором"

probe-phone: ## сквозной тест телефонии: притворяемся Asterisk-ом по AudioSocket
	cd $(BACKEND) && $(PY) tools/phone_probe.py --http $(HTTP) --as $(ASOCK) --caller +77010000004 "Что с моим заявлением?" "Соедините меня с оператором"

validate: ## фикстуры против JSON-схем контрактов
	$(PY) tools/validate_contracts.py
