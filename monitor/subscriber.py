#!/usr/bin/env python3
"""
=============================================================================
 MQTT Monitor — CLI Telemetry Subscriber
=============================================================================
 Subscribes to telemetry/# and displays incoming messages with colour-coded
 output: green for normal data, yellow for warnings, red for critical alerts.

 Usage:
   python subscriber.py --broker localhost --port 1883
=============================================================================
"""

import argparse
import json
import sys
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# ANSI colour codes for terminal output
# ---------------------------------------------------------------------------
RESET   = "\033[0m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
CYAN    = "\033[96m"
BOLD    = "\033[1m"
DIM     = "\033[2m"


def colorize(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def format_telemetry(topic: str, payload: dict) -> str:
    """Format a telemetry data message."""
    device = payload.get("device_id", "?")
    tag = payload.get("tag", "?")
    value = payload.get("value", "?")
    unit = payload.get("unit", "")
    ts = payload.get("timestamp", "")

    line = (
        f"{DIM}{ts}{RESET}  "
        f"{CYAN}[{device}]{RESET}  "
        f"{GREEN}{tag}={value}{unit}{RESET}"
    )
    return line


def format_alert(topic: str, payload: dict) -> str:
    """Format an alert message with color based on severity."""
    device = payload.get("device_id", "?")
    tag = payload.get("tag", "?")
    level = payload.get("level", "UNKNOWN")
    value = payload.get("value", "?")
    unit = payload.get("unit", "")
    threshold = payload.get("threshold", "?")
    message = payload.get("message", "")
    ts = payload.get("timestamp", "")

    if level == "CRITICAL":
        color = RED
        icon = "🚨"
    elif level == "WARNING":
        color = YELLOW
        icon = "⚠️ "
    else:
        color = GREEN
        icon = "✅"

    line = (
        f"{DIM}{ts}{RESET}  "
        f"{CYAN}[{device}]{RESET}  "
        f"{color}{BOLD}{icon} {level}: {message}{RESET}"
    )
    return line


def format_status(topic: str, payload_str: str) -> str:
    """Format a status (LWT) message."""
    status = payload_str.strip()
    color = GREEN if status == "online" else RED
    icon = "🟢" if status == "online" else "🔴"

    parts = topic.split("/")
    device = parts[1] if len(parts) > 1 else "?"

    line = (
        f"  {CYAN}[{device}]{RESET}  "
        f"{color}{BOLD}{icon} Gateway {status.upper()}{RESET}"
    )
    return line


class TelemetryMonitor:
    def __init__(self, broker: str, port: int, base_topic: str = "telemetry"):
        self.broker = broker
        self.port = port
        self.base_topic = base_topic
        self.msg_count = 0
        self.alert_count = 0

        self.client = mqtt.Client(
            client_id="monitor_cli",
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(colorize(f"\n✅ Connected to MQTT broker {self.broker}:{self.port}", GREEN))
            # Subscribe to all telemetry topics using wildcard
            subscribe_topic = f"{self.base_topic}/#"
            client.subscribe(subscribe_topic)
            print(colorize(f"📡 Subscribed to: {subscribe_topic}\n", CYAN))
            print(f"{'─' * 80}")
        else:
            print(colorize(f"❌ Connection failed: rc={rc}", RED))

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            print(colorize(f"\n⚠️  Disconnected unexpectedly (rc={rc}), reconnecting...", YELLOW))

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode("utf-8", errors="replace")

        try:
            if topic.endswith("/data"):
                payload = json.loads(payload_str)
                print(format_telemetry(topic, payload))
                self.msg_count += 1

            elif topic.endswith("/alert"):
                payload = json.loads(payload_str)
                print(format_alert(topic, payload))
                self.alert_count += 1

            elif topic.endswith("/status"):
                print(format_status(topic, payload_str))

            else:
                # Unknown topic type — print raw
                print(f"  {DIM}[{topic}] {payload_str}{RESET}")

        except json.JSONDecodeError:
            print(f"  {DIM}[{topic}] (non-JSON) {payload_str}{RESET}")

    def run(self):
        print(f"\n{BOLD}{'=' * 60}{RESET}")
        print(f"{BOLD}  Edge Telemetry Monitor — MQTT Subscriber{RESET}")
        print(f"{BOLD}{'=' * 60}{RESET}")
        print(f"  Broker  : {self.broker}:{self.port}")
        print(f"  Topic   : {self.base_topic}/#")
        print(f"  Press Ctrl+C to stop\n")

        try:
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_forever()
        except KeyboardInterrupt:
            print(f"\n\n{'─' * 80}")
            print(f"  Messages received: {self.msg_count}")
            print(f"  Alerts received  : {self.alert_count}")
            print(f"{'─' * 80}")
            print(colorize("  Monitor stopped.", CYAN))
            self.client.disconnect()
        except Exception as e:
            print(colorize(f"\n❌ Error: {e}", RED))
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="MQTT Telemetry Monitor")
    parser.add_argument("--broker", default="localhost", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", default="telemetry", help="Base topic")
    args = parser.parse_args()

    monitor = TelemetryMonitor(args.broker, args.port, args.topic)
    monitor.run()


if __name__ == "__main__":
    main()
