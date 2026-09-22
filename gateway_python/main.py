#!/usr/bin/env python3
"""
=============================================================================
 Edge Telemetry Gateway — Python Main Entry Point
=============================================================================
 Orchestrates the full pipeline:
   1. Loads YAML configuration
   2. Creates the bounded dispatch queue
   3. Initialises the alert engine
   4. Starts the MQTT publisher (dispatcher thread)
   5. Starts the Modbus poller (poller thread)
   6. Handles SIGINT/SIGTERM for graceful shutdown

 Usage:
   python main.py --config ../config/gateway_config.yaml
=============================================================================
"""

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time

import yaml

from alerts import AlertEngine
from decoder import parse_register_map
from poller import ModbusPoller
from publisher import MqttPublisher

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(config: dict):
    """Configure logging based on config settings."""
    level_str = config.get("logging", {}).get("level", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)
    fmt = config.get("logging", {}).get("format", "text")

    if fmt == "structured":
        log_format = '{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    else:
        log_format = "[%(asctime)s] [%(levelname)-5s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


log = logging.getLogger("Gateway")


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
shutdown_event = threading.Event()


def signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM for clean shutdown."""
    sig_name = signal.Signals(signum).name
    log.info("Received %s — initiating graceful shutdown...", sig_name)
    shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Edge Telemetry Gateway — Modbus TCP to MQTT Bridge",
    )
    parser.add_argument(
        "--config", "-c",
        default=os.path.join(os.path.dirname(__file__), "..", "config", "gateway_config.yaml"),
        help="Path to gateway_config.yaml",
    )
    args = parser.parse_args()

    # --- Load configuration ---
    config_path = os.path.abspath(args.config)
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        print(f"[INIT] Loaded configuration from {config_path}")
    except Exception as e:
        print(f"[FATAL] Cannot load config {config_path}: {e}", file=sys.stderr)
        sys.exit(1)

    setup_logging(config)

    log.info("=" * 65)
    log.info("  Edge Telemetry Gateway — Modbus TCP to MQTT Bridge")
    log.info("=" * 65)

    # --- Register signal handlers ---
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # --- Create bounded dispatch queue ---
    queue_cfg = config.get("queue", {})
    max_queue_size = queue_cfg.get("max_size", 1000)
    dispatch_queue = queue.Queue(maxsize=max_queue_size)
    log.info("Dispatch queue: max_size=%d, policy=%s",
             max_queue_size, queue_cfg.get("overflow_policy", "drop_oldest"))

    # --- Initialise alert engine ---
    threshold_config = config.get("thresholds", {})
    alert_engine = AlertEngine(threshold_config)

    # --- Initialise MQTT publisher ---
    mqtt_cfg = config.get("mqtt", {})
    reconnect_cfg = config.get("reconnect", {})
    publisher = MqttPublisher(
        dispatch_queue=dispatch_queue,
        alert_engine=alert_engine,
        broker_host=mqtt_cfg.get("broker_host", "localhost"),
        broker_port=mqtt_cfg.get("broker_port", 1883),
        client_id=mqtt_cfg.get("client_id", "edge_gateway_01"),
        base_topic=mqtt_cfg.get("base_topic", "telemetry"),
        data_qos=mqtt_cfg.get("data_qos", 0),
        alert_qos=mqtt_cfg.get("alert_qos", 1),
        keepalive=mqtt_cfg.get("keepalive_s", 60),
        lwt_topic=mqtt_cfg.get("lwt_topic", ""),
        lwt_payload=mqtt_cfg.get("lwt_payload", "offline"),
        lwt_qos=mqtt_cfg.get("lwt_qos", 1),
        lwt_retain=mqtt_cfg.get("lwt_retain", True),
        initial_backoff_ms=reconnect_cfg.get("initial_delay_ms", 1000),
        max_backoff_ms=reconnect_cfg.get("max_delay_ms", 60000),
        jitter_ms=reconnect_cfg.get("jitter_ms", 500),
    )
    publisher.start()

    # --- Initialise pollers (one per device) ---
    pollers: list[ModbusPoller] = []
    for dev_cfg in config.get("devices", []):
        device_id = dev_cfg["id"]
        reg_defs = parse_register_map(config, device_id)

        poller = ModbusPoller(
            device_id=device_id,
            host=dev_cfg.get("host", "localhost"),
            port=dev_cfg.get("port", 5020),
            unit_id=dev_cfg.get("unit_id", 1),
            poll_interval_ms=dev_cfg.get("poll_interval_ms", 500),
            register_defs=reg_defs,
            dispatch_queue=dispatch_queue,
            timeout_s=dev_cfg.get("timeout_ms", 1000) / 1000.0,
            retries=dev_cfg.get("retries", 3),
            initial_backoff_ms=reconnect_cfg.get("initial_delay_ms", 1000),
            max_backoff_ms=reconnect_cfg.get("max_delay_ms", 60000),
            jitter_ms=reconnect_cfg.get("jitter_ms", 500),
        )
        poller.start()
        pollers.append(poller)

    log.info("Gateway running — %d device(s) being polled", len(pollers))
    log.info("Press Ctrl+C to stop")

    # --- Wait for shutdown signal ---
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)

            # Periodic status log
            total_polls = sum(p.poll_count for p in pollers)
            total_errors = sum(p.poll_errors for p in pollers)
            log.debug(
                "Status: polls=%d errors=%d queue=%d/%d published=%d alerts=%d",
                total_polls, total_errors,
                dispatch_queue.qsize(), max_queue_size,
                publisher.publish_count, publisher.alert_count,
            )
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt caught")
        shutdown_event.set()

    # --- Graceful shutdown ---
    log.info("Shutting down...")

    # Stop pollers first (stop producing)
    for poller in pollers:
        poller.stop()

    # Then stop publisher (finish consuming)
    publisher.stop()

    # Final statistics
    log.info("=" * 65)
    log.info("  FINAL STATISTICS")
    log.info("=" * 65)
    for poller in pollers:
        log.info("  [%s] polls=%d errors=%d reconnects=%d",
                 poller.device_id, poller.poll_count,
                 poller.poll_errors, poller.reconnect_count)
    log.info("  Publisher: published=%d alerts=%d dropped=%d reconnects=%d",
             publisher.publish_count, publisher.alert_count,
             publisher.drop_count, publisher.reconnect_count)
    log.info("=" * 65)
    log.info("Gateway shutdown complete.")


if __name__ == "__main__":
    main()
