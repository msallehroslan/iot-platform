# IoT Platform

A production-ready, multi-tenant IoT management platform built with FastAPI and React.
Connect physical devices, visualise live sensor data, and manage alarms — all from a
single dashboard. Deployable on Render in under 15 minutes.

---

## Live Demo

| Service | URL |
|---|---|
| Frontend | https://iot-platform-1-hqnz.onrender.com |
| Backend API | https://iot-platform-k198.onrender.com |
| API Docs (Swagger) | https://iot-platform-k198.onrender.com/docs |

Demo credentials: `demo@iotplatform.com` / `demo1234`

---

## Features

### Device Management
- Add, edit, and delete devices
- Auto-generated device tokens for secure telemetry ingestion
- Token regeneration on demand
- Device status tracking (ACTIVE / INACTIVE)

### Telemetry Ingestion
- **HTTP POST** — send data directly from any device or script
- **MQTT publish** — connect any MQTT-capable device
- Both paths share identical processing logic — alarm checks, DB writes, WS broadcast
- Supports any key name (`temperature`, `glucose`, `voltage`, or anything custom)
- Numeric, boolean, string, and JSON value types all supported

### Real-time Dashboards
- **WebSocket push** — widgets update the moment data arrives
- REST fallback polling (5 s) when WebSocket is unavailable
- Automatic reconnect with exponential backoff
- **Drag and drop** widget repositioning
- **Resize** widgets by dragging the corner handle
- Layout positions persist to PostgreSQL — survive page reload

### Two Dashboard Systems
| | Device Dashboard | My Dashboards |
|---|---|---|
| Scope | One device per dashboard | Mix devices on one dashboard |
| Access | Via Device Dashboards menu | Via My Dashboards menu |
| Tabs | Multiple dashboards per device | Sidebar with multiple dashboards |
| Use case | Deep-dive on one device | Cross-device overview |

### 11 Widget Types
| Widget | Description |
|---|---|
| Value Card | Large number display with optional alert threshold colouring |
| Line Chart | Time-series history graph |
| Gauge | Circular dial with configurable min / max |
| Status Light | Green = online, Grey = offline indicator |
| Bar Chart | Compare multiple telemetry keys side by side |
| Pie / Donut | Proportion distribution across multiple keys |
| Alarm List | Active alarms for the bound device |
| History Table | Raw telemetry rows in reverse chronological order |
| Entity Table | All latest key-value pairs for the device |
| Text / Markdown | Free-text notes with basic formatting |
| HTML Card | Custom HTML with `${key}` live value substitution |

### Alarm Engine
Auto-triggered rules on every telemetry ingest:

| Key | Threshold | Severity |
|---|---|---|
| temperature | ≥ 80 | CRITICAL |
| temperature | ≥ 60 | WARNING |
| humidity | ≥ 90 | WARNING |
| voltage | ≥ 250 | CRITICAL |
| voltage | ≥ 230 | WARNING |

Alarms can be acknowledged, cleared, and deleted from the UI.

### Multi-tenant Security
- Every registered account creates an isolated tenant
- All data (devices, telemetry, dashboards, alarms) is scoped to the tenant
- JWT-protected endpoints on every read and write route
- WebSocket connections validated with JWT query parameter
- Cross-tenant access returns 403 Forbidden

### Password Reset
- "Forgot password?" link on the login page
- Enter email + new password — instant reset, no email required

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI 0.111, Python 3.11 |
| Database | PostgreSQL 15 (SQLAlchemy 2.0) |
| Authentication | JWT (python-jose) + bcrypt |
| Real-time | WebSocket (FastAPI native) |
| MQTT | paho-mqtt 1.6.1 |
| Frontend framework | React 18, Vite 5 |
| Styling | Tailwind CSS |
| Drag & drop grid | react-grid-layout |
| Charts | Pure SVG (no external chart library) |
| Hosting | Render (Web Service + PostgreSQL + Static Site) |

---

## Project Structure

