# ── Required ──────────────────────────────────────────────────────────────────
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=CHANGE_ME_generate_with_secrets_token_hex_32

DATABASE_URL=postgresql://user:password@host:5432/iotdb

# ── Optional (defaults shown) ─────────────────────────────────────────────────
CORS_ORIGINS=https://your-frontend.onrender.com
BASE_URL=https://your-backend.onrender.com

# JWT
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# Telemetry retention (days before rows are purged)
TELEMETRY_RETENTION_DAYS=90

# MQTT — use a private broker, NOT the public HiveMQ default
MQTT_ENABLED=true
MQTT_BROKER_HOST=your-private-broker.example.com
MQTT_BROKER_PORT=8883
MQTT_USERNAME=your_username
MQTT_PASSWORD=your_password
MQTT_USE_TLS=true
