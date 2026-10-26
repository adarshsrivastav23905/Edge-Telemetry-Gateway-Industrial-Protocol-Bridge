# Edge Telemetry Gateway & Industrial Protocol Bridge

[![CI Tests](https://img.shields.io/badge/tests-25%20passed-brightgreen.svg)](#test-suite)
[![C++17](https://img.shields.io/badge/C%2B%2B-17-blue.svg)](gateway_cpp/)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](gateway_python/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](docker-compose.yml)
[![Protocol](https://img.shields.io/badge/Protocol-Modbus%20TCP%20%E2%86%92%20MQTT-orange.svg)](#protocol-translation)

> An industrial-grade Edge Gateway that polls smart meters and RTUs over **Modbus TCP**, decodes raw binary holding registers into engineering units, runs local threshold evaluation with hysteresis anti-flapping, and publishes structured JSON telemetry to an **MQTT broker** with Last Will and Testament (LWT) and exponential reconnect backoff.
   
---

## 🏭 Industry Context
     
Protocol conversion between field devices and IT/cloud systems is the foundation of substation automation, smart grids, and Industry 4.0. Commercial gateways from vendors such as **Kalkitech (SYNC series)**, **Schneider Electric (EcoStruxure)**, **Siemens (SICAM / Ruggedcom)**, **ABB**, **GE Grid**, and **Moxa** operate on this exact paradigm: translating deterministic fieldbuses (Modbus TCP/RTU, IEC 60870-5-104, DNP3) into asynchronous publish-subscribe telemetry (MQTT, Sparkplug B, AMQP).

---   

## 📐 Architecture Overview

```
                      OPERATIONAL TECHNOLOGY (OT)
                     +---------------------------+
                     |  Modbus TCP Smart Meters  |
                     |  (Port 5020, Slave ID 1)  |
                     +-------------+-------------+
                                   |
                  Modbus TCP (Holding Registers uint16_t)
                                   v
             +-------------------------------------------+
             |       EDGE TELEMETRY GATEWAY CORE         |
             |       (C++17 High Perf / Python)          |
             |                                           |
             |  1. Drift-Free Periodic Poller (500ms)    |
             |  2. Register Decoder (IEEE 754 / Scales)  |
             |  3. Alert Engine (Hysteresis & Debounce)  |
             |  4. Bounded Dispatch Queue (Drop-Oldest)  |
             |  5. Reconnect Backoff with Jitter         |
             +---------------------+---------------------+
                                   |
                         MQTT TCP/TLS (1883)
                                   v
                     +-------------+-------------+
                     |   Eclipse Mosquitto Broker|
                     +-------------+-------------+
                                   |
                       +-----------+-----------+
                       |                       |
                       v                       v
          +------------+----------+  +---------+------------+
          | telemetry/{id}/data   |  | telemetry/{id}/alert |
          | QoS 0 Normal Stream   |  | QoS 1 Alarm Channel  |
          +-----------------------+  +----------------------+
                       INFORMATION TECHNOLOGY (IT)
```

---

## ✨ Core Features

- **Drift-Free Scheduling**: Employs monotonic `steady_clock` absolute deadline sleeping (`sleep_until`) to eliminate cumulative timestamp drift.
- **Dual Gateway Runtimes**:
  - **C++17 Gateway (`gateway_cpp/`)**: Ultra-low latency, bounded memory, zero-heap parsing using `libmodbus` and `paho.mqtt.cpp`.
  - **Python Gateway (`gateway_python/`)**: Modular, clean, readable architecture using `pymodbus` and `paho-mqtt`.
- **Data-Driven Register Map**: Supports `uint16`, signed `int16`, and IEEE 754 `float32` across multiple registers with configurable word orders (**AB** big-endian and **BA** little-endian).
- **Anti-Flapping Alert Engine**:
  - Configurable Warning and Critical thresholds.
  - Hysteresis band prevents alarms from toggling rapidly on noisy signals.
  - Debounce window ensures only sustained deviations trigger alarms.
  - Change-of-Value (CoV) filtering reduces network bandwidth.
- **Bounded Memory Queue & Backpressure Relief**: Drops oldest packets when upstream broker network is congested, ensuring fresh telemetry is never delayed.
- **Resilient Network Reconnect**: Exponential backoff with random jitter prevents thundering herd on network reboots.
- **Docker Compose Stack**: One-command launch of broker, simulator, gateway, and live telemetry monitor.

---

## 🗂️ Repository Structure

```
.
├── config/
│   ├── gateway_config.yaml        # Master YAML configuration (Python gateway)
│   ├── gateway_config.json        # Master JSON configuration (C++ gateway)
│   ├── gateway_config.docker.yaml # Docker Compose YAML configuration
│   ├── gateway_config.docker.json # Docker Compose JSON configuration
│   └── mosquitto.conf             # Mosquitto broker configuration
├── simulator/
│   ├── meter_sim.py               # Modbus TCP slave smart meter simulator
│   ├── requirements.txt           # Simulator dependencies
│   └── Dockerfile                 # Simulator container
├── gateway_python/
│   ├── poller.py                  # Drift-free Modbus register poller
│   ├── decoder.py                 # Register decoding (uint16, int16, float32)
│   ├── alerts.py                  # Alert engine with hysteresis & debounce
│   ├── publisher.py               # Async MQTT publisher & queue dispatcher
│   ├── main.py                    # Gateway orchestrator and signal handler
│   ├── requirements.txt           # Python gateway dependencies
│   └── Dockerfile                 # Gateway container
├── gateway_cpp/
│   ├── CMakeLists.txt             # Modern CMake build system
│   ├── Dockerfile                 # Multi-stage C++ container build
│   └── src/
│       ├── ThreadSafeQueue.hpp    # Bounded producer-consumer queue (drop-oldest)
│       ├── Config.hpp / .cpp      # Configuration loader
│       ├── ModbusClient.hpp / .cpp# libmodbus wrapper
│       ├── Decoder.hpp / .cpp     # Binary register unpacking
│       ├── Poller.hpp / .cpp      # Steady clock periodic thread
│       ├── AlertEngine.hpp / .cpp # Threshold and hysteresis engine
│       ├── MqttPublisher.hpp / .cpp# Paho MQTT C++ async dispatcher
│       └── main.cpp               # Gateway entry point & signal handling
├── monitor/
│   ├── subscriber.py              # Colored CLI real-time telemetry tail
│   ├── requirements.txt           # Monitor dependencies
│   └── Dockerfile                 # Monitor container
├── tests/
│   ├── test_decoder.py            # Unit tests for register decoding
│   ├── test_alerts.py             # Unit tests for alert hysteresis & debounce
│   ├── test_fault_injection.py    # Resilience & backoff tests
│   └── test_integration.py        # End-to-end payload & dispatch tests
├── docs/
│   ├── architecture.md            # Comprehensive system architecture specification
│   ├── project_report.md          # Academic / technical formal report
│   └── interview_prep.md          # 25 Technical interview Q&As & resume bullets
├── docker-compose.yml             # Full-stack Docker orchestration
└── README.md
```

---

## 🚀 Quickstart

### Method 1: Instant Run with Docker Compose (Recommended)

Start the Mosquitto broker, Modbus slave simulator, Python gateway, and live monitor:

```bash
docker compose up --build
```

To run with the high-performance **C++ Gateway**:

```bash
docker compose --profile cpp up --build
```

---

### Method 2: Native Local Execution

#### Step 1: Start the Modbus Slave Simulator
```bash
cd simulator
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python meter_sim.py --port 5020
```

#### Step 2: Start an MQTT Broker
```bash
# Using local mosquitto or Docker
docker run -d --name mosquitto -p 1883:1883 eclipse-mosquitto:2
```

#### Step 3: Run the Python Gateway
```bash
cd gateway_python
pip install -r requirements.txt
python main.py --config ../config/gateway_config.yaml
```

#### Step 4: Run the CLI Monitor
```bash
cd monitor
pip install -r requirements.txt
python subscriber.py --broker localhost --port 1883
```

---

## 🧪 Test Suite

Run the full automated pytest suite (25 unit, integration, and fault injection tests):

```bash
python -m pytest tests/ -v
```

### Test Coverage Highlights:
- **Decoder Tests**: Validates `uint16`, signed `int16`, `float32` across `AB` and `BA` word orders, register boundaries, and scale/offset mathematics.
- **Alert Tests**: Validates debounce sample gating, hysteresis band clear prevention, anti-flapping, and Change-of-Value deadbands.
- **Fault Injection Tests**: Validates truncated exponential backoff formula, jitter bounds, drop-oldest queue overflow during broker failure, and malformed register recovery.
- **Integration Tests**: Validates end-to-end schema compliance and MQTT QoS routing.

---

## 📊 MQTT Topic & QoS Hierarchy

| Topic | QoS | Retain | Purpose |
|---|---|---|---|
| `telemetry/{device_id}/data` | 0 | False | High-throughput periodic measurement stream |
| `telemetry/{device_id}/alert` | 1 | False | Debounced threshold alerts (Warning / Critical) |
| `telemetry/{client_id}/status` | 1 | True | Last Will and Testament (LWT) gateway online/offline status |

---

## 📜 License

Distributed under the Apache 2.0 License. Built for industrial automation and edge computing engineering.
