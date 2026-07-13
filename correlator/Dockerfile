FROM python:3.12-slim

WORKDIR /app

# Deps first (better layer caching)
COPY correlator/requirements.txt ./correlator/requirements.txt
RUN pip install --no-cache-dir -r correlator/requirements.txt

# Package layout preserved so `python -m correlator.service` resolves correlator.*,
# shared.* and the Video Verifier. The verifier now lives in the starter/solution
# packages (loaded via shared/verifier_pkg.py + $VERIFIER_PACKAGE) — a deployed
# container never sources activate.sh, so it falls back to the code default,
# solution.video_verifier. Both trees are copied so either can be selected by env.
COPY shared/ ./shared/
COPY observers/ ./observers/
COPY starter/ ./starter/
COPY solution/ ./solution/
COPY correlator/ ./correlator/

ENV PYTHONUNBUFFERED=1
# The verifier + report narrative use Vertex Gemini (ADC, no API key).
ENV GOOGLE_GENAI_USE_VERTEXAI=1
# Deployed correlator runs the reference verifier unless overridden at deploy time.
ENV VERIFIER_PACKAGE=solution.video_verifier

# Worker Pool: subscribe fe-observations, fuse, verify stops on CCTV, publish
# fe-incidents (+ the verifier's video reads), write incidents/ to Firestore.
CMD ["python", "-m", "correlator.service"]
