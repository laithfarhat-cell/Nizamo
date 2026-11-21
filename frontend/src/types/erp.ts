export type AppKey =
  | "accounting"
  | "invoicing"
  | "sales"
  | "purchase"
  | "inventory"
  | "contacts"
  | "configuration";

export type Locale = "en" | "ar";

export interface Company {
  id: number;
  code: string;
  name: string;
  name_ar: string | null;
  tax_id: string | null;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  fiscalyear_lock_date: string | null;
  tax_lock_date: string | null;
}

export interface UserInfo {
  id: number;
  login: string;
  full_name: string;
  email: string;
  roles: string[];
  active_company_id: number;
  allowed_company_ids: number[];
}

export interface Currency {
  id: number;
  code: string;
  name: string;
  symbol: string;
  decimal_places: number;
  latest_rate: string;
  rates: { id: number; effective_date: string; rate: string }[];
}

export interface Partner {
  id: number;
  name: string;
  name_ar: string | null;
  ref: string | null;
  email: string | null;
  phone: string | null;
  vat: string | null;
  address: string | null;
  is_customer: boolean;
  is_supplier: boolean;
}

export interface Account {
  id: number;
  code: string;
  name: string;
  name_ar: string | null;
  account_type: string;
  reconcilable: boolean;
  currency_id: number | null;
  parent_id: number | null;
}

export interface Journal {
  id: number;
  code: string;
  name: string;
  name_ar: string | null;
  journal_type: string;
  default_account_id: number | null;
  currency_id: number | null;
}

export interface JournalCard {
  journal_id: number;
  code: string;
  name: string;
  name_ar: string;
  journal_type: string;
  draft_count: number;
  unreconciled_count: number;
  open_balance: string;
}

export interface TaxRule {
  id: number;
  name: string;
  name_ar: string | null;
  tax_type: string;
  amount_type: string;
  amount: string;
  price_include: boolean;
  include_base_amount: boolean;
  sequence: number;
  tax_account_id: number;
}

export interface AnalyticAccount {
  id: number;
  code: string;
  name: string;
  partner_id: number | null;
}

export interface AccountMoveLine {
  id: number;
  account_id: number;
  account_code: string;
  account_name: string;
  partner_id: number | null;
  analytic_account_id: number | null;
  product_id: number | null;
  name: string;
  quantity: string;
  price_unit: string;
  discount: string;
  debit: string;
  credit: string;
  balance: string;
  currency_id: number;
  amount_currency: string;
  amount_residual: string;
  reconciled: boolean;
  tax_ids: number[];
  is_tax_line: boolean;
  display_type: string;
}

export interface AccountMove {
  id: number;
  name: string;
  ref: string | null;
  move_type: "entry" | "out_invoice" | "in_invoice" | "out_refund" | "in_refund";
  state: "DRAFT" | "POSTED" | "CANCELLED";
  date: string;
  invoice_date: string | null;
  invoice_date_due: string | null;
  journal_id: number;
  journal_name: string;
  journal_code: string;
  partner_id: number | null;
  partner_name: string;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  exchange_rate: string;
  amount_untaxed: string;
  amount_tax: string;
  amount_total: string;
  amount_residual: string;
  payment_state: "NOT_PAID" | "PARTIAL" | "IN_PAYMENT" | "PAID";
  reversed_entry_id: number | null;
  invoice_origin: string | null;
  narration: string | null;
  lines: AccountMoveLine[];
}

export interface ChatterMessage {
  id: number;
  res_model: string;
  res_id: number;
  message_type: string;
  body: string;
  author_name: string;
  created_at: string;
  tracking_values: {
    field_name: string;
    field_label: string;
    old_value: string;
    new_value: string;
  }[];
}

export interface SaleOrder {
  id: number;
  name: string;
  partner_id: number;
  partner_name: string;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  date_order: string;
  state: "DRAFT" | "CONFIRMED" | "DONE" | "CANCELLED";
  invoice_policy: string;
  amount_untaxed: string;
  amount_tax: string;
  amount_total: string;
  note: string | null;
  delivery_count: number;
  invoice_count: number;
  lines: {
    id: number;
    product_id: number | null;
    name: string;
    quantity: string;
    qty_delivered: string;
    qty_invoiced: string;
    price_unit: string;
    discount: string;
    tax_ids: number[];
    price_subtotal: string;
    price_tax: string;
    price_total: string;
  }[];
}

export interface PurchaseOrder {
  id: number;
  name: string;
  partner_id: number;
  partner_name: string;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  date_order: string;
  state: "DRAFT" | "CONFIRMED" | "DONE" | "CANCELLED";
  three_way_status: string;
  amount_untaxed: string;
  amount_tax: string;
  amount_total: string;
  note: string | null;
  receipt_count: number;
  bill_count: number;
  lines: {
    id: number;
    product_id: number | null;
    name: string;
    quantity: string;
    qty_received: string;
    qty_billed: string;
    price_unit: string;
    tax_ids: number[];
    price_subtotal: string;
    price_tax: string;
    price_total: string;
  }[];
}

export interface Product {
  id: number;
  default_code: string;
  name: string;
  name_ar: string | null;
  product_type: string;
  category_id: number;
  category_name: string;
  costing_method: string;
  income_account_id: number | null;
  expense_account_id: number | null;
  stock_input_account_id: number | null;
  list_price: string;
  standard_price: string;
  qty_on_hand: string;
  uom: string;
}

export interface StockPicking {
  id: number;
  name: string;
  picking_type: string;
  origin: string | null;
  partner_id: number | null;
  partner_name: string;
  location_name: string;
  location_dest_name: string;
  scheduled_date: string;
  date_done: string | null;
  state: string;
  moves: {
    id: number;
    product_id: number;
    product_sku: string;
    product_name: string;
    description: string;
    product_uom_qty: string;
    quantity_done: string;
    price_unit: string;
    state: string;
  }[];
}

export interface StockQuant {
  id: number;
  product_id: number;
  sku: string;
  product_name: string;
  location_name: string;
  location_usage: string;
  quantity: string;
  reserved_quantity: string;
  unit_cost: string;
  total_value: string;
}

export interface ValuationLayer {
  id: number;
  sku: string;
  product_name: string;
  quantity: string;
  remaining_qty: string;
  unit_cost: string;
  value: string;
  remaining_value: string;
  account_move_id: number | null;
  description: string;
  created_at: string;
}

export interface InstalledModule {
  technical_name: string;
  name: string;
  version: string;
  depends: string[];
  data: string[];
  installable: boolean;
  summary: string;
  category: string;
  load_rank: number;
}
