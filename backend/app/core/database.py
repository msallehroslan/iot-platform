from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


# ── Render free tier PostgreSQL — connection pool ────────────────────────────
#
# Render free PostgreSQL actual limit: ~97 connections (PostgreSQL default),
# but the free plan has limited RAM so practical safe limit is ~20 concurrent.
#
# Connection consumers in this single-process app (peak concurrent):
#   - Dashboard load (3 simultaneous HTTP requests from frontend)   = 3
#   - Telemetry ingest (2 ESP32s at 1Hz, ~50ms per request)        = 2
#   - Background tasks (intelligence, rpc, offline check)           = 1-2
#   - Peak total                                                     = ~7
#
# Auth cache (added to auth_deps.py) eliminates DB hit for most requests —
# only cache misses (first request per user per 60s) need a DB connection.
#
# Pool sizing:
#   pool_size=5    → 5 persistent connections (handles normal load)
#   max_overflow=5 → 5 burst connections (handles spikes, total cap = 10)
#   pool_timeout=10 → queue briefly on burst rather than failing immediately
engine = create_engine(
    settings.DATABASE_URL,

    pool_pre_ping=True,

    pool_size=10,
    max_overflow=20,
    pool_timeout=30,

    # Recycle every 30 minutes
    pool_recycle=1800,

    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=20000",
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
