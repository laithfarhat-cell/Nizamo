import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, History, MessageSquarePlus, Send } from "lucide-react";
import { apiRequest } from "../api/client";
import type { ChatterMessage, Locale } from "../types/erp";
import { t } from "../i18n/translations";

interface ChatterPanelProps {
  resModel: string;
  resId: number;
  locale: Locale;
}

export const ChatterPanel: React.FC<ChatterPanelProps> = ({ resModel, resId, locale }) => {
  const [comment, setComment] = useState("");
  const queryClient = useQueryClient();

  const { data: messages = [], isLoading } = useQuery<ChatterMessage[]>({
    queryKey: ["chatter", resModel, resId],
    queryFn: () => apiRequest<ChatterMessage[]>(`/core/chatter/${resModel}/${resId}`),
    enabled: resId > 0,
  });

  const postMutation = useMutation({
    mutationFn: (body: string) =>
      apiRequest<ChatterMessage>(`/core/chatter/${resModel}/${resId}`, {
        method: "POST",
        body: JSON.stringify({ body }),
      }),
    onSuccess: () => {
      setComment("");
      queryClient.invalidateQueries({ queryKey: ["chatter", resModel, resId] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!comment.trim()) return;
    postMutation.mutate(comment.trim());
  };

  return (
    <section className="mt-6 rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
          <History className="h-4 w-4 text-odoo-purple" />
          <span>{t(locale, "chatterTitle")}</span>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            {messages.length}
          </span>
        </div>
      </div>

      {/* Comment Composer */}
      <form onSubmit={handleSubmit} className="border-b border-slate-100 bg-slate-50/70 p-4">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <MessageSquarePlus className="pointer-events-none absolute start-3 top-2.5 h-4 w-4 text-slate-400" />
            <input
              type="text"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={t(locale, "notePlaceholder")}
              className="w-full rounded-lg border border-slate-300 bg-white py-2 ps-9 pe-3 text-sm outline-none focus:border-odoo-purple focus:ring-2 focus:ring-odoo-purple/15"
            />
          </div>
          <button
            type="submit"
            disabled={postMutation.isPending || !comment.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-odoo-purple px-4 py-2 text-xs font-semibold text-white hover:bg-odoo-purpleDark disabled:opacity-50 transition"
          >
            <Send className="h-3.5 w-3.5" />
            {t(locale, "postNote")}
          </button>
        </div>
      </form>

      {/* Chronological Immutable Audit Trail */}
      <div className="divide-y divide-slate-100 px-5 py-2">
        {isLoading ? (
          <div className="py-6 text-center text-xs text-slate-400">Loading audit trail...</div>
        ) : messages.length === 0 ? (
          <div className="py-6 text-center text-xs text-slate-400">No audit events recorded yet.</div>
        ) : (
          messages.map((m) => (
            <div key={m.id} className="flex gap-3 py-3">
              <div
                className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  m.message_type === "comment"
                    ? "bg-teal-100 text-teal-800"
                    : m.message_type === "state_change"
                    ? "bg-purple-100 text-odoo-purple"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {m.author_name.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold text-slate-800">{m.author_name}</span>
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-500">
                    {m.message_type}
                  </span>
                  <span className="text-[11px] text-slate-400">
                    {new Date(m.created_at).toLocaleString()}
                  </span>
                </div>
                <p className="mt-1 text-sm text-slate-700">{m.body}</p>
                {m.tracking_values.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {m.tracking_values.map((tv, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs"
                      >
                        <span className="font-semibold text-slate-600">{tv.field_label}:</span>
                        <span className="text-rose-600 line-through">{tv.old_value || "—"}</span>
                        <ArrowRight className="h-3 w-3 text-slate-400" />
                        <span className="font-semibold text-emerald-700">{tv.new_value || "—"}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
};
