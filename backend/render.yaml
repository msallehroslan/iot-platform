services:
  - type: web
    name: iot-platform-api
    runtime: docker
    dockerfilePath: ./Dockerfile
    dockerContext: .
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: iot-platform-db
          property: connectionString
      - key: SECRET_KEY
        generateValue: true
      - key: CORS_ORIGINS
        value: https://iot-platform-ui.onrender.com
      - key: PYTHON_VERSION
        value: "3.11"

      # ── MQTT ──────────────────────────────────────────────────────────────
      - key: MQTT_ENABLED
        value: "true"
      - key: MQTT_BROKER_HOST
        value: broker.hivemq.com
      - key: MQTT_BROKER_PORT
        value: "1883"
      # Override with your private broker credentials in the Render dashboard:
      # - key: MQTT_USERNAME
      #   value: your-mqtt-user
      # - key: MQTT_PASSWORD
      #   value: your-mqtt-password
      - key: MQTT_TOPIC_PREFIX
        value: iot
      - key: MQTT_KEEPALIVE
        value: "60"
      # TLS — uncomment and set MQTT_BROKER_PORT=8883 for secure brokers:
      # - key: MQTT_USE_TLS
      #   value: "true"

databases:
  - name: iot-platform-db
    databaseName: iotdb
    user: iotuser
    plan: free
