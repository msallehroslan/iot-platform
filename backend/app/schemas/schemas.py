from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List, Any, Dict
from datetime import datetime
from uuid import UUID
from enum import Enum


class DeviceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DISABLED = "DISABLED"


class AlarmSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    WARNING = "WARNING"
    INDETERMINATE = "INDETERMINATE"


class AlarmStatus(str, Enum):
    ACTIVE_UNACK = "ACTIVE_UNACK"
    ACTIVE_ACK = "ACTIVE_ACK"
    CLEARED_UNACK = "CLEARED_UNACK"
    CLEARED_ACK = "CLEARED_ACK"


# ── Tenant ────────────────────────────────────────────────────────────────────

class TenantBase(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    description: Optional[str] = None


class TenantCreate(TenantBase):
    pass


class TenantOut(TenantBase):
    id: UUID
    created_at: datetime
    class Config:
        from_attributes = True


# ── Customer ──────────────────────────────────────────────────────────────────

class CustomerBase(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None


class CustomerCreate(CustomerBase):
    tenant_id: UUID


class CustomerOut(CustomerBase):
    id: UUID
    tenant_id: UUID
    created_at: datetime
    class Config:
        from_attributes = True


# ── User ──────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: str
    password: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str = "TENANT_ADMIN"


class UserOut(BaseModel):
    id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str
    is_active: bool
    tenant_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    created_at: datetime
    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Device ────────────────────────────────────────────────────────────────────

class DeviceBase(BaseModel):
    name: str
    device_type: Optional[str] = "DEFAULT"
    label: Optional[str] = None
    description: Optional[str] = None
    additional_info: Optional[Dict[str, Any]] = None


class DeviceCreate(DeviceBase):
    tenant_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    device_type: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    status: Optional[DeviceStatus] = None
    additional_info: Optional[Dict[str, Any]] = None


# token included — all read routes require JWT + tenant ownership check
class DeviceOut(DeviceBase):
    id: UUID
    token: str
    status: DeviceStatus
    last_seen_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    tenant_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    class Config:
        from_attributes = True


class DeviceWithToken(DeviceOut):
    class Config:
        from_attributes = True


# ── Pagination ────────────────────────────────────────────────────────────────

# FIX 15: paginated response envelope
class PaginatedDevices(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[DeviceOut]


# ── Telemetry ─────────────────────────────────────────────────────────────────

class TelemetryValue(BaseModel):
    key: str
    value: Any
    ts: Optional[datetime] = None


# FIX 12: payload validation — max 50 keys, key max 64 chars
class TelemetryIngest(BaseModel):
    values: Dict[str, Any]
    ts: Optional[datetime] = None

    @field_validator("values")
    @classmethod
    def validate_values(cls, v):
        if not v:
            raise ValueError("values must not be empty")
        if len(v) > 50:
            raise ValueError("Maximum 50 keys per payload")
        for key in v:
            if len(key) > 64:
                raise ValueError(f"Key '{key[:20]}...' exceeds 64 character limit")
        return v


class TelemetryDataPoint(BaseModel):
    ts: datetime
    value: Any
    class Config:
        from_attributes = True


# FIX 14: bulk history request
class BulkHistoryRequest(BaseModel):
    keys: List[str]
    limit: int = 50

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, v):
        if not v:
            raise ValueError("keys must not be empty")
        if len(v) > 50:
            raise ValueError("Maximum 50 keys per bulk request")
        return v


class BulkHistoryResponse(BaseModel):
    data: Dict[str, List[TelemetryDataPoint]]


# ── Provisioning ──────────────────────────────────────────────────────────────

class ProvisionRequest(BaseModel):
    provision_key: str
    device_name: str
    device_type: str = "DEFAULT"
    label: Optional[str] = None


class ProvisionResponse(BaseModel):
    device_id: str
    name: str
    token: str
    status: str


class ProvisioningKeyOut(BaseModel):
    provisioning_key: str
    provision_endpoint: str


# ── Telemetry Key Metadata ────────────────────────────────────────────────────

class TelemetryKeyOut(BaseModel):
    key: str
    label: Optional[str] = None
    unit: Optional[str] = None
    data_type: str = "number"
    class Config:
        from_attributes = True


class TelemetryKeyUpdate(BaseModel):
    label: Optional[str] = None
    unit: Optional[str] = None
    data_type: Optional[str] = None


class LatestTelemetryOut(BaseModel):
    key: str
    value: Any
    ts: datetime
    class Config:
        from_attributes = True


# ── Alarms ────────────────────────────────────────────────────────────────────

class AlarmBase(BaseModel):
    alarm_type: str
    severity: AlarmSeverity = AlarmSeverity.WARNING
    details: Optional[Dict[str, Any]] = None
    propagate: bool = False


class AlarmCreate(AlarmBase):
    device_id: UUID


class AlarmOut(AlarmBase):
    id: UUID
    device_id: UUID
    status: AlarmStatus
    start_ts: datetime
    end_ts: Optional[datetime] = None
    ack_ts: Optional[datetime] = None
    clear_ts: Optional[datetime] = None
    ack_by: Optional[str] = None
    cleared_by: Optional[str] = None
    created_at: datetime
    class Config:
        from_attributes = True


class AlarmWithDevice(AlarmOut):
    device_name: Optional[str] = None
    class Config:
        from_attributes = True


# ── Dashboard Stats ───────────────────────────────────────────────────────────

class DashboardStats(BaseModel):
    total_devices: int
    active_devices: int
    active_alarms: int
    telemetry_today: int


# ── ThresholdRule ─────────────────────────────────────────────────────────────

class ThresholdRuleCreate(BaseModel):
    device_id: Optional[UUID] = None   # null = tenant-wide
    key: str
    condition: str = "gt"              # gt | lt | gte | lte | eq
    threshold: float
    severity: AlarmSeverity = AlarmSeverity.WARNING
    alarm_type: str
    is_active: bool = True

    @field_validator("condition")
    @classmethod
    def validate_condition(cls, v):
        if v not in ("gt", "lt", "gte", "lte", "eq"):
            raise ValueError("condition must be one of: gt, lt, gte, lte, eq")
        return v


class ThresholdRuleOut(ThresholdRuleCreate):
    id: UUID
    tenant_id: UUID
    created_at: datetime
    class Config:
        from_attributes = True


# ── Dashboard & Widget ────────────────────────────────────────────────────────

class WidgetPosition(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 2
    h: int = 3


class WidgetCreate(BaseModel):
    widget_type: str
    title: str = "Widget"
    config: Dict[str, Any] = {}
    position: WidgetPosition = WidgetPosition()


class WidgetUpdate(BaseModel):
    widget_type: Optional[str] = None
    title: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    position: Optional[WidgetPosition] = None


class WidgetOut(BaseModel):
    id: UUID
    dashboard_id: UUID
    widget_type: str
    title: str
    config: Dict[str, Any]
    position: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True


class DashboardCreate(BaseModel):
    name: str = "My Dashboard"
    description: Optional[str] = None
    is_default: bool = False


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None


class DashboardOut(BaseModel):
    id: UUID
    device_id: UUID
    name: str
    description: Optional[str] = None
    is_default: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    widgets: List[WidgetOut] = []
    class Config:
        from_attributes = True


class DashboardListItem(BaseModel):
    id: UUID
    device_id: UUID
    name: str
    description: Optional[str] = None
    is_default: bool
    created_at: datetime
    widget_count: int = 0
    class Config:
        from_attributes = True
