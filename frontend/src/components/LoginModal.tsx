import React, { useState } from "react";
import { KeyRound, LogIn, ShieldCheck, X } from "lucide-react";
import { apiRequest, clearAuthTokens, saveAuthTokens } from "../api/client";
import type { Locale, UserInfo } from "../types/erp";

interface LoginModalProps {
  locale: Locale;
  currentUser: UserInfo | null;
  onClose: () => void;
  onSuccess: () => void;
}

interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: UserInfo;
}

const DEMO_ROLES = [
  { login: "admin", password: "admin123", label: "System Admin", desc: "Full access across all companies & modules" },
  { login: "accountant", password: "account123", label: "Chief Accountant", desc: "Accounting, GL, Closing, Reconciliation & Reports" },
  { login: "sales", password: "sales123", label: "Sales Representative", desc: "Sales Orders & Customer Invoicing (restricted CoA)" },
];

export const LoginModal: React.FC<LoginModalProps> = ({
  locale,
  currentUser,
  onClose,
  onSuccess,
}) => {
  const [login, setLogin] = useState(currentUser?.login || "admin");
  const [password, setPassword] = useState("admin123");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async (u = login, p = password) => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiRequest<LoginResponse>("/core/auth/login", {
        method: "POST",
        body: JSON.stringify({ login: u, password: p }),
      });
      saveAuthTokens(res.access_token, res.refresh_token);
      onSuccess();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    clearAuthTokens();
    onSuccess();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm p-4">
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 max-w-md w-full overflow-hidden">
        <div className="bg-odoo-purple text-white px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-emerald-300" />
            <div>
              <h3 className="font-bold text-base">
                {locale === "ar" ? "تسجيل الدخول وصلاحيات المستخدم" : "JWT Sign In & RBAC Role Switcher"}
              </h3>
              <p className="text-[11px] text-white/75">
                {locale === "ar"
                  ? "جلسة آمنة برموز JWT Access + Refresh"
                  : "Secure Access + Refresh JWT Session"}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/15">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {currentUser && (
            <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 flex items-center justify-between text-xs">
              <div>
                <div className="font-semibold text-slate-800">{currentUser.full_name}</div>
                <div className="text-slate-500">
                  @{currentUser.login} · {currentUser.roles.join(", ")}
                </div>
              </div>
              <button
                onClick={handleLogout}
                className="text-rose-700 hover:underline font-medium"
              >
                {locale === "ar" ? "تسجيل الخروج" : "Clear Session"}
              </button>
            </div>
          )}

          {error && (
            <div className="bg-rose-50 border border-rose-200 text-rose-700 text-xs px-3 py-2 rounded">
              {error}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleLogin();
            }}
            className="space-y-3"
          >
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                {locale === "ar" ? "اسم المستخدم" : "Login Username"}
              </label>
              <input
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                required
                className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-odoo-purple"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                {locale === "ar" ? "كلمة المرور" : "Password"}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-odoo-purple"
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-odoo-purple hover:opacity-95 text-white py-2 rounded-md text-sm font-medium disabled:opacity-50"
            >
              <LogIn className="w-4 h-4" />
              {loading
                ? locale === "ar"
                  ? "جارٍ التحقق..."
                  : "Authenticating..."
                : locale === "ar"
                ? "تسجيل الدخول"
                : "Sign In"}
            </button>
          </form>

          <div className="border-t border-slate-200 pt-3">
            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-500 uppercase mb-2">
              <KeyRound className="w-3.5 h-3.5" />
              {locale === "ar" ? "تبديل سريع لاختبار الصلاحيات (RBAC)" : "Quick Role Demo Switch"}
            </div>
            <div className="space-y-1.5">
              {DEMO_ROLES.map((r) => (
                <button
                  key={r.login}
                  type="button"
                  disabled={loading}
                  onClick={() => {
                    setLogin(r.login);
                    setPassword(r.password);
                    handleLogin(r.login, r.password);
                  }}
                  className="w-full text-start px-3 py-2 rounded-lg border border-slate-200 hover:border-odoo-purple hover:bg-purple-50/40 transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-800">{r.label}</span>
                    <span className="font-mono text-[11px] text-slate-500">
                      {r.login} / {r.password}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500">{r.desc}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
