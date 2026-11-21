"""Immutable Audit Trail & Document Chatter (`mail_message` & `mail_tracking_value`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from backend.core.database import Base, CompanyScopedMixin, utcnow


class MailMessage(Base, CompanyScopedMixin):
    """Immutable audit log entry, state transition notification, or user comment on any ERP document."""

    __tablename__ = "mail_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    res_model: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    res_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(
        String(32), default="notification", nullable=False
    )  # 'comment', 'notification', 'state_change'
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author_name: Mapped[str] = mapped_column(String(128), default="System", nullable=False)
    author_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("res_user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False, index=True
    )

    tracking_values: Mapped[list[MailTrackingValue]] = relationship(
        "MailTrackingValue",
        back_populates="message",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class MailTrackingValue(Base):
    """Immutable field-level diff (`Old Value -> New Value`) attached to a MailMessage."""

    __tablename__ = "mail_tracking_value"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("mail_message.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    field_label: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    new_value: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    message: Mapped[MailMessage] = relationship("MailMessage", back_populates="tracking_values")


async def log_chatter(
    session: AsyncSession,
    *,
    company_id: int,
    res_model: str,
    res_id: int,
    body: str,
    message_type: str = "notification",
    author_name: str = "System",
    author_id: int | None = None,
    diffs: list[tuple[str, str, Any, Any]] | None = None,
) -> MailMessage:
    """Append an immutable audit trail entry with optional field-level diffs.

    Each diff tuple is `(field_name, field_label, old_val, new_val)`.
    Only diffs where `str(old_val) != str(new_val)` are persisted.
    """
    msg = MailMessage(
        company_id=company_id,
        res_model=res_model,
        res_id=res_id,
        message_type=message_type,
        body=body,
        author_name=author_name,
        author_id=author_id,
    )
    if diffs:
        for field_name, field_label, old_val, new_val in diffs:
            old_s = "" if old_val is None else str(old_val)
            new_s = "" if new_val is None else str(new_val)
            if old_s != new_s:
                msg.tracking_values.append(
                    MailTrackingValue(
                        field_name=field_name,
                        field_label=field_label,
                        old_value=old_s,
                        new_value=new_s,
                    )
                )
    session.add(msg)
    await session.flush()
    return msg


async def get_document_chatter(
    session: AsyncSession,
    *,
    company_id: int,
    res_model: str,
    res_id: int,
) -> list[dict[str, Any]]:
    """Fetch chronological audit trail & comments for a document."""
    stmt = (
        select(MailMessage)
        .options(selectinload(MailMessage.tracking_values))
        .where(
            MailMessage.company_id == company_id,
            MailMessage.res_model == res_model,
            MailMessage.res_id == res_id,
        )
        .order_by(MailMessage.created_at.desc(), MailMessage.id.desc())
    )
    messages = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": m.id,
            "res_model": m.res_model,
            "res_id": m.res_id,
            "message_type": m.message_type,
            "body": m.body,
            "author_name": m.author_name,
            "created_at": m.created_at.isoformat(),
            "tracking_values": [
                {
                    "field_name": tv.field_name,
                    "field_label": tv.field_label,
                    "old_value": tv.old_value,
                    "new_value": tv.new_value,
                }
                for tv in m.tracking_values
            ],
        }
        for m in messages
    ]
