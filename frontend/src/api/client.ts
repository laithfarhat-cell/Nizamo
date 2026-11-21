/**
 * Relative API client using Vite/Nginx `/api` proxy with JWT Access + Refresh Token support.
 */

let activeCompanyId: number | null = null;

export function setActiveCompanyId(companyId: number | null): void {
  activeCompanyId = companyId;
}

export function getActiveCompanyId(): number | null {
  return activeCompanyId;
}

export function saveAuthTokens(accessToken: string, refreshToken?: string): void {
  localStorage.setItem("nizamo_token", accessToken);
  if (refreshToken) {
    localStorage.setItem("nizamo_refresh_token", refreshToken);
  }
}

export function clearAuthTokens(): void {
  localStorage.removeItem("nizamo_token");
  localStorage.removeItem("nizamo_refresh_token");
}

async function tryRefreshAccessToken(): Promise<boolean> {
  const refresh = localStorage.getItem("nizamo_refresh_token");
  if (!refresh) return false;
  try {
    const res = await fetch("/api/v1/core/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) {
      clearAuthTokens();
      return false;
    }
    const data = await res.json();
    saveAuthTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  _retried = false
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

  if (res.status === 401 && !_retried && !path.includes("/auth/")) {
    const refreshed = await tryRefreshAccessToken();
    if (refreshed) {
      return apiRequest<T>(path, options, true);
    }
  }

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
  const token = localStorage.getItem("nizamo_token");
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`/api/v1${path}`, { headers });
  if (!res.ok) {
    throw new Error(`Download failed (${res.status})`);
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
