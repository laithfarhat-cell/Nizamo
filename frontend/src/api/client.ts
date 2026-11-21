/**
 * Relative API client using Vite `/api` proxy (never hardcodes localhost/127.0.0.1 in browser code).
 */

let activeCompanyId: number | null = null;

export function setActiveCompanyId(companyId: number | null): void {
  activeCompanyId = companyId;
}

export function getActiveCompanyId(): number | null {
  return activeCompanyId;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (activeCompanyId !== null) {
    headers["X-Company-Id"] = String(activeCompanyId);
  }
  const token = localStorage.getItem("nizamo_token");
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`/api/v1${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await res.text();
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function downloadReportExport(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (activeCompanyId !== null) {
    headers["X-Company-Id"] = String(activeCompanyId);
  }
  const res = await fetch(`/api/v1${path}`, { headers });
  if (!res.ok) {
    throw new Error(`Export failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function formatMoney(val: string | number | undefined, symbol = "$"): string {
  const num = Number(val ?? 0);
  if (Number.isNaN(num)) return `${symbol}0.00`;
  const formatted = num.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${symbol}${formatted}`;
}
