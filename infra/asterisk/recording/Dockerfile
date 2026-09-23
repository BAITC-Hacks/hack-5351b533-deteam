FROM python:3.12-slim

WORKDIR /app
COPY infra/asterisk/recording/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY infra/asterisk/recording/uploader.py /app/uploader.py

CMD ["python", "-u", "/app/uploader.py"]
