import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Trash2, Wallet } from "lucide-react";
import { apiRequest, formatMoney } from "../api/client";
import type {
  Account,
  AccountMove,
  AnalyticAccount,
  Currency,
  Journal,
  Locale,
  Partner,
  TaxRule,
} from "../types/erp";
import { t } from "../i18n/translations";
import { FormAction, FormView } from "./FormView";

interface InvoiceEditorProps {
  moveId: number | null; // null = create new
  defaultMoveType?: AccountMove["move_type"];
  onBack: () => void;
  onSelectMove: (id: number) => void;
  locale: Locale;
}

interface DraftLine {
  account_id: number;
  name: string;
  quantity: string;
  price_unit: string;
  discount: string;
  tax_ids: number[];
  analytic_account_id: number | null;
  debit: string;
  credit: string;
}

export const InvoiceEditor: React.FC<InvoiceEditorProps> = ({
  moveId,
  defaultMoveType = "out_invoice",
  onBack,
  onSelectMove,
  locale,
}) => {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"lines" | "journal_items">("lines");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [payModalOpen, setPayModalOpen] = useState(false);

  // Master data queries
  const { data: accounts = [] } = useQuery<Account[]>({
    queryKey: ["accounts"],
    queryFn: () => apiRequest<Account[]>("/accounting/accounts"),
  });
  const { data: journals = [] } = useQuery<Journal[]>({
    queryKey: ["journals"],
    queryFn: () => apiRequest<Journal[]>("/accounting/journals"),
  });
  const { data: partners = [] } = useQuery<Partner[]>({
    queryKey: ["partners"],
    queryFn: () => apiRequest<Partner[]>("/core/partners"),
  });
  const { data: taxes = [] } = useQuery<TaxRule[]>({
    queryKey: ["taxes"],
    queryFn: () => apiRequest<TaxRule[]>("/accounting/taxes"),
  });
  const { data: currencies = [] } = useQuery<Currency[]>({
    queryKey: ["currencies"],
    queryFn: () => apiRequest<Currency[]>("/core/currencies"),
  });
  const { data: analytics = [] } = useQuery<AnalyticAccount[]>({
    queryKey: ["analytics"],
    queryFn: () => apiRequest<AnalyticAccount[]>("/accounting/analytics"),
  });

  const { data: move, isLoading } = useQuery<AccountMove>({
    queryKey: ["move", moveId],
    queryFn: () => apiRequest<AccountMove>(`/accounting/moves/${moveId}`),
    enabled: moveId !== null,
  });

  // New Move Form State
  const today = new Date().toISOString().slice(0, 10);
  const [moveType, setMoveType] = useState<AccountMove["move_type"]>(defaultMoveType);
  const [partnerId, setPartnerId] = useState<number>(0);
  const [journalId, setJournalId] = useState<number>(0);
  const [currencyId, setCurrencyId] = useState<number>(0);
  const [moveDate, setMoveDate] = useState(today);
  const [dueDate, setDueDate] = useState(today);
  const [refText, setRefText] = useState("");
  const [draftLines, setDraftLines] = useState<DraftLine[]>([
    {
      account_id: 0,
      name: "",
      quantity: "1",
      price_unit: "1000.00",
      discount: "0",
      tax_ids: [],
      analytic_account_id: null,
      debit: "0",
      credit: "0",
    },
  ]);

  // Payment Modal State
  const [payJournalId, setPayJournalId] = useState<number>(0);
  const [payAmount, setPayAmount] = useState<string>("");
  const [payCurrencyId, setPayCurrencyId] = useState<number>(0);
  const [payDate, setPayDate] = useState<string>(today);
  const [payRef, setPayRef] = useState<string>("");

  const resolvedJournalId = useMemo(() => {
    if (journalId) return journalId;
    const typeMap: Record<string, string> = {
      out_invoice: "SALE",
      out_refund: "SALE",
      in_invoice: "PURCHASE",
      in_refund: "PURCHASE",
      entry: "GENERAL",
    };
    const match = journals.find((j) => j.journal_type === typeMap[moveType]);
    return match?.id ?? journals[0]?.id ?? 0;
  }, [journalId, journals, moveType]);

  const resolvedPartnerId = partnerId || partners[0]?.id || 0;
  const resolvedCurrencyId = currencyId || currencies[0]?.id || 0;
  const defaultProductAccountId =
    accounts.find((a) =>
      moveType.startsWith("out_") ? a.account_type === "income" : a.account_type === "expense"
    )?.id ?? accounts[0]?.id ?? 0;

  // Live real-time subtotal & tax computation for draft lines
  const liveTotals = useMemo(() => {
    if (moveType === "entry") {
      const dr = draftLines.reduce((s, l) => s + Number(l.debit || 0), 0);
      const cr = draftLines.reduce((s, l) => s + Number(l.credit || 0), 0);
      return { untaxed: dr, tax: 0, total: dr, debit: dr, credit: cr, balanced: Math.abs(dr - cr) < 0.0001 };
    }
    let untaxed = 0;
    let taxSum = 0;
    for (const l of draftLines) {
      const qty = Number(l.quantity || 0);
      const price = Number(l.price_unit || 0);
      const disc = Number(l.discount || 0);
      const base = qty * price * (1 - disc / 100);
      untaxed += base;
      let runningBase = base;
      for (const tid of l.tax_ids) {
        const rule = taxes.find((tx) => tx.id === tid);
        if (!rule) continue;
        const amt =
          rule.amount_type === "FIXED"
            ? Number(rule.amount) * qty
            : runningBase * (Number(rule.amount) / 100);
        taxSum += amt;
        if (rule.include_base_amount) runningBase += amt;
      }
    }
    return { untaxed, tax: taxSum, total: untaxed + taxSum, debit: 0, credit: 0, balanced: true };
  }, [draftLines, taxes, moveType]);

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["moves"] });
    queryClient.invalidateQueries({ queryKey: ["move", moveId] });
    queryClient.invalidateQueries({ queryKey: ["accounting-dashboard"] });
    queryClient.invalidateQueries({ queryKey: ["chatter"] });
  };

  const createMutation = useMutation({
    mutationFn: () => {
      const payload =
        moveType === "entry"
          ? {
              move_type: "entry",
              journal_id: resolvedJournalId,
              partner_id: resolvedPartnerId || null,
              date: moveDate,
              currency_id: resolvedCurrencyId,
              ref: refText || null,
              journal_lines: draftLines.map((l) => ({
                account_id: l.account_id || defaultProductAccountId,
                name: l.name || "Journal Line",
                debit: l.debit || "0",
                credit: l.credit || "0",
                analytic_account_id: l.analytic_account_id,
              })),
            }
          : {
              move_type: moveType,
              journal_id: resolvedJournalId,
              partner_id: resolvedPartnerId,
              date: moveDate,
              invoice_date_due: dueDate,
              currency_id: resolvedCurrencyId,
              ref: refText || null,
              invoice_lines: draftLines.map((l) => ({
                account_id: l.account_id || defaultProductAccountId,
                name: l.name || "Line Item",
                quantity: l.quantity || "1",
                price_unit: l.price_unit || "0",
                discount: l.discount || "0",
                tax_ids: l.tax_ids,
                analytic_account_id: l.analytic_account_id,
              })),
            };
      return apiRequest<AccountMove>("/accounting/moves", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    },
    onSuccess: (created) => {
      setErrorMsg(null);
      invalidateAll();
      onSelectMove(created.id);
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const postMutation = useMutation({
    mutationFn: (id: number) =>
      apiRequest<AccountMove>(`/accounting/moves/${id}/post`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidateAll();
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) =>
      apiRequest<AccountMove>(`/accounting/moves/${id}/cancel`, { method: "POST" }),
    onSuccess: () => {
      setErrorMsg(null);
      invalidateAll();
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const reverseMutation = useMutation({
    mutationFn: (id: number) =>
      apiRequest<AccountMove>(`/accounting/moves/${id}/reverse`, {
        method: "POST",
        body: JSON.stringify({
          reversal_date: today,
          reason: "Automated Credit Note / Reversal",
          auto_post: true,
          auto_reconcile: true,
        }),
      }),
    onSuccess: (rev) => {
      setErrorMsg(null);
      invalidateAll();
      onSelectMove(rev.id);
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  const paymentMutation = useMutation({
    mutationFn: () => {
      if (!move) throw new Error("No move loaded");
      const bankJournals = journals.filter((j) => j.journal_type === "BANK" || j.journal_type === "CASH");
      const jId = payJournalId || bankJournals[0]?.id;
      const isInbound = move.move_type === "out_invoice" || move.move_type === "in_refund";
      return apiRequest("/accounting/payments", {
        method: "POST",
        body: JSON.stringify({
          payment_type: isInbound ? "INBOUND" : "OUTBOUND",
          partner_type: isInbound ? "CUSTOMER" : "SUPPLIER",
          partner_id: move.partner_id,
          journal_id: jId,
          amount: payAmount || move.amount_residual,
          currency_id: payCurrencyId || move.currency_id,
          date: payDate,
          ref: payRef || `Payment for ${move.name}`,
          invoice_move_id: move.id,
        }),
      });
    },
    onSuccess: () => {
      setPayModalOpen(false);
      setErrorMsg(null);
      invalidateAll();
    },
    onError: (err: Error) => setErrorMsg(err.message),
  });

  // CREATE MODE
  if (moveId === null) {
    return (
      <FormView
        title="New Accounting Document"
        subtitle={moveType.replace("_", " ").toUpperCase()}
        onBack={onBack}
        locale={locale}
        errorBanner={errorMsg}
        pipelineSteps={[
          { id: "DRAFT", label: t(locale, "draft") },
          { id: "POSTED", label: t(locale, "posted") },
          { id: "PAID", label: t(locale, "paid") },
        ]}
        currentStepId="DRAFT"
        actions={[
          {
            id: "save",
            label: createMutation.isPending ? "Saving..." : "Save Draft",
            variant: "primary",
            disabled: createMutation.isPending,
            onClick: () => createMutation.mutate(),
          },
          { id: "discard", label: t(locale, "cancel"), variant: "secondary", onClick: onBack },
        ]}
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div>
            <label className="block text-xs font-semibold text-slate-600">Document Type</label>
            <select
              value={moveType}
              onChange={(e) => setMoveType(e.target.value as AccountMove["move_type"])}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            >
              <option value="out_invoice">Customer Invoice (out_invoice)</option>
              <option value="in_invoice">Vendor Bill (in_invoice)</option>
              <option value="out_refund">Customer Credit Note (out_refund)</option>
              <option value="in_refund">Vendor Credit Note (in_refund)</option>
              <option value="entry">General Journal Entry (entry)</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600">Partner</label>
            <select
              value={resolvedPartnerId}
              onChange={(e) => setPartnerId(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            >
              {partners.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600">Journal</label>
            <select
              value={resolvedJournalId}
              onChange={(e) => setJournalId(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            >
              {journals.map((j) => (
                <option key={j.id} value={j.id}>
                  [{j.code}] {j.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600">Currency</label>
            <select
              value={resolvedCurrencyId}
              onChange={(e) => setCurrencyId(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            >
              {currencies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code} — {c.name} (Rate: {c.latest_rate})
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600">Accounting / Invoice Date</label>
            <input
              type="date"
              value={moveDate}
              onChange={(e) => setMoveDate(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-600">Reference</label>
            <input
              type="text"
              value={refText}
              onChange={(e) => setRefText(e.target.value)}
              placeholder="Contract / Bill Ref..."
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            />
          </div>
        </div>

        {/* Inline Editable Line-Item Grid */}
        <div className="mt-6">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wide text-slate-600">
              {moveType === "entry" ? "Journal Entry Lines (Debit / Credit)" : "Invoice Line Items"}
            </h3>
            <button
              type="button"
              onClick={() =>
                setDraftLines((prev) => [
                  ...prev,
                  {
                    account_id: defaultProductAccountId,
                    name: "",
                    quantity: "1",
                    price_unit: "0.00",
                    discount: "0",
                    tax_ids: [],
                    analytic_account_id: null,
                    debit: "0.00",
                    credit: "0.00",
                  },
                ])
              }
              className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-200"
            >
              <Plus className="h-3.5 w-3.5" /> Add Line
            </button>
          </div>

          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-start text-xs">
              <thead className="bg-slate-50 text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-2.5 py-2">Account</th>
                  <th className="px-2.5 py-2">Description</th>
                  <th className="px-2.5 py-2">Analytic</th>
                  {moveType === "entry" ? (
                    <>
                      <th className="px-2.5 py-2">Debit</th>
                      <th className="px-2.5 py-2">Credit</th>
                    </>
                  ) : (
                    <>
                      <th className="px-2.5 py-2 w-20">Qty</th>
                      <th className="px-2.5 py-2 w-28">Unit Price</th>
                      <th className="px-2.5 py-2 w-20">Disc %</th>
                      <th className="px-2.5 py-2">Tax</th>
                    </>
                  )}
                  <th className="w-10 px-2 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {draftLines.map((line, idx) => (
                  <tr key={idx}>
                    <td className="p-2">
                      <select
                        value={line.account_id || defaultProductAccountId}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, account_id: v } : x)));
                        }}
                        className="w-48 rounded border border-slate-200 px-2 py-1 text-xs"
                      >
                        {accounts.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.code} {a.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="p-2">
                      <input
                        type="text"
                        value={line.name}
                        onChange={(e) => {
                          const v = e.target.value;
                          setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, name: v } : x)));
                        }}
                        placeholder="Description..."
                        className="w-full min-w-[160px] rounded border border-slate-200 px-2 py-1 text-xs"
                      />
                    </td>
                    <td className="p-2">
                      <select
                        value={line.analytic_account_id ?? ""}
                        onChange={(e) => {
                          const v = e.target.value ? Number(e.target.value) : null;
                          setDraftLines((ls) =>
                            ls.map((x, i) => (i === idx ? { ...x, analytic_account_id: v } : x))
                          );
                        }}
                        className="w-36 rounded border border-slate-200 px-2 py-1 text-xs"
                      >
                        <option value="">—</option>
                        {analytics.map((an) => (
                          <option key={an.id} value={an.id}>
                            {an.code} {an.name}
                          </option>
                        ))}
                      </select>
                    </td>
                    {moveType === "entry" ? (
                      <>
                        <td className="p-2">
                          <input
                            type="number"
                            step="0.01"
                            value={line.debit}
                            onChange={(e) => {
                              const v = e.target.value;
                              setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, debit: v } : x)));
                            }}
                            className="w-28 rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            step="0.01"
                            value={line.credit}
                            onChange={(e) => {
                              const v = e.target.value;
                              setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, credit: v } : x)));
                            }}
                            className="w-28 rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
                          />
                        </td>
                      </>
                    ) : (
                      <>
                        <td className="p-2">
                          <input
                            type="number"
                            step="0.01"
                            value={line.quantity}
                            onChange={(e) => {
                              const v = e.target.value;
                              setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, quantity: v } : x)));
                            }}
                            className="w-20 rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            step="0.01"
                            value={line.price_unit}
                            onChange={(e) => {
                              const v = e.target.value;
                              setDraftLines((ls) =>
                                ls.map((x, i) => (i === idx ? { ...x, price_unit: v } : x))
                              );
                            }}
                            className="w-28 rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
                          />
                        </td>
                        <td className="p-2">
                          <input
                            type="number"
                            step="0.1"
                            value={line.discount}
                            onChange={(e) => {
                              const v = e.target.value;
                              setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, discount: v } : x)));
                            }}
                            className="w-16 rounded border border-slate-200 px-2 py-1 text-xs tabular-nums"
                          />
                        </td>
                        <td className="p-2">
                          <select
                            value={line.tax_ids[0] ?? ""}
                            onChange={(e) => {
                              const tid = e.target.value ? [Number(e.target.value)] : [];
                              setDraftLines((ls) => ls.map((x, i) => (i === idx ? { ...x, tax_ids: tid } : x)));
                            }}
                            className="w-40 rounded border border-slate-200 px-2 py-1 text-xs"
                          >
                            <option value="">No Tax</option>
                            {taxes.map((tx) => (
                              <option key={tx.id} value={tx.id}>
                                {tx.name}
                              </option>
                            ))}
                          </select>
                        </td>
                      </>
                    )}
                    <td className="p-2 text-center">
                      {draftLines.length > 1 && (
                        <button
                          type="button"
                          onClick={() => setDraftLines((ls) => ls.filter((_, i) => i !== idx))}
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

          {/* Real-time totals panel */}
          <div className="mt-4 flex justify-end">
            <div className="w-72 space-y-1.5 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs tabular-nums">
              {moveType === "entry" ? (
                <>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Total Debit</span>
                    <span className="font-semibold">{formatMoney(liveTotals.debit)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Total Credit</span>
                    <span className="font-semibold">{formatMoney(liveTotals.credit)}</span>
                  </div>
                  <div className="border-t border-slate-200 pt-1.5 flex justify-between font-bold">
                    <span>Ledger Status</span>
                    <span className={liveTotals.balanced ? "text-emerald-700" : "text-rose-600"}>
                      {liveTotals.balanced ? "Balanced" : "Unbalanced"}
                    </span>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Untaxed Amount</span>
                    <span className="font-semibold">{formatMoney(liveTotals.untaxed)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Taxes</span>
                    <span className="font-semibold">{formatMoney(liveTotals.tax)}</span>
                  </div>
                  <div className="border-t border-slate-200 pt-1.5 flex justify-between text-sm font-bold text-slate-900">
                    <span>Total</span>
                    <span>{formatMoney(liveTotals.total)}</span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </FormView>
    );
  }

  // VIEW / ACTION MODE
  if (isLoading || !move) {
    return <div className="p-8 text-center text-sm text-slate-500">Loading document...</div>;
  }

  const isInvoiceDoc = move.move_type !== "entry";
  const currentStep =
    move.state === "POSTED" && isInvoiceDoc && move.payment_state === "PAID"
      ? "PAID"
      : move.state;

  const actions: FormAction[] = [];
  if (move.state === "DRAFT") {
    actions.push({
      id: "post",
      label: postMutation.isPending ? "Posting..." : t(locale, "post"),
      variant: "primary",
      onClick: () => postMutation.mutate(move.id),
    });
    actions.push({
      id: "cancel",
      label: t(locale, "cancel"),
      variant: "danger",
      onClick: () => cancelMutation.mutate(move.id),
    });
  }
  if (move.state === "POSTED") {
    if (isInvoiceDoc && move.payment_state !== "PAID") {
      actions.push({
        id: "pay",
        label: t(locale, "registerPayment"),
        variant: "teal",
        onClick: () => {
          setPayAmount(move.amount_residual);
          setPayCurrencyId(move.currency_id);
          setPayModalOpen(true);
        },
      });
    }
    actions.push({
      id: "reverse",
      label: t(locale, "reverse"),
      variant: "secondary",
      onClick: () => reverseMutation.mutate(move.id),
    });
    actions.push({
      id: "print",
      label: t(locale, "printPdf"),
      variant: "secondary",
      onClick: () => window.print(),
    });
  }

  const productLines = move.lines.filter((l) => l.display_type === "product");
  const totalDebit = move.lines.reduce((s, l) => s + Number(l.debit), 0);
  const totalCredit = move.lines.reduce((s, l) => s + Number(l.credit), 0);

  return (
    <>
      <FormView
        title={move.name}
        subtitle={`${move.journal_name} · ${move.move_type.replace("_", " ").toUpperCase()}`}
        onBack={onBack}
        locale={locale}
        errorBanner={errorMsg}
        actions={actions}
        pipelineSteps={
          isInvoiceDoc
            ? [
                { id: "DRAFT", label: t(locale, "draft") },
                { id: "POSTED", label: t(locale, "posted") },
                { id: "PAID", label: t(locale, "paid") },
              ]
            : [
                { id: "DRAFT", label: t(locale, "draft") },
                { id: "POSTED", label: t(locale, "posted") },
              ]
        }
        currentStepId={currentStep}
        smartButtons={[
          {
            id: "lines_count",
            label: "Journal Lines",
            count: move.lines.length,
            icon: <FileText className="h-4 w-4" />,
            onClick: () => setActiveTab("journal_items"),
          },
          ...(isInvoiceDoc
            ? [
                {
                  id: "residual",
                  label: "Amount Due",
                  count: formatMoney(move.amount_residual, move.currency_symbol),
                  icon: <Wallet className="h-4 w-4" />,
                },
              ]
            : []),
        ]}
        chatter={{ resModel: "account_move", resId: move.id }}
      >
        {/* Document Metadata */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4 text-xs">
          <div>
            <div className="text-slate-400 font-medium">Partner</div>
            <div className="mt-0.5 font-semibold text-slate-900">{move.partner_name || "—"}</div>
          </div>
          <div>
            <div className="text-slate-400 font-medium">Accounting Date</div>
            <div className="mt-0.5 font-semibold text-slate-900">{move.date}</div>
          </div>
          <div>
            <div className="text-slate-400 font-medium">Currency & Effective Rate</div>
            <div className="mt-0.5 font-semibold text-slate-900 tabular-nums">
              {move.currency_code} @ {move.exchange_rate}
            </div>
          </div>
          <div>
            <div className="text-slate-400 font-medium">Payment Status / Origin</div>
            <div className="mt-0.5 flex items-center gap-2">
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${
                  move.payment_state === "PAID"
                    ? "bg-emerald-100 text-emerald-800"
                    : move.payment_state === "PARTIAL"
                    ? "bg-amber-100 text-amber-800"
                    : "bg-slate-100 text-slate-700"
                }`}
              >
                {move.payment_state}
              </span>
              {move.invoice_origin && (
                <span className="text-slate-500">({move.invoice_origin})</span>
              )}
            </div>
          </div>
        </div>

        {/* Notebook Tabs: Invoice Lines vs Double-Entry Journal Items */}
        <div className="mt-6 border-b border-slate-200 flex gap-6 text-xs font-semibold">
          {isInvoiceDoc && (
            <button
              type="button"
              onClick={() => setActiveTab("lines")}
              className={`pb-2.5 border-b-2 transition ${
                activeTab === "lines"
                  ? "border-odoo-purple text-odoo-purple"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              Invoice Lines
            </button>
          )}
          <button
            type="button"
            onClick={() => setActiveTab("journal_items")}
            className={`pb-2.5 border-b-2 transition ${
              activeTab === "journal_items" || !isInvoiceDoc
                ? "border-odoo-purple text-odoo-purple"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            Journal Items (Double-Entry Ledger)
          </button>
        </div>

        {activeTab === "lines" && isInvoiceDoc ? (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-start text-xs">
              <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2">Description</th>
                  <th className="px-3 py-2 text-end">Qty</th>
                  <th className="px-3 py-2 text-end">Unit Price</th>
                  <th className="px-3 py-2 text-end">Subtotal ({move.currency_code})</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {productLines.map((l) => (
                  <tr key={l.id}>
                    <td className="px-3 py-2.5 font-mono text-slate-600">
                      {l.account_code} {l.account_name}
                    </td>
                    <td className="px-3 py-2.5 font-medium text-slate-900">{l.name}</td>
                    <td className="px-3 py-2.5 text-end tabular-nums">{l.quantity}</td>
                    <td className="px-3 py-2.5 text-end tabular-nums">
                      {formatMoney(l.price_unit, move.currency_symbol)}
                    </td>
                    <td className="px-3 py-2.5 text-end font-semibold tabular-nums">
                      {formatMoney(Math.abs(Number(l.amount_currency)), move.currency_symbol)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="mt-4 flex justify-end">
              <div className="w-72 space-y-1.5 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs tabular-nums">
                <div className="flex justify-between">
                  <span className="text-slate-500">Untaxed Amount</span>
                  <span>{formatMoney(move.amount_untaxed, move.currency_symbol)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Tax</span>
                  <span>{formatMoney(move.amount_tax, move.currency_symbol)}</span>
                </div>
                <div className="flex justify-between border-t border-slate-200 pt-1.5 text-sm font-bold text-slate-900">
                  <span>Total</span>
                  <span>{formatMoney(move.amount_total, move.currency_symbol)}</span>
                </div>
                <div className="flex justify-between text-odoo-teal font-semibold">
                  <span>Amount Due (Residual)</span>
                  <span>{formatMoney(move.amount_residual, move.currency_symbol)}</span>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-start text-xs">
              <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase text-slate-500">
                <tr>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2">Label</th>
                  <th className="px-3 py-2 text-end">Amount Currency</th>
                  <th className="px-3 py-2 text-end">Debit (Base)</th>
                  <th className="px-3 py-2 text-end">Credit (Base)</th>
                  <th className="px-3 py-2 text-end">Residual</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {move.lines.map((l) => (
                  <tr key={l.id}>
                    <td className="px-3 py-2.5 font-mono text-slate-700">
                      {l.account_code} {l.account_name}
                    </td>
                    <td className="px-3 py-2.5 text-slate-800">{l.name}</td>
                    <td className="px-3 py-2.5 text-end tabular-nums text-slate-600">
                      {l.amount_currency}
                    </td>
                    <td className="px-3 py-2.5 text-end font-semibold tabular-nums text-slate-900">
                      {formatMoney(l.debit)}
                    </td>
                    <td className="px-3 py-2.5 text-end font-semibold tabular-nums text-slate-900">
                      {formatMoney(l.credit)}
                    </td>
                    <td className="px-3 py-2.5 text-end tabular-nums">
                      {l.reconciled ? (
                        <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">
                          RECONCILED
                        </span>
                      ) : (
                        l.amount_residual
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t-2 border-slate-200 bg-slate-50 font-bold tabular-nums">
                <tr>
                  <td colSpan={3} className="px-3 py-2">
                    Double-Entry Ledger Check
                  </td>
                  <td className="px-3 py-2 text-end">{formatMoney(totalDebit)}</td>
                  <td className="px-3 py-2 text-end">{formatMoney(totalCredit)}</td>
                  <td className="px-3 py-2 text-end text-emerald-700">
                    {Math.abs(totalDebit - totalCredit) < 0.001 ? "BALANCED" : "UNBALANCED"}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </FormView>

      {/* Register Payment Modal */}
      {payModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl">
            <h3 className="text-base font-bold text-slate-900">Register Payment — {move.name}</h3>
            <p className="mt-1 text-xs text-slate-500">
              Creates a balanced Bank/Cash entry, reconciles receivable/payable, and posts Realized FX Gain/Loss if rates changed.
            </p>
            <div className="mt-4 space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-slate-600">Bank / Cash Journal</label>
                <select
                  value={payJournalId}
                  onChange={(e) => setPayJournalId(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                >
                  {journals
                    .filter((j) => j.journal_type === "BANK" || j.journal_type === "CASH")
                    .map((j) => (
                      <option key={j.id} value={j.id}>
                        {j.name}
                      </option>
                    ))}
                </select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block font-semibold text-slate-600">Amount</label>
                  <input
                    type="number"
                    step="0.01"
                    value={payAmount}
                    onChange={(e) => setPayAmount(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 tabular-nums"
                  />
                </div>
                <div>
                  <label className="block font-semibold text-slate-600">Payment Date</label>
                  <input
                    type="date"
                    value={payDate}
                    onChange={(e) => setPayDate(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                  />
                </div>
              </div>
              <div>
                <label className="block font-semibold text-slate-600">Memo / Reference</label>
                <input
                  type="text"
                  value={payRef}
                  onChange={(e) => setPayRef(e.target.value)}
                  placeholder={`Payment ${move.name}`}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
                />
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPayModalOpen(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={paymentMutation.isPending}
                onClick={() => paymentMutation.mutate()}
                className="rounded-lg bg-odoo-teal px-4 py-2 text-xs font-semibold text-white hover:opacity-90"
              >
                {paymentMutation.isPending ? "Processing..." : "Validate & Reconcile"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
