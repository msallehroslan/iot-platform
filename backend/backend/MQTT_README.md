# MQTT Telemetry Ingestion

## Overview

The system now accepts telemetry from **two parallel paths**:

```
ESP32/Device ──(HTTP)──► POST /api/v1/telemetry/ingest/{token}  ──►┐
                                                                     ├─► telemetry_service.ingest_telemetry()
ESP32/Device ──(MQTT)──► iot/{token}/telemetry               ──────►┘
                                   │                                      │
                                   └──────────────────────────────────────┤
                                                                           ▼
                                                                     PostgreSQL (save)
                                                                     Alarm rules (check)
                                                                     WebSocket broadcast
                                                                           │
                                                                           ▼
                                                                     Browser widgets update
```

Both paths call the **same service function** — zero logic duplication.

---

## Topic Structure

```
iot/{device_token}/telemetry
```

- `iot` — configurable via `MQTT_TOPIC_PREFIX` env var
- `{device_token}` — the exact token shown in the Devices page
- `telemetry` — fixed suffix

**Examples:**
```
iot/a1b2c3d4-token/telemetry     ← UUID-style token
iot/my-sensor-01/telemetry       ← short custom token
```

---

## Payload Format

```json
{ "temperature": 28.5, "humidity": 70 }
```

Optional timestamp (ISO-8601):
```json
{ "temperature": 28.5, "humidity": 70, "ts": "2025-04-29T10:00:00Z" }
```

- Any JSON keys become telemetry keys
- Numbers, booleans, strings, and nested objects are all accepted
- `ts` is extracted and used as the record timestamp (not stored as a key)

---

## Configuration

| Env var           | Default              | Description                                   |
|-------------------|----------------------|-----------------------------------------------|
| `MQTT_ENABLED`    | `true`               | Set `false` to disable MQTT entirely          |
| `MQTT_BROKER_HOST`| `broker.hivemq.com`  | MQTT broker hostname                          |
| `MQTT_BROKER_PORT`| `1883`               | MQTT broker port                              |
| `MQTT_USERNAME`   | *(empty)*            | Leave empty for anonymous brokers             |
| `MQTT_PASSWORD`   | *(empty)*            | Leave empty for anonymous brokers             |
| `MQTT_TOPIC_PREFIX`| `iot`               | Topic root (`{prefix}/{token}/telemetry`)     |
| `MQTT_CLIENT_ID`  | `iot-platform-{host}`| Must be unique per deployment                 |
| `MQTT_KEEPALIVE`  | `60`                 | Seconds between keepalive pings               |

---

## Test Instructions

### 1. Quick test with mosquitto_pub (command line)

```bash
# Install mosquitto clients (macOS)
brew install mosquitto

# Install mosquitto clients (Ubuntu/Debian)
sudo apt-get install -y mosquitto-clients

# Publish a single telemetry message
# Replace <YOUR_DEVICE_TOKEN> with the token from the Devices page
mosquitto_pub \
  -h broker.hivemq.com \
  -p 1883 \
  -t "iot/<YOUR_DEVICE_TOKEN>/telemetry" \
  -m '{"temperature": 28.5, "humidity": 70}'

# Publish with a custom timestamp
mosquitto_pub \
  -h broker.hivemq.com \
  -t "iot/<YOUR_DEVICE_TOKEN>/telemetry" \
  -m '{"temperature": 35.1, "voltage": 220, "ts": "2025-04-29T10:00:00Z"}'

# Continuous publish every 2 seconds (simulates a sensor)
while true; do
  TEMP=$(python3 -c "import random; print(round(random.uniform(20,35),1))")
  mosquitto_pub \
    -h broker.hivemq.com \
    -t "iot/<YOUR_DEVICE_TOKEN>/telemetry" \
    -m "{\"temperature\": $TEMP, \"humidity\": 65}"
  sleep 2
done
```

### 2. Arduino / ESP32 (PubSubClient)

