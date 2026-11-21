import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { FileCheck2, FileText, Plus, Trash2, Truck } from "lucide-react";
import { apiRequest, formatMoney } from "../api/client";
import type {
  Locale,
  Partner,
  Product,
  PurchaseOrder,
  SaleOrder,
  TaxRule,
} from "../types/erp";
import { t } from "../i18n/translations";
import { FormAction, FormView } from "./FormView";
import { ListView } from "./ListView";

interface OrdersWorkspaceProps {
  mode: "sales" | "purchase";
  locale: Locale;
  onJumpToInvoice: (moveId: number) => void;
  onJumpToInventory: () => void;
}

export const OrdersWorkspace: React.FC<OrdersWorkspaceProps> = ({
  mode,
  locale,
  onJumpToInvoice,
  onJumpToInventory,
}) => {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const endpoint = mode === "sales" ? "/sales/orders" : "/purchase/orders";
  const { data: orders = [], isLoading } = useQuery<any[]>({
    queryKey: [mode, "orders"],
    queryFn: () => apiRequest<any[]>(endpoint),
  });
  const { data: partners = [] } = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: () => apiRequest<Partner[]>("/core/partners"),
  });
  const { data: products = [] } = useQuery<Product[]>({
    queryKey: ["products"],
    queryFn: () => apiRequest<Product[]>("/inventory/products"),
  });
  const { data: taxes = [] } = useQuery<TaxRule[]>({
    queryKey: ["taxes"],
    queryFn: () => apiRequest<TaxRule[]>("/accounting/taxes"),
  });

  const activeOrder = useMemo(
    () => orders.find((o) => o.id === selectedId) ?? null,
    [orders, selectedId]
  );

  // Create form state
  const today = new Date().toISOString().slice(0, 10);
  const [partnerId, setPartnerId] = useState<number>(0);
  const [orderDate, setOrderDate] = useState(today);
  const [lines, setLines] = useState<
    { product_id: number; name: string; quantity: string; price_unit: string; tax_ids: number[] }[]
  >([{ product_id: 0, name: "", quantity: "1", price_unit: "1000.00", tax_ids: [] }]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: [mode, "orders"] });
    queryClient.invalidateQueries({ queryKey: ["pickings"] });
    queryClient.invalidateQueries({ queryKey: ["moves"] });
    queryClient.invalidateQueries({ queryKey: ["chatter"] });
  };

  const createMutation = useMutation({
    mutationFn: () => {
      const filteredPartners = partners.filter((p) =>
        mode === "sales" ? p.is_customer : p.is_supplier
      );
      const pid = partnerId || filteredPartners[0]?.id || partners[0]?.id;
      const defaultProd = products[0];
      return apiRequest<any>(endpoint, {
        method: "POST",
        body: JSON.stringify({
          partner_id: pid,
          date_order: orderDate,
          lines: lines.map((l) => {
            const p = products.find((pr) => pr.id === l.product_id) ?? defaultProd;
            return {
              product_id: p?.id ?? null,
              name: l.name || p?.name || "Item",
              quantity: l.quantity || "1",
              price_unit: l.price_unit || (mode === "sales" ? p?.list_price : p?.standard_price) || "0",
              tax_ids: l.tax_ids,
            };
          }),
        }),
      });
    },
    onSuccess: (created) => {
      setCreating(false);
      setErrorBanner(null);
      invalidate();
      setSelectedId(created.id);
    },
    onError: (err: Error) => setErrorBanner(err.message),
  });

  const confirmMutation = useMutation({
    mutationFn: (id: number) => apiRequest(`${endpoint}/${id}/confirm`, { method: "POST" }),
    onSuccess: () => {
      setErrorBanner(null);
      invalidate();
    },
    onError: (err: Error) => setErrorBanner(err.message),
  });

  const invoiceOrBillMutation = useMutation({
    mutationFn: (id: number) =>
      apiRequest<any>(
        mode === "sales" ? `${endpoint}/${id}/create-invoice` : `${endpoint}/${id}/create-bill`,
        {
          method: "POST",
          body: mode === "purchase" ? JSON.stringify({ enforce_three_way_match: true }) : undefined,
        }
      ),
    onSuccess: (res) => {
      setErrorBanner(null);
      invalidate();
      const targetId = res.invoice_id || res.bill_id;
      if (targetId) onJumpToInvoice(targetId);
    },
    onError: (err: Error) => setErrorBanner(err.message),
  });

  const columns = useMemo<ColumnDef<any>[]>(
    () => [
      {
        accessorKey: "name",
        header: mode === "sales" ? "Order #" : "PO #",
        cell: (info) => <span className="font-bold text-odoo-purple">{String(info.getValue())}</span>,
      },
      { accessorKey: "partner_name", header: mode === "sales" ? "Customer" : "Vendor" },
      { accessorKey: "date_order", header: "Date" },
      {
        accessorKey: "amount_total",
        header: "Total",
        cell: (info) => (
          <span className="font-semibold tabular-nums">
            {formatMoney(String(info.getValue()), info.row.original.currency_symbol)}
          </span>
        ),
      },
      ...(mode === "purchase"
        ? [
            {
              accessorKey: "three_way_status",
              header: "3-Way Match",
              cell: (info: any) => {
                const st = String(info.getValue());
                const cls =
                  st === "MATCHED"
                    ? "bg-emerald-100 text-emerald-800"
                    : st === "READY_TO_BILL"
                    ? "bg-sky-100 text-sky-800"
                    : "bg-amber-100 text-amber-800";
                return <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${cls}`}>{st}</span>;
              },
            },
          ]
        : []),
      {
        accessorKey: "state",
        header: "Status",
        cell: (info) => {
          const st = String(info.getValue());
          const cls =
            st === "DONE"
              ? "bg-emerald-100 text-emerald-800"
              : st === "CONFIRMED"
              ? "bg-purple-100 text-odoo-purple"
              : "bg-slate-100 text-slate-700";
          return <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold ${cls}`}>{st}</span>;
        },
      },
    ],
    [mode]
  );

  // CREATE VIEW
  if (creating) {
    const filteredPartners = partners.filter((p) => (mode === "sales" ? p.is_customer : p.is_supplier));
    const partnerList = filteredPartners.length > 0 ? filteredPartners : partners;
    return (
      <FormView
        title={mode === "sales" ? "New Sales Quotation" : "New Request for Quotation (RFQ)"}
        subtitle={mode === "sales" ? "ORDER-TO-CASH" : "PROCURE-TO-PAY"}
        onBack={() => setCreating(false)}
        locale={locale}
        errorBanner={errorBanner}
        pipelineSteps={[
          { id: "DRAFT", label: t(locale, "draft") },
          { id: "CONFIRMED", label: t(locale, "confirmed") },
          { id: "DONE", label: t(locale, "done") },
        ]}
        currentStepId="DRAFT"
        actions={[
          {
            id: "save",
            label: createMutation.isPending ? "Saving..." : "Save Draft",
            variant: "primary",
            onClick: () => createMutation.mutate(),
          },
          { id: "cancel", label: t(locale, "cancel"), variant: "secondary", onClick: () => setCreating(false) },
        ]}
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 text-xs">
          <div>
            <label className="block font-semibold text-slate-600">
              {mode === "sales" ? "Customer" : "Vendor"}
            </label>
            <select
              value={partnerId || partnerList[0]?.id || 0}
              onChange={(e) => setPartnerId(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            >
              {partnerList.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block font-semibold text-slate-600">Order Date</label>
            <input
              type="date"
              value={orderDate}
              onChange={(e) => setOrderDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
            />
          </div>
        </div>

        <div className="mt-5">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase text-slate-600">Order Lines</h3>
            <button
              type="button"
              onClick={() =>
                setLines((prev) => [
                  ...prev,
                  {
                    product_id: products[0]?.id ?? 0,
                    name: products[0]?.name ?? "",
                    quantity: "1",
                    price_unit: mode === "sales" ? products[0]?.list_price ?? "0" : products[0]?.standard_price ?? "0",
                    tax_ids: [],
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded bg-slate-100 px-2.5 py-1 text-xs font-semibold hover:bg-slate-200"
            >
              <Plus className="h-3.5 w-3.5" /> Add Product
            </button>
          </div>
          <table className="w-full text-start text-xs border border-slate-200 rounded-lg overflow-hidden">
            <thead className="bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="p-2">Product</th>
                <th className="p-2">Qty</th>
                <th className="p-2">Unit Price</th>
                <th className="p-2">Tax</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {lines.map((l, idx) => (
                <tr key={idx}>
                  <td className="p-2">
                    <select
                      value={l.product_id || products[0]?.id || 0}
                      onChange={(e) => {
                        const pid = Number(e.target.value);
                        const pr = products.find((x) => x.id === pid);
                        setLines((prev) =>
                          prev.map((row, i) =>
                            i === idx
                              ? {
                                  ...row,
                                  product_id: pid,
                                  name: pr?.name ?? row.name,
                                  price_unit:
                                    mode === "sales" ? pr?.list_price ?? "0" : pr?.standard_price ?? "0",
                                }
                              : row
                          )
                        );
                      }}
                      className="w-full rounded border border-slate-200 px-2 py-1"
                    >
                      {products.map((p) => (
                        <option key={p.id} value={p.id}>
                          [{p.default_code}] {p.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-2 w-24">
                    <input
                      type="number"
                      value={l.quantity}
                      onChange={(e) =>
                        setLines((prev) =>
                          prev.map((row, i) => (i === idx ? { ...row, quantity: e.target.value } : row))
                        )
                      }
                      className="w-full rounded border border-slate-200 px-2 py-1 tabular-nums"
                    />
                  </td>
                  <td className="p-2 w-32">
                    <input
                      type="number"
                      step="0.01"
                      value={l.price_unit}
                      onChange={(e) =>
                        setLines((prev) =>
                          prev.map((row, i) => (i === idx ? { ...row, price_unit: e.target.value } : row))
                        )
                      }
                      className="w-full rounded border border-slate-200 px-2 py-1 tabular-nums"
                    />
                  </td>
                  <td className="p-2 w-44">
                    <select
                      value={l.tax_ids[0] ?? ""}
                      onChange={(e) => {
                        const tid = e.target.value ? [Number(e.target.value)] : [];
                        setLines((prev) =>
                          prev.map((row, i) => (i === idx ? { ...row, tax_ids: tid } : row))
                        );
                      }}
                      className="w-full rounded border border-slate-200 px-2 py-1"
                    >
                      <option value="">No Tax</option>
                      {taxes.map((tx) => (
                        <option key={tx.id} value={tx.id}>
                          {tx.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-2">
                    {lines.length > 1 && (
                      <button
                        type="button"
                        onClick={() => setLines((prev) => prev.filter((_, i) => i !== idx))}
                        className="text-slate-400 hover:text-rose-600"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </FormView>
    );
  }

  // DETAIL FORM VIEW
  if (activeOrder) {
    const actions: FormAction[] = [];
    if (activeOrder.state === "DRAFT") {
      actions.push({
        id: "confirm",
        label: confirmMutation.isPending ? "Confirming..." : t(locale, "confirm"),
        variant: "primary",
        onClick: () => confirmMutation.mutate(activeOrder.id),
      });
    }
    if (activeOrder.state === "CONFIRMED") {
      actions.push({
        id: "bill_or_inv",
        label: mode === "sales" ? t(locale, "createInvoice") : t(locale, "createBill"),
        variant: "teal",
        onClick: () => invoiceOrBillMutation.mutate(activeOrder.id),
      });
    }

    return (
      <FormView
        title={activeOrder.name}
        subtitle={mode === "sales" ? "SALES ORDER" : "PURCHASE ORDER"}
        onBack={() => {
          setSelectedId(null);
          setErrorBanner(null);
        }}
        locale={locale}
        errorBanner={errorBanner}
        actions={actions}
        pipelineSteps={[
          { id: "DRAFT", label: t(locale, "draft") },
          { id: "CONFIRMED", label: t(locale, "confirmed") },
          { id: "DONE", label: t(locale, "done") },
        ]}
        currentStepId={activeOrder.state}
        smartButtons={[
          {
            id: "transfer",
            label: mode === "sales" ? t(locale, "deliveries") : t(locale, "receipts"),
            count: mode === "sales" ? activeOrder.delivery_count : activeOrder.receipt_count,
            icon: <Truck className="h-4 w-4" />,
            onClick: onJumpToInventory,
          },
          {
            id: "doc",
            label: mode === "sales" ? t(locale, "invoices") : t(locale, "bills"),
            count: mode === "sales" ? activeOrder.invoice_count : activeOrder.bill_count,
            icon: <FileText className="h-4 w-4" />,
          },
          ...(mode === "purchase"
            ? [
                {
                  id: "three_way",
                  label: "3-Way Match",
                  count: activeOrder.three_way_status,
                  icon: <FileCheck2 className="h-4 w-4" />,
                },
              ]
            : []),
        ]}
        chatter={{
          resModel: mode === "sales" ? "sale_order" : "purchase_order",
          resId: activeOrder.id,
        }}
      >
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4 text-xs mb-6">
          <div>
            <div className="text-slate-400">{mode === "sales" ? "Customer" : "Vendor"}</div>
            <div className="mt-0.5 font-semibold text-slate-900">{activeOrder.partner_name}</div>
          </div>
          <div>
            <div className="text-slate-400">Order Date</div>
            <div className="mt-0.5 font-semibold text-slate-900">{activeOrder.date_order}</div>
          </div>
          <div>
            <div className="text-slate-400">Currency</div>
            <div className="mt-0.5 font-semibold text-slate-900">{activeOrder.currency_code}</div>
          </div>
          <div>
            <div className="text-slate-400">Total Amount</div>
            <div className="mt-0.5 font-bold text-odoo-purple tabular-nums">
              {formatMoney(activeOrder.amount_total, activeOrder.currency_symbol)}
            </div>
          </div>
        </div>

        <table className="w-full text-start text-xs">
          <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2 text-end">Ordered</th>
              <th className="px-3 py-2 text-end">{mode === "sales" ? "Delivered" : "Received"}</th>
              <th className="px-3 py-2 text-end">{mode === "sales" ? "Invoiced" : "Billed"}</th>
              <th className="px-3 py-2 text-end">Unit Price</th>
              <th className="px-3 py-2 text-end">Subtotal</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 tabular-nums">
            {activeOrder.lines.map((l: any) => (
              <tr key={l.id}>
                <td className="px-3 py-2.5 font-medium text-slate-900">{l.name}</td>
                <td className="px-3 py-2.5 text-end">{l.quantity}</td>
                <td className="px-3 py-2.5 text-end text-odoo-teal font-semibold">
                  {mode === "sales" ? l.qty_delivered : l.qty_received}
                </td>
                <td className="px-3 py-2.5 text-end font-semibold">
                  {mode === "sales" ? l.qty_invoiced : l.qty_billed}
                </td>
                <td className="px-3 py-2.5 text-end">{formatMoney(l.price_unit, activeOrder.currency_symbol)}</td>
                <td className="px-3 py-2.5 text-end font-semibold">
                  {formatMoney(l.price_subtotal, activeOrder.currency_symbol)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </FormView>
    );
  }

  // LIST VIEW
  return (
    <ListView
      title={mode === "sales" ? t(locale, "salesOrders") : t(locale, "purchaseOrders")}
      subtitle={
        mode === "sales"
          ? "Order-to-Cash Pipeline · Quotation -> Confirmed SO -> Auto Delivery -> Customer Invoice"
          : "Procure-to-Pay Pipeline · RFQ -> Confirmed PO -> Warehouse Receipt -> 3-Way Matched Vendor Bill"
      }
      data={orders}
      columns={columns}
      locale={locale}
      isLoading={isLoading}
      searchFields={["name", "partner_name", "state"]}
      sumFields={["amount_total"]}
      filterChips={[
        { id: "draft", label: "Draft / RFQ", predicate: (r) => r.state === "DRAFT" },
        { id: "confirmed", label: "Confirmed", predicate: (r) => r.state === "CONFIRMED" },
        { id: "done", label: "Done", predicate: (r) => r.state === "DONE" },
      ]}
      groupByOptions={[
        { label: "Status", field: "state" },
        { label: "Partner", field: "partner_name" },
      ]}
      onRowClick={(r) => setSelectedId(r.id)}
      onCreateNew={() => setCreating(true)}
    />
  );
};
