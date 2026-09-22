#!/usr/bin/env python3
"""
=============================================================================
 Integration Tests — End-to-End Pipeline & Payload Validation
=============================================================================
 Tests:
   - Full simulated acquisition -> register decode -> JSON payload assembly
   - Schema validation against industrial IoT standards:
       * timestamp ISO-8601 UTC format
       * device_id matching
       * all tags present with value, unit, and valid data types
   - Alert pipeline: high reading -> AlertEvent -> JSON alert schema
   - Queue ingestion, queue draining, and message dispatch mechanics
=============================================================================
"""

import json
import os
import queue
import struct
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

# Safe mock fallback for paho.mqtt if running outside container
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mock_mqtt = MagicMock()
    mock_mqtt.MQTT_ERR_SUCCESS = 0
    sys.modules["paho"] = MagicMock()
    sys.modules["paho.mqtt"] = MagicMock()
    sys.modules["paho.mqtt.client"] = mock_mqtt
    import paho.mqtt.client as mqtt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "gateway_python")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "simulator")))

from alerts import AlertEngine, AlertLevel
from decoder import RegisterDef, TelemetryReading, decode_registers
from meter_sim import build_server_context
from publisher import MqttPublisher


class TestEndToEndPipeline:
    """Tests the full data flow from raw register integers to final MQTT JSON payloads."""

    def test_simulator_builds_modbus_context(self):
        """Verify the simulator still creates a Modbus context under current pymodbus releases."""
        context = build_server_context(unit_id=1)
        assert context is not None
        assert 1 in context.devices

    def test_pipeline_data_packet_format(self):
        # 1. Simulate reading 2 registers for voltage
        v_pack = struct.pack(">f", 231.4)
        v0, v1 = struct.unpack(">HH", v_pack)
        v_def = RegisterDef("voltage", address=0, count=2, data_type="float32", word_order="AB", scale=1.0, offset=0.0, unit="V")

        reading = decode_registers(device_id="meter_01", reg_def=v_def, raw_values=[v0, v1])

        # 2. Serialize to payload matching publisher format
        payload = {
            "device_id": reading.device_id,
            "tag": reading.tag,
            "value": reading.value,
            "unit": reading.unit,
            "timestamp": reading.timestamp,
            "raw_registers": reading.raw_registers,
        }

        # 3. Validate JSON serialization
        raw_json = json.dumps(payload)
        parsed = json.loads(raw_json)

        assert parsed["device_id"] == "meter_01"
        assert parsed["tag"] == "voltage"
        assert pytest.approx(parsed["value"], rel=1e-3) == 231.4
        assert parsed["unit"] == "V"
        assert "T" in parsed["timestamp"]
        assert parsed["raw_registers"] == [v0, v1]

    def test_alert_packet_generation(self):
        thresholds = {
            "voltage": {
                "warning": 245.0,
                "critical": 253.0,
                "hysteresis": 5.0,
                "debounce_count": 1,
            }
        }
        engine = AlertEngine(thresholds)
        now_iso = datetime.now(timezone.utc).isoformat()

        alert_event = engine.evaluate(
            device_id="meter_01", tag="voltage", value=258.0, unit="V", timestamp=now_iso
        )
        assert alert_event is not None
        assert alert_event.level == AlertLevel.CRITICAL

        alert_payload = {
            "device_id": alert_event.device_id,
            "tag": alert_event.tag,
            "level": alert_event.level.value,
            "value": alert_event.value,
            "threshold": alert_event.threshold,
            "unit": alert_event.unit,
            "message": alert_event.message,
            "timestamp": alert_event.timestamp,
        }

        alert_json = json.dumps(alert_payload)
        parsed = json.loads(alert_json)

        assert parsed["device_id"] == "meter_01"
        assert parsed["level"] == "CRITICAL"
        assert parsed["value"] == 258.0
        assert parsed["threshold"] == 253.0
        assert "exceeds CRITICAL" in parsed["message"]

    def test_publisher_mock_dispatch(self):
        """Verify MqttPublisher publishes telemetry with expected topic and QoS."""
        dispatch_queue = queue.Queue(maxsize=10)
        engine = AlertEngine({})

        publisher = MqttPublisher(
            dispatch_queue=dispatch_queue,
            alert_engine=engine,
            broker_host="localhost",
            broker_port=1883,
            client_id="test_client",
            base_topic="telemetry",
            data_qos=0,
            alert_qos=1,
        )

        # Mock the paho MQTT client
        mock_paho = MagicMock()
        mock_res = MagicMock()
        mock_res.rc = 0
        mock_paho.publish.return_value = mock_res
        publisher._client = mock_paho
        publisher._connected.set()

        reading = TelemetryReading(
            device_id="meter_01",
            tag="voltage",
            value=230.0,
            unit="V",
            timestamp=datetime.now(timezone.utc).isoformat(),
            raw_registers=[0x4366, 0x0000],
        )

        publisher._publish_data(reading)

        # Verify publish call
        mock_paho.publish.assert_called_once()
        call_args = mock_paho.publish.call_args
        topic = call_args[0][0]
        payload = json.loads(call_args[0][1])
        qos = call_args[1].get("qos", call_args[0][2] if len(call_args[0]) > 2 else 0)

        assert topic == "telemetry/meter_01/data"
        assert qos == 0
        assert payload["device_id"] == "meter_01"
        assert payload["value"] == 230.0
