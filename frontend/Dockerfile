FROM python:3.12-slim

WORKDIR /app

# Deps first (better layer caching)
COPY frontend/requirements.txt ./frontend/requirements.txt
RUN pip install --no-cache-dir -r frontend/requirements.txt

# Package layout preserved so `python -m frontend.main` resolves frontend.* (incl.
# the static/ assets) and shared.observation_bus / shared.models (under /app).
COPY shared/ ./shared/
COPY frontend/ ./frontend/

ENV PYTHONUNBUFFERED=1

# HTTP SERVICE: binds $PORT (Cloud Run injects it; falls back to 8080 locally),
# serves the Race Control console + websocket, and subscribes to both buses.
CMD ["python", "-m", "frontend.main"]
