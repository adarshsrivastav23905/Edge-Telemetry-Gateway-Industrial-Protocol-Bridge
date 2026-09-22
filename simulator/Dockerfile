# =============================================================================
#  Dockerfile — Modbus TCP Slave Simulator
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install security updates and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy simulator code
COPY meter_sim.py .

# Create non-root user for security
RUN useradd -m -u 1000 simuser && chown -R simuser:simuser /app
USER simuser

# Expose default Modbus TCP port
EXPOSE 5020

# Run simulator
ENTRYPOINT ["python", "meter_sim.py"]
CMD ["--host", "0.0.0.0", "--port", "5020", "--unit-id", "1"]
