"""Async SQLAlchemy 2.0 database engine, session management, base mixins, and row-locking helpers."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import DateTime, ForeignKey, Integer, Select, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.core.config import settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Declarative base shared across all ERP kernel and plugin models."""

    pass


class TimestampMixin:
    """Standard audit timestamps on every ERP model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class CompanyScopedMixin:
    """Multi-company tenant isolation column referencing res_company."""

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_company.id", ondelete="RESTRICT"), nullable=False, index=True
    )


# Fallback in-process reentrant lock used ONLY when running on SQLite
# (PostgreSQL enforces native row-level `SELECT ... FOR UPDATE` locks).
_sqlite_write_lock = asyncio.Lock()
_sqlite_lock_depth: ContextVar[int] = ContextVar("_sqlite_lock_depth", default=0)


def create_engine_from_url(url: str) -> AsyncEngine:
    connect_args = {}
    kwargs: dict = {"echo": settings.DEBUG, "future": True}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        connect_args["timeout"] = 30
        kwargs["connect_args"] = connect_args
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    return create_async_engine(url, **kwargs)


engine: AsyncEngine = create_engine_from_url(settings.DATABASE_URL)
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


def configure_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Rebind global engine and sessionmaker (used by scripts and tests)."""
    global engine, AsyncSessionLocal
    engine = create_engine_from_url(database_url)
    AsyncSessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    return engine, AsyncSessionLocal


def apply_for_update(stmt: Select, session: AsyncSession) -> Select:
    """Apply PostgreSQL SELECT ... FOR UPDATE row lock to a query.

    On PostgreSQL, emits native `FOR UPDATE`. On SQLite (dev/test fallback), returns the
    query safely without dialect error while `atomic_lock` serializes critical sections.
    """
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"
    if dialect_name == "postgresql":
        return stmt.with_for_update()
    return stmt


@asynccontextmanager
async def atomic_lock(session: AsyncSession) -> AsyncGenerator[None, None]:
    """Serialize critical sections safely across PostgreSQL (row locks) and SQLite (reentrant asyncio lock)."""
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"
    if dialect_name == "sqlite":
        depth = _sqlite_lock_depth.get()
        if depth > 0:
            _sqlite_lock_depth.set(depth + 1)
            try:
                yield
            finally:
                _sqlite_lock_depth.set(depth)
        else:
            async with _sqlite_write_lock:
                _sqlite_lock_depth.set(1)
                try:
                    yield
                finally:
                    _sqlite_lock_depth.set(0)
    else:
        yield


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session with commit/rollback lifecycle."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
