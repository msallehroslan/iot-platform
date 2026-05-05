from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):
    # ── Core ──────────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/iotdb"
    # SECRET_KEY has NO default — must be set as env var.
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS:   int = 7
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    BASE_URL: str = ""

    # ── Phase 4: Redis ────────────────────────────────────────────────────────
    # Set REDIS_URL to enable:
    #   - Redis-backed WebSocket pub/sub (multi-worker safe)
    #   - Optional ingest queue
    # If not set → falls back to in-process ConnectionManager (single worker)
    REDIS_URL: Optional[str] = None   # Set via Render env var: redis://red-d7sv4qq8qa3s73f17s4g:6379

    # ── Phase 4: Tenant Quotas ────────────────────────────────────────────────
    # Default limits applied to all tenants unless overridden in tenant.quotas
    DEFAULT_MAX_DEVICES:       int = 100    # max devices per tenant
    DEFAULT_MAX_DASHBOARDS:    int = 50     # max dashboards per tenant
    DEFAULT_TELEMETRY_RATE:    int = 1000   # max ingest events/min per tenant
    TELEMETRY_RETENTION_DAYS:  int = 90

    # ── MQTT ──────────────────────────────────────────────────────────────────
    MQTT_ENABLED:     bool  = True
    MQTT_BROKER_HOST: str   = "broker.hivemq.com"
    MQTT_BROKER_PORT: int   = 1883
    MQTT_USE_TLS:     bool  = False
    MQTT_USERNAME:    Optional[str] = None
    MQTT_PASSWORD:    Optional[str] = None

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def redis_enabled(self) -> bool:
        return bool(self.REDIS_URL)

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
