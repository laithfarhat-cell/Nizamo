"""Application configuration loaded via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Nizamo ERP"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Production defaults to PostgreSQL 16 via asyncpg; override with sqlite+aiosqlite for local sandbox/testing
    DATABASE_URL: str = "postgresql+asyncpg://nizamo:nizamo@postgres:5432/nizamo"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://nizamo:nizamo@postgres:5432/nizamo"

    JWT_SECRET: str = "nizamo-erp-enterprise-secret-change-in-production-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24

    DEFAULT_COMPANY_CODE: str = "NIZAMO"
    CORS_ORIGINS: list[str] = ["*"]


settings = Settings()
