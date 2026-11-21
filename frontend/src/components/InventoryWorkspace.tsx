import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Layers, Package, Truck } from "lucide-react";
import { apiRequest, formatMoney } from "../api/client";
import type { Locale, Product, StockPicking, StockQuant, ValuationLayer } from "../types/erp";
import { t } from "../i18n/translations";

interface InventoryWorkspaceProps {
  locale: Locale;
}

export const InventoryWorkspace: React.FC<InventoryWorkspaceProps> = ({ locale }) => {
  const [subTab, setSubTab] = useState<"transfers" | "products" | "quants" | "valuation">("transfers");
  const [banner, setBanner] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: pickings = [] } = useQuery<StockPicking[]>({
    queryKey: ["pickings"],
    queryFn: () => apiRequest<StockPicking[]>("/inventory/pickings"),
  });
  const { data: products = [] } = useQuery<Product[]>({
    queryKey: ["products"],
    queryFn: () => apiRequest<Product[]>("/inventory/products"),
  });
  const { data: quants = [] } = useQuery<StockQuant[]>({
    queryKey: ["quants"],
    queryFn: () => apiRequest<StockQuant[]>("/inventory/quants"),
  });
  const { data: layers = [] } = useQuery<ValuationLayer[]>({
    queryKey: ["valuation-layers"],
    queryFn: () => apiRequest<ValuationLayer[]>("/inventory/valuation-layers"),
  });

  const validateMutation = useMutation({
    mutationFn: (id: number) => apiRequest(`/inventory/pickings/${id}/validate`, { method: "POST" }),
    onSuccess: () => {
      setBanner(null);
      queryClient.invalidateQueries({ queryKey: ["pickings"] });
      queryClient.invalidateQueries({ queryKey: ["products"] });
      queryClient.invalidateQueries({ queryKey: ["quants"] });
      queryClient.invalidateQueries({ queryKey: ["valuation-layers"] });
      queryClient.invalidateQueries({ queryKey: ["moves"] });
      queryClient.invalidateQueries({ queryKey: ["sales", "orders"] });
      queryClient.invalidateQueries({ queryKey: ["purchase", "orders"] });
    },
    onError: (err: Error) => setBanner(err.message),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <h1 className="text-base font-bold text-slate-900">{t(locale, "inventory")}</h1>
          <p className="text-xs text-slate-500">
            Double-Entry Stock Ledger · FIFO / AVCO / Standard Costing & Real-Time Perpetual GL Integration
          </p>
        </div>
        <div className="flex gap-1.5">
          {[
            { id: "transfers", label: t(locale, "transfers"), icon: Truck },
            { id: "products", label: t(locale, "products"), icon: Package },
            { id: "quants", label: t(locale, "quants"), icon: Layers },
            { id: "valuation", label: t(locale, "valuation"), icon: CheckCircle2 },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setSubTab(id as any)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                subTab === id
                  ? "bg-odoo-purple text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {banner && (
        <div className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-xs font-medium text-rose-800">
          {banner}
        </div>
      )}

      {/* Transfers */}
      {subTab === "transfers" && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-start text-xs">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2.5">Transfer #</th>
                <th className="px-3 py-2.5">Type</th>
                <th className="px-3 py-2.5">Origin</th>
                <th className="px-3 py-2.5">Route (Double-Entry Stock)</th>
                <th className="px-3 py-2.5">Items</th>
                <th className="px-3 py-2.5">Status</th>
                <th className="px-3 py-2.5 text-end">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {pickings.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5 font-bold text-odoo-purple">{p.name}</td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`rounded px-2 py-0.5 text-[10px] font-bold ${
                        p.picking_type === "INCOMING"
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {p.picking_type}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-600">{p.origin || "Manual"}</td>
                  <td className="px-3 py-2.5 text-slate-600">
                    {p.location_name} → {p.location_dest_name}
                  </td>
                  <td className="px-3 py-2.5">
                    {p.moves.map((m) => `${m.product_sku} (x${m.product_uom_qty})`).join(", ")}
                  </td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold ${
                        p.state === "DONE"
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-purple-100 text-odoo-purple"
                      }`}
                    >
                      {p.state}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-end">
                    {p.state !== "DONE" && (
                      <button
                        type="button"
                        disabled={validateMutation.isPending}
                        onClick={() => validateMutation.mutate(p.id)}
                        className="rounded-lg bg-odoo-teal px-3 py-1 text-xs font-semibold text-white hover:opacity-90"
                      >
                        {t(locale, "validateTransfer")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Products */}
      {subTab === "products" && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-start text-xs tabular-nums">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2.5">SKU</th>
                <th className="px-3 py-2.5">Product</th>
                <th className="px-3 py-2.5">Category</th>
                <th className="px-3 py-2.5">Costing Method</th>
                <th className="px-3 py-2.5 text-end">On Hand</th>
                <th className="px-3 py-2.5 text-end">Unit Cost</th>
                <th className="px-3 py-2.5 text-end">Sales Price</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {products.map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5 font-mono font-semibold text-odoo-purple">{p.default_code}</td>
                  <td className="px-3 py-2.5 font-medium text-slate-900">
                    {locale === "ar" && p.name_ar ? p.name_ar : p.name}
                  </td>
                  <td className="px-3 py-2.5 text-slate-600">{p.category_name}</td>
                  <td className="px-3 py-2.5">
                    <span className="rounded bg-indigo-50 px-2 py-0.5 text-[10px] font-bold text-indigo-700">
                      {p.costing_method}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-end font-bold">{p.qty_on_hand}</td>
                  <td className="px-3 py-2.5 text-end">{formatMoney(p.standard_price)}</td>
                  <td className="px-3 py-2.5 text-end font-semibold">{formatMoney(p.list_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Double-Entry Stock Quants */}
      {subTab === "quants" && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-start text-xs tabular-nums">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2.5">Location</th>
                <th className="px-3 py-2.5">Usage</th>
                <th className="px-3 py-2.5">SKU</th>
                <th className="px-3 py-2.5">Product</th>
                <th className="px-3 py-2.5 text-end">Quantity</th>
                <th className="px-3 py-2.5 text-end">Unit Cost</th>
                <th className="px-3 py-2.5 text-end">Estimated Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {quants.map((q) => (
                <tr key={q.id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5 font-semibold text-slate-800">{q.location_name}</td>
                  <td className="px-3 py-2.5">
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600">
                      {q.location_usage}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-odoo-purple">{q.sku}</td>
                  <td className="px-3 py-2.5">{q.product_name}</td>
                  <td className="px-3 py-2.5 text-end font-bold">{q.quantity}</td>
                  <td className="px-3 py-2.5 text-end">{formatMoney(q.unit_cost)}</td>
                  <td className="px-3 py-2.5 text-end font-semibold">{formatMoney(q.total_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Perpetual Valuation Layers (FIFO / AVCO) */}
      {subTab === "valuation" && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-start text-xs tabular-nums">
            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
              <tr>
                <th className="px-3 py-2.5">SKU</th>
                <th className="px-3 py-2.5">Product</th>
                <th className="px-3 py-2.5">Description</th>
                <th className="px-3 py-2.5 text-end">Qty Delta</th>
                <th className="px-3 py-2.5 text-end">Remaining FIFO Qty</th>
                <th className="px-3 py-2.5 text-end">Unit Cost</th>
                <th className="px-3 py-2.5 text-end">Valuation Impact</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {layers.map((l) => (
                <tr key={l.id} className="hover:bg-slate-50">
                  <td className="px-3 py-2.5 font-mono text-odoo-purple">{l.sku}</td>
                  <td className="px-3 py-2.5 font-medium">{l.product_name}</td>
                  <td className="px-3 py-2.5 text-slate-600">{l.description}</td>
                  <td
                    className={`px-3 py-2.5 text-end font-bold ${
                      Number(l.quantity) >= 0 ? "text-emerald-700" : "text-rose-700"
                    }`}
                  >
                    {l.quantity}
                  </td>
                  <td className="px-3 py-2.5 text-end">{l.remaining_qty}</td>
                  <td className="px-3 py-2.5 text-end">{formatMoney(l.unit_cost)}</td>
                  <td className="px-3 py-2.5 text-end font-semibold">{formatMoney(l.value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
