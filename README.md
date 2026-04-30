# TriAxis IoT Platform

A production-ready, multi-tenant IoT management platform built with FastAPI and React.
Connect physical devices, visualise live sensor data, manage alarms, and analyse trends —
all from a single dashboard. Full RBAC with tenant isolation. Deployable on Render in under 15 minutes.

---

## Live Demo

| Service | URL |
|---|---|
| Frontend | https://iot-platform-1-hqnz.onrender.com |
| Backend API | https://iot-platform-k198.onrender.com |
| API Docs (Swagger) | https://iot-platform-k198.onrender.com/docs |

Demo credentials: `demo@triaxisai.com` / `demo1234`

---

## Features

### Role-Based Access Control (RBAC)

Three roles with enforced permissions at both API and UI level:

| Role | Who | Access |
|---|---|---|
| `TENANT_ADMIN` | Business owner | Full control — create/edit/delete everything, manage users and customers |
| `TENANT_USER` | Staff / technician | Read-only — view all devices, telemetry, dashboards; can ack/clear alarms |
| `CUSTOMER_USER` | Client's staff | Scoped — sees only devices assigned to their customer |

**How users are created:**
- `TENANT_ADMIN` — self-register via "New Organization" on login page (creates a new tenant)
- `TENANT_USER` — invited by admin from **Users & Roles** page (joins existing tenant)
- `CUSTOMER_USER` — created by admin from **Customers** page (scoped to one customer)

**What each role can do:**

| Action | TENANT_ADMIN | TENANT_USER | CUSTOMER_USER |
|---|:---:|:---:|:---:|
| View all devices | ✅ | ✅ | ❌ own only |
| Create / edit / delete devices | ✅ | ❌ | ❌ |
| Assign device to customer | ✅ | ❌ | ❌ |
| Add / edit / delete widgets | ✅ | ❌ | ❌ |
| Create / delete dashboards | ✅ | ❌ | ❌ |
| Manage personal (My) dashboards | ✅ | ✅ | ✅ |
| View telemetry | ✅ | ✅ | ✅ own only |
| View alarms | ✅ | ✅ | ✅ own only |
| Acknowledge / clear alarms | ✅ | ✅ | ❌ |
| Delete alarms | ✅ | ❌ | ❌ |
| Invite staff users | ✅ | ❌ | ❌ |
| Create / delete customers | ✅ | ❌ | ❌ |
| Add customer users | ✅ | ❌ | ❌ |
| Manage threshold rules | ✅ | ❌ | ❌ |
| Regenerate device tokens | ✅ | ❌ | ❌ |
| View provisioning key | ✅ | ❌ | ❌ |

---

### Device Management
- Add, edit, and delete devices
- Assign devices to customers for scoped access
- Auto-generated device tokens for secure telemetry ingestion
- Token regeneration on demand (admin only)
- Device status tracking: `ACTIVE` (sending data) / `INACTIVE` (offline)
- Offline detection — device marked `INACTIVE` automatically after 5 minutes of no data
- `last_seen_at` timestamp updated on every ingest
- **Device Provisioning** — ESP32 / firmware self-registers on first boot using a provisioning key

### Telemetry Ingestion
- **HTTP POST** — send data from any device or script
- **MQTT publish** — connect any MQTT-capable device
- Both paths share identical processing: alarm checks, DB writes, WebSocket broadcast
- Rate limited: 100 requests/minute per device token
- Payload validation: max 50 keys, max 64 characters per key name
- Supports numeric, boolean, string, and JSON value types

### Telemetry Aggregation
- Time-windowed aggregation over any numeric key
- Windows: `1m` `5m` `15m` `30m` `1h` `6h` `12h` `24h` `7d`
- Functions: `avg` `min` `max` `sum` `count`
- Computed entirely in PostgreSQL — no data loaded into memory
- Widgets show real AVG / MIN / MAX / PTS from backend (not estimated from in-memory points)

