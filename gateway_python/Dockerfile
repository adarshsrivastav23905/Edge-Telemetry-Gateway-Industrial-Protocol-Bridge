# =============================================================================
#  Dockerfile — Edge Telemetry Gateway (Python)
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install security updates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy gateway source code
COPY *.py ./

# Create non-root user
RUN useradd -m -u 1000 gateway && chown -R gateway:gateway /app
USER gateway

# Default entrypoint
ENTRYPOINT ["python", "main.py"]
CMD ["--config", "/app/config/gateway_config.yaml"]
