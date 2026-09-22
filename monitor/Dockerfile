# =============================================================================
#  Dockerfile — MQTT Telemetry Monitor
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY subscriber.py .

RUN useradd -m -u 1000 monitor && chown -R monitor:monitor /app
USER monitor

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "subscriber.py"]
CMD ["--broker", "mosquitto", "--port", "1883", "--topic", "telemetry/#"]