```cpp
#include <WiFi.h>
#include <PubSubClient.h>

const char* WIFI_SSID     = "your-wifi";
const char* WIFI_PASSWORD = "your-password";
const char* MQTT_BROKER   = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;
const char* DEVICE_TOKEN  = "your-device-token-from-dashboard";

// Topic: iot/{token}/telemetry
char TOPIC[128];

WiFiClient    wifiClient;
PubSubClient  mqttClient(wifiClient);

void setup() {
  Serial.begin(115200);
  snprintf(TOPIC, sizeof(TOPIC), "iot/%s/telemetry", DEVICE_TOKEN);

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(500); }
  Serial.println("WiFi connected");

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
}

void reconnect() {
  while (!mqttClient.connected()) {
    String clientId = "esp32-" + String(random(0xffff), HEX);
    if (mqttClient.connect(clientId.c_str())) {
      Serial.println("MQTT connected");
    } else {
      delay(3000);
    }
  }
}

void loop() {
  if (!mqttClient.connected()) reconnect();
  mqttClient.loop();

  // Read sensor (replace with real sensor reads)
  float temperature = 25.0 + (random(100) / 100.0);
  float humidity    = 60.0 + (random(200) / 100.0);

  // Build JSON payload
  char payload[128];
  snprintf(payload, sizeof(payload),
    "{\"temperature\": %.2f, \"humidity\": %.2f}",
    temperature, humidity
  );

  mqttClient.publish(TOPIC, payload);
  Serial.printf("Published: %s → %s\n", TOPIC, payload);

  delay(2000);  // send every 2 seconds
}
```

### 3. Python test script

```python
import paho.mqtt.publish as publish
import json, time, random

BROKER = "broker.hivemq.com"
TOKEN  = "your-device-token-here"   # from the Devices page
TOPIC  = f"iot/{TOKEN}/telemetry"

for i in range(10):
    payload = {
        "temperature": round(20 + random.random() * 15, 1),
        "humidity":    round(50 + random.random() * 40, 1),
        "voltage":     round(220 + random.random() * 5,  1),
    }
    publish.single(TOPIC, json.dumps(payload), hostname=BROKER)
    print(f"Sent: {payload}")
    time.sleep(1)
```

### 4. Check MQTT status

```bash
# Is the backend MQTT client connected?
curl http://localhost:8000/status

# Response:
# {
#   "status": "ok",
#   "mqtt": {
#     "enabled": true,
#     "connected": true,
#     "broker": "broker.hivemq.com:1883",
#     "client_id": "iot-platform-hostname",
#     "topic": "iot/+/telemetry"
#   },
#   "websocket": { "total_clients": 2, "active_devices": ["uuid1"] }
# }
```

### 5. Verify in the dashboard

1. Open the web UI → **Devices** page
2. Find your device — status should flip to **ACTIVE** on first MQTT message
3. Click the device → **Device Dashboard**
4. Add a `Value Card` widget for `temperature`
5. Publish MQTT messages — the widget updates **instantly** via WebSocket

---

## Architecture Diagram

```
ESP32  ──MQTT──►  broker.hivemq.com  ──MQTT──►  mqtt_client.py (paho, background thread)
                                                        │
                                          on_message callback (paho thread)
                                                        │
                                    asyncio.run_coroutine_threadsafe()
                                                        │
                                                  (event loop)
                                                        ▼
                                          telemetry_service.ingest_telemetry()
                                          ├── device lookup (DB)
                                          ├── save TelemetryData (DB)
                                          ├── upsert LatestTelemetry (DB)
                                          ├── check alarm rules (DB)
                                          └── websocket_manager.broadcast()
                                                        │
                                                        ▼
                                          Browser WebSocket clients
                                          → widget values update in real time
```

---

## Private Broker (Production)

For production, replace HiveMQ public with a private broker:

- **HiveMQ Cloud** (free tier): `{id}.s1.eu.hivemq.cloud:8883` (TLS)
- **EMQX Cloud** (free tier): cloud.emqx.com
- **Mosquitto** (self-hosted): any VPS or Docker container

Set `MQTT_USERNAME` and `MQTT_PASSWORD` in your Render environment variables.
For TLS (port 8883), a future upgrade to paho's `tls_set()` will be needed.
