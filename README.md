# TriAxis IoT Platform

A production-ready, multi-tenant IoT management platform built with FastAPI and React.
Connect physical devices, visualise live sensor data, control actuators via RPC, manage
alarms, and analyse trends — all from a single dashboard. Full RBAC with tenant isolation.
Deployable on Render in under 15 minutes.

---

## Live Demo

| Service | URL |
|---|---|
| Frontend | https://iot-platform-1-hqnz.onrender.com |
| Backend API | https://iot-platform-k198.onrender.com |
| API Docs | https://iot-platform-k198.onrender.com/docs |

Demo credentials: `demo@triaxisai.com` / `demo1234`

---

## Features

### Role-Based Access Control (RBAC)

| Role | Who | Access |
|---|---|---|
| `TENANT_ADMIN` | Business owner | Full control |
| `TENANT_USER` | Staff / technician | Read + ack/clear alarms |
| `CUSTOMER_USER` | Client's staff | Scoped to their assigned devices only |

| Action | ADMIN | USER | CUSTOMER |
|---|:---:|:---:|:---:|
| Create / edit / delete devices | ✅ | ❌ | ❌ |
| Add / edit widgets | ✅ | ❌ | ❌ |
| Send RPC commands | ✅ | ❌ | ❌ |
| View telemetry | ✅ | ✅ | ✅ own |
| Acknowledge alarms | ✅ | ✅ | ❌ |
| Manage users | ✅ | ❌ | ❌ |
| API Keys / System Metrics / Audit Log | ✅ | ❌ | ❌ |

---

### Device Management
- Add, edit, delete devices
- Assign devices to customers for scoped access
- Auto-generated device tokens (shown once on create/regenerate)
- Device status: ACTIVE / INACTIVE — auto-updated every 5 minutes
- `last_seen_at` updated on every telemetry ingest
- **Self-provisioning** — ESP32 registers on first boot using a provisioning key

### Telemetry Ingestion
- HTTP POST from any device or script
- MQTT publish from any MQTT-capable device
- Rate limited: 100 requests/minute per device token
- Payload: any JSON key-value pairs (`{"temperature":25.5,"humidity":60}`)
- Supports numeric, boolean, string values
- Auto-purge of data older than `TELEMETRY_RETENTION_DAYS` (default 90)

### Real-time Dashboards
- WebSocket push — widgets update the moment data arrives
- REST fallback polling (5s) when WebSocket unavailable
- Drag-and-drop repositioning, resize by corner handle
- Layout persists to PostgreSQL

### Two Dashboard Systems

| | Device Dashboard | My Dashboards |
|---|---|---|
| Scope | One device per dashboard | Mix any devices |
| Who can edit | TENANT_ADMIN only | All roles |
| Use case | Official device view | Personal cross-device overview |

### 18 Widget Types

| Widget | Category | Description |
|---|---|---|
| Value Card | data | Large number + sparkline + AVG/MIN/MAX |
| Line Chart | data | Time-series with window selector |
| Bar Chart | data | Time-series bars |
| Gauge | data | Circular dial |
| Multi-Axis Chart | data | Multiple keys on one chart |
| Timeseries Table | data | Raw telemetry rows |
| Pie Chart | data | Distribution |
| Entity Table | data | All latest key-values |
| Status Light | status | ONLINE/OFFLINE **or** key ON/OFF |
| Device Summary | status | Status + metrics grid |
| Alarm List | status | Active alarms |
| Map | status | GPS coordinates → OpenStreetMap |
| RPC Button | control | Admin — one-shot command |
| RPC Toggle | control | Admin — ON/OFF with state monitoring |
| RPC Input | control | Admin — numeric/text value override |
| Markdown | content | Free text notes |
| HTML Card | content | Custom HTML with `${key}` substitution |

### Status Light Widget
Two modes selectable in config:
- **No key** → shows device ONLINE / OFFLINE based on last heartbeat
- **Key selected** (e.g. `led1`) → shows ON (green) / OFF (grey) based on telemetry value

### RPC Toggle Widget
Industry-standard `set` method pattern:
```json
{"method": "set", "params": {"led1": true}}
```
- **Monitor Key** — reads telemetry to show current ON/OFF state
- **Control Key** — param key sent in RPC command (defaults to same)
- Works for any actuator: LED, relay, motor, pump, fan
- Backward compatible with legacy `method_on`/`method_off` configs
- Shows "Waiting for data" (amber) until first telemetry arrives

### Alarm Engine
- Rules stored in `threshold_rules` DB table
- Conditions: `gt` `gte` `lt` `lte` `eq`
- Severities: `CRITICAL` `MAJOR` `MINOR` `WARNING` `INDETERMINATE`
- Device-specific rules override tenant-wide rules
- Auto-clear when condition no longer met
- Full lifecycle: ACTIVE_UNACK → ACTIVE_ACK → CLEARED_UNACK → CLEARED_ACK

### Audit Log
Every admin action is recorded with timestamp, user, and details:
- User management, device CRUD, alarm ack/clear, RPC commands
- API key creation/revocation, dashboard/widget changes
- Viewable in **Audit Log** sidebar page (admin only)
- 29 distinct action types

### API Keys
- Long-lived keys for server-to-server integrations
- Raw key shown once on creation, stored as SHA-256 hash
- Prefix shown for identification
- Optional expiry date
- Revocable at any time

