import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime, ForeignKey,
    Text, Enum, Integer, JSON, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


class DeviceStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DISABLED = "DISABLED"


class AlarmSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    WARNING = "WARNING"
    INDETERMINATE = "INDETERMINATE"


class AlarmStatus(str, enum.Enum):
    ACTIVE_UNACK = "ACTIVE_UNACK"
    ACTIVE_ACK = "ACTIVE_ACK"
    CLEARED_UNACK = "CLEARED_UNACK"
    CLEARED_ACK = "CLEARED_ACK"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    provisioning_key = Column(String(64), nullable=True, unique=True, index=True,
        default=lambda: str(uuid.uuid4()).replace("-", ""))

    users = relationship("User", back_populates="tenant")
    customers = relationship("Customer", back_populates="tenant")
    devices = relationship("Device", back_populates="tenant")
    threshold_rules = relationship("ThresholdRule", back_populates="tenant", cascade="all, delete-orphan")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="customers")
    devices = relationship("Device", back_populates="customer")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    role = Column(String(50), default="TENANT_ADMIN")
    # For CUSTOMER_USER role — scopes device access to this customer only
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="users")


class Device(Base):
    __tablename__ = "devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    name = Column(String(255), nullable=False, index=True)
    device_type = Column(String(100), default="DEFAULT")
    label = Column(String(255), nullable=True)
    token = Column(String(255), nullable=False, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    status = Column(Enum(DeviceStatus), default=DeviceStatus.INACTIVE)
    description = Column(Text, nullable=True)
    additional_info = Column(JSON, nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)   # FIX 8: updated on every ingest
    latitude     = Column(Float, nullable=True)   # fixed device location
    longitude    = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="devices")
    customer = relationship("Customer", back_populates="devices")
    telemetry = relationship("TelemetryData", back_populates="device", cascade="all, delete-orphan")
    latest_telemetry = relationship("LatestTelemetry", back_populates="device", cascade="all, delete-orphan")
    alarms = relationship("Alarm", back_populates="device", cascade="all, delete-orphan")
    telemetry_keys = relationship("TelemetryKey", back_populates="device", cascade="all, delete-orphan")
    dashboards = relationship("Dashboard", back_populates="device", cascade="all, delete-orphan")
    threshold_rules = relationship("ThresholdRule", back_populates="device", cascade="all, delete-orphan")


