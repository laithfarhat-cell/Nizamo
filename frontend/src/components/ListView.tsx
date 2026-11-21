import React, { useMemo, useState } from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowUpDown, Download, Layers, Plus, Search } from "lucide-react";
import type { Locale } from "../types/erp";
import { t } from "../i18n/translations";

export interface FilterChip {
  id: string;
  label: string;
  predicate: (row: any) => boolean;
}

export interface BulkAction<T> {
  id: string;
  label: string;
  variant?: "primary" | "danger" | "default";
  onClick: (selectedRows: T[]) => void;
}

interface ListViewProps<T extends { id: number | string }> {
  title: string;
  subtitle?: string;
  data: T[];
  columns: ColumnDef<T, any>[];
  locale: Locale;
  searchFields: (keyof T)[];
  filterChips?: FilterChip[];
  groupByOptions?: { label: string; field: keyof T }[];
  sumFields?: (keyof T)[];
  onRowClick?: (row: T) => void;
  onCreateNew?: () => void;
  createLabel?: string;
  bulkActions?: BulkAction<T>[];
  isLoading?: boolean;
}

export function ListView<T extends { id: number | string }>({
  title,
  subtitle,
  data,
  columns,
  locale,
  searchFields,
  filterChips = [],
  groupByOptions = [],
  sumFields = [],
  onRowClick,
  onCreateNew,
  createLabel,
  bulkActions = [],
  isLoading = false,
}: ListViewProps<T>) {
  const [search, setSearch] = useState("");
  const [activeChip, setActiveChip] = useState<string | null>(null);
  const [groupByField, setGroupByField] = useState<keyof T | "">("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string | number>>(new Set());

  const filteredData = useMemo(() => {
    let rows = data;
    if (activeChip) {
      const chip = filterChips.find((c) => c.id === activeChip);
      if (chip) rows = rows.filter(chip.predicate);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter((row) =>
        searchFields.some((f) => String(row[f] ?? "").toLowerCase().includes(q))
      );
    }
    return rows;
  }, [data, activeChip, filterChips, search, searchFields]);

  const table = useReactTable({
    data: filteredData,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const groupedRows = useMemo(() => {
    if (!groupByField) return null;
    const groups = new Map<string, T[]>();
    for (const row of filteredData) {
      const key = String(row[groupByField] ?? "Unspecified");
      const list = groups.get(key) ?? [];
      list.push(row);
      groups.set(key, list);
    }
    return Array.from(groups.entries());
  }, [filteredData, groupByField]);

  const columnSums = useMemo(() => {
    const sums: Record<string, number> = {};
    for (const f of sumFields) {
      sums[String(f)] = filteredData.reduce((acc, r) => acc + Number(r[f] ?? 0), 0);
    }
    return sums;
  }, [filteredData, sumFields]);

  const toggleSelect = (id: string | number, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedIds.size === filteredData.length && filteredData.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(filteredData.map((r) => r.id)));
    }
  };

  const selectedRows = filteredData.filter((r) => selectedIds.has(r.id));

  const exportVisibleCsv = () => {
    const keys = columns
      .map((c) => ("accessorKey" in c ? String(c.accessorKey) : ""))
      .filter(Boolean);
    const lines = [
      keys.join(","),
      ...filteredData.map((r) =>
        keys.map((k) => `"${String((r as any)[k] ?? "").replace(/"/g, '""')}"`).join(",")
      ),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title.toLowerCase().replace(/\s+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-3">
      {/* Control Panel Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex items-center gap-3">
          {onCreateNew && (
            <button
              type="button"
              onClick={onCreateNew}
              className="inline-flex items-center gap-1.5 rounded-lg bg-odoo-purple px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-odoo-purpleDark transition"
            >
              <Plus className="h-4 w-4" />
              {createLabel || t(locale, "newRecord")}
            </button>
          )}
          <div>
            <h1 className="text-base font-bold text-slate-900">{title}</h1>
            {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
          </div>
        </div>

        {/* Search + Group By + Export */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute start-2.5 top-2 h-4 w-4 text-slate-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t(locale, "searchPlaceholder")}
              className="w-56 rounded-lg border border-slate-300 py-1.5 ps-8 pe-3 text-xs outline-none focus:border-odoo-purple focus:ring-2 focus:ring-odoo-purple/15"
            />
          </div>

          {groupByOptions.length > 0 && (
            <div className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-50 px-2.5 py-1.5 text-xs">
              <Layers className="h-3.5 w-3.5 text-slate-500" />
              <span className="text-slate-500">{t(locale, "groupBy")}:</span>
              <select
                value={String(groupByField)}
                onChange={(e) => setGroupByField(e.target.value as keyof T | "")}
                className="bg-transparent font-medium text-slate-800 outline-none cursor-pointer"
              >
                <option value="">{t(locale, "none")}</option>
                {groupByOptions.map((opt) => (
                  <option key={String(opt.field)} value={String(opt.field)}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            type="button"
            onClick={exportVisibleCsv}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition"
          >
            <Download className="h-3.5 w-3.5" />
            {t(locale, "exportCsv")}
          </button>
        </div>
      </div>

      {/* Filter Chips + Bulk Actions Bar */}
      {(filterChips.length > 0 || selectedRows.length > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {filterChips.map((chip) => {
              const isActive = activeChip === chip.id;
              return (
                <button
                  key={chip.id}
                  type="button"
                  onClick={() => setActiveChip(isActive ? null : chip.id)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                    isActive
                      ? "bg-odoo-teal text-white shadow-sm"
                      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {chip.label}
                </button>
              );
            })}
          </div>

          {selectedRows.length > 0 && (
            <div className="flex items-center gap-2 rounded-lg border border-purple-200 bg-purple-50 px-3 py-1.5">
              <span className="text-xs font-semibold text-odoo-purple">
                {selectedRows.length} selected
              </span>
              {bulkActions.map((act) => (
                <button
                  key={act.id}
                  type="button"
                  onClick={() => {
                    act.onClick(selectedRows);
                    setSelectedIds(new Set());
                  }}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition ${
                    act.variant === "danger"
                      ? "bg-rose-600 text-white hover:bg-rose-700"
                      : act.variant === "primary"
                      ? "bg-odoo-purple text-white hover:bg-odoo-purpleDark"
                      : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  {act.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* High-Density Data Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full border-collapse text-start text-xs">
          <thead className="border-b border-slate-200 bg-slate-50 text-[11px] font-semibold uppercase tracking-wide text-slate-600">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                <th className="w-9 px-3 py-2.5">
                  <input
                    type="checkbox"
                    checked={filteredData.length > 0 && selectedIds.size === filteredData.length}
                    onChange={toggleAll}
                    className="rounded border-slate-300 text-odoo-purple focus:ring-odoo-purple"
                  />
                </th>
                {hg.headers.map((header) => (
                  <th
                    key={header.id}
                    onClick={header.column.getToggleSortingHandler()}
                    className="cursor-pointer select-none px-3 py-2.5 hover:bg-slate-100"
                  >
                    <div className="inline-flex items-center gap-1">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      <ArrowUpDown className="h-3 w-3 opacity-40" />
                    </div>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading ? (
              <tr>
                <td colSpan={columns.length + 1} className="py-10 text-center text-slate-400">
                  Loading records...
                </td>
              </tr>
            ) : filteredData.length === 0 ? (
              <tr>
                <td colSpan={columns.length + 1} className="py-10 text-center text-slate-400">
                  No matching records found.
                </td>
              </tr>
            ) : groupedRows ? (
              groupedRows.map(([groupKey, items]) => (
                <React.Fragment key={groupKey}>
                  <tr className="bg-slate-100/80 font-semibold text-slate-800">
                    <td colSpan={columns.length + 1} className="px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span>
                          {groupKey} <span className="text-slate-500">({items.length})</span>
                        </span>
                        {sumFields.length > 0 && (
                          <div className="flex gap-4 tabular-nums">
                            {sumFields.map((sf) => {
                              const gSum = items.reduce((a, r) => a + Number(r[sf] ?? 0), 0);
                              return (
                                <span key={String(sf)} className="text-odoo-purple">
                                  {gSum.toLocaleString("en-US", {
                                    minimumFractionDigits: 2,
                                    maximumFractionDigits: 2,
                                  })}
                                </span>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                  {items.map((item) => {
                    const rowObj = table.getRowModel().rows.find((r) => r.original.id === item.id);
                    if (!rowObj) return null;
                    return (
                      <tr
                        key={rowObj.id}
                        onClick={() => onRowClick?.(item)}
                        className="cursor-pointer hover:bg-purple-50/40 transition"
                      >
                        <td className="px-3 py-2.5" onClick={(e) => toggleSelect(item.id, e)}>
                          <input
                            type="checkbox"
                            checked={selectedIds.has(item.id)}
                            onChange={() => {}}
                            className="rounded border-slate-300"
                          />
                        </td>
                        {rowObj.getVisibleCells().map((cell) => (
                          <td key={cell.id} className="px-3 py-2.5 whitespace-nowrap">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </React.Fragment>
              ))
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onRowClick?.(row.original)}
                  className={`cursor-pointer transition ${
                    selectedIds.has(row.original.id) ? "bg-purple-50/70" : "hover:bg-slate-50"
                  }`}
                >
                  <td className="px-3 py-2.5" onClick={(e) => toggleSelect(row.original.id, e)}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(row.original.id)}
                      onChange={() => {}}
                      className="rounded border-slate-300"
                    />
                  </td>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2.5 whitespace-nowrap">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>

          {/* Column Sum Footer */}
          {sumFields.length > 0 && filteredData.length > 0 && (
            <tfoot className="border-t-2 border-slate-200 bg-slate-50 font-bold text-slate-900">
              <tr>
                <td className="px-3 py-2.5" />
                {table.getHeaderGroups()[0].headers.map((header, idx) => {
                  const key =
                    "accessorKey" in header.column.columnDef
                      ? String(header.column.columnDef.accessorKey)
                      : "";
                  const hasSum = key && key in columnSums;
                  return (
                    <td key={header.id} className="px-3 py-2.5 tabular-nums">
                      {idx === 0
                        ? `Total (${filteredData.length})`
                        : hasSum
                        ? columnSums[key].toLocaleString("en-US", {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })
                        : ""}
                    </td>
                  );
                })}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
