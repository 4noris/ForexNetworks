import React, { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  BarChart3,
  Settings,
  ChevronLeft,
  ChevronRight,
  Menu,
  X,
  LogOut,
  Sun,
  Moon,
  ShieldAlert,
  Activity,
  TrendingUp,
  BellRing,
  GitBranch,
  CalendarDays,
  ArrowRightLeft,
  WalletCards,
  RefreshCw,
} from "lucide-react";
import { cn } from "../lib/utils";
import { clearSession, storeSelectedProfile, useStoredProfile } from "../lib/api";
import { useManagerStatus } from "../hooks/useManagerQueries";
import { applyTheme, getStoredTheme, isBlackPantherTheme } from "../lib/theme";

const navItems = [
  { path: "/command-center", icon: LayoutDashboard, label: "Command Center" },
  { path: "/dealing-risk", icon: ShieldAlert, label: "Dealing and Risk" },
  { path: "/risk-center", icon: ShieldAlert, label: "Risk Center" },
  { path: "/manager", icon: Activity, label: "Manager" },
  { path: "/market-watch", icon: TrendingUp, label: "Market Watch" },
  { path: "/operations", icon: BellRing, label: "Operations" },
  { path: "/book-shifting", icon: ArrowRightLeft, label: "Book Shifting" },
  { path: "/risk-automation", icon: GitBranch, label: "Risk Automation" },
  { path: "/calendar", icon: CalendarDays, label: "Calendar" },
  { path: "/bbook-pnl", icon: WalletCards, label: "Book P&L" },
  { path: "/deal-report", icon: LayoutDashboard, label: "PnL" },
  { path: "/real-brokerage", icon: BarChart3, label: "Brokerage" },
  { path: "/settings", icon: Settings, label: "Controls" },
];

const preloadPage = {
  "/command-center": () => import("../pages/DealerCommandCenter"),
  "/dealing-risk": () => import("../pages/DealingRisk"),
  "/risk-center": () => import("../pages/RiskCenter"),
  "/manager": () => import("../pages/ManagerOverview"),
  "/market-watch": () => import("../pages/MarketWatchPage"),
  "/operations": () => import("../pages/OperationsCenter"),
  "/book-shifting": () => import("../pages/BookShifting"),
  "/risk-automation": () => import("../pages/RiskAutomation"),
  "/calendar": () => import("../pages/EconomicCalendar"),
  "/bbook-pnl": () => import("../pages/BBookPnL"),
  "/deal-report": () => import("../pages/Dashboard"),
  "/real-brokerage": () => import("../pages/BrokerageWorkspace"),
  "/settings": () => import("../pages/Settings"),
};
const warmPage = (path) => {
  // Navigation still owns the load error; speculative preloads must not
  // surface as unhandled promise rejections during rapid hover/navigation.
  preloadPage[path]?.().catch(() => {});
};

