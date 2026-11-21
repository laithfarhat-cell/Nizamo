"""Atomic Gapless Document Sequence Generator with SELECT ... FOR UPDATE locking."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base, CompanyScopedMixin, TimestampMixin, apply_for_update, atomic_lock


class IrSequence(Base, TimestampMixin, CompanyScopedMixin):
    """Configurable gapless document sequence (e.g. INV/2026/00001, SO/2026/00014)."""

    __tablename__ = "ir_sequence"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_sequence_company_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    suffix: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    padding: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    number_next: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    number_increment: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def format_number(self, number: int, ref_date: date | None = None) -> str:
        d = ref_date or date.today()
        prefix = self.prefix.replace("%(year)s", f"{d.year:04d}").replace("%(month)s", f"{d.month:02d}")
        suffix = self.suffix.replace("%(year)s", f"{d.year:04d}").replace("%(month)s", f"{d.month:02d}")
        return f"{prefix}{number:0{self.padding}d}{suffix}"


async def next_sequence_by_id(
    session: AsyncSession, sequence_id: int, ref_date: date | None = None
) -> str:
    """Atomically lock the sequence row (`SELECT ... FOR UPDATE`), increment, and format."""
    async with atomic_lock(session):
        stmt = apply_for_update(
            select(IrSequence).where(IrSequence.id == sequence_id),
            session,
        )
        seq = (await session.execute(stmt)).scalar_one_or_none()
        if seq is None:
            raise ValueError(f"Sequence id={sequence_id} not found")
        current = seq.number_next
        seq.number_next = current + seq.number_increment
        await session.flush()
        return seq.format_number(current, ref_date)


async def next_sequence_by_code(
    session: AsyncSession,
    *,
    company_id: int,
    code: str,
    ref_date: date | None = None,
    default_prefix: str | None = None,
    default_padding: int = 5,
) -> str:
    """Atomically fetch next gapless number for `(company_id, code)` with row locking."""
    async with atomic_lock(session):
        stmt = apply_for_update(
            select(IrSequence).where(
                IrSequence.company_id == company_id,
                IrSequence.code == code,
            ),
            session,
        )
        seq = (await session.execute(stmt)).scalar_one_or_none()
        if seq is None:
            prefix = default_prefix if default_prefix is not None else f"{code.upper()}/%(year)s/"
            seq = IrSequence(
                company_id=company_id,
                code=code,
                name=f"Auto Sequence {code}",
                prefix=prefix,
                suffix="",
                padding=default_padding,
                number_next=1,
                number_increment=1,
            )
            session.add(seq)
            await session.flush()
        current = seq.number_next
        seq.number_next = current + seq.number_increment
        await session.flush()
        return seq.format_number(current, ref_date)
