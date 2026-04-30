# IoT Platform

A production-ready, multi-tenant IoT management platform built with FastAPI and React.
Connect physical devices, visualise live sensor data, manage alarms, and analyse trends —
all from a single dashboard. Deployable on Render in under 15 minutes.

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
- **Device Provisioning** — ESP32 / firmware can self-register on first boot using a provisioning key, no user JWT required

### Telemetry Ingestion
- **HTTP POST** — send data from any device or script
- **MQTT publish** — connect any MQTT-capable device
- Both paths share identical processing: alarm checks, DB writes, WebSocket broadcast
- Supports any key name (`glucose`, `temperature`, `voltage`, or anything custom)
- Numeric, boolean, string, and JSON value types all supported

### Telemetry Metadata
- Auto-created when a new key is ingested for the first time
- Stores `label`, `unit`, and `data_type` per key per device
- Updatable via API (e.g. set label = "Blood Glucose", unit = "mg/dL")

### Telemetry Aggregation
- Time-windowed aggregation over any numeric key
- Windows: 1m, 5m, 15m, 30m, 1h, 6h, 12h, 24h, 7d
- Functions: avg, min, max, sum, count
- Computed entirely in PostgreSQL — no data loaded into memory

### Real-time Dashboards
- **WebSocket push** — widgets update the moment data arrives
- REST fallback polling (5s) when WebSocket is unavailable
- Auto-reconnect with exponential backoff
- **Drag and drop** widget repositioning
- **Resize** widgets by dragging the corner handle
- Layout positions persist to PostgreSQL — survive page reload

### Two Dashboard Systems
| | Device Dashboard | My Dashboards |
|---|---|---|
| Scope | One device per dashboard | Mix any devices on one dashboard |
| Access | Via Device Dashboards menu | Via My Dashboards sidebar |
| Tabs | Multiple dashboards per device | Multiple dashboards per user |
| Use case | Deep-dive on one device | Cross-device overview |

### 11 Widget Types
| Widget | Description |
|---|---|
| Value Card | Large number with optional alert threshold colouring and sparkline |
| Line Chart | Time-series history graph |
| Bar Chart | Time-series bars over time (same data as line chart, different visual) |
| Gauge | Circular dial with configurable min / max |
| Status Light | Green = online, Grey = offline indicator |
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
- All data scoped by tenant — devices, telemetry, dashboards, alarms
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
│   │   │   ├── auth_deps.py          # get_current_user / get_current_user_id
│   │   │   ├── config.py             # Settings from env vars (pydantic-settings)
│   │   │   ├── database.py           # SQLAlchemy engine + session
│   │   │   ├── security.py           # JWT encode/decode, bcrypt hash/verify
│   │   │   └── websocket_manager.py  # In-memory WS connection registry (single worker)
│   │   ├── models/
│   │   │   └── models.py             # 12 ORM models
│   │   ├── routers/
│   │   │   ├── auth.py               # /register /login /seed-demo /reset-password
│   │   │   ├── alarms.py             # CRUD + ack/clear — JWT + tenant-scoped
│   │   │   ├── customers.py          # CRUD — JWT + tenant-scoped
│   │   │   ├── dashboard.py          # GET /stats — JWT + tenant-scoped
│   │   │   ├── dashboards.py         # Device dashboards + widgets — JWT + tenant
│   │   │   ├── devices.py            # CRUD + provisioning — JWT + tenant
│   │   │   ├── telemetry.py          # Ingest (open) + read + metadata + aggregate
│   │   │   ├── user_dashboards.py    # User multi-dashboards — JWT + user
│   │   │   └── ws.py                 # WebSocket endpoint
│   │   ├── services/
│   │   │   ├── dashboard_service.py
│   │   │   ├── mqtt_client.py        # paho-mqtt → asyncio bridge
│   │   │   ├── telemetry_service.py  # Shared ingest pipeline (HTTP + MQTT)
│   │   │   └── user_dashboard_service.py
│   │   ├── schemas/
│   │   │   └── schemas.py            # All Pydantic request/response models
│   │   └── main.py                   # FastAPI app, CORS, router registration
│   ├── migrations/
│   │   └── apply.py                  # Idempotent migrations 001–007
│   ├── Dockerfile                    # Python 3.11-slim, non-root, --workers 1
│   ├── render.yaml
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── dashboard/GridLayout.jsx
    │   │   ├── sidebar/DashboardSidebar.jsx
    │   │   └── widgets/index.jsx      # All 11 widget types + WidgetRenderer
    │   ├── hooks/
    │   │   └── useTelemetry.js
    │   ├── pages/
    │   │   ├── DashboardPage.jsx      # Device-scoped dashboard
    │   │   └── UserDashboardPage.jsx  # User multi-dashboard
    │   ├── services/
    │   │   ├── api.js                 # All HTTP API calls
    │   │   ├── dashboardService.js
    │   │   ├── userDashboardService.js
    │   │   ├── websocket.js           # TelemetrySocket singleton
    │   │   └── widgetService.js       # Layout converters + persistLayout
    │   └── App.jsx
    ├── package.json
    └── vite.config.js