export default function Sidebar() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const refreshCurrentSection = () => {
    const keys = location.pathname === "/risk-center" ? ["risk-health", "risk-center-limits", "risk-configured-limits"]
      : ["manager-status", "dashboard", "accounts", "market-watch", "risk-automation-overview", "risk-realized"];
    queryClient.invalidateQueries({ predicate: (query) => keys.includes(query.queryKey[0]) });
    window.dispatchEvent(new CustomEvent("rms:refresh-section", { detail: { path: location.pathname } }));
  };
  const profile = useStoredProfile();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState(() => getStoredTheme());
  const { data: manager } = useManagerStatus();
  const isConnected = manager?.status === "connected";
  const synchronizing = manager?.status === "warming";
  const [now, setNow] = useState(() => Date.now());
  const cacheAge = manager?.last_update ? Math.max(0, now / 1000 - Number(manager.last_update)) : null;
  const isLive = isConnected && cacheAge !== null && Number.isFinite(cacheAge) && cacheAge < 8;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 768px)");
    const handleViewport = (event) => {
      if (event.matches) setMobileOpen(false);
    };
    media.addEventListener("change", handleViewport);
    return () => media.removeEventListener("change", handleViewport);
  }, []);
  useEffect(() => {
    if (
      manager?.server &&
      (profile?.id !== "default" ||
        profile.server !== manager.server ||
        Number(profile.mt5_login) !== Number(manager.login))
    ) {
      storeSelectedProfile({
        id: "default",
        name: "Manager",
        server: manager.server,
        mt5_login: Number(manager.login),
        mt5_password: "",
      });
    }
  }, [manager?.server, manager?.login, profile?.id, profile?.mt5_login, profile?.server]);

  const toggleTheme = () =>
    setTheme((cur) =>
      isBlackPantherTheme(cur) ? "white-panther" : "black-panther",
    );

  const isDark = isBlackPantherTheme(theme);

  return (
    <>
      <div className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-3 border-b border-[rgb(var(--color-border))] bg-[rgb(var(--color-surface))] px-3 md:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-[rgb(var(--color-border))] bg-[var(--btn-ghost-bg)]"
          aria-label="Open navigation"
          aria-expanded={mobileOpen}
        >
          <Menu size={18} />
        </button>
        <div className="min-w-0">
          <p className="text-[9px] uppercase tracking-[0.25em] text-[rgb(var(--color-text-3))]">
            Brokerage Suite
          </p>
        </div>
      </div>
      {mobileOpen && (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-black/55 backdrop-blur-sm md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="Close navigation"
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r transition-all duration-300 md:relative md:z-20 md:translate-x-0",
          "bg-[rgb(var(--color-surface))] border-[rgb(var(--color-border))]",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          collapsed ? "md:w-[72px]" : "md:w-64",
        )}
        style={{ boxShadow: "var(--surface-shadow)" }}
      >
        {/* Logo row */}
        <div className="flex h-14 items-center justify-between border-b border-[rgb(var(--color-border))] px-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <div
              className={cn(
                "min-w-0 overflow-hidden whitespace-nowrap transition-all duration-300",
                collapsed ? "w-0 opacity-0" : "w-auto opacity-100",
              )}
            >
              <p className="text-[10px] uppercase tracking-[0.3em] text-[rgb(var(--color-text-3))]">
                Brokerage Suite
              </p>
            </div>
          </div>
          <button
            onClick={() => {
              if (window.matchMedia("(min-width: 768px)").matches)
                setCollapsed(!collapsed);
              else setMobileOpen(false);
            }}
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-[rgb(var(--color-border))] bg-[var(--btn-ghost-bg)] text-[rgb(var(--color-text-3))] transition-colors hover:text-[rgb(var(--color-text))]"
            aria-label="Toggle sidebar"
          >
            <span className="md:hidden">
              <X size={16} />
            </span>
            <span className="hidden md:inline">
              {collapsed ? (
                <ChevronRight size={16} />
              ) : (
                <ChevronLeft size={16} />
              )}
            </span>
          </button>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-0.5 px-2 py-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                end={item.path === "/dealing-risk"}
                onMouseEnter={() => warmPage(item.path)}
                onFocus={() => warmPage(item.path)}
                onTouchStart={() => warmPage(item.path)}
                onClick={() => setMobileOpen(false)}
                title={collapsed ? item.label : undefined}
                className={({ isActive }) =>
                  cn("nav-item", isActive && "active")
                }
              >
                <Icon size={18} className="flex-shrink-0" />
                <span
                  className={cn(
                    "transition-all duration-300 text-sm",
                    collapsed
                      ? "w-0 opacity-0 pointer-events-none"
                      : "w-auto opacity-100",
                  )}
                >
                  {item.label}
                </span>
              </NavLink>
            );
          })}
        </nav>

        {/* Bottom panel */}
        <div
          className={cn(
            "mt-auto border-t border-[rgb(var(--color-border))] p-3",
            collapsed && "px-2",
          )}
        >
          {/* System manager status */}
          <div className="mb-3 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3">
            {!collapsed && (
              <>
                <p className="text-[10px] uppercase tracking-[0.28em] text-[rgb(var(--color-text-3))]">
                  Live Manager
                </p>
                <div title={manager?.error || ""} className="mt-2 flex items-center gap-1.5 border-t border-[rgb(var(--color-border))] pt-2 text-[11px]">
                  <Activity size={12} className={isLive ? "text-success" : isConnected || synchronizing ? "text-amber-500" : "text-danger"} />
                  <span className={isLive ? "text-success" : "text-[rgb(var(--color-text-2))]"}>
                    {isLive ? "MT5 live" : synchronizing ? (manager.source_connected ? "MT5 updating" : "MT5 synchronizing") : isConnected ? "MT5 updating" : "MT5 offline"}
                  </span>
                  <span className="ml-auto text-[rgb(var(--color-text-3))]">
                    {cacheAge === null || !Number.isFinite(cacheAge) ? "no snapshot" : `${Math.round(cacheAge)}s ago`}
                  </span>
                </div>
                <button type="button" onClick={refreshCurrentSection}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-2 py-2 text-xs font-semibold transition hover:border-emerald-500/50 hover:text-success focus-visible:outline focus-visible:outline-2 focus-visible:outline-emerald-500"
                  aria-label="Refresh current section" title="Refresh current section and request live data again">
                  <RefreshCw size={13} /> Refresh current section
                </button>
              </>
            )}
            {collapsed && (
              <button type="button" onClick={refreshCurrentSection} className="mx-auto flex h-8 w-8 items-center justify-center rounded-lg border border-[rgb(var(--color-border))]" aria-label="Refresh current section" title="Refresh current section">
                <RefreshCw size={14} />
              </button>
            )}
          </div>

          <div className={cn("flex gap-2", collapsed && "flex-col")}>
            {/* Logout */}
            <NavLink
              to="/login"
              onClick={clearSession}
              className={cn(
                "btn-ghost flex-1 border-transparent justify-center",
                "text-danger hover:border-danger/20 hover:bg-danger/5",
                collapsed && "p-2",
              )}
            >
              <LogOut size={16} className="flex-shrink-0" />
              {!collapsed && (
                <span className="text-sm font-medium">Logout</span>
              )}
            </NavLink>

            {/* Theme toggle */}
            <button
              type="button"
              onClick={toggleTheme}
              className={cn(
                "flex items-center justify-center rounded-lg border border-[rgb(var(--color-border))] bg-[var(--btn-ghost-bg)] text-[rgb(var(--color-text-2))] transition-all hover:text-[rgb(var(--color-text))] flex-shrink-0",
                collapsed ? "p-2" : "w-10",
              )}
              aria-label="Toggle theme"
            >
              {isDark ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}
