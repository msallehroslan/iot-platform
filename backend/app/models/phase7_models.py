"""
Phase 7 Intelligence Models — append these to models.py
"""

# ── Phase 7: Anomaly Scores ───────────────────────────────────────────────────

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
    z_score   = Column(Float, nullable=False)          # standard deviations from mean
    is_anomaly = Column(Boolean, default=False)        # True if |z_score| > threshold
    baseline_mean   = Column(Float, nullable=True)
    baseline_stddev = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── Phase 7: Device Baselines ─────────────────────────────────────────────────

class DeviceBaseline(Base):
    """
    Rolling statistical baseline per device/key/hour-of-day.
    Updated nightly by baseline_service.
    Used for anomaly detection and adaptive threshold suggestions.
    """
    __tablename__ = "device_baselines"
    __table_args__ = (
        UniqueConstraint("device_id", "key", "hour_of_day", name="uq_baseline_device_key_hour"),
        Index("ix_device_baselines_device_key", "device_id", "key"),
    )

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id    = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    key          = Column(String(255), nullable=False)
    hour_of_day  = Column(Integer, nullable=False)     # 0–23, NULL = all-hours aggregate
    mean         = Column(Float, nullable=False)
    stddev       = Column(Float, nullable=False, default=0.0)
    min_val      = Column(Float, nullable=True)
    max_val      = Column(Float, nullable=True)
    sample_count = Column(Integer, default=0)          # how many points used
    suggested_upper = Column(Float, nullable=True)     # mean + 3*stddev
    suggested_lower = Column(Float, nullable=True)     # mean - 3*stddev
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Phase 7: Device Health Scores ─────────────────────────────────────────────

class DeviceHealthScore(Base):
    """
    Composite health score per device, updated hourly.
    Tracks uptime %, alarm frequency, trend volatility, and data freshness.
    Used for predictive maintenance and fleet overview.
    """
    __tablename__ = "device_health_scores"
    __table_args__ = (
        Index("ix_device_health_device_ts", "device_id", "scored_at"),
    )

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id       = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    scored_at       = Column(DateTime(timezone=True), nullable=False, index=True)
    # Component scores (0–100 each)
    uptime_score    = Column(Float, default=100.0)     # based on online/offline ratio last 24h
    alarm_score     = Column(Float, default=100.0)     # penalised per alarm severity
    stability_score = Column(Float, default=100.0)     # based on trend volatility
    freshness_score = Column(Float, default=100.0)     # based on last_seen_at age
    # Composite
    health_score    = Column(Float, default=100.0)     # weighted average of above
    health_label    = Column(String(20), default="HEALTHY")  # HEALTHY/WARNING/CRITICAL/UNKNOWN
    # Maintenance prediction
    maintenance_due       = Column(Boolean, default=False)
    maintenance_reason    = Column(Text, nullable=True)
    predicted_failure_hrs = Column(Float, nullable=True)  # hours until predicted issue
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