```
iot-platform/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── auth_deps.py          # get_current_user / get_current_user_id deps
│   │   │   ├── config.py             # Settings from env vars (pydantic-settings)
│   │   │   ├── database.py           # SQLAlchemy engine + session
│   │   │   ├── security.py           # JWT encode/decode, bcrypt hash/verify
│   │   │   └── websocket_manager.py  # In-memory WS connection registry
│   │   ├── models/
│   │   │   └── models.py             # 11 ORM models
│   │   ├── routers/
│   │   │   ├── auth.py               # /register /login /seed-demo /reset-password
│   │   │   ├── alarms.py             # CRUD + ack/clear — JWT + tenant-scoped
│   │   │   ├── customers.py          # CRUD — JWT + tenant-scoped
│   │   │   ├── dashboard.py          # GET /stats — JWT + tenant-scoped
│   │   │   ├── dashboards.py         # Device dashboards + widgets — JWT + tenant
│   │   │   ├── devices.py            # Device CRUD — JWT + tenant-scoped
│   │   │   ├── telemetry.py          # Ingest (open) + read (JWT + tenant)
│   │   │   ├── user_dashboards.py    # User multi-dashboards — JWT + user
│   │   │   └── ws.py                 # WebSocket endpoint — JWT token param
│   │   ├── services/
│   │   │   ├── dashboard_service.py      # Device dashboard business logic
│   │   │   ├── mqtt_client.py            # paho-mqtt → asyncio bridge
│   │   │   ├── telemetry_service.py      # Shared ingest pipeline (HTTP + MQTT)
│   │   │   └── user_dashboard_service.py # User dashboard business logic
│   │   ├── schemas/
│   │   │   └── schemas.py            # All Pydantic request/response models
│   │   └── main.py                   # FastAPI app, CORS, router registration
│   ├── migrations/
│   │   └── apply.py                  # Idempotent SQL migrations for existing DBs
│   ├── Dockerfile                    # Python 3.11-slim, non-root user, --workers 1
│   ├── render.yaml                   # Render deployment config
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── dashboard/
    │   │   │   └── GridLayout.jsx        # react-grid-layout wrapper
    │   │   ├── sidebar/
    │   │   │   └── DashboardSidebar.jsx  # User dashboard sidebar
    │   │   └── widgets/
    │   │       └── index.jsx             # All 11 widget types + WidgetRenderer
    │   ├── hooks/
    │   │   └── useTelemetry.js           # useDeviceTelemetry hook
    │   ├── pages/
    │   │   ├── DashboardPage.jsx         # Device-scoped dashboard
    │   │   └── UserDashboardPage.jsx     # User multi-dashboard
    │   ├── services/
    │   │   ├── api.js                    # All HTTP API calls + apiFetch
    │   │   ├── dashboardService.js       # Device dashboard helpers
    │   │   ├── userDashboardService.js   # User dashboard helpers
    │   │   ├── websocket.js              # TelemetrySocket singleton
    │   │   └── widgetService.js          # Layout converters + persistLayout
    │   └── App.jsx                       # App shell, routing, all pages
    ├── package.json
    ├── vite.config.js
    └── .env.example
```

---

## Database Schema

```
tenants
  id, name, created_at

users
  id, tenant_id, email, hashed_password, first_name, last_name, role, is_active

customers
  id, tenant_id, name, email, phone, city, country

devices
  id, tenant_id, customer_id, name, device_type, label, description,
  token (unique), status, created_at

telemetry_data           ← time-series, never updated
  id, device_id, key, value_num, value_str, value_bool, value_json, ts

latest_telemetry         ← one row per (device, key), upserted atomically
  id, device_id, key, value_num, value_str, value_bool, value_json, ts
  UNIQUE (device_id, key)

alarms
  id, device_id, alarm_type, severity, status,
  start_ts, end_ts, ack_ts, clear_ts, ack_by, cleared_by

dashboards               ← device-scoped dashboards
  id, device_id, name, description, is_default

widgets                  ← belongs to a device dashboard
  id, dashboard_id, widget_type, title, config (JSON), position (JSON)

user_dashboards          ← user-scoped multi-dashboards
  id, user_id, name, description, is_default

user_widgets             ← belongs to a user dashboard
  id, dashboard_id, widget_type, title, config (JSON), position (JSON)
```

---

## Deploy to Render

### Step 1 — Push to GitHub

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/YOUR_USERNAME/iot-platform.git
git push -u origin main
```

### Step 2 — Create PostgreSQL database

1. Render dashboard → **New + → PostgreSQL**
2. Name: `iot-platform-db` · Plan: Free · Region: Singapore (or closest)
3. Click **Create Database**, wait ~2 min
4. Copy the **Internal Database URL**

### Step 3 — Deploy backend (Web Service)

1. **New + → Web Service** → connect GitHub repo
2. Settings:

| Field | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | Docker |
| Dockerfile Path | `./Dockerfile` |
| Plan | Free |

3. Environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | Paste Internal Database URL |
| `SECRET_KEY` | Click **Generate** |
| `CORS_ORIGINS` | `https://YOUR-FRONTEND.onrender.com` |
| `MQTT_ENABLED` | `true` |
| `MQTT_BROKER_HOST` | `broker.hivemq.com` |
| `MQTT_BROKER_PORT` | `1883` |
| `MQTT_TOPIC_PREFIX` | `iot` |
| `MQTT_KEEPALIVE` | `60` |

