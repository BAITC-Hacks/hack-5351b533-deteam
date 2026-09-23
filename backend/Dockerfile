# Voice Router API. Контекст сборки — корень репозитория:
#   docker build -f backend/Dockerfile -t voice-router .      (или docker compose up --build)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости отдельным слоем (кэш сборки). websockets/jsonschema/referencing нужны ARI-контроллеру,
# пробам и сценарным тестам; httpx2 ставится вместе с openai.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt websockets jsonschema referencing

# Код бэкенда, данные кита и контракты (схемы нужны тестам и пробам)
COPY backend/ /app/backend/
COPY data/ /app/data/
COPY contracts/ /app/contracts/

ENV DATA_DIR=/app/data \
    CATALOG_DIR=/app/catalog \
    REGRESSION_DIR=/app/regression

RUN mkdir -p /app/catalog /app/regression /app/backend/tts_cache /app/backend/logs/sessions

WORKDIR /app/backend

# 8000 — REST + WS (/ws/voice, /ws/supervisor); 9092 — AudioSocket для Asterisk
EXPOSE 8000 9092

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
