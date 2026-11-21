"""Official Invoice / Credit Note PDF Generator with ZATCA Phase-1 Base64 TLV QR Code."""

from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from decimal import Decimal

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.decimal_utils import D, quantize_currency
from backend.core.tenancy import ResCompany, ResCurrency, ResPartner
from backend.modules.accounting.models import AccountAccount, AccountMove


def _tlv(tag: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 255:
        raw = raw[:255]
    return bytes([tag, len(raw)]) + raw


def encode_zatca_tlv_base64(
    *,
    seller_name: str,
    vat_number: str,
    timestamp_iso: str,
    invoice_total: Decimal | str,
    vat_total: Decimal | str,
) -> str:
    """Encode ZATCA Phase-1 e-Invoicing QR payload (Tags 1–5) as Base64 TLV bytes.

    Tag 1: Seller Name
    Tag 2: Seller VAT Registration Number
    Tag 3: Invoice Timestamp (ISO 8601 UTC)
    Tag 4: Invoice Total (with VAT)
    Tag 5: VAT Total
    """
    payload = b"".join(
        [
            _tlv(1, seller_name),
            _tlv(2, vat_number or "N/A"),
            _tlv(3, timestamp_iso),
            _tlv(4, f"{quantize_currency(D(invoice_total), 2):.2f}"),
            _tlv(5, f"{quantize_currency(D(vat_total), 2):.2f}"),
        ]
    )
    return base64.b64encode(payload).decode("ascii")


def decode_zatca_tlv_base64(b64_payload: str) -> dict[int, str]:
    """Decode a Base64 ZATCA TLV string back into `{tag: value}` for verification/testing."""
    raw = base64.b64decode(b64_payload.encode("ascii"))
    idx = 0
    out: dict[int, str] = {}
    while idx + 2 <= len(raw):
        tag = raw[idx]
        length = raw[idx + 1]
        val = raw[idx + 2 : idx + 2 + length].decode("utf-8")
        out[tag] = val
        idx += 2 + length
    return out


async def generate_invoice_pdf(session: AsyncSession, move: AccountMove) -> tuple[bytes, str]:
    """Render an official A4 Invoice / Credit Note / Bill PDF embedding a ZATCA TLV QR Code.

    Returns `(pdf_bytes, zatca_qr_base64)`.
    """
    company = await session.get(ResCompany, move.company_id)
    partner = await session.get(ResPartner, move.partner_id) if move.partner_id else None
    currency = await session.get(ResCurrency, move.currency_id)
    curr_code = currency.code if currency else "USD"
    curr_sym = currency.symbol if currency else "$"

    acct_ids = {l.account_id for l in move.lines}
    accts = (
        await session.execute(select(AccountAccount).where(AccountAccount.id.in_(acct_ids)))
    ).scalars().all() if acct_ids else []
    acct_map = {a.id: a for a in accts}

    ts_iso = datetime(
        move.date.year, move.date.month, move.date.day, 12, 0, 0, tzinfo=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    qr_b64 = encode_zatca_tlv_base64(
        seller_name=company.name if company else "Nizamo ERP",
        vat_number=(company.tax_id if company else "") or "N/A",
        timestamp_iso=ts_iso,
        invoice_total=move.amount_total,
        vat_total=move.amount_tax,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#714B67"),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle("InvMeta", parent=styles["Normal"], fontSize=8.5, leading=12)
    bold_style = ParagraphStyle("InvBold", parent=styles["Normal"], fontSize=9, fontName="Helvetica-Bold")

    doc_label_map = {
        "out_invoice": "TAX INVOICE",
        "out_refund": "CREDIT NOTE",
        "in_invoice": "VENDOR BILL",
        "in_refund": "VENDOR CREDIT NOTE",
        "entry": "JOURNAL VOUCHER",
    }
    doc_label = doc_label_map.get(move.move_type, "ACCOUNTING DOCUMENT")

    # Build QR graphic widget
    qr_widget = qr.QrCodeWidget(qr_b64)
    bounds = qr_widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    qr_size = 30 * mm
    qr_drawing = Drawing(qr_size, qr_size, transform=[qr_size / w, 0, 0, qr_size / h, 0, 0])
    qr_drawing.add(qr_widget)

    # Header block: Company Info (Left) + QR Code & Doc Title (Right)
    company_html = (
        f"<b>{company.name if company else 'Nizamo ERP'}</b><br/>"
        f"Tax / VAT ID: {(company.tax_id if company else None) or 'N/A'}<br/>"
        f"{(company.address if company else None) or ''}<br/>"
        f"{(company.email if company else None) or ''} | {(company.phone if company else None) or ''}"
    )
    doc_html = (
        f"<b>{doc_label}</b><br/>"
        f"Number: <b>{move.name}</b><br/>"
        f"Date: {move.date.isoformat()}<br/>"
        f"Due Date: {move.invoice_date_due.isoformat() if move.invoice_date_due else move.date.isoformat()}<br/>"
        f"Status: <b>{move.state} / {move.payment_state}</b>"
    )

    header_table = Table(
        [[Paragraph(company_html, meta_style), Paragraph(doc_html, meta_style), qr_drawing]],
        colWidths=[78 * mm, 66 * mm, 34 * mm],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )

    elements = [
        Paragraph(f"{doc_label} — {move.name}", title_style),
        header_table,
        Spacer(1, 10),
    ]

    # Partner & Currency metadata strip
    partner_html = (
        f"<b>Bill To / Counterparty:</b> {partner.name if partner else 'General Ledger'}<br/>"
        f"VAT: {(partner.vat if partner else None) or '—'} | Ref: {move.ref or move.invoice_origin or '—'}<br/>"
        f"Address: {(partner.address if partner else None) or '—'}"
    )
    curr_html = (
        f"<b>Currency:</b> {curr_code} ({curr_sym})<br/>"
        f"<b>Effective FX Rate:</b> {move.exchange_rate}<br/>"
        f"<b>ZATCA E-Invoice QR:</b> Embedded"
    )
    info_table = Table(
        [[Paragraph(partner_html, meta_style), Paragraph(curr_html, meta_style)]],
        colWidths=[116 * mm, 62 * mm],
    )
    info_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elements.extend([info_table, Spacer(1, 12)])

    # Line Items Table
    if move.move_type == "entry":
        rows = [["Account", "Description", "Debit", "Credit"]]
        for l in move.lines:
            acct = acct_map.get(l.account_id)
            rows.append(
                [
                    f"{acct.code} {acct.name}" if acct else str(l.account_id),
                    l.name[:55],
                    f"{quantize_currency(l.debit, 2):,.2f}",
                    f"{quantize_currency(l.credit, 2):,.2f}",
                ]
            )
        col_widths = [54 * mm, 68 * mm, 28 * mm, 28 * mm]
    else:
        rows = [["Account", "Description", "Qty", "Unit Price", f"Subtotal ({curr_code})"]]
        product_lines = [l for l in move.lines if l.display_type == "product"]
        for l in product_lines:
            acct = acct_map.get(l.account_id)
            sub = abs(D(l.amount_currency))
            rows.append(
                [
                    f"{acct.code}" if acct else "",
                    l.name[:55],
                    f"{quantize_currency(l.quantity, 2):,.2f}",
                    f"{curr_sym}{quantize_currency(l.price_unit, 2):,.2f}",
                    f"{curr_sym}{quantize_currency(sub, 2):,.2f}",
                ]
            )
        col_widths = [24 * mm, 76 * mm, 20 * mm, 28 * mm, 30 * mm]

    lines_table = Table(rows, colWidths=col_widths, repeatRows=1)
    lines_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#714B67")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.extend([lines_table, Spacer(1, 12)])

    # Totals summary box (right aligned)
    if move.move_type != "entry":
        totals_data = [
            ["", "Untaxed Subtotal:", f"{curr_sym}{quantize_currency(move.amount_untaxed, 2):,.2f}"],
            ["", "Tax / VAT:", f"{curr_sym}{quantize_currency(move.amount_tax, 2):,.2f}"],
            ["", "Total Amount:", f"{curr_sym}{quantize_currency(move.amount_total, 2):,.2f}"],
            ["", "Amount Due (Residual):", f"{curr_sym}{quantize_currency(move.amount_residual, 2):,.2f}"],
        ]
        totals_table = Table(totals_data, colWidths=[96 * mm, 44 * mm, 38 * mm])
        totals_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("FONTNAME", (1, 2), (-1, 2), "Helvetica-Bold"),
                    ("FONTSIZE", (1, 0), (-1, -1), 9),
                    ("LINEABOVE", (1, 2), (-1, 2), 0.8, colors.HexColor("#714B67")),
                    ("TEXTCOLOR", (1, 3), (-1, 3), colors.HexColor("#017E84")),
                    ("TOPPADDING", (1, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (1, 0), (-1, -1), 3),
                ]
            )
        )
        elements.append(totals_table)

    elements.append(Spacer(1, 18))
    elements.append(
        Paragraph(
            f"Generated electronically by <b>Nizamo ERP Double-Entry Engine</b> · ZATCA TLV Base64: "
            f"<font size=7 color='#64748b'>{qr_b64[:64]}...</font>",
            meta_style,
        )
    )

    doc.build(elements)
    return buf.getvalue(), qr_b64