4. Click **Create Web Service**, wait for build
5. Test: open `https://YOUR-BACKEND.onrender.com/health` → should return `{"status":"healthy"}`

### Step 4 — Seed demo user

```bash
curl -X POST https://YOUR-BACKEND.onrender.com/api/v1/auth/seed-demo
# Returns: {"email":"demo@iotplatform.com","password":"demo1234"}
```

### Step 5 — Deploy frontend (Static Site)

1. **New + → Static Site** → same GitHub repo
2. Settings:

| Field | Value |
|---|---|
| Root Directory | `frontend` |
| Build Command | `npm install && npm run build` |
| Publish Directory | `dist` |

3. Environment variable:

| Key | Value |
|---|---|
| `VITE_API_URL` | `https://YOUR-BACKEND.onrender.com` |

4. Click **Create Static Site**, wait for build
5. Copy your frontend URL

### Step 6 — Update CORS

1. Backend Web Service → **Environment**
2. Update `CORS_ORIGINS` to your exact frontend URL
3. Save — backend redeploys automatically

### Step 7 — Run database migration (existing DB only)

If upgrading an existing database, run the migration script once to apply any schema changes that `create_all()` cannot handle:

```bash
cd backend
pip install psycopg2-binary

DATABASE_URL="postgresql://iotuser:PASSWORD@HOST/DB" \
  python migrations/apply.py
```

New deployments do not need this — `create_all()` handles everything at startup.

---

## Local Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Start PostgreSQL
docker run -d -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=iotdb \
  postgres:15

# Configure
cp .env.example .env
# Edit DATABASE_URL=postgresql://postgres:postgres@localhost:5432/iotdb

uvicorn app.main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
# .env already has VITE_API_URL=http://localhost:8000
npm run dev
```

Frontend: http://localhost:5173

---

## Sending Telemetry

### HTTP (curl)

```bash
curl -X POST http://localhost:8000/api/v1/telemetry/ingest/YOUR_DEVICE_TOKEN \
  -H "Content-Type: application/json" \
  -d '{"values": {"temperature": 28.5, "humidity": 65, "voltage": 220}}'
```

### Continuous simulation (bash)

```bash
while true; do
  TEMP=$(python3 -c "import random; print(round(random.uniform(20,90),1))")
  curl -s -X POST https://YOUR-BACKEND.onrender.com/api/v1/telemetry/ingest/YOUR_TOKEN \
    -H "Content-Type: application/json" \
    -d "{\"values\": {\"temperature\": $TEMP}}"
  echo "Sent $TEMP"
  sleep 5
done
```

### MQTT (mosquitto)

```bash
# macOS
brew install mosquitto

# Ubuntu
sudo apt install mosquitto-clients

mosquitto_pub \
  -h broker.hivemq.com \
  -t "iot/YOUR_DEVICE_TOKEN/telemetry" \
  -m '{"temperature": 42.0, "humidity": 80}'
```

### ESP32 (Arduino)

```cpp
#include <WiFi.h>
#include <HTTPClient.h>

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"
#define IOT_HOST      "https://YOUR-BACKEND.onrender.com"
#define DEVICE_TOKEN  "your-device-token"

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (!WiFi.isConnected()) delay(500);
  Serial.println("WiFi connected");
}

void sendTelemetry(float value) {
  HTTPClient http;
  String url = String(IOT_HOST) + "/api/v1/telemetry/ingest/" + DEVICE_TOKEN;
  String payload = "{\"values\":{\"temperature\":" + String(value, 1) + "}}";

  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(payload);
  Serial.printf("Sent %.1f → HTTP %d\n", value, code);
  http.end();
}

void loop() {
  sendTelemetry(20.0 + random(700) / 10.0);
  delay(5000);
}
```

### ESP32 via MQTT

```cpp
#include <WiFi.h>
#include <PubSubClient.h>

const char* BROKER = "broker.hivemq.com";
const char* TOKEN  = "your-device-token";

WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);

void setup() {
  WiFi.begin("SSID", "PASSWORD");
  while (!WiFi.isConnected()) delay(500);
  mqtt.setServer(BROKER, 1883);
}

void loop() {
  if (!mqtt.connected()) mqtt.connect("esp32-device");
  mqtt.loop();

  char topic[128], payload[64];
  snprintf(topic,   sizeof(topic),   "iot/%s/telemetry", TOKEN);
  snprintf(payload, sizeof(payload), "{\"temperature\": %.1f}", 20.0 + random(70));
  mqtt.publish(topic, payload);
  delay(5000);
}
```

---

## API Reference

### Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | — | Register new account + tenant |
| POST | `/api/v1/auth/login` | — | Login, returns JWT |
| POST | `/api/v1/auth/seed-demo` | — | Create demo account |
| POST | `/api/v1/auth/reset-password` | — | Reset password by email |

### Devices

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/devices/` | JWT | List tenant devices |
| POST | `/api/v1/devices/` | JWT | Create device |
| GET | `/api/v1/devices/{id}` | JWT | Get device |
| PUT | `/api/v1/devices/{id}` | JWT | Update device |
| DELETE | `/api/v1/devices/{id}` | JWT | Delete device |
| POST | `/api/v1/devices/{id}/token/regenerate` | JWT | Regenerate token |

### Telemetry

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/telemetry/ingest/{token}` | Device token | Ingest telemetry data |
| GET | `/api/v1/telemetry/latest/{device_id}` | JWT | Latest key-value pairs |
| GET | `/api/v1/telemetry/history/{device_id}?key=&limit=` | JWT | Time-series history |
| GET | `/api/v1/telemetry/keys/{device_id}` | JWT | Available telemetry keys |

### Alarms

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/alarms/` | JWT | List alarms (filterable) |
| POST | `/api/v1/alarms/` | JWT | Create alarm manually |
| GET | `/api/v1/alarms/{id}` | JWT | Get alarm |
| POST | `/api/v1/alarms/{id}/ack` | JWT | Acknowledge alarm |
| POST | `/api/v1/alarms/{id}/clear` | JWT | Clear alarm |
| DELETE | `/api/v1/alarms/{id}` | JWT | Delete alarm |

### Device Dashboards

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/dashboards/?device_id=` | JWT | List dashboards for device |
| POST | `/api/v1/dashboards/` | JWT | Create dashboard |
| GET | `/api/v1/dashboards/{id}` | JWT | Get dashboard with widgets |
| PUT | `/api/v1/dashboards/{id}` | JWT | Rename / update dashboard |
| DELETE | `/api/v1/dashboards/{id}` | JWT | Delete dashboard |
| POST | `/api/v1/dashboards/{id}/widgets/` | JWT | Add widget |
| PUT | `/api/v1/dashboards/{id}/widgets/{wid}` | JWT | Update widget |
| DELETE | `/api/v1/dashboards/{id}/widgets/{wid}` | JWT | Delete widget |
| PUT | `/api/v1/dashboards/{id}/layout` | JWT | Save drag-drop layout |

### User Dashboards

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/user-dashboards/` | JWT | List all user dashboards |
| GET | `/api/v1/user-dashboards/default` | JWT | Get default dashboard |
| POST | `/api/v1/user-dashboards/` | JWT | Create dashboard |
| POST | `/api/v1/user-dashboards/{id}/set-default` | JWT | Set as default |
| PUT | `/api/v1/user-dashboards/{id}/rename` | JWT | Rename dashboard |
| DELETE | `/api/v1/user-dashboards/{id}` | JWT | Delete dashboard |
| POST | `/api/v1/user-dashboards/{id}/widgets/` | JWT | Add widget |
| PUT | `/api/v1/user-dashboards/{id}/widgets/{wid}` | JWT | Update widget |
| DELETE | `/api/v1/user-dashboards/{id}/widgets/{wid}` | JWT | Delete widget |
| PUT | `/api/v1/user-dashboards/{id}/layout` | JWT | Save drag-drop layout |
| POST | `/api/v1/user-dashboards/deduplicate` | JWT | Remove duplicate dashboards |

### System

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness probe |
| GET | `/status` | JWT | MQTT + WS connection state |
| GET | `/docs` | — | Swagger UI |
| WS | `/api/v1/ws/telemetry/{device_id}?token=` | JWT token | Live telemetry stream |

---

## Environment Variables