### System Metrics
Live infrastructure health (admin only):
- Process CPU + memory, host CPU + memory
- Database pool size, checked-out connections, latency
- WebSocket connected clients, active devices
- Redis connection status
- Tenant ingest rate (events/min)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI 0.111, Python 3.11 |
| Database | PostgreSQL 15, SQLAlchemy 2.0 |
| Authentication | JWT (python-jose) + bcrypt + refresh tokens |
| Real-time | WebSocket (FastAPI native) + optional Redis pub/sub |
| MQTT | paho-mqtt 1.6.1 |
| Frontend | React 18, Vite 5, Tailwind CSS |
| Charts | Pure SVG (no external library) |
| Grid | react-grid-layout |
| Hosting | Render (Web Service + PostgreSQL + Static Site) |

---

## Deploy to Render

### Step 1 — Push to GitHub
```bash
git init && git add . && git commit -m "initial"
git remote add origin https://github.com/YOUR_USERNAME/iot-platform.git
git push -u origin main
```

### Step 2 — Create PostgreSQL
Render dashboard → **New + → PostgreSQL** → Free → copy **Internal Database URL**

### Step 3 — Deploy Backend (Web Service)
**New + → Web Service** → Root Directory: `backend` → Runtime: Docker

Environment variables:
| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL from Step 2 |
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ORIGINS` | `https://YOUR-FRONTEND.onrender.com` |

### Step 4 — Deploy Frontend (Static Site)
**New + → Static Site** → Root Directory: `frontend`
- Build: `npm install && npm run build`
- Publish: `dist`
- Env: `VITE_API_URL` = `https://YOUR-BACKEND.onrender.com`

### Step 5 — Run Migrations
In Render Shell (backend service → Shell tab):
```bash
python migrations/apply.py
```

### Step 6 — Seed Demo (optional)
```bash
curl -X POST https://YOUR-BACKEND.onrender.com/api/v1/auth/seed-demo
```

---

## Local Development

### Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=iotdb postgres:15
cp .env.example .env
# Set DATABASE_URL and SECRET_KEY in .env
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend && npm install
# Set VITE_API_URL=http://localhost:8000 in .env
npm run dev
```

---

## ESP32 Integration

### Self-Provisioning (First Boot)
```cpp
#define IOT_HOST      "https://YOUR-BACKEND.onrender.com"
#define PROVISION_KEY "your-provisioning-key"   // from Settings page

void provisionDevice() {
  HTTPClient http;
  http.begin(String(IOT_HOST) + "/api/v1/devices/provision");
  http.addHeader("Content-Type", "application/json");
  String payload = "{\"provision_key\":\"" + String(PROVISION_KEY) +
                   "\",\"device_name\":\"ESP32-001\",\"device_type\":\"SENSOR\"}";
  int code = http.POST(payload);
  // parse token from response, save to Preferences
}
```

### Telemetry Ingest
```cpp
// POST every 5 seconds — include all sensor values + actuator states
String payload = "{\"values\":{\"temperature\":25.5,\"humidity\":60,\"led1\":1,\"led2\":0}}";
http.POST(payload);
```

### RPC Control (Standard Set Method)
```cpp
// Poll every 3 seconds
GET /api/v1/rpc/pending/<device_token>
// Returns: [{"id":"...","method":"set","params":{"led1":true}}]

void handleRPC(String cmdId, String method, JsonVariant params) {
  if (method == "set") {
    JsonObject obj = params.as<JsonObject>();  // MUST cast from JsonVariant
    if (obj.containsKey("led1")) setLed1(obj["led1"].as<bool>());
    if (obj.containsKey("led2")) setLed2(obj["led2"].as<bool>());
    // add more keys here for new actuators
    ackRPC(cmdId, true);
  }
}

// After handling — MUST ACK or command stays SENT forever
POST /api/v1/rpc/ack/<device_token>/<cmd_id>
Body: {"result":"ok"}
```

### Dashboard Widget Setup for LED Control
| Widget | Type | Monitor Key | Control Key | Label |
|---|---|---|---|---|
| LED 1 | RPC Toggle | `led1` | `led1` | LED 1 |
| LED 2 | RPC Toggle | `led2` | `led2` | LED 2 |
| LED 1 Status | Status Light | `led1` | — | LED 1 State |

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | ✅ | Render internal Postgres URL |
| `SECRET_KEY` | ✅ | No default — app refuses to start without it |
| `CORS_ORIGINS` | ✅ | Frontend URL, no trailing slash |
| `REDIS_URL` | optional | Multi-worker WebSocket scaling |
| `MQTT_BROKER_HOST` | optional | Private broker only (public blocked) |
| `MQTT_BROKER_PORT` | optional | 8883 for TLS |
| `MQTT_USE_TLS` | optional | `true` for secure brokers |
| `MQTT_USERNAME` | optional | Broker credentials |
| `MQTT_PASSWORD` | optional | Broker credentials |
| `TELEMETRY_RETENTION_DAYS` | optional | Default 90 |
| `DEFAULT_MAX_DEVICES` | optional | Default 100 per tenant |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| App won't start | SECRET_KEY not set | Add to Render environment variables |
| RPC Toggle sends `turnOn` not `set` | Legacy method_on/method_off in widget config | Run DB fix: pop method_on/method_off from widget configs |
| ESP32 RPC ignored silently | `params.containsKey()` on JsonVariant | Cast first: `params.as<JsonObject>().containsKey(...)` |
| Add Widget shows blank page | availableKeys undefined | Update UserDashboardPage.jsx |
| audit_logs NULL id error | Missing gen_random_uuid() default | Run `python migrations/apply.py` (migration 027) |
| Migrations show 2/27 | Old apply.py deployed | Push latest apply.py with if __name__ at end |
| WebSocket shows "Static" | JWT expired | Log out → log in again |
| Customer user sees no devices | Device not assigned | Devices → Edit → Assign to Customer |
