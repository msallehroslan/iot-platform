# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL=postgresql://user:password@host:5432/iotdb

# ── Auth ──────────────────────────────────────────────────────────────────────
SECRET_KEY=your-super-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ── CORS ──────────────────────────────────────────────────────────────────────
CORS_ORIGINS=http://localhost:5173,https://your-frontend.onrender.com

# ── MQTT ──────────────────────────────────────────────────────────────────────
# Set MQTT_ENABLED=false to disable MQTT (useful for local dev without broker)
MQTT_ENABLED=true

# Public test broker — replace with your private broker in production
MQTT_BROKER_HOST=broker.hivemq.com
MQTT_BROKER_PORT=1883

# Leave empty for anonymous brokers (HiveMQ public requires no auth)
MQTT_USERNAME=
MQTT_PASSWORD=

# Topic prefix: devices publish to {prefix}/{device_token}/telemetry
MQTT_TOPIC_PREFIX=iot

# TLS — set true for HiveMQ Cloud, AWS IoT, EMQX Cloud (port 8883)
MQTT_USE_TLS=false
# MQTT_BROKER_PORT=8883  # change when MQTT_USE_TLS=true

# Unique client ID per deployment — auto-set from hostname if left empty
# MQTT_CLIENT_ID=iot-platform-prod

# Connection keepalive in seconds
MQTT_KEEPALIVE=60
