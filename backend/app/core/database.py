from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


# ── Render free tier PostgreSQL — connection budget ──────────────────────────
#
# Render free PostgreSQL hard limit: 5 concurrent connections TOTAL.
#
# Connection consumers in this single-process app:
#   - FastAPI request handlers     (via get_db dependency)
#   - WebSocket auth               (SessionLocal on connect)
#   - MQTT message handlers        (SessionLocal on ingest)
#   - RealtimeCoordinator          (SessionLocal for anomaly memory writes)
#   - IntelligenceCoordinator      (SessionLocal every 10s per device)
#   - Scheduled RPC dispatcher     (SessionLocal every 5s)
#   - Offline checker              (SessionLocal every 2min)
#
# Budget allocation:
#   pool_size=2    → 2 persistent connections for API requests
#   max_overflow=2 → 2 burst connections (total cap = 4, leaving 1 for admin)
#   pool_timeout=5 → fail fast, don't queue — background tasks retry naturally
#
# Background tasks MUST open and close sessions within milliseconds.
# If they hold sessions across async awaits, pool exhaustion occurs.
engine = create_engine(
    settings.DATABASE_URL,

    pool_pre_ping=True,

    # 2 persistent + 2 overflow = 4 max. Leaves 1 slot for migrations/admin.
    pool_size=2,
    max_overflow=2,

    # 5 second timeout — background tasks that can't get a connection will
    # log and retry on next cycle rather than blocking API requests.
    pool_timeout=5,

    # Recycle every 10 minutes — Render drops idle PG connections around this mark
    pool_recycle=600,

    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=20000",  # 20s max per statement
    },
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    """
    FastAPI database dependency.

    Ensures every request properly closes DB sessions
    to prevent connection leaks.
    """

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()
