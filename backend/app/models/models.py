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
    is_active  = Column(Boolean, default=True)
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
