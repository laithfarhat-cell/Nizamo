"""Production hardening: PostgreSQL immutability trigger on posted move lines, composite indexes & v2 tables.

Revision ID: 0002_production_hardening_and_v2
Revises: 0001_initial_erp_schema
Create Date: 2026-11-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import backend.core.tenancy  # noqa: F401
import backend.core.sequences  # noqa: F401
import backend.core.security  # noqa: F401
import backend.core.chatter  # noqa: F401
from backend.core.database import Base
from backend.core.registry import registry

registry.load_all()

revision: str = "0002_production_hardening_and_v2"
down_revision: Union[str, None] = "0001_initial_erp_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Create any newly added tables (account_bank_statement, account_bank_statement_line, account_fiscal_closing)
    Base.metadata.create_all(bind=bind)

    if bind.dialect.name == "postgresql":
        # Composite performance indexes for high-volume SQL financial reporting
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_aml_company_account_date
            ON account_move_line (company_id, account_id, date);
            """
        )
        op.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_move_company_state_date_type
            ON account_move (company_id, state, date, move_type);
            """
        )

        # Database-level Defense-in-Depth Immutability Trigger on POSTED AccountMoveLine rows:
        # Permits ONLY reconciliation residual updates (amount_residual, amount_residual_currency, reconciled, updated_at),
        # while strictly blocking ANY modification of debit, credit, balance, account_id, date, or deletion of posted rows!
        op.execute(
            """
            CREATE OR REPLACE FUNCTION nizamo_prevent_posted_aml_mutation()
            RETURNS TRIGGER AS $$
            DECLARE
                v_state VARCHAR(16);
            BEGIN
                SELECT state INTO v_state FROM account_move WHERE id = OLD.move_id;
                IF v_state = 'POSTED' THEN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'Immutable Ledger Trigger: Cannot DELETE account_move_line % belonging to POSTED move %', OLD.id, OLD.move_id;
                    END IF;
                    IF TG_OP = 'UPDATE' THEN
                        IF (NEW.account_id IS DISTINCT FROM OLD.account_id)
                           OR (NEW.debit IS DISTINCT FROM OLD.debit)
                           OR (NEW.credit IS DISTINCT FROM OLD.credit)
                           OR (NEW.balance IS DISTINCT FROM OLD.balance)
                           OR (NEW.amount_currency IS DISTINCT FROM OLD.amount_currency)
                           OR (NEW.currency_id IS DISTINCT FROM OLD.currency_id)
                           OR (NEW.date IS DISTINCT FROM OLD.date)
                           OR (NEW.move_id IS DISTINCT FROM OLD.move_id) THEN
                            RAISE EXCEPTION 'Immutable Ledger Trigger: Cannot modify financial fields on POSTED account_move_line % (move %)', OLD.id, OLD.move_id;
                        END IF;
                    END IF;
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_prevent_posted_aml_mutation ON account_move_line;"
        )
        op.execute(
            """
            CREATE TRIGGER trg_prevent_posted_aml_mutation
            BEFORE UPDATE OR DELETE ON account_move_line
            FOR EACH ROW
            EXECUTE FUNCTION nizamo_prevent_posted_aml_mutation();
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_prevent_posted_aml_mutation ON account_move_line;")
        op.execute("DROP FUNCTION IF EXISTS nizamo_prevent_posted_aml_mutation();")
    op.drop_table("account_fiscal_closing")
    op.drop_table("account_bank_statement_line")
    op.drop_table("account_bank_statement")
