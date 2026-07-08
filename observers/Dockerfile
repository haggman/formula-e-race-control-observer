FROM python:3.12-slim

WORKDIR /app

# Deps first (better layer caching)
COPY observers/requirements.txt ./observers/requirements.txt
RUN pip install --no-cache-dir -r observers/requirements.txt

# Package layout preserved so `python -m observers.telemetry.consumer` resolves
# observers.telemetry.detector and shared.* (both under /app). We copy ONLY the
# telemetry subpackage — the video verifier (with its genai/storage deps) runs in
# the correlator, not here, so the observer image stays lean.
COPY shared/ ./shared/
COPY observers/__init__.py ./observers/__init__.py
COPY observers/telemetry/ ./observers/telemetry/

ENV PYTHONUNBUFFERED=1

# Worker Pool: no web server, no $PORT contract — pull fe-telemetry, detect,
# and PUBLISH Observations to fe-observations for the correlator.
CMD ["python", "-m", "observers.telemetry.consumer", "--publish"]
