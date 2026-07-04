FROM python:3.12-slim

WORKDIR /app

# Install Python deps first (better layer caching)
COPY state_writer/requirements.txt ./state_writer/requirements.txt
RUN pip install --no-cache-dir -r state_writer/requirements.txt

# Package layout preserved so `python -m state_writer.worker` resolves
# state_writer.writer_core and shared.models (both under /app).
COPY shared/ ./shared/
COPY state_writer/ ./state_writer/

ENV PYTHONUNBUFFERED=1

# Worker Pool: no web server, no $PORT contract — just pull and process.
CMD ["python", "-m", "state_writer.worker"]
