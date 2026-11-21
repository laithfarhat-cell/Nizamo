"""Initial Nizamo ERP schema across Core, Accounting, Inventory, Sales, and Purchase modules.

Revision ID: 0001_initial_erp_schema
Revises:
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Load complete ORM metadata from core + registry modules
import backend.core.tenancy  # noqa: F401
import backend.core.sequences  # noqa: F401
import backend.core.security  # noqa: F401
import backend.core.chatter  # noqa: F401
from backend.core.database import Base
from backend.core.registry import registry

registry.load_all()

revision: str = "0001_initial_erp_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
