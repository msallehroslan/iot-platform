# TriAxis Nexus IoT Platform

A production-ready, multi-tenant IoT management platform with built-in AI intelligence.
Connect any device via HTTP, MQTT or WebSocket. Visualise live sensor data, control
actuators via RPC, manage alarms — all from a single dashboard. Full RBAC with tenant isolation.

Deployed as **TriAxis Nexus** in collaboration with **Greenson Technology**.

**Live Demo:**
- Frontend: https://iot-platform-1-hqnz.onrender.com
- API Docs: https://iot-platform-k198.onrender.com/docs

---

## Features

### Device Connectivity
- HTTP REST ingest (any device, any language)
- MQTT (private broker)
- WebSocket real-time bidirectional
- Self-provisioning — device registers on first boot
- Rate limiting: 100 req/min per device token

### Dashboards
- **Device Dashboard** — per-device widget dashboard
- **My Dashboards** — personal multi-device dashboards
- Drag-and-drop layout with live WebSocket push

### 20 Widget Types
| Category | Widgets |
|---|---|
| Data | Value Card, Line Chart, Multi-Axis Chart, Gauge, Bar Chart, Timeseries Table, Pie Chart, Entity Table, Trend Indicator |
| Status | Status Light, Device Summary, Alarm List, Map (GPS + status dot), Fleet Map |
| Control | RPC Button, RPC Toggle, RPC Input |
| Content | Markdown, HTML Card |

### Map Widget
- Real OpenStreetMap tiles — no API key needed
- Custom colored dot: Green=ONLINE, Red=OFFLINE, Yellow=UNKNOWN
- Pulsing ring animation + glow effect
- Set fixed location via device Latitude/Longitude fields
- Or send latitude/longitude as live telemetry for GPS tracking

### Alarm Engine
- Rules for any telemetry key on any device
- Conditions: gt, gte, lt, lte, eq
- Severities: CRITICAL, MAJOR, MINOR, WARNING
- Auto-clear when condition normalises

### RPC Control
```json
{"method": "set", "params": {"led1": true, "relay1": false}}
```
- Any actuator: LED, relay, motor, pump, fan, valve
- ACK system — commands marked COMPLETED after device confirms

---

## TAAT — AI Intelligence Agent

Floating AI assistant — bottom right corner on every page.

### What TAAT Can Do

**Device Control**
- "Turn on led1" / "Turn off led2"
- "Start the pump" / "Stop the motor"
- "Set motor speed to 80"
- Works for any key your device sends

**Alarm Rule Management**
- "Set distance alarm on Temperature above 410 warning"
- "Create critical alarm when temperature exceeds 80"
- "Change the humidity rule to 75"
- "Delete the temperature rule"
- "Delete all rules chain"
- Works for any telemetry key — fully automatic

**Alarm Actions**
- "Acknowledge all alarms"
- "Acknowledge all critical alarms"
- "Clear all warnings"

**User Management** (TENANT_ADMIN only)
- "List all users"
- "Invite john@example.com as admin"
- "Delete john@example.com"
- "Make john@example.com admin"

**Insights**
- "Which device is most critical?"
- "Give me a fleet overview"
- "Why did the humidity alarm fire?"
- Daily fleet health report (Report tab)

### How Rule Management Works (No Hallucination)
Rule operations bypass AI entirely — pure keyword + database logic:
- DELETE: finds rule by key name in DB → deletes it
- UPDATE: finds rule by key name → extracts number from message
- CREATE: extracts key, threshold, severity directly from message
- Works for any sensor key automatically

### Intelligence Features
- **Anomaly Detection** — Z-score on every telemetry ingest
- **Baseline Learning** — per-key hourly patterns, updates nightly
- **Health Scoring** — composite 0-100, updates hourly
- **Root Cause Analysis** — AI analysis button on device dashboard
- **Daily Fleet Report** — executive summary in Report tab
- **Trend Detection** — RISING/FALLING/STABLE/SPIKE/DROP/VOLATILE

---

## Deploy to Render

### 1. PostgreSQL
New + → PostgreSQL → Free → copy Internal Database URL

### 2. Backend (Web Service)
Root: `backend` | Runtime: Docker

| Env var | Value |
|---|---|
| DATABASE_URL | Internal Database URL |
| SECRET_KEY | `python -c "import secrets; print(secrets.token_hex(32))"` |
| CORS_ORIGINS | https://your-frontend.onrender.com |
| GROQ_API_KEY | Free key from console.groq.com |

### 3. Frontend (Static Site)
Root: `frontend` | Build: `npm install && npm run build` | Publish: `dist`

Env: `VITE_API_URL` = https://your-backend.onrender.com

### 4. Run Migrations
```bash
python3 migrations/apply.py
```

---

## Local Development

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=iotdb postgres:15
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend && npm install
npm run dev
```

---

## ESP32 Integration

```cpp
// 1. Provision on first boot
POST /api/v1/devices/provision
{"provision_key":"<key>","device_name":"ESP32-001","device_type":"SENSOR"}
// Returns: {"token":"<device_token>"}

// 2. Send telemetry every 5s
POST /api/v1/telemetry/ingest/<token>
{"values":{"temperature":25.5,"humidity":60,"led1":1,"led2":0}}

// 3. Poll for RPC commands every 3s
GET /api/v1/rpc/pending/<token>

// 4. Handle RPC
void handleRPC(String method, JsonVariant params) {
  JsonObject obj = params.as<JsonObject>(); // MUST cast first
  if (obj.containsKey("led1")) digitalWrite(LED1, obj["led1"].as<bool>());
  if (obj.containsKey("motor_speed")) setSpeed(obj["motor_speed"].as<int>());
}

// 5. ACK after handling
POST /api/v1/rpc/ack/<token>/<cmd_id>
{"result":"ok"}
```

---

## AI Setup (Free)

1. Go to **console.groq.com** — no credit card needed
2. Create API key
3. Add to Render: `GROQ_API_KEY = your_key`
4. Free tier: 14,400 requests/day, llama-3.1-8b-instant

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| DATABASE_URL | YES | Render internal Postgres |
| SECRET_KEY | YES | App refuses to start without it |
| CORS_ORIGINS | YES | Frontend URL, no trailing slash |
| GROQ_API_KEY | Recommended | Enables all AI features |
| GROQ_CHAT_LIMIT | Optional | Default 20 req/user/hour |
| GROQ_MODEL_FAST | Optional | Default llama-3.1-8b-instant |
| REDIS_URL | Optional | Multi-worker WebSocket |

---

## Required Files in frontend/public/

- `taat-robot.png` — TAAT AI robot icon
- `taat-logo-2.png` — TriAxis AI Technologies logo
- `greenson-logo.jpg` — Greenson Technology logo
- `industrial_iot_illustration.png` — Login page illustration

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Map shows No location data | Set Latitude/Longitude in device edit form |
| Device dashboard blank page | DashboardPage.jsx missing intelligenceApi import |
| Chat says 500 error | Check Render logs — likely UnboundLocalError |
| Rule not deleted via chat | Ensure rule key matches exactly what's in DB |
| Duplicate rules created | Say "delete all rules chain" then recreate |
| Migrations stop at 029 | Run manual SQL for 030-032 tables |
