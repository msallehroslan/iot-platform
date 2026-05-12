"""
app/services/mqtt_client.py

MQTT telemetry ingestion via paho-mqtt (synchronous library).

Architecture
────────────
paho-mqtt runs its network loop in a dedicated background THREAD (not asyncio).
FastAPI / uvicorn run on asyncio.  The bridge between them is:

    asyncio.run_coroutine_threadsafe(coro, loop)

This submits the coroutine to the running event loop from paho's thread and
returns a concurrent.futures.Future — we don't await it; we fire-and-forget.

Topic structure
───────────────
Subscribe:  iot/+/telemetry           (wildcard — one subscription for all devices)
Example:    iot/abc-device-token/telemetry
Payload:    { "temperature": 28.5, "humidity": 70 }

The device token in the topic is used to look up the device in the database,
then the full ingest pipeline (save → alarm check → WS broadcast) is called.

Optional extras
───────────────
Devices can also send a timestamp in the payload:
    { "temperature": 28.5, "ts": "2025-04-29T10:00:00Z" }

Configuration (env vars / .env)
───────────────────────────────
MQTT_BROKER_HOST    = broker.hivemq.com   (default: public HiveMQ broker)
MQTT_BROKER_PORT    = 1883
MQTT_USERNAME       = ""                  (leave empty for anonymous brokers)
MQTT_PASSWORD       = ""
MQTT_CLIENT_ID      = iot-platform-{hostname}
MQTT_TOPIC_PREFIX   = iot                 (subscribes to {prefix}/+/telemetry)
MQTT_ENABLED        = true                (set false to disable at runtime)

Render-compatibility
────────────────────
paho runs in a plain OS thread — no special infrastructure required.
Works fine on Render's free-tier web service alongside uvicorn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import threading
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt

from app.core.database import SessionLocal
from app.services.telemetry_service import DeviceNotFoundError, ingest_telemetry

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────────────────────

class MqttSettings:
    """Reads MQTT configuration from environment variables.
    Centralised here so changes never touch settings.py or config.py.
    """

    def __init__(self) -> None:
        import os
        self.enabled      = os.getenv("MQTT_ENABLED",      "true").lower() not in ("false", "0", "no")
        self.broker_host  = os.getenv("MQTT_BROKER_HOST",  "broker.hivemq.com")
        self.broker_port  = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        self.username     = os.getenv("MQTT_USERNAME",     "")
        self.password     = os.getenv("MQTT_PASSWORD",     "")
        self.topic_prefix = os.getenv("MQTT_TOPIC_PREFIX", "iot")
        self.keepalive    = int(os.getenv("MQTT_KEEPALIVE", "60"))
        # TLS: set MQTT_USE_TLS=true and MQTT_BROKER_PORT=8883 for secure brokers
        # (HiveMQ Cloud, AWS IoT, EMQX Cloud all require TLS on port 8883)
        self.use_tls      = os.getenv("MQTT_USE_TLS", "false").lower() in ("true", "1", "yes")
        # Unique client ID — important: two clients with the same ID fight for the connection
        hostname          = platform.node() or "app"
        self.client_id    = os.getenv("MQTT_CLIENT_ID", f"iot-platform-{hostname}")

    @property
    def subscribe_topic(self) -> str:
        """Wildcard topic covering every device token."""
        return f"{self.topic_prefix}/+/telemetry"


mqtt_settings = MqttSettings()


# ── Topic parsing ─────────────────────────────────────────────────────────────

def _parse_token(topic: str, prefix: str) -> Optional[str]:
    """
    Extract device token from topic string.
    e.g. "iot/abc123/telemetry" → "abc123"
    Returns None if the topic does not match the expected pattern.
    """
    parts = topic.split("/")
    # Expected: [prefix, token, "telemetry"]
    if len(parts) != 3:
        return None
    if parts[0] != prefix or parts[2] != "telemetry":
        return None
    token = parts[1].strip()
    return token if token else None


# ── paho → asyncio bridge ─────────────────────────────────────────────────────

async def _process_message(token: str, values: dict, ts: Optional[datetime]) -> None:
    """
    Coroutine that runs on the asyncio event loop.
    Opens a DB session, calls ingest_telemetry, closes the session.
    This is the async side of the paho→asyncio bridge.
    """
    db = SessionLocal()
    try:
        result = await ingest_telemetry(
            db=db,
            token=token,
            values=values,
            ts=ts,
            source="mqtt",
        )
        logger.info(
            "MQTT ingest ok  token=%s  keys=%d  ts=%s",
            token, result["keys_saved"], result["ts"],
        )
    except DeviceNotFoundError:
        logger.warning("MQTT unknown token=%s — message ignored", token)
    except Exception as exc:
        logger.error("MQTT ingest error token=%s: %s", token, exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


# ── MQTT client ───────────────────────────────────────────────────────────────

class MqttClient:
    """
    Thin wrapper around paho.mqtt.Client.

    Lifecycle:
        start(loop)  — call from FastAPI lifespan, passes the running event loop
        stop()       — call from FastAPI lifespan teardown
    """

    def __init__(self) -> None:
        self._client: Optional[mqtt.Client] = None
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected = threading.Event()
        self._stopped   = False

    # ── paho callbacks ────────────────────────────────────────────────────────

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc == 0:
            logger.info(
                "MQTT connected  broker=%s:%d  topic=%s",
                mqtt_settings.broker_host,
                mqtt_settings.broker_port,
                mqtt_settings.subscribe_topic,
            )
            client.subscribe(mqtt_settings.subscribe_topic, qos=1)
            self._connected.set()
        else:
            err_map = {
                1: "unacceptable protocol version",
                2: "identifier rejected",
                3: "server unavailable",
                4: "bad username/password",
                5: "not authorised",
            }
            logger.error("MQTT connect failed rc=%d (%s)", rc, err_map.get(rc, "unknown"))

    def _on_disconnect(self, client: mqtt.Client, userdata, rc: int) -> None:
        self._connected.clear()
        if rc == 0:
            logger.info("MQTT disconnected cleanly")
        else:
            logger.warning("MQTT unexpected disconnect rc=%d — paho will reconnect", rc)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        """
        paho calls this from its network THREAD.
        We must NOT call any asyncio directly here — instead we schedule a
        coroutine on the main event loop via run_coroutine_threadsafe.
        """
        topic   = msg.topic
        payload = msg.payload

        # ── 1. Parse token from topic ──────────────────────────────────────
        token = _parse_token(topic, mqtt_settings.topic_prefix)
        if not token:
            logger.debug("MQTT ignoring unrecognised topic=%s", topic)
            return

        # ── 2. Decode payload ──────────────────────────────────────────────
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("MQTT bad JSON on topic=%s: %s", topic, exc)
            return

        if not isinstance(raw, dict) or not raw:
            logger.warning("MQTT payload must be a non-empty JSON object, got %r", raw)
            return

        # ── 3. Extract optional timestamp from payload ─────────────────────
        ts: Optional[datetime] = None
        ts_raw = raw.pop("ts", None)        # remove "ts" so it isn't stored as a telemetry key
        if ts_raw:
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                logger.debug("MQTT ignoring bad ts=%r on topic=%s", ts_raw, topic)

        values = raw  # remaining keys are telemetry values

        if not values:
            logger.warning("MQTT payload has no telemetry keys after removing ts, topic=%s", topic)
            return

        # ── 4. Bridge to asyncio ───────────────────────────────────────────
        if self._loop is None or self._loop.is_closed():
            logger.error("MQTT: event loop unavailable, dropping message topic=%s", topic)
            return

        # Fire-and-forget: submit coroutine to the FastAPI event loop
        future = asyncio.run_coroutine_threadsafe(
            _process_message(token, values, ts),
            self._loop,
        )
        # Attach a done callback for error logging; we don't block here
        future.add_done_callback(
            lambda f: logger.error("MQTT bridge exception: %s", f.exception())
            if f.exception() else None
        )

    def _on_subscribe(self, client, userdata, mid, granted_qos) -> None:
        logger.info("MQTT subscribed  topic=%s  qos=%s", mqtt_settings.subscribe_topic, granted_qos)

    def _on_log(self, client, userdata, level, buf) -> None:
        # Map paho log levels to Python logging
        lvl_map = {
            mqtt.MQTT_LOG_DEBUG:   logging.DEBUG,
            mqtt.MQTT_LOG_INFO:    logging.DEBUG,   # paho INFO is very chatty
            mqtt.MQTT_LOG_NOTICE:  logging.INFO,
            mqtt.MQTT_LOG_WARNING: logging.WARNING,
            mqtt.MQTT_LOG_ERR:     logging.ERROR,
        }
        logger.log(lvl_map.get(level, logging.DEBUG), "paho: %s", buf)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Connect to the broker and start the paho loop in a background thread.
        Called from FastAPI's lifespan startup.

        Args:
            loop: The running asyncio event loop from the main thread.
        """
        if not mqtt_settings.enabled:
            logger.info("MQTT disabled (MQTT_ENABLED=false) — skipping MQTT startup")
            return

        # Safety guard: refuse to connect to known public brokers.
        # A public broker means ALL telemetry is readable by anyone on the internet.
        # Set MQTT_BROKER_HOST to a private broker, or set MQTT_ENABLED=false.
        _PUBLIC_BROKERS = {
            "broker.hivemq.com",
            "test.mosquitto.org",
            "mqtt.eclipseprojects.io",
            "broker.emqx.io",
            "public.mqtthq.com",
        }
        if mqtt_settings.broker_host.lower() in _PUBLIC_BROKERS:
            logger.error(
                "MQTT STARTUP BLOCKED: broker_host=%r is a known public broker. "
                "All telemetry on a public broker is readable by anyone. "
                "Set MQTT_BROKER_HOST to a private broker, or set MQTT_ENABLED=false. "
                "Disabling MQTT for this session.",
                mqtt_settings.broker_host,
            )
            return

        self._loop    = loop
        self._stopped = False

        self._client = mqtt.Client(
            client_id=mqtt_settings.client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        # Optional authentication
        if mqtt_settings.username:
            self._client.username_pw_set(
                mqtt_settings.username,
                mqtt_settings.password or None,
            )

        # Optional TLS (required for HiveMQ Cloud, AWS IoT, EMQX Cloud, port 8883)
        if mqtt_settings.use_tls:
            import ssl
            self._client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            logger.info("MQTT TLS enabled")

        # Register callbacks
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message
        self._client.on_subscribe  = self._on_subscribe
        self._client.on_log        = self._on_log

        # Reconnect behaviour: paho handles this automatically in loop_forever()
        self._client.reconnect_delay_set(min_delay=2, max_delay=60)

        # Connect asynchronously (connect_async returns immediately)
        try:
            self._client.connect_async(
                mqtt_settings.broker_host,
                mqtt_settings.broker_port,
                keepalive=mqtt_settings.keepalive,
            )
        except Exception as exc:
            logger.error("MQTT connect_async failed: %s", exc)
            return

        # Start the paho network loop in a background daemon thread.
        # loop_forever() blocks the thread and handles reconnect automatically.
        self._thread = threading.Thread(
            target=self._client.loop_forever,
            name="mqtt-loop",
            daemon=True,   # dies when the main process exits
        )
        self._thread.start()
        logger.info(
            "MQTT loop started  broker=%s:%d  client_id=%s",
            mqtt_settings.broker_host,
            mqtt_settings.broker_port,
            mqtt_settings.client_id,
        )

    def stop(self) -> None:
        """
        Gracefully disconnect and stop the paho loop.
        Called from FastAPI's lifespan teardown.
        """
        self._stopped = True
        if self._client:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception as exc:
                logger.debug("MQTT stop error (ignorable): %s", exc)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("MQTT client stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def status(self) -> dict:
        """Return a dict suitable for the health/status endpoint."""
        return {
            "enabled":     mqtt_settings.enabled,
            "connected":   self.is_connected,
            "broker":      f"{mqtt_settings.broker_host}:{mqtt_settings.broker_port}",
            "client_id":   mqtt_settings.client_id,
            "topic":       mqtt_settings.subscribe_topic,
        }


# ── Module-level singleton ────────────────────────────────────────────────────

# Imported by main.py for startup/shutdown, and by the status endpoint.
mqtt_client = MqttClient()
