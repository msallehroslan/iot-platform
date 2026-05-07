from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


# ── Render free tier PostgreSQL connection limits ─────────────────────────────
# Free PostgreSQL on Render allows a MAXIMUM of 5 concurrent connections.
# pool_size=20 + max_overflow=40 = 60 attempted connections → instant exhaustion.
# Every request then queues for 30s (pool_timeout) before failing.
#
# Correct settings for Render free tier:
#   pool_size=3    → 3 persistent connections (leaves 2 for admin/migrations)
#   max_overflow=1 → 1 burst connection maximum (never exceeds 4 total)
#   pool_timeout=10 → fail fast rather than queue for 30s
#
# If you upgrade to Render's paid PostgreSQL (Standard plan), you can increase
# pool_size to 8 and max_overflow to 4.
engine = create_engine(
    settings.DATABASE_URL,

    # Validate dead/stale connections — essential for Render's idle-disconnect behavior
    pool_pre_ping=True,

    # Render free PostgreSQL: max 5 connections total across ALL processes.
    # With --workers 1 (single process), keep pool_size low to avoid exhaustion.
    pool_size=3,

    # Allow 1 burst connection during spikes (total cap = pool_size + max_overflow = 4)
    max_overflow=1,

    # Fail fast — don't make requests queue for 30s waiting for a connection slot
    pool_timeout=10,

    # Recycle connections every 10 minutes — Render drops idle connections after ~10min
    pool_recycle=600,

    connect_args={
        "connect_timeout": 10,
        # Reduce connection overhead on Render's internal network
        "options": "-c statement_timeout=25000",  # 25s statement timeout
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