### Backend

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `SECRET_KEY` | ✅ | dev key | JWT signing secret — must be random in production |
| `CORS_ORIGINS` | ✅ | localhost | Comma-separated allowed origins |
| `MQTT_ENABLED` | — | `true` | Set `false` to disable MQTT client |
| `MQTT_BROKER_HOST` | — | `broker.hivemq.com` | MQTT broker hostname |
| `MQTT_BROKER_PORT` | — | `1883` | Broker port (use `8883` with TLS) |
| `MQTT_USE_TLS` | — | `false` | Set `true` for HiveMQ Cloud / AWS IoT |
| `MQTT_USERNAME` | — | — | Broker username (private brokers) |
| `MQTT_PASSWORD` | — | — | Broker password |
| `MQTT_TOPIC_PREFIX` | — | `iot` | Topic root: `{prefix}/{token}/telemetry` |
| `MQTT_KEEPALIVE` | — | `60` | Keepalive seconds |

### Frontend

| Variable | Required | Description |
|---|---|---|
| `VITE_API_URL` | ✅ | Backend base URL e.g. `https://api.onrender.com` |

---

## Security Architecture

```
Registration → creates Tenant + User (tenant_id assigned to user)
Login       → JWT { sub: user_id, tenant_id, role }

Every API request:
  JWT validated → user.tenant_id extracted
  All queries filtered: WHERE tenant_id = user.tenant_id
  Cross-tenant access → 403 Forbidden

WebSocket:
  ?token=<jwt> query parameter required
  Validated before accepting connection
  Cross-device subscription → no access to other tenants' data

Telemetry ingest:
  POST /telemetry/ingest/{device_token}
  No JWT required — device authenticates by token
  device.tenant_id written at ingest time
```

---

## Architecture Notes

### Why --workers 1

The WebSocket `ConnectionManager` and the MQTT paho loop both live in process memory. Running multiple uvicorn workers would create separate registries per worker — a telemetry ingest on worker A would never broadcast to WebSocket clients connected to worker B. The `Dockerfile` CMD enforces `--workers 1`. Scale vertically (larger Render instance) not horizontally.

### Why PostgreSQL atomic upsert

`latest_telemetry` uses `INSERT ... ON CONFLICT DO UPDATE` (PostgreSQL upsert) rather than a read-then-write pattern. This prevents duplicate rows under concurrent HTTP + MQTT ingestion for the same device/key pair, which would cause incorrect widget values.

### MQTT topic format

```
{MQTT_TOPIC_PREFIX}/{device_token}/telemetry

Example:
iot/7989c86b-xxxx-xxxx-xxxx-xxxxxxxxxxxx/telemetry
```

Payload must be a JSON object with a `values` key:
```json
{"values": {"temperature": 28.5, "humidity": 65}}
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Login returns 401 Invalid credentials | Account registered before bcrypt fix deployed | Re-register or reset password |
| Build fails: `Could not resolve ./websocket.js` | Wrong import path in useTelemetry.js | Import from `../services/websocket.js` |
| Dashboard shows `[object Object]` error | FastAPI 422 array detail | Deploy latest code — apiFetch now handles array detail |
| Widgets show no data (line chart empty) | UserDashboardPage not seeding history | Deploy latest code — history now seeded on mount |
| WebSocket shows Static not Live | Free tier cold start | Wait 30 s after first request; upgrades to paid removes cold start |
| 401 after redeploy | Old JWT signed with previous SECRET_KEY | Clear localStorage: `localStorage.clear(); location.reload()` |
| Duplicate Default Dashboards | Race condition on first login | Dashboard deduplication runs automatically on every page load |
| Device dashboard blank page | Device had null tenant_id from before auth fix | Run DB migration: `UPDATE devices SET tenant_id = ... WHERE tenant_id IS NULL` |

---

## Production Checklist

- [ ] `SECRET_KEY` set to a cryptographically random value (Render generates with `generateValue: true`)
- [ ] `CORS_ORIGINS` matches exact frontend URL — no trailing slash
- [ ] `DATABASE_URL` uses Render **Internal** URL (not External)
- [ ] `--workers 1` in Dockerfile CMD (already enforced)
- [ ] For real devices: switch to private MQTT broker with `MQTT_USE_TLS=true` and `MQTT_BROKER_PORT=8883`
- [ ] Run `python migrations/apply.py` if upgrading an existing database
- [ ] Delete or disable the `/auth/seed-demo` endpoint before going to production