### Telemetry Retention
- Automatic daily purge of rows older than `TELEMETRY_RETENTION_DAYS` (default: 90 days)
- Configurable via environment variable

### Real-time Dashboards
- **WebSocket push** — widgets update the moment data arrives
- REST fallback polling (5s) when WebSocket is unavailable
- Auto-reconnect with exponential backoff + jitter
- Drag and drop widget repositioning
- Resize widgets by dragging the corner handle
- Layout positions persist to PostgreSQL — survive page reload

### Two Dashboard Systems
| | Device Dashboard | My Dashboards |
|---|---|---|
| Scope | One device per dashboard | Mix any devices on one dashboard |
| Who can edit | TENANT_ADMIN only | All roles (personal workspace) |
| Access | Via Device Dashboards menu | Via My Dashboards sidebar |
| Use case | Official device view | Personal cross-device overview |

### 11 Widget Types
| Widget | Description |
|---|---|
| Value Card | Large number + sparkline + AVG/MIN/MAX from aggregate API |
| Line Chart | Time-series with window selector + AVG/MIN/MAX/PTS stats |
| Bar Chart | Time-series bars + window selector + AVG/MIN/MAX/PTS stats |
| Gauge | Circular dial + window selector + AVG/MIN/MAX range |
| Status Light | Green = online, Grey = offline indicator |
| Pie / Donut | Proportion distribution across multiple keys |
| Alarm List | Active alarms for the bound device |
| History Table | Raw telemetry rows + AVG/MIN/MAX summary footer |
| Entity Table | All latest key-value pairs for the device |
| Text / Markdown | Free-text notes with basic formatting |
| HTML Card | Custom HTML with `${key}` live value substitution |

### Alarm Engine
- Alarm rules stored in `threshold_rules` DB table — fully configurable per tenant/device
- Conditions: `gt` `gte` `lt` `lte` `eq`
- Severities: `CRITICAL` `MAJOR` `MINOR` `WARNING` `INDETERMINATE`
- Device-specific rules override tenant-wide rules
- Alarms can be acknowledged, cleared, and deleted from the UI
- Alarm scoping: `CUSTOMER_USER` only sees alarms from their assigned devices

### Multi-Tenant Security
- Every "New Organization" registration creates an isolated tenant
- All data scoped by `tenant_id` — devices, telemetry, dashboards, alarms, threshold rules
- JWT access tokens (30 min expiry) + refresh tokens (7 days)
- `SECRET_KEY` has no default — must be set as environment variable
- WebSocket connections validated with JWT before `accept()`
- Tenant ownership verified on every device/telemetry/alarm access
- Cross-tenant access returns `403 Forbidden`
- Device tokens only returned on create and regenerate — never on list/get