```

---

## Database Schema

```
tenants
  id, name, provisioning_key (unique), created_at

users
  id, tenant_id, email, hashed_password, first_name, last_name, role, is_active

customers
  id, tenant_id, name, email, phone, city, country

devices
  id, tenant_id, customer_id, name, device_type, label, description,
  token (unique), status, created_at

telemetry_data              ← append-only time-series
  id, device_id, key, value_num, value_str, value_bool, value_json, ts

latest_telemetry            ← one row per (device, key), atomic upsert
  id, device_id, key, value_num, value_str, value_bool, value_json, ts
  UNIQUE (device_id, key)

telemetry_keys              ← metadata per (device, key)
  id, device_id, key, label, unit, data_type, created_at, updated_at
  UNIQUE (device_id, key)

alarms
  id, device_id, alarm_type, severity, status,
  start_ts, end_ts, ack_ts, clear_ts, ack_by, cleared_by

dashboards                  ← device-scoped
  id, device_id, name, description, is_default

widgets
  id, dashboard_id, widget_type, title, config (JSON), position (JSON)

user_dashboards             ← user-scoped multi-dashboards
  id, user_id, name, description, is_default

user_widgets
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
2. Name: `iot-platform-db` · Plan: Free · Region: Singapore
3. Wait ~2 min → copy **Internal Database URL**

### Step 3 — Deploy backend (Web Service)

1. **New + → Web Service** → connect repo
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

4. Create → wait for build → test:
```bash
curl https://YOUR-BACKEND.onrender.com/health
# {"status":"healthy"}
```

5. Seed demo user:
```bash
curl -X POST https://YOUR-BACKEND.onrender.com/api/v1/auth/seed-demo
```

### Step 4 — Deploy frontend (Static Site)

1. **New + → Static Site** → same repo
2. Settings:

| Field | Value |
|---|---|
| Root Directory | `frontend` |
| Build Command | `npm install && npm run build` |
| Publish Directory | `dist` |
| `VITE_API_URL` (env var) | `https://YOUR-BACKEND.onrender.com` |

### Step 5 — Update CORS

Backend → Environment → set `CORS_ORIGINS` to your exact frontend URL → Save.

### Step 6 — Run database migrations

In DBeaver or Render PSQL, run each block separately:

```sql
-- 004: provisioning key column
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS provisioning_key VARCHAR(64) UNIQUE;

-- 005: backfill provisioning keys
UPDATE tenants SET provisioning_key = REPLACE(gen_random_uuid()::text, '-', '')
WHERE provisioning_key IS NULL;

-- 006: telemetry metadata table
CREATE TABLE IF NOT EXISTS telemetry_keys (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id  UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    key        VARCHAR(255) NOT NULL,
    label      VARCHAR(255),
    unit       VARCHAR(50),
    data_type  VARCHAR(20) NOT NULL DEFAULT 'number',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ,
    CONSTRAINT uq_telemetry_keys_device_key UNIQUE (device_id, key)
);
CREATE INDEX IF NOT EXISTS ix_telemetry_keys_device_id ON telemetry_keys (device_id);

-- 007: backfill metadata from existing telemetry
INSERT INTO telemetry_keys (id, device_id, key, data_type)
SELECT gen_random_uuid(), device_id, key,
    CASE WHEN value_num IS NOT NULL THEN 'number'
         WHEN value_bool IS NOT NULL THEN 'boolean'
         ELSE 'string' END
FROM latest_telemetry
ON CONFLICT (device_id, key) DO NOTHING;
```

---

## Local Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

docker run -d -p 5432:5432 \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=iotdb \
  postgres:15

cp .env.example .env
# Edit DATABASE_URL=postgresql://postgres:postgres@localhost:5432/iotdb

uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
# VITE_API_URL=http://localhost:8000
npm run dev
```

Frontend: http://localhost:5173

---

## Device Provisioning

Devices can self-register without a user JWT using the provisioning key from **Settings → Device Provisioning**.

### ESP32 Arduino Example

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>

#define WIFI_SSID      "your-wifi"
#define WIFI_PASSWORD  "your-password"
#define IOT_HOST       "https://YOUR-BACKEND.onrender.com"
#define PROVISION_KEY  "your-provisioning-key"   // from Settings page

Preferences prefs;
String deviceToken = "";

String extractToken(String response) {
  int start = response.indexOf("\"token\":\"") + 9;
  int end   = response.indexOf("\"", start);
  return response.substring(start, end);
}

void provisionDevice() {
  HTTPClient http;
  http.begin(String(IOT_HOST) + "/api/v1/devices/provision");
  http.addHeader("Content-Type", "application/json");

  uint64_t chipid    = ESP.getEfuseMac();
  String   devName   = "ESP32-" + String((uint32_t)(chipid >> 32), HEX);
  String   payload   = "{\"provision_key\":\"" + String(PROVISION_KEY) +
                       "\",\"device_name\":\""  + devName +
                       "\",\"device_type\":\"SENSOR\"}";

  int code = http.POST(payload);
  if (code == 200 || code == 201) {          // 201=new device, 200=existing
    deviceToken = extractToken(http.getString());
    prefs.begin("iot", false);
    prefs.putString("token", deviceToken);
    prefs.end();
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (!WiFi.isConnected()) delay(500);

  prefs.begin("iot", true);
  deviceToken = prefs.getString("token", "");
  prefs.end();

  if (deviceToken == "") provisionDevice();
}

void loop() {
  if (deviceToken == "") return;

  String payload = "{\"values\":{\"temperature\":25.5}}";
  HTTPClient http;
  http.begin(String(IOT_HOST) + "/api/v1/telemetry/ingest/" + deviceToken);
  http.addHeader("Content-Type", "application/json");
  http.POST(payload);
  http.end();
  delay(5000);
}
```

---

## Sending Telemetry

### HTTP curl

```bash
curl -X POST https://YOUR-BACKEND.onrender.com/api/v1/telemetry/ingest/DEVICE_TOKEN \
  -H "Content-Type: application/json" \
  -d '{"values": {"glucose": 87, "temperature": 36.5}}'
```

### MQTT

```bash
mosquitto_pub \
  -h broker.hivemq.com \
  -t "iot/DEVICE_TOKEN/telemetry" \
  -m '{"glucose": 87}'
```

---

## API Reference

### Authentication

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | — | Register + create tenant |
| POST | `/api/v1/auth/login` | — | Login, returns JWT |
| POST | `/api/v1/auth/seed-demo` | — | Create demo account |
| POST | `/api/v1/auth/reset-password` | — | Reset password by email |

### Devices

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/devices/` | JWT | List tenant devices |
| POST | `/api/v1/devices/` | JWT | Create device |
| GET | `/api/v1/devices/provisioning-key` | JWT | Get tenant provisioning key |
| POST | `/api/v1/devices/provision` | — | Self-register device (firmware) |
| GET | `/api/v1/devices/{id}` | JWT | Get device |
| PUT | `/api/v1/devices/{id}` | JWT | Update device |
| DELETE | `/api/v1/devices/{id}` | JWT | Delete device |
| POST | `/api/v1/devices/{id}/token/regenerate` | JWT | Regenerate token |

### Telemetry

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/telemetry/ingest/{token}` | Device token | Ingest data |
| GET | `/api/v1/telemetry/latest/{device_id}` | JWT | Latest key-value pairs |
| GET | `/api/v1/telemetry/history/{device_id}?key=&limit=` | JWT | Time-series history |
| GET | `/api/v1/telemetry/keys/{device_id}` | JWT | List available keys |
| GET | `/api/v1/telemetry/metadata/{device_id}` | JWT | Key metadata (label, unit) |
| PUT | `/api/v1/telemetry/metadata/{device_id}/{key}` | JWT | Update key metadata |
| GET | `/api/v1/telemetry/aggregate/{device_id}?key=&window=&function=` | JWT | Time-windowed aggregation |

### Aggregation Parameters

| Param | Values | Default |
|---|---|---|
| `key` | any telemetry key | required |
| `window` | `1m` `5m` `15m` `30m` `1h` `6h` `12h` `24h` `7d` | `1h` |
| `function` | `avg` `min` `max` `sum` `count` | `avg` |