class TelemetryData(Base):
    __tablename__ = "telemetry_data"
    __table_args__ = (
        # FIX 10: composite index for aggregate + history queries
        Index("ix_telemetry_device_key_ts", "device_id", "key", "ts"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String(255), nullable=False, index=True)
    value_str = Column(Text, nullable=True)
    value_num = Column(Float, nullable=True)
    value_bool = Column(Boolean, nullable=True)
    value_json = Column(JSON, nullable=True)
    ts = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    device = relationship("Device", back_populates="telemetry")


class LatestTelemetry(Base):
    __tablename__ = "latest_telemetry"
    __table_args__ = (
        UniqueConstraint("device_id", "key", name="uq_latest_telemetry_device_key"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String(255), nullable=False, index=True)
    value_str = Column(Text, nullable=True)
    value_num = Column(Float, nullable=True)
    value_bool = Column(Boolean, nullable=True)
    value_json = Column(JSON, nullable=True)
    ts = Column(DateTime(timezone=True), server_default=func.now())

    device = relationship("Device", back_populates="latest_telemetry")


class TelemetryKey(Base):
    __tablename__ = "telemetry_keys"
    __table_args__ = (
        UniqueConstraint("device_id", "key", name="uq_telemetry_keys_device_key"),
    )

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key       = Column(String(255), nullable=False, index=True)
    label     = Column(String(255), nullable=True)
    unit      = Column(String(50),  nullable=True)
    data_type = Column(String(20),  nullable=False, default="number")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    device = relationship("Device", back_populates="telemetry_keys")


class Alarm(Base):
    __tablename__ = "alarms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    alarm_type = Column(String(255), nullable=False)
    severity = Column(Enum(AlarmSeverity), default=AlarmSeverity.WARNING)
    status = Column(Enum(AlarmStatus), default=AlarmStatus.ACTIVE_UNACK)
    details = Column(JSON, nullable=True)
    start_ts = Column(DateTime(timezone=True), server_default=func.now())
    end_ts = Column(DateTime(timezone=True), nullable=True)
    ack_ts = Column(DateTime(timezone=True), nullable=True)
    clear_ts = Column(DateTime(timezone=True), nullable=True)
    ack_by = Column(String(255), nullable=True)
    cleared_by = Column(String(255), nullable=True)
    propagate = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    device = relationship("Device", back_populates="alarms")


# FIX 9: ThresholdRule — DB-backed alarm rules replacing hardcoded Python dict
class ThresholdRule(Base):
    """
    Per-device, per-key alarm threshold rules.
    Replaces the hardcoded ALARM_RULES dict in telemetry_service.py.
    Users can create/edit rules from the UI via /api/v1/threshold-rules/.
    """
    __tablename__ = "threshold_rules"
    __table_args__ = (
        Index("ix_threshold_rules_tenant_device", "tenant_id", "device_id"),
    )

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id  = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)  # null = applies to all devices in tenant
    key        = Column(String(255), nullable=False)
    condition  = Column(String(10), nullable=False, default="gt")  # gt | lt | gte | lte | eq
    threshold  = Column(Float, nullable=False)
    severity   = Column(Enum(AlarmSeverity), default=AlarmSeverity.WARNING)
    alarm_type = Column(String(255), nullable=False)
    is_active        = Column(Boolean, default=True)
    # ── Intelligence: Auto RPC on alarm ──────────────────────────────────────
    # When alarm fires, automatically send RPC command to device.
    # auto_rpc_method: "set" (standard) or any custom method
    # auto_rpc_params: JSON e.g. {"led1": true, "buzzer": true}
    # auto_rpc_clear:  if True, send opposite params when alarm clears
    auto_rpc_method  = Column(String(100), nullable=True)
    auto_rpc_params  = Column(JSON, nullable=True)
    auto_rpc_clear   = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="threshold_rules")
    device = relationship("Device", back_populates="threshold_rules")


class Dashboard(Base):
    __tablename__ = "dashboards"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id   = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    name        = Column(String(255), nullable=False, default="My Dashboard")
    description = Column(Text, nullable=True)
    is_default  = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())

    device  = relationship("Device", back_populates="dashboards")
    widgets = relationship("Widget", back_populates="dashboard", cascade="all, delete-orphan", order_by="Widget.created_at")


class Widget(Base):
    __tablename__ = "widgets"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    widget_type  = Column(String(50), nullable=False)
    title        = Column(String(255), nullable=False, default="Widget")
    config       = Column(JSON, nullable=False, default=dict)
    position     = Column(JSON, nullable=False, default=lambda: {"x": 0, "y": 0, "w": 2, "h": 3})
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    dashboard = relationship("Dashboard", back_populates="widgets")


class UserDashboard(Base):
    __tablename__ = "user_dashboards"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id     = Column(String(255), nullable=False, index=True)
    name        = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_default  = Column(Boolean, default=False, nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())

    widgets = relationship("UserWidget", back_populates="dashboard",
        cascade="all, delete-orphan", order_by="UserWidget.created_at")