### Password Reset
- `POST /auth/forgot-password` — generates signed reset token (logs it; wire SMTP for email)
- `POST /auth/reset-password` — consumes token, sets new password
- Token is single-use and expires

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI 0.111, Python 3.11 |
| Database | PostgreSQL 15 (SQLAlchemy 2.0) |
| Authentication | JWT (python-jose) + bcrypt + refresh tokens |
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
│   │   │   ├── auth_deps.py          # get_current_user, require_admin, require_tenant_member
│   │   │   ├── config.py             # Settings from env vars (SECRET_KEY required)
│   │   │   ├── database.py           # SQLAlchemy engine + session (pool_size=5)
│   │   │   ├── security.py           # JWT access+refresh tokens, bcrypt
│   │   │   └── websocket_manager.py  # In-memory WS registry (single worker)
│   │   ├── models/
│   │   │   └── models.py             # 14 ORM models incl. ThresholdRule
│   │   ├── routers/
│   │   │   ├── auth.py               # register, login, refresh, invite, reset-password, user management
│   │   │   ├── alarms.py             # CRUD + ack/clear — RBAC enforced
│   │   │   ├── customers.py          # CRUD + customer user creation — admin only
│   │   │   ├── dashboard.py          # GET /stats
│   │   │   ├── dashboards.py         # Device dashboards + widgets — write: admin only
│   │   │   ├── devices.py            # CRUD + provisioning — write: admin only
│   │   │   ├── telemetry.py          # Ingest (rate-limited) + read + bulk history + aggregate
│   │   │   ├── threshold_rules.py    # Alarm rule CRUD — admin only
│   │   │   ├── user_dashboards.py    # Personal multi-dashboards — per user
│   │   │   └── ws.py                 # WebSocket — JWT required before accept
│   │   ├── services/
│   │   │   ├── mqtt_client.py        # paho-mqtt → asyncio bridge
│   │   │   ├── telemetry_service.py  # Shared ingest pipeline: last_seen_at, DB rules, retention
│   │   │   └── user_dashboard_service.py
│   │   ├── schemas/
│   │   │   └── schemas.py            # Pydantic models incl. DeviceWithToken, BulkHistory, ThresholdRule
│   │   └── main.py                   # Lifespan: DB init, MQTT start, offline detection task, purge task
│   ├── migrations/
│   │   └── apply.py                  # Idempotent migrations 001–011
│   ├── Dockerfile
│   ├── render.yaml
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/
    ├── src/
    │   ├── components/
    │   │   ├── dashboard/GridLayout.jsx
    │   │   ├── sidebar/DashboardSidebar.jsx
    │   │   └── widgets/index.jsx      # All 11 widget types + aggregate API calls
    │   ├── hooks/
    │   │   └── useTelemetry.js        # WebSocket + REST fallback hook
    │   ├── pages/
    │   │   ├── DashboardPage.jsx      # Device dashboard — edit hidden for non-admin
    │   │   └── UserDashboardPage.jsx  # Personal dashboard — edit hidden for non-admin
    │   ├── services/
    │   │   ├── api.js                 # authApi, deviceApi, telemetryApi, userApi, customerApi
    │   │   ├── websocket.js           # TelemetrySocket singleton + REST fallback
    │   │   └── widgetService.js
    │   └── App.jsx                    # All pages incl. UsersPage, CustomersPage — RBAC-aware UI
    ├── package.json
    └── vite.config.js
```

---

## Database Schema

```
tenants
  id, name, provisioning_key (unique), created_at

users
  id, tenant_id, email, hashed_password, first_name, last_name,
  role (TENANT_ADMIN|TENANT_USER|CUSTOMER_USER), customer_id, is_active

customers
  id, tenant_id, name, email, phone, city, country

devices
  id, tenant_id, customer_id, name, device_type, label,
  token (unique), status, last_seen_at, created_at

telemetry_data              ← append-only time-series
  id, device_id, key, value_num, value_str, value_bool, value_json, ts
  INDEX (device_id, key, ts DESC)   ← composite index for fast aggregates

latest_telemetry            ← one row per (device, key), atomic upsert
  id, device_id, key, value_num, value_str, value_bool, value_json, ts
  UNIQUE (device_id, key)

telemetry_keys              ← metadata per (device, key)
  id, device_id, key, label, unit, data_type
  UNIQUE (device_id, key)

threshold_rules             ← DB-backed alarm rules (replaces hardcoded dict)
  id, tenant_id, device_id (nullable = tenant-wide), key,
  condition (gt|gte|lt|lte|eq), threshold, severity, alarm_type, is_active

alarms
  id, device_id, alarm_type, severity, status,
  start_ts, end_ts, ack_ts, clear_ts, ack_by, cleared_by

dashboards                  ← device-scoped
  id, device_id, name, is_default

widgets
  id, dashboard_id, widget_type, title, config (JSON), position (JSON)

user_dashboards             ← personal per-user
  id, user_id, name, is_default

user_widgets
  id, dashboard_id, widget_type, title, config (JSON), position (JSON)
