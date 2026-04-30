FROM python:3.11-slim

# Create non-root user (Render best practice)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Switch to non-root user
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# IMPORTANT: --workers 1 is intentional.
# The WebSocket ConnectionManager and the MQTT paho loop both live in
# process memory. Running multiple workers means each worker has its own
# registry, so a telemetry ingest on worker A would never broadcast to
# WebSocket clients connected to worker B. Single-worker is correct for
# this architecture. Scale vertically (bigger instance) not horizontally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
