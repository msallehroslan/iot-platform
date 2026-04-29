from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # pydantic-settings reads these directly from environment variables.
    # Fallback values are used for local development only.
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/iotdb"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    # Comma-separated list of allowed CORS origins.
    # Example: "https://myapp.onrender.com,http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