```

---

## Deploy to Render

### Step 1 — Push to GitHub

```bash
git init && git add . && git commit -m "initial"
git remote add origin https://github.com/YOUR_USERNAME/iot-platform.git
git push -u origin main
```

### Step 2 — Create PostgreSQL database

Render dashboard → **New + → PostgreSQL** → Free → copy **Internal Database URL**

### Step 3 — Deploy backend (Web Service)

**New + → Web Service** → connect repo → Root Directory: `backend` → Runtime: Docker

Environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | Internal Database URL |
| `SECRET_KEY` | Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `CORS_ORIGINS` | `https://YOUR-FRONTEND.onrender.com` |
| `MQTT_BROKER_HOST` | Your private broker (e.g. EMQX Cloud) |
| `MQTT_BROKER_PORT` | `8883` |
| `MQTT_USE_TLS` | `true` |
| `MQTT_USERNAME` | Broker username |
| `MQTT_PASSWORD` | Broker password |
| `TELEMETRY_RETENTION_DAYS` | `90` (optional) |

> ⚠️ `SECRET_KEY` has **no default** — the app will refuse to start without it.

### Step 4 — Deploy frontend (Static Site)

**New + → Static Site** → Root Directory: `frontend` → Build: `npm install && npm run build` → Publish: `dist`

Add env var: `VITE_API_URL` = `https://YOUR-BACKEND.onrender.com`

### Step 5 — Run database migrations

In Render Shell (backend service → Shell tab):

```bash
python migrations/apply.py
```

This runs all 11 migrations idempotently. Safe to run multiple times.

### Step 6 — Seed demo account (optional)

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
# Set: DATABASE_URL=postgresql://postgres:postgres@localhost:5432/iotdb
# Set: SECRET_KEY=any-local-dev-key

uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend && npm install
cp .env.example .env
# Set: VITE_API_URL=http://localhost:8000
npm run dev
```

---

## RBAC User Management

### Invite a staff member (TENANT_USER)
1. Log in as `TENANT_ADMIN`
2. Sidebar → **Users & Roles** → **Invite User**
3. Fill email, password, name → select `TENANT_USER` → Create
4. Give them the credentials — they log in and see all devices (read-only)

### Create a customer with scoped access (CUSTOMER_USER)
1. Sidebar → **Customers** → **New Customer** → fill company details
2. Sidebar → **Devices** → Edit each device → set **Assign to Customer** dropdown
3. Sidebar → **Customers** → click the customer → **Add User** → fill email + password
4. Give them the credentials — they log in and only see their assigned devices

### Change a user's role
Sidebar → **Users & Roles** → click edit (pencil icon) on any user → change role → Save

---

## Device Provisioning

ESP32 self-registers without a user JWT using the provisioning key from **Settings → Device Provisioning**.

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>

#define IOT_HOST      "https://YOUR-BACKEND.onrender.com"
#define PROVISION_KEY "your-provisioning-key"

Preferences prefs;
String deviceToken = "";

void provisionDevice() {
  HTTPClient http;
  http.begin(String(IOT_HOST) + "/api/v1/devices/provision");
  http.addHeader("Content-Type", "application/json");

  String devName = "ESP32-" + String((uint32_t)(ESP.getEfuseMac() >> 32), HEX);
  String payload = "{\"provision_key\":\"" + String(PROVISION_KEY) +
                   "\",\"device_name\":\"" + devName +
                   "\",\"device_type\":\"SENSOR\"}";

  int code = http.POST(payload);
  if (code == 200 || code == 201) {
    String resp = http.getString();
    int t1 = resp.indexOf("\"token\":\"") + 9;
    deviceToken = resp.substring(t1, resp.indexOf("\"", t1));
    prefs.begin("iot", false);
    prefs.putString("token", deviceToken);
    prefs.end();
  }
  http.end();
}

void setup() {
  WiFi.begin("SSID", "PASSWORD");
  while (!WiFi.isConnected()) delay(500);

  prefs.begin("iot", true);
  deviceToken = prefs.getString("token", "");
  prefs.end();

  if (deviceToken == "") provisionDevice();
}

void loop() {
  HTTPClient http;
  http.begin(String(IOT_HOST) + "/api/v1/telemetry/ingest/" + deviceToken);
  http.addHeader("Content-Type", "application/json");
  http.POST("{\"values\":{\"temperature\":25.5,\"humidity\":60}}");
  http.end();
  delay(5000);
}
```

