"""RBAC Security Engine, Model-Level CRUD Access Rights, Company Record Rules, and JWT Auth."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Table, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from backend.core.config import settings
from backend.core.database import Base, TimestampMixin, get_db
from backend.core.tenancy import ResCompany

ROLE_ADMIN = "System Admin"
ROLE_ACCOUNTANT = "Accountant"
ROLE_BILLING = "Billing Officer"
ROLE_SALES = "Sales User"
ROLE_INVENTORY = "Inventory Manager"

ALL_ROLES = [
    ROLE_ADMIN,
    ROLE_ACCOUNTANT,
    ROLE_BILLING,
    ROLE_SALES,
    ROLE_INVENTORY,
]

res_user_group_rel = Table(
    "res_user_group_rel",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("res_user.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("res_group.id", ondelete="CASCADE"), primary_key=True),
)

res_user_company_rel = Table(
    "res_user_company_rel",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("res_user.id", ondelete="CASCADE"), primary_key=True),
    Column("company_id", Integer, ForeignKey("res_company.id", ondelete="CASCADE"), primary_key=True),
)


class ResGroup(Base, TimestampMixin):
    """Security role group (e.g., Accountant, Billing Officer, Sales User, Inventory Manager, System Admin)."""

    __tablename__ = "res_group"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)

    access_rights: Mapped[list[IrModelAccess]] = relationship(
        "IrModelAccess", back_populates="group", cascade="all, delete-orphan"
    )


class ResUser(Base, TimestampMixin):
    """Authenticated ERP user with allowed companies and RBAC groups."""

    __tablename__ = "res_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    default_company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_company.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    groups: Mapped[list[ResGroup]] = relationship("ResGroup", secondary=res_user_group_rel, lazy="selectin")
    companies: Mapped[list[ResCompany]] = relationship(
        "ResCompany", secondary=res_user_company_rel, lazy="selectin"
    )

    @property
    def role_names(self) -> set[str]:
        return {g.name for g in self.groups}

    @property
    def allowed_company_ids(self) -> set[int]:
        ids = {c.id for c in self.companies}
        ids.add(self.default_company_id)
        return ids


class IrModelAccess(Base, TimestampMixin):
    """Odoo-style Model-level CRUD permissions per security group."""

    __tablename__ = "ir_model_access"
    __table_args__ = (UniqueConstraint("group_id", "model_name", name="uq_model_access_group_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("res_group.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    perm_read: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    perm_write: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    perm_create: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    perm_unlink: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    group: Mapped[ResGroup] = relationship("ResGroup", back_populates="access_rights")


class IrRule(Base, TimestampMixin):
    """Company-scoped record-level security rule."""

    __tablename__ = "ir_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    domain_description: Mapped[str] = mapped_column(
        String(256), default="[('company_id', 'in', user.allowed_company_ids)]", nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --- Password Hashing (PBKDF2-HMAC-SHA256) ---

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, salt, hex_digest = stored_hash.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
        return hmac.compare_digest(dk.hex(), hex_digest)
    except Exception:
        return False


# --- JWT Access & Refresh Token Helpers ---

def create_access_token(user_id: int, login: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "login": login, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: int, login: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "login": login, "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str, expected_type: str = "access") -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
        ) from exc
    token_type = payload.get("type", "access")
    if token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token type: expected '{expected_type}'",
        )
    return payload


# --- FastAPI Request Context & RBAC Dependencies ---

class RequestContext:
    """Resolved authenticated user and active company scope for every API call."""

    def __init__(self, user: ResUser, company_id: int) -> None:
        self.user = user
        self.company_id = company_id

    def has_role(self, *roles: str) -> bool:
        user_roles = self.user.role_names
        if ROLE_ADMIN in user_roles:
            return True
        return bool(user_roles.intersection(set(roles)))


_bearer = HTTPBearer(auto_error=False)


async def get_current_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    session: AsyncSession = Depends(get_db),
) -> RequestContext:
    """Resolve authenticated user (falling back to seeded admin in demo mode if no token header)
    and enforce multi-company isolation via X-Company-Id.
    """
    user: ResUser | None = None
    if credentials and credentials.credentials:
        payload = decode_access_token(credentials.credentials)
        user_id = int(payload["sub"])
        stmt = (
            select(ResUser)
            .options(selectinload(ResUser.groups), selectinload(ResUser.companies))
            .where(ResUser.id == user_id, ResUser.active.is_(True))
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    else:
        if not settings.ALLOW_DEMO_FALLBACK:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required. Supply a valid Bearer access token.",
            )
        # Seamless demo access using the default System Admin user when ALLOW_DEMO_FALLBACK=true
        stmt = (
            select(ResUser)
            .options(selectinload(ResUser.groups), selectinload(ResUser.companies))
            .where(ResUser.active.is_(True))
            .order_by(ResUser.id.asc())
            .limit(1)
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authenticated user available. Run scripts/seed_erp.py first.",
            )

    company_id = x_company_id if x_company_id is not None else user.default_company_id
    if ROLE_ADMIN not in user.role_names and company_id not in user.allowed_company_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Record Rule Violation: user '{user.login}' has no access to company_id={company_id}",
        )
    return RequestContext(user=user, company_id=company_id)


async def check_model_permission(
    session: AsyncSession,
    user: ResUser,
    model_name: str,
    operation: str,
) -> bool:
    """Check `ir_model_access` CRUD right (`read`, `write`, `create`, `unlink`) for a user."""
    if ROLE_ADMIN in user.role_names:
        return True
    group_ids = [g.id for g in user.groups]
    if not group_ids:
        return False

    col_map = {
        "read": IrModelAccess.perm_read,
        "write": IrModelAccess.perm_write,
        "create": IrModelAccess.perm_create,
        "unlink": IrModelAccess.perm_unlink,
    }
    perm_col = col_map[operation]
    stmt = select(IrModelAccess).where(
        IrModelAccess.group_id.in_(group_ids),
        IrModelAccess.model_name == model_name,
        perm_col.is_(True),
    )
    allowed = (await session.execute(stmt)).scalars().first()
    return allowed is not None


def require_permission(model_name: str, operation: str) -> Callable:
    """FastAPI dependency enforcing model-level CRUD permission."""

    async def _dependency(
        ctx: RequestContext = Depends(get_current_context),
        session: AsyncSession = Depends(get_db),
    ) -> RequestContext:
        allowed = await check_model_permission(session, ctx.user, model_name, operation)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access Denied: missing '{operation}' right on model '{model_name}'",
            )
        return ctx

    return _dependency
