"""Pure Decimal Tax Calculation Engine (Percent, Fixed, Tax-Included, Compound/Cascading)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Protocol

from backend.core.decimal_utils import D, HUNDRED, ONE, ZERO, quantize_currency


class TaxRuleProtocol(Protocol):
    id: int
    name: str
    amount_type: str  # PERCENT | FIXED
    amount: Decimal
    price_include: bool
    include_base_amount: bool
    sequence: int
    tax_account_id: int
    refund_account_id: int | None


@dataclass
class ComputedTaxLine:
    tax_id: int
    name: str
    account_id: int
    base: Decimal
    amount: Decimal


@dataclass
class TaxComputationResult:
    total_excluded: Decimal
    total_included: Decimal
    total_tax: Decimal
    tax_lines: list[ComputedTaxLine] = field(default_factory=list)


def compute_line_taxes(
    *,
    price_unit: Decimal,
    quantity: Decimal,
    discount: Decimal = ZERO,
    taxes: Iterable[TaxRuleProtocol] = (),
    currency_precision: int = 2,
    is_refund: bool = False,
) -> TaxComputationResult:
    """Calculate exact line subtotal and taxes using fixed-point Decimal + ROUND_HALF_UP.

    Supports:
      - Percentage taxes (`PERCENT`), e.g. 15% VAT
      - Fixed per-unit taxes (`FIXED`), e.g. $2.50/unit excise
      - Tax-included (`price_include=True`) vs tax-excluded (`price_include=False`)
      - Cascading / compound taxes (`include_base_amount=True`), evaluated in `sequence` order
    """
    qty = D(quantity)
    unit = D(price_unit)
    disc_factor = ONE - (D(discount) / HUNDRED)
    raw_gross = quantize_currency(qty * unit * disc_factor, currency_precision)

    ordered_taxes = sorted(taxes, key=lambda t: (t.sequence, t.id))
    if not ordered_taxes:
        return TaxComputationResult(
            total_excluded=raw_gross,
            total_included=raw_gross,
            total_tax=ZERO,
            tax_lines=[],
        )

    # Step 1: Extract tax-excluded base if any taxes are price_include=True
    included_taxes = [t for t in ordered_taxes if t.price_include]
    if included_taxes:
        # Walk backwards through included taxes to peel off compound & standard included taxes cleanly
        base = raw_gross
        # Separate fixed included vs percent included (handle cascading in reverse sequence)
        # First handle non-cascading total percent/fixed together, or exact reverse chain:
        fixed_inc_total = ZERO
        pct_chain_factor = ONE
        # Group: non-compound included percent taxes sum linearly; compound ones multiply
        non_compound_pct = ZERO
        compound_pcts: list[Decimal] = []
        for t in included_taxes:
            if t.amount_type == "FIXED":
                fixed_inc_total += D(t.amount) * qty
            elif t.include_base_amount:
                compound_pcts.append(D(t.amount) / HUNDRED)
            else:
                non_compound_pct += D(t.amount) / HUNDRED

        # Base after peeling fixed included taxes
        base = raw_gross - fixed_inc_total
        for cp in reversed(compound_pcts):
            pct_chain_factor *= ONE + cp
        total_factor = pct_chain_factor * (ONE + non_compound_pct)
        if total_factor != ZERO:
            base = (base / total_factor).quantize(
                Decimal(f"0.{'0' * (currency_precision - 1)}1") if currency_precision > 0 else Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
    else:
        base = raw_gross

    # Step 2: Forward pass in sequence order computing each tax and updating cascading base
    running_base = base
    computed_lines: list[ComputedTaxLine] = []
    total_tax = ZERO

    for t in ordered_taxes:
        if t.amount_type == "PERCENT":
            tax_amt = quantize_currency(running_base * (D(t.amount) / HUNDRED), currency_precision)
        elif t.amount_type == "FIXED":
            tax_amt = quantize_currency(D(t.amount) * qty, currency_precision)
        else:
            raise ValueError(f"Unsupported tax amount_type: {t.amount_type}")

        acct_id = (t.refund_account_id or t.tax_account_id) if is_refund else t.tax_account_id
        computed_lines.append(
            ComputedTaxLine(
                tax_id=t.id,
                name=t.name,
                account_id=acct_id,
                base=running_base,
                amount=tax_amt,
            )
        )
        total_tax += tax_amt
        if t.include_base_amount:
            running_base = quantize_currency(running_base + tax_amt, currency_precision)

    # Reconcile 1-cent rounding drift when all taxes are price-included
    if included_taxes and all(t.price_include for t in ordered_taxes):
        total_included = raw_gross
        total_excluded = quantize_currency(total_included - total_tax, currency_precision)
    else:
        total_excluded = quantize_currency(base, currency_precision)
        total_included = quantize_currency(total_excluded + total_tax, currency_precision)

    return TaxComputationResult(
        total_excluded=total_excluded,
        total_included=total_included,
        total_tax=quantize_currency(total_tax, currency_precision),
        tax_lines=computed_lines,
    )
