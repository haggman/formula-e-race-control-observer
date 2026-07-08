FROM python:3.12-slim

WORKDIR /app

# Deps first (better layer caching)
COPY correlator/requirements.txt ./correlator/requirements.txt
RUN pip install --no-cache-dir -r correlator/requirements.txt

# Package layout preserved so `python -m correlator.service` resolves
# correlator.*, observers.video.verifier (+ clock), and shared.* (all under /app).
COPY shared/ ./shared/
COPY observers/ ./observers/
COPY correlator/ ./correlator/

ENV PYTHONUNBUFFERED=1
# The verifier + report narrative use Vertex Gemini (ADC, no API key).
ENV GOOGLE_GENAI_USE_VERTEXAI=1

# Worker Pool: subscribe fe-observations, fuse, verify stops on CCTV, publish
# fe-incidents (+ the verifier's video reads), write incidents/ to Firestore.
CMD ["python", "-m", "correlator.service"]