---

## API Reference

### Authentication & Users

| Method | Endpoint | Role | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | — | New organization signup |
| POST | `/api/v1/auth/login` | — | Login → access + refresh tokens |
| POST | `/api/v1/auth/refresh` | — | Refresh access token |
| POST | `/api/v1/auth/forgot-password` | — | Request reset token |
| POST | `/api/v1/auth/reset-password` | — | Reset with token |
| POST | `/api/v1/auth/seed-demo` | — | Create demo account |
| POST | `/api/v1/auth/users/invite` | ADMIN | Invite staff to tenant |
| GET | `/api/v1/auth/users` | ADMIN | List tenant users |
| PUT | `/api/v1/auth/users/{id}/role` | ADMIN | Change role |
| DELETE | `/api/v1/auth/users/{id}` | ADMIN | Remove user |

### Devices

| Method | Endpoint | Role | Description |
|---|---|---|---|
| GET | `/api/v1/devices/` | ALL | List devices (paginated, customer-scoped) |
| POST | `/api/v1/devices/` | ADMIN | Create device |
| GET | `/api/v1/devices/provisioning-key` | ADMIN | Get provisioning key |
| POST | `/api/v1/devices/provision` | — | Self-register (firmware) |
| GET | `/api/v1/devices/{id}` | ALL | Get device |
| PUT | `/api/v1/devices/{id}` | ADMIN | Update (incl. customer assignment) |
| DELETE | `/api/v1/devices/{id}` | ADMIN | Delete |
| POST | `/api/v1/devices/{id}/token/regenerate` | ADMIN | Regenerate token |

### Telemetry

| Method | Endpoint | Role | Description |
|---|---|---|---|
| POST | `/api/v1/telemetry/ingest/{token}` | Device | Ingest (rate-limited: 100/min) |
| GET | `/api/v1/telemetry/latest/{device_id}` | ALL | Latest values |
| GET | `/api/v1/telemetry/history/{device_id}?key=&limit=` | ALL | Time-series |
| POST | `/api/v1/telemetry/history/{device_id}/bulk` | ALL | Bulk history (multiple keys) |
| GET | `/api/v1/telemetry/keys/{device_id}` | ALL | Available keys |
| GET | `/api/v1/telemetry/aggregate/{device_id}?key=&window=&function=` | ALL | Aggregation |
| GET | `/api/v1/telemetry/metadata/{device_id}` | ALL | Key metadata |
| PUT | `/api/v1/telemetry/metadata/{device_id}/{key}` | ADMIN | Update metadata |

### Customers

| Method | Endpoint | Role | Description |
|---|---|---|---|
| GET | `/api/v1/customers/` | ADMIN | List customers |
| POST | `/api/v1/customers/` | ADMIN | Create customer |
| DELETE | `/api/v1/customers/{id}` | ADMIN | Delete customer |
| GET | `/api/v1/customers/{id}/users` | ADMIN | List customer users |
| POST | `/api/v1/customers/{id}/users` | ADMIN | Create CUSTOMER_USER |

### Threshold Rules

| Method | Endpoint | Role | Description |
|---|---|---|---|
| GET | `/api/v1/threshold-rules/` | ADMIN | List rules |
| POST | `/api/v1/threshold-rules/` | ADMIN | Create rule |
| PUT | `/api/v1/threshold-rules/{id}` | ADMIN | Update rule |
| DELETE | `/api/v1/threshold-rules/{id}` | ADMIN | Delete rule |

### Alarms