class UserWidget(Base):
    __tablename__ = "user_widgets"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey("user_dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    widget_type  = Column(String(50), nullable=False)
    title        = Column(String(255), nullable=False, default="Widget")
    config       = Column(JSON, nullable=False, default=dict)
    position     = Column(JSON, nullable=False, default=lambda: {"x": 0, "y": 0, "w": 2, "h": 3})
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    dashboard = relationship("UserDashboard", back_populates="widgets")


# ── Security tables ───────────────────────────────────────────────────────────

class RefreshToken(Base):
    """
    Persisted refresh tokens with rotation support.
    On each /auth/refresh call:
      1. Verify token exists + not revoked + not expired
      2. Issue new refresh token (insert new row)
      3. Mark this row as revoked
    Revoked tokens are kept for audit; a daily cleanup job can purge old ones.
    """
    __tablename__ = "refresh_tokens"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token      = Column(String(512), nullable=False, unique=True, index=True)
    revoked    = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordReset(Base):
    """
    Single-use, time-limited password reset tokens stored in DB.
    Replaces the in-memory _reset_tokens dict.
    """
    __tablename__ = "password_resets"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email      = Column(String(255), nullable=False, index=True)
    token      = Column(String(512), nullable=False, unique=True, index=True)
    used       = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RateLimit(Base):
    """
    Per-device-token rate limit counters stored in DB.
    Replaces the in-memory _rate_store defaultdict.
    Window is a sliding 60-second bucket tracked by window_start.
    """
    __tablename__ = "rate_limits"
    __table_args__ = (
        Index("ix_rate_limits_token_window", "token", "window_start"),
    )

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token         = Column(String(255), nullable=False, index=True)
    request_count = Column(Integer, default=1, nullable=False)
    window_start  = Column(DateTime(timezone=True), nullable=False)
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Phase 3: RPC Commands ─────────────────────────────────────────────────────

class RpcCommandStatus(str, enum.Enum):
    PENDING   = "PENDING"
    SENT      = "SENT"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    TIMEOUT   = "TIMEOUT"


class RpcCommand(Base):
    """
    Commands sent from dashboard to devices.
    Stored in DB for audit trail and HTTP polling fallback.
    """
    __tablename__ = "rpc_commands"
    __table_args__ = (
        Index("ix_rpc_commands_device_status", "device_id", "status"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id    = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    method       = Column(String(255), nullable=False)   # e.g. "setValue", "toggle", "reboot"
    params       = Column(JSON, nullable=False, default=dict)
    status       = Column(String(20), nullable=False, default="PENDING")
    result       = Column(JSON, nullable=True)
    created_by   = Column(String(255), nullable=True)    # user_id who sent it
    sent_at      = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


# ── Phase 3: Widget Templates ─────────────────────────────────────────────────

class WidgetTemplate(Base):
    """
    Reusable widget configurations saved by users.
    A template captures widget_type + config so the same widget
    can be quickly added to multiple dashboards without reconfiguring.
    """
    __tablename__ = "widget_templates"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by  = Column(String(255), nullable=False)    # user_id
    name        = Column(String(255), nullable=False)
    widget_type = Column(String(50),  nullable=False)
    config      = Column(JSON, nullable=False, default=dict)
    is_public   = Column(Boolean, default=False)         # visible to all tenant users
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())


# ── Phase 3: Ingest Metrics ───────────────────────────────────────────────────

class IngestMetric(Base):
    """
    Rolling 1-minute ingest rate counters per tenant.
    Written on every ingest; read by /metrics endpoint.
    Old rows purged by the daily cleanup task.
    """
    __tablename__ = "ingest_metrics"
    __table_args__ = (
        Index("ix_ingest_metrics_tenant_ts", "tenant_id", "ts"),
    )

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    device_id = Column(UUID(as_uuid=True), nullable=False)
    ts        = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    key_count = Column(Integer, default=1)


# ── Phase 4: Tenant Quotas ────────────────────────────────────────────────────

class TenantQuota(Base):
    """
    Per-tenant resource limits. Overrides global defaults from settings.
    If a field is NULL, the system default from config.py is used.
    """
    __tablename__ = "tenant_quotas"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id            = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
                                  nullable=False, unique=True, index=True)
    max_devices          = Column(Integer, nullable=True)   # NULL = use DEFAULT_MAX_DEVICES
    max_dashboards       = Column(Integer, nullable=True)   # NULL = use DEFAULT_MAX_DASHBOARDS
    max_telemetry_rate   = Column(Integer, nullable=True)   # events/min, NULL = use DEFAULT
    plan                 = Column(String(50), default="free")  # free | pro | enterprise
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), onupdate=func.now())


# ── Phase 4: Audit Log ────────────────────────────────────────────────────────