### Alarms

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/alarms/` | JWT | List alarms |
| POST | `/api/v1/alarms/` | JWT | Create alarm |
| POST | `/api/v1/alarms/{id}/ack` | JWT | Acknowledge |
| POST | `/api/v1/alarms/{id}/clear` | JWT | Clear |
| DELETE | `/api/v1/alarms/{id}` | JWT | Delete |

### Device Dashboards

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/dashboards/?device_id=` | JWT | List dashboards |
| POST | `/api/v1/dashboards/` | JWT | Create dashboard |
| GET | `/api/v1/dashboards/{id}` | JWT | Get with widgets |
| PUT | `/api/v1/dashboards/{id}` | JWT | Update |
| DELETE | `/api/v1/dashboards/{id}` | JWT | Delete |
| POST | `/api/v1/dashboards/{id}/widgets/` | JWT | Add widget |
| PUT | `/api/v1/dashboards/{id}/widgets/{wid}` | JWT | Update widget |
| DELETE | `/api/v1/dashboards/{id}/widgets/{wid}` | JWT | Delete widget |
| PUT | `/api/v1/dashboards/{id}/layout` | JWT | Save layout |

### User Dashboards

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/user-dashboards/` | JWT | List all |
| GET | `/api/v1/user-dashboards/default` | JWT | Get default |
| POST | `/api/v1/user-dashboards/` | JWT | Create |
| DELETE | `/api/v1/user-dashboards/{id}` | JWT | Delete |
| POST | `/api/v1/user-dashboards/{id}/widgets/` | JWT | Add widget |
| PUT | `/api/v1/user-dashboards/{id}/widgets/{wid}` | JWT | Update widget |
| DELETE | `/api/v1/user-dashboards/{id}/widgets/{wid}` | JWT | Delete widget |
| PUT | `/api/v1/user-dashboards/{id}/layout` | JWT | Save layout |

### System

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness probe |
| GET | `/status` | JWT | MQTT + WS state |
| GET | `/docs` | — | Swagger UI |
| WS | `/api/v1/ws/telemetry/{device_id}?token=` | JWT | Live stream |

---

## Environment Variables

### Backend

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `SECRET_KEY` | ✅ | dev key | JWT signing secret |
| `CORS_ORIGINS` | ✅ | localhost | Comma-separated allowed origins |
| `MQTT_ENABLED` | — | `true` | Set `false` to disable MQTT |
| `MQTT_BROKER_HOST` | — | `broker.hivemq.com` | Broker hostname |
| `MQTT_BROKER_PORT` | — | `1883` | Broker port (`8883` for TLS) |
| `MQTT_USE_TLS` | — | `false` | `true` for HiveMQ Cloud / AWS IoT |
| `MQTT_USERNAME` | — | — | Broker username |
| `MQTT_PASSWORD` | — | — | Broker password |
| `MQTT_TOPIC_PREFIX` | — | `iot` | Topic root: `{prefix}/{token}/telemetry` |

### Frontend

| Variable | Required | Description |
|---|---|---|
| `VITE_API_URL` | ✅ | Backend base URL |

---

## Security Architecture

```
Registration  →  creates Tenant (with provisioning_key) + User
Login         →  JWT { sub: user_id, tenant_id, role }

Every API request:
  JWT validated → user.tenant_id extracted
  All queries:  WHERE tenant_id = user.tenant_id
  Cross-tenant: → 403 Forbidden

WebSocket:
  ?token=<jwt> required before connection accepted

Telemetry ingest (device → platform):
  POST /telemetry/ingest/{device_token}   ← no JWT, device token only

Device provisioning (firmware → platform):
  POST /devices/provision { provision_key, device_name }
  provision_key maps to exactly one tenant → device created there
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Provisioning key empty in Settings | Column not in DB | Run migration 004 + 005 in DBeaver |
| `❌ Provision failed HTTP 201` | ESP32 code only checks `== 200` | Change to `code == 200 \|\| code == 201` |
| Login returns 401 | Account registered before bcrypt fix | Re-register or use reset-password |
| Build fails: `Could not resolve ./websocket.js` | Wrong import path | Import from `../services/websocket.js` |
| Bar chart blank | liveTelem empty on first render | Shows "Waiting for data…" — updates when first telemetry arrives |
| Duplicate Default Dashboards | Race condition on first login | Auto-deduplicated on every page load |
| 401 after redeploy | JWT signed with old SECRET_KEY | `localStorage.clear(); location.reload()` |
| WebSocket shows Static | Free tier cold start (30s) | Upgrade to paid Render instance |

---

## Production Checklist

- [ ] `SECRET_KEY` is a strong random value (Render `generateValue: true`)
- [ ] `CORS_ORIGINS` matches exact frontend URL — no trailing slash
- [ ] `DATABASE_URL` uses Render **Internal** URL
- [ ] All 7 database migrations applied
- [ ] For real devices: switch to private MQTT broker with TLS (`MQTT_USE_TLS=true`, port `8883`)
- [ ] Disable or restrict `/api/v1/auth/seed-demo` before production
- [ ] `--workers 1` in Dockerfile CMD (already enforced — required for WebSocket)
