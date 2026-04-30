from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker  # declarative_base moved to orm in SQLAlchemy 2.0
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,   # reconnect transparently after idle timeouts (critical on Render)
    pool_size=3,          # Render free tier: max 25 connections total; keep per-worker count low
    max_overflow=7,       # burst headroom — max 10 conns per worker, safe with 1-2 Render workers
    pool_timeout=30,
    pool_recycle=1800,    # recycle connections every 30 min to avoid Render's idle TCP cutoff
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
