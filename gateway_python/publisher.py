#!/usr/bin/env python3
"""
=============================================================================
 MQTT Publisher — Asynchronous Dispatch with Reconnect Backoff
=============================================================================
 Drains the dispatch queue on a dedicated thread, serializes telemetry
 readings to JSON, and publishes them to the MQTT broker.

 Topic routing:
   telemetry/{device_id}/data   — normal readings (QoS 0)
   telemetry/{device_id}/alert  — threshold breaches (QoS 1)
   telemetry/{device_id}/status — Last Will and Testament

 Reconnect:
   Paho MQTT has built-in reconnect, but we add our own backoff logic
   with jitter to coordinate with the rest of the gateway lifecycle.
=============================================================================
"""

import json
import logging
import queue
import random
import threading
import time
from typing import Optional

import paho.mqtt.client as mqtt

from alerts import AlertEngine, AlertEvent, AlertLevel
from decoder import TelemetryReading

log = logging.getLogger("Publisher")


class MqttPublisher:
    """
    Consumes TelemetryReading objects from a queue, evaluates alerts,
    serializes to JSON, and publishes over MQTT.
    """

    def __init__(
        self,
        dispatch_queue: queue.Queue,
        alert_engine: AlertEngine,
        broker_host: str = "localhost",
        broker_port: int = 1883,
        client_id: str = "edge_gateway_01",
        base_topic: str = "telemetry",
        data_qos: int = 0,
        alert_qos: int = 1,
        keepalive: int = 60,
        lwt_topic: str = "",
        lwt_payload: str = "offline",
        lwt_qos: int = 1,
        lwt_retain: bool = True,
        initial_backoff_ms: int = 1000,
        max_backoff_ms: int = 60000,
        jitter_ms: int = 500,
    ):
        self.dispatch_queue = dispatch_queue
        self.alert_engine = alert_engine
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.base_topic = base_topic
        self.data_qos = data_qos
        self.alert_qos = alert_qos
        self.initial_backoff_ms = initial_backoff_ms
        self.max_backoff_ms = max_backoff_ms
        self.jitter_ms = jitter_ms

        # MQTT client setup
        self._client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )

        # Last Will and Testament — broker publishes this if we disconnect ungracefully
        if lwt_topic:
            self._client.will_set(lwt_topic, lwt_payload, qos=lwt_qos, retain=lwt_retain)
        else:
            default_lwt = f"{base_topic}/{client_id}/status"
            self._client.will_set(default_lwt, lwt_payload, qos=lwt_qos, retain=lwt_retain)

        # Callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_publish = self._on_publish

        # Auto-reconnect (Paho built-in)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)

        # State
        self._connected = threading.Event()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="MqttDispatcher"
        )

        # Counters
        self.publish_count = 0
        self.alert_count = 0
        self.drop_count = 0
        self.reconnect_count = 0

    # ----- Callbacks -----

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected.set()
            log.info("MQTT connected to %s:%d", self.broker_host, self.broker_port)
            # Publish online status
            status_topic = f"{self.base_topic}/{self._client._client_id.decode() if isinstance(self._client._client_id, bytes) else self._client._client_id}/status"
            self._client.publish(status_topic, "online", qos=1, retain=True)
        else:
            log.error("MQTT connection failed: rc=%d", rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self._connected.clear()
        self.reconnect_count += 1
        if rc != 0:
            log.warning("MQTT disconnected unexpectedly (rc=%d), will reconnect", rc)
        else:
            log.info("MQTT disconnected cleanly")

    def _on_publish(self, client, userdata, mid, rc=None, properties=None):
        log.debug("MQTT message published (mid=%d)", mid)

    # ----- Public API -----

    def start(self):
        """Connect to broker and start the dispatch thread."""
        self._mqtt_connect()
        self._thread.start()
        log.info("Publisher started — dispatching to %s:%d",
                 self.broker_host, self.broker_port)

    def stop(self):
        """Stop dispatching and disconnect."""
        self._stop_event.set()
        self._thread.join(timeout=10.0)
        try:
            # Publish offline status before disconnecting
            status_topic = f"{self.base_topic}/{self._client._client_id.decode() if isinstance(self._client._client_id, bytes) else self._client._client_id}/status"
            self._client.publish(status_topic, "offline", qos=1, retain=True)
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:
            pass
        log.info("Publisher stopped — published=%d alerts=%d dropped=%d reconnects=%d",
                 self.publish_count, self.alert_count,
                 self.drop_count, self.reconnect_count)

    # ----- MQTT Connection -----

    def _mqtt_connect(self):
        """Connect with backoff retry."""
        attempt = 0
        while not self._stop_event.is_set():
            try:
                self._client.connect(
                    self.broker_host,
                    self.broker_port,
                    keepalive=60,
                )
                self._client.loop_start()
                # Wait for connection callback
                if self._connected.wait(timeout=5.0):
                    return
                log.warning("MQTT connect timeout, retrying...")
            except Exception as e:
                log.error("MQTT connect error: %s", e)

            # Backoff
            delay_ms = min(
                self.initial_backoff_ms * (2 ** attempt)
                + random.randint(0, self.jitter_ms),
                self.max_backoff_ms,
            )
            attempt += 1
            log.warning("MQTT backoff %d ms (attempt %d)", delay_ms, attempt)
            self._stop_event.wait(timeout=delay_ms / 1000.0)

    # ----- Dispatch loop -----

    def _dispatch_loop(self):
        """
        Main dispatch loop: drain the queue, evaluate alerts, publish.
        Runs on a dedicated thread.
        """
        log.info("Dispatch loop started")
        while not self._stop_event.is_set():
            try:
                # Block with timeout so we can check stop_event periodically
                reading: TelemetryReading = self.dispatch_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            # --- Evaluate alerts ---
            alert_event = self.alert_engine.evaluate(
                device_id=reading.device_id,
                tag=reading.tag,
                value=reading.value,
                unit=reading.unit,
                timestamp=reading.timestamp,
            )

            # --- Publish alert if state changed ---
            if alert_event is not None:
                self._publish_alert(alert_event)

            # --- Publish normal telemetry (with CoV gating) ---
            if self.alert_engine.has_changed(reading.tag, reading.value):
                self._publish_data(reading)
                self.alert_engine.mark_published(reading.tag, reading.value)

        # --- Drain remaining items on shutdown ---
        log.info("Draining queue on shutdown...")
        drained = 0
        while not self.dispatch_queue.empty():
            try:
                reading = self.dispatch_queue.get_nowait()
                self._publish_data(reading)
                drained += 1
            except queue.Empty:
                break
        log.info("Drained %d readings", drained)

    def _publish_data(self, reading: TelemetryReading):
        """Serialize and publish a normal telemetry reading."""
        topic = f"{self.base_topic}/{reading.device_id}/data"
        payload = json.dumps({
            "device_id": reading.device_id,
            "tag": reading.tag,
            "value": reading.value,
            "unit": reading.unit,
            "timestamp": reading.timestamp,
            "raw_registers": reading.raw_registers,
        })

        if self._connected.is_set():
            result = self._client.publish(topic, payload, qos=self.data_qos)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.publish_count += 1
                log.debug("Published: %s → %s", topic, payload)
            else:
                self.drop_count += 1
                log.warning("Publish failed (rc=%d): %s", result.rc, topic)
        else:
            self.drop_count += 1
            log.warning("Not connected — dropped: %s/%s", reading.device_id, reading.tag)

    def _publish_alert(self, alert: AlertEvent):
        """Serialize and publish an alert event at higher QoS."""
        topic = f"{self.base_topic}/{alert.device_id}/alert"
        payload = json.dumps({
            "device_id": alert.device_id,
            "tag": alert.tag,
            "level": alert.level.value,
            "value": alert.value,
            "threshold": alert.threshold,
            "unit": alert.unit,
            "message": alert.message,
            "timestamp": alert.timestamp,
        })

        if self._connected.is_set():
            result = self._client.publish(topic, payload, qos=self.alert_qos)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.alert_count += 1
                log.info("ALERT published: %s → %s", topic, alert.message)
            else:
                log.error("Alert publish failed (rc=%d): %s", result.rc, topic)
        else:
            log.error("Not connected — alert dropped: %s", alert.message)
