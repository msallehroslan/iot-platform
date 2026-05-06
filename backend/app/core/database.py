from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


engine = create_engine(
    settings.DATABASE_URL,

    # Validate dead/stale connections automatically
    pool_pre_ping=True,

    # Increased pool for TAAT + dashboard concurrency
    pool_size=20,

    # Temporary overflow connections during spikes
    max_overflow=40,

    # Wait longer before failing connection requests
    pool_timeout=30,

    # Recycle stale Render/Postgres connections
    pool_recycle=1800,

    connect_args={
        "connect_timeout": 10,
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