| Method | Endpoint | Role | Description |
|---|---|---|---|
| GET | `/api/v1/alarms/` | ALL | List (customer-scoped) |
| POST | `/api/v1/alarms/` | ADMIN | Create manually |
| POST | `/api/v1/alarms/{id}/ack` | ADMIN, USER | Acknowledge |
| POST | `/api/v1/alarms/{id}/clear` | ADMIN, USER | Clear |
| DELETE | `/api/v1/alarms/{id}` | ADMIN | Delete |

### System

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | DB ping — returns 503 if Postgres down |
| GET | `/status` | JWT | MQTT + WebSocket state |
| GET | `/docs` | — | Swagger UI |
| WS | `/api/v1/ws/telemetry/{device_id}?token=` | JWT | Live telemetry stream |

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `SECRET_KEY` | ✅ | **none** | JWT signing key — app won't start without this |
| `CORS_ORIGINS` | ✅ | localhost | Comma-separated allowed origins |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | `30` | JWT access token expiry |
| `REFRESH_TOKEN_EXPIRE_DAYS` | — | `7` | JWT refresh token expiry |
| `TELEMETRY_RETENTION_DAYS` | — | `90` | Days before telemetry rows are purged |
| `MQTT_ENABLED` | — | `true` | Set `false` to disable MQTT |
| `MQTT_BROKER_HOST` | — | `broker.hivemq.com` | Use a private broker in production |
| `MQTT_BROKER_PORT` | — | `1883` | Use `8883` for TLS |
| `MQTT_USE_TLS` | — | `false` | `true` for secure brokers |
| `MQTT_USERNAME` | — | — | Broker username |
| `MQTT_PASSWORD` | — | — | Broker password |
| `VITE_API_URL` | ✅ | — | Frontend: backend base URL |

---

## Security Architecture

```
Registration ("New Organization")
  → creates Tenant (provisioning_key) + TENANT_ADMIN user

Login
  → returns { access_token (30m), refresh_token (7d) }
  → access_token: JWT { sub, email, role, type:"access" }

Every API request:
  Bearer token validated → role extracted
  TENANT_ADMIN  → full access
  TENANT_USER   → read + ack/clear alarms only
  CUSTOMER_USER → read only, filtered to customer_id

WebSocket:
  ?token=<jwt> required → tenant + device ownership verified before accept()
  Wrong tenant → close(4003)
  No/invalid token → close(4001)

Telemetry ingest (device → platform):
  POST /telemetry/ingest/{token}  ← device token, no JWT
  Rate limited: 100 req/min per token

Device provisioning:
  POST /devices/provision { provision_key, device_name }
  provision_key maps to exactly one tenant
```

---

## Production Checklist

- [ ] `SECRET_KEY` set in Render environment (no default — app won't start without it)
- [ ] `CORS_ORIGINS` matches exact frontend URL — no trailing slash
- [ ] `DATABASE_URL` uses Render **Internal** URL
- [ ] All 11 migrations applied: `python migrations/apply.py`
- [ ] Private MQTT broker configured with TLS (`MQTT_USE_TLS=true`, port `8883`)
- [ ] `TELEMETRY_RETENTION_DAYS` set to match your storage budget
- [ ] Remove or protect `/api/v1/auth/seed-demo` before public launch
- [ ] `--workers 1` enforced in Dockerfile (required for WebSocket in-process registry)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| App won't start | `SECRET_KEY` not set | Add to Render Environment variables |
| Can't log in after redeploy | JWT signed with old key | Log out and log in again |
| HTTP 500 on ingest | `threshold_rules` or `last_seen_at` missing | Run `python migrations/apply.py` in Render Shell |
| WebSocket shows "Static" | JWT expired or not passed | Log out → log in → reconnect |
| Customer user sees no devices | Device not assigned to customer | Devices → Edit → set Assign to Customer |
| `column users.customer_id does not exist` | Migration 011 not run | Run `python migrations/apply.py` |
| Build fails: `Could not resolve ../services/api.js` | Wrong import path in widgets | Already fixed in this version |
| Duplicate Default Dashboards | Race condition on first login | Auto-deduplicated on page load |