class AuditLog(Base):
    """
    Immutable record of user actions for security and compliance.
    Written on: device CRUD, alarm ack/clear, user management, RPC sends.
    Never deleted — use archival rotation after 1 year.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_ts",   "tenant_id", "created_at"),
        Index("ix_audit_logs_user_action", "user_id",   "action"),
    )

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id     = Column(UUID(as_uuid=True), nullable=True)   # NULL = system action
    user_email  = Column(String(255), nullable=True)
    action      = Column(String(100), nullable=False)         # e.g. "device.create"
    resource    = Column(String(50),  nullable=True)          # e.g. "device"
    resource_id = Column(String(255), nullable=True)          # UUID of affected entity
    detail      = Column(JSON, nullable=True)                 # before/after or params
    ip_address  = Column(String(45),  nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# ── Phase 4: API Keys ─────────────────────────────────────────────────────────

class ApiKey(Base):
    """
    Long-lived API keys for server-to-server integrations.
    Alternative to JWT for non-interactive clients (scripts, dashboards, CI).
    Hashed before storage — raw key shown only once on creation.
    """
    __tablename__ = "api_keys"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id   = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    user_id     = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False)
    name        = Column(String(255), nullable=False)         # human label
    key_hash    = Column(String(255), nullable=False, unique=True)  # SHA-256 of raw key
    key_prefix  = Column(String(8),   nullable=False)         # first 8 chars for display
    is_active   = Column(Boolean, default=True)
    last_used_at= Column(DateTime(timezone=True), nullable=True)
    expires_at  = Column(DateTime(timezone=True), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# ── Phase 7: Anomaly Detection ────────────────────────────────────────────────

class AnomalyScore(Base):
    """
    Per-telemetry-point anomaly score computed by Z-score vs rolling baseline.
    Written by anomaly_service on every ingest after enough data exists.
    """
    __tablename__ = "anomaly_scores"
    __table_args__ = (
        Index("ix_anomaly_scores_device_key_ts", "device_id", "key", "ts"),
    )

    id        = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key       = Column(String(255), nullable=False)
    ts        = Column(DateTime(timezone=True), nullable=False, index=True)
    value     = Column(Float, nullable=False)
    z_score   = Column(Float, nullable=False)
    is_anomaly = Column(Boolean, default=False)
    baseline_mean   = Column(Float, nullable=True)
    baseline_stddev = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Phase 7: Device Baselines ─────────────────────────────────────────────────

class DeviceBaseline(Base):
    """
    Rolling statistical baseline per device/key/hour-of-day.
    Updated nightly by baseline_service.
    """
    __tablename__ = "device_baselines"
    __table_args__ = (
        UniqueConstraint("device_id", "key", "hour_of_day", name="uq_baseline_device_key_hour"),
        Index("ix_device_baselines_device_key", "device_id", "key"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id    = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key          = Column(String(255), nullable=False)
    hour_of_day  = Column(Integer, nullable=False)
    mean         = Column(Float, nullable=False)
    stddev       = Column(Float, nullable=False, default=0.0)
    min_val      = Column(Float, nullable=True)
    max_val      = Column(Float, nullable=True)
    sample_count = Column(Integer, default=0)
    suggested_upper = Column(Float, nullable=True)
    suggested_lower = Column(Float, nullable=True)
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Phase 7: Device Health Scores ─────────────────────────────────────────────

class DeviceHealthScore(Base):
    """
    Composite health score per device, updated hourly by health_service.
    Used for predictive maintenance and fleet overview.
    """
    __tablename__ = "device_health_scores"
    __table_args__ = (
        Index("ix_device_health_device_ts", "device_id", "scored_at"),
    )

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id       = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    scored_at       = Column(DateTime(timezone=True), nullable=False, index=True)
    uptime_score    = Column(Float, default=100.0)
    alarm_score     = Column(Float, default=100.0)
    stability_score = Column(Float, default=100.0)
    freshness_score = Column(Float, default=100.0)
    health_score    = Column(Float, default=100.0)
    health_label    = Column(String(20), default="HEALTHY")
    maintenance_due       = Column(Boolean, default=False)
    maintenance_reason    = Column(Text, nullable=True)
    predicted_failure_hrs = Column(Float, nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
