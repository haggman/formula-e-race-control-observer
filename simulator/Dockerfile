FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# src/ includes the bundled frames.jsonl.gz artifact
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

CMD exec uvicorn src.main:app --host 0.0.0.0 --port ${PORT}
