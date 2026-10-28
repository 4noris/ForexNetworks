import React, { useCallback, useEffect, useRef, useState } from "react";
import gsap from "gsap";
import {
  Activity,
  BellRing,
  BookOpen,
  ChevronDown,
  Loader2,
  Save,
  Send,
  Server,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import GroupTreeSelect from "../components/GroupTreeSelect";
import HelpCenter from "../components/HelpCenter";
import NewsPushSettings from "../components/NewsPushSettings";
import SpreadAlertSettings from "../components/SpreadAlertSettings";
import { useManagerStatus } from "../hooks/useManagerQueries";
import {
  clearBookShiftEvents,
  fetchCompleteMt5Groups,
  getBookGroupSettings,
  getSelectedProfile,
  getTelegramStatus,
  resetRmsDatabase,
  sendTelegramTest,
  updateBookGroupSettings,
  updateTelegramSettings,
} from "../lib/api";

const DEFAULT_SETTINGS = {
  enabled: true,
  risk_alerts: true,
  antifraud_alerts: true,
  profit_alert_pct: 30,
  double_profit_alert_pct: 100,
  high_lot_threshold: 1,
  account_lot_threshold: 5,
  channel_routes: {
    vps: "",
    scalpers: "",
    win_rate: "",
    risk: "",
    antifraud: "",
    spread: "",
  },
};

const TELEGRAM_ROUTES = [
  ["spread", "Spread alerts", "Spread threshold breaches from fresh MT5 quotes"],
  ["vps", "VPS alerts", "Live VPS and hosting-provider detections"],
  ["scalpers", "Scalper alerts", "Scalping and HFT activity"],
  ["win_rate", "Win-rate alerts", "High and low win-rate signals"],
  ["risk", "Risk alerts", "Exposure, margin and position alerts"],
  ["antifraud", "Anti-fraud alerts", "Fraud evidence and toxic patterns"],
];

const EMPTY_BOOKS = { a_book: [], b_book: [], cent: [], hybrid: [] };
const BOOK_FIELDS = [
  ["a_book", "A Book", "Externally routed or hedged group set"],
  ["b_book", "B Book", "Internalized or dealing-desk group set"],
  ["cent", "Cent", "Cent-denominated account groups"],
  ["hybrid", "Hybrid", "Mixed execution model groups"],
];

function Toggle({ checked, onChange, label, description }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4 border-b border-[rgb(var(--color-border))] py-4 last:border-0">
      <span className="min-w-0">
        <span className="block text-sm font-medium text-[rgb(var(--color-text))]">
          {label}
        </span>
        <span className="mt-1 block text-xs leading-5 text-[rgb(var(--color-text-3))]">
          {description}
        </span>
      </span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 accent-black"
      />
    </label>
  );
}

function NumberField({ label, value, onChange, suffix }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[10px] font-semibold uppercase tracking-[0.2em] text-[rgb(var(--color-text-3))]">
        {label}
      </span>
      <div className="relative">
        <input
          type="number"
          min="0"
          step="0.1"
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="field-input pr-12"
        />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[rgb(var(--color-text-3))]">
          {suffix}
        </span>
      </div>
    </label>
  );
}

export default function Settings() {
  const profile = getSelectedProfile();
  const containerRef = useRef(null);
  const { data: manager } = useManagerStatus();
  const [telegram, setTelegram] = useState(null);
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testRoute, setTestRoute] = useState("default");
  const [availableGroups, setAvailableGroups] = useState([]);
  const [books, setBooks] = useState(EMPTY_BOOKS);
  const [savingBooks, setSavingBooks] = useState(false);
  const [booksExpanded, setBooksExpanded] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [clearShiftsOpen, setClearShiftsOpen] = useState(false);
  const [clearShiftsConfirmation, setClearShiftsConfirmation] = useState("");
  const [clearingShifts, setClearingShifts] = useState(false);
  const [resetPassword, setResetPassword] = useState("");
  const [resetConfirmation, setResetConfirmation] = useState("");
  const [resetting, setResetting] = useState(false);

  const refresh = useCallback(async () => {
    const [telegramResult, booksResult] =
      await Promise.allSettled([
        getTelegramStatus(),
        getBookGroupSettings({ refresh: true }),
      ]);
    if (telegramResult.status === "fulfilled") {
      setTelegram(telegramResult.value);
      setSettings({
        ...DEFAULT_SETTINGS,
        ...(telegramResult.value?.settings || {}),
        channel_routes: {
          ...DEFAULT_SETTINGS.channel_routes,
          ...(telegramResult.value?.settings?.channel_routes || {}),
        },
      });
    }
    if (booksResult.status === "fulfilled") {
      setBooks({ ...EMPTY_BOOKS, ...booksResult.value });
    }
  }, []);

  useEffect(() => {
    gsap.fromTo(
      containerRef.current,
      { opacity: 0, y: 10 },
      { opacity: 1, y: 0, duration: 0.35, ease: "power3.out" },
    );
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!booksExpanded || availableGroups.length) return;
    let active = true;
    fetchCompleteMt5Groups(profile)
      .then((groups) => {
        if (active) setAvailableGroups(groups);
      })
      .catch((error) => {
        if (active) toast.error(error.message || "Unable to load MT5 groups");
      });
    return () => {
      active = false;
    };
  }, [availableGroups.length, booksExpanded, profile]);

  const update = (key, value) =>
    setSettings((current) => ({ ...current, [key]: value }));

  const save = async () => {
    setSaving(true);
    try {
      const result = await updateTelegramSettings(settings);
      setTelegram(result);
      setSettings({ ...DEFAULT_SETTINGS, ...(result?.settings || {}) });
      toast.success("Alert controls saved");
    } catch (error) {
      toast.error(error.message || "Unable to save alert controls");
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async () => {
    setTesting(true);
    try {
      await sendTelegramTest(
        `MT5 Brokerage alert test\nTime: ${new Date().toLocaleString()}`,
        testRoute,
      );
      toast.success("Telegram test sent");
    } catch (error) {
      toast.error(error.message || "Telegram test failed");
    } finally {
      setTesting(false);
    }
  };

  const updateBook = (book, groups) => {
    const selected = new Set(groups);
    setBooks((current) =>
      Object.fromEntries(
        Object.keys(EMPTY_BOOKS).map((key) => [
          key,
          key === book
            ? groups
            : (current[key] || []).filter((group) => !selected.has(group)),
        ]),
      ),
    );
  };

  const saveBooks = async () => {
    setSavingBooks(true);
    try {
      const result = await updateBookGroupSettings(books);
      setBooks({ ...EMPTY_BOOKS, ...result });
      toast.success("Book group presets saved");
    } catch (error) {
      toast.error(error.message || "Unable to save book group presets");
    } finally {
      setSavingBooks(false);
    }
  };

  const connected = manager?.status === "connected";
  const synchronizing = manager?.status === "warming";
  const updateChannelRoute = (route, chatId) =>
    setSettings((current) => ({
      ...current,
      channel_routes: { ...current.channel_routes, [route]: chatId },
    }));

  const resetDatabase = async (event) => {
    event.preventDefault();
    if (resetConfirmation !== "DELETE ALL DATA") {
      return toast.error('Type "DELETE ALL DATA" exactly');
    }
    if (!window.confirm("Permanently clear all RMS database data?")) return;
    setResetting(true);
    try {
      const result = await resetRmsDatabase(resetPassword, resetConfirmation);
      for (const key of Object.keys(localStorage)) {
        if (
          key.startsWith("performanceLeaders:") ||
          key.startsWith("bbook") ||
          key.startsWith("bookPnl")
        ) {
          localStorage.removeItem(key);
        }
      }
      toast.success(
        `Database reset complete: ${result.deleted_rows} rows cleared`,
      );
      setResetOpen(false);
      setResetPassword("");
      setResetConfirmation("");
      await refresh();
    } catch (error) {
      toast.error(error.message || "Database reset failed");
    } finally {
      setResetting(false);
    }
  };

  const clearShifts = async (event) => {
    event.preventDefault();
    if (clearShiftsConfirmation !== "CLEAR BOOK SHIFTS") return;
    setClearingShifts(true);
    try {
      const result = await clearBookShiftEvents();
      window.dispatchEvent(new Event("book-shifts-cleared"));
      toast.success(`${result.deleted_rows} book-shift events cleared`);
      setClearShiftsOpen(false);
      setClearShiftsConfirmation("");
    } catch (error) {
      toast.error(error.message || "Unable to clear book-shift history");
    } finally {
      setClearingShifts(false);
    }
  };

  return (
    <div
      ref={containerRef}
      className="mx-auto w-full max-w-[1600px] px-4 pb-10 pt-5 md:px-6"
    >
      <header>
        <p className="text-[9px] font-semibold uppercase tracking-[0.36em] text-[rgb(var(--color-text-3))]">
          Administrative
        </p>
        <h1 className="mt-1 text-xl font-semibold text-[rgb(var(--color-text))]">
          System controls
        </h1>
      </header>

      <section className="mt-7 border-y border-[rgb(var(--color-border))] py-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <div className="settings-icon">
              <Server size={17} />
            </div>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-[rgb(var(--color-text-3))]">
                Single MT5 manager
              </p>
              <h2 className="mt-1 text-sm font-semibold">{profile.name}</h2>
            </div>
          </div>
          <span
            className={connected ? "badge badge-success" : synchronizing ? "badge text-amber-500" : "badge badge-danger"}
          >
            <Activity size={12} /> {connected ? "Connected" : synchronizing ? (manager.source_connected ? "Updating" : "Synchronizing") : "Disconnected"}
          </span>
        </div>
        {!connected && manager?.error && (
          <p className={`mt-3 text-sm ${synchronizing ? "text-amber-500" : "text-danger"}`}>
            {synchronizing ? "MT5 data status" : "MT5 connection error"}: {manager.error}
          </p>
        )}
      </section>

      <section className="mt-7 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 sm:p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="settings-icon"><BookOpen size={18} /></span>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-[rgb(var(--color-text-3))]">
                Books
              </p>
              <h2 className="mt-1 text-sm font-semibold">
                Reusable group presets
              </h2>
              <p className="mt-1 text-xs text-[rgb(var(--color-text-3))]">
                Configure once, then select a book from group dropdowns
                throughout RMS.
              </p>
            </div>
          </div>
          <div className="min-w-0 text-left sm:text-right">
            <button
              type="button"
              onClick={() => setBooksExpanded((expanded) => !expanded)}
              aria-expanded={booksExpanded}
              aria-controls="book-group-editor"
              className="btn-ghost flex items-center gap-2 px-2.5 py-1.5 sm:ml-auto"
            >
              <span className="badge">
                {availableGroups.length ? `All Groups · ${availableGroups.length}` : "Choose book groups"}
              </span>
              <ChevronDown
                size={14}
                className={`transition-transform duration-200 ${booksExpanded ? "rotate-180" : ""}`}
              />
            </button>
            <p className="mt-1 text-[10px] text-[rgb(var(--color-text-3))]">
              Group choices load when you open this selector.
            </p>
          </div>
        </div>
        {booksExpanded && (
          <div id="book-group-editor">
            <div className="mt-5 grid gap-4 md:grid-cols-2">
              {BOOK_FIELDS.map(([key, name, description]) => (
                <div
                  key={key}
                  className="rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <div>
                      <p className="text-xs font-semibold">{name}</p>
                      <p className="text-[10px] text-muted">{description}</p>
                    </div>
                    <span className="badge">{books[key]?.length || 0}</span>
                  </div>
                  <GroupTreeSelect
                    groups={availableGroups}
                    value={books[key] || []}
                    onChange={(groups) => updateBook(key, groups)}
                    label={`${name} groups`}
                    showBookPresets={false}
                    emptyMeansAll={false}
                  />
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={saveBooks}
              disabled={savingBooks || !availableGroups.length}
              className="btn-primary mt-5 px-5 py-2.5 text-xs"
            >
              {savingBooks ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Save size={14} />
              )}{" "}
              Save Books
            </button>
          </div>
        )}
      </section>

      <section className="mt-4 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-sm font-semibold">Book Shifting history</h2><p className="mt-1 text-xs text-muted">Clear recorded group movements. Book presets, current membership, and Book P&amp;L history stay available.</p></div>
          <button type="button" className="btn-secondary text-xs" onClick={() => setClearShiftsOpen(true)}><Trash2 size={14} /> Clear shift history</button>
        </div>
      </section>

      {clearShiftsOpen && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
        <form onSubmit={clearShifts} className="w-full max-w-md rounded-2xl border border-red-500/30 bg-[rgb(var(--color-card))] p-5 shadow-2xl">
          <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Clear Book Shifting history?</h2><button type="button" className="btn-ghost p-2" aria-label="Close dialog" onClick={() => setClearShiftsOpen(false)}><X size={16} /></button></div>
          <p className="mt-3 text-xs text-muted">This permanently deletes recorded shift events. It keeps book group settings, the current membership baseline, and Book P&amp;L history.</p>
          <label className="mt-4 block text-xs font-medium">Type CLEAR BOOK SHIFTS<input className="field-input mt-1.5" value={clearShiftsConfirmation} onChange={(event) => setClearShiftsConfirmation(event.target.value)} autoComplete="off" required /></label>
          <button type="submit" className="btn-primary mt-5 w-full" disabled={clearingShifts || clearShiftsConfirmation !== "CLEAR BOOK SHIFTS"}>{clearingShifts ? "Clearing..." : "Clear shift history"}</button>
        </form>
      </div>}

      <NewsPushSettings />
      <SpreadAlertSettings telegramReady={Boolean(telegram?.bot_token && telegram?.settings?.enabled && (telegram?.chat_id || telegram?.settings?.channel_routes?.spread))} />

      <div className="mt-7 grid items-start gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5">
          <div className="flex items-start gap-3">
            <span className="settings-icon"><BellRing size={18} /></span>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-[rgb(var(--color-text-3))]">
                Telegram delivery
              </p>
              <h2 className="mt-1 text-sm font-semibold">Alert channels</h2>
            </div>
          </div>
          <div className="mt-3">
            <Toggle
              checked={settings.enabled}
              onChange={(value) => update("enabled", value)}
              label="Telegram alerts"
              description="Master control for all automatic Telegram delivery."
            />
            <Toggle
              checked={settings.risk_alerts}
              onChange={(value) => update("risk_alerts", value)}
              label="Risk alerts"
              description="Critical accounts, high exposure, VPS signals, and large positions."
            />
            <Toggle
              checked={settings.antifraud_alerts}
              onChange={(value) => update("antifraud_alerts", value)}
              label="Anti-fraud alerts"
              description="High-severity trading patterns and network evidence."
            />
          </div>
          <div className="mt-4 rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] p-3">
            <div className="mb-3">
              <p className="text-xs font-semibold">Channel routing</p>
              <p className="mt-1 text-[10px] leading-4 text-muted">
                Enter a Telegram chat ID for each category. Leave it blank to
                use the default channel.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {TELEGRAM_ROUTES.map(([route, label, description]) => (
                <label key={route} className="min-w-0 text-xs text-muted">
                  <span className="block font-medium text-primary">
                    {label}
                  </span>
                  <span
                    className="mt-0.5 block truncate text-[10px]"
                    title={description}
                  >
                    {description}
                  </span>
                  <input
                    className="field-input mt-1.5 !py-2"
                    value={settings.channel_routes?.[route] || ""}
                    onChange={(event) =>
                      updateChannelRoute(route, event.target.value)
                    }
                    placeholder="Default channel"
                    autoComplete="off"
                  />
                </label>
              ))}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span
              className={
                telegram?.bot_token
                  ? "badge badge-success"
                  : "badge badge-danger"
              }
            >
              Bot token
            </span>
            <span
              className={
                telegram?.chat_id ? "badge badge-success" : "badge badge-danger"
              }
            >
              Chat ID
            </span>
            <select
              className="field-input ml-auto !w-auto !py-2 text-xs"
              value={testRoute}
              onChange={(event) => setTestRoute(event.target.value)}
              aria-label="Telegram test channel"
            >
              <option value="default">Default channel</option>
              {TELEGRAM_ROUTES.map(([route, label]) => (
                <option key={route} value={route}>
                  {label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={sendTest}
              disabled={testing}
              className="btn-ghost text-xs"
            >
              {testing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Send size={14} />
              )}{" "}
              Test
            </button>
          </div>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="btn-primary mt-4 w-full py-2.5 text-xs"
          >
            {saving ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Save size={14} />
            )}{" "}
            Save Telegram delivery
          </button>
        </section>

        <div className="space-y-6">
          <section className="rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5">
            <div className="flex items-start gap-3">
              <span className="settings-icon"><ShieldAlert size={18} /></span>
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-[rgb(var(--color-text-3))]">
                  Trigger thresholds
                </p>
                <h2 className="mt-1 text-sm font-semibold">
                  Alert sensitivity
                </h2>
              </div>
            </div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <NumberField
                label="Profit alert"
                value={settings.profit_alert_pct}
                onChange={(value) => update("profit_alert_pct", value)}
                suffix="%"
              />
              <NumberField
                label="Critical profit"
                value={settings.double_profit_alert_pct}
                onChange={(value) => update("double_profit_alert_pct", value)}
                suffix="%"
              />
              <NumberField
                label="Position size"
                value={settings.high_lot_threshold}
                onChange={(value) => update("high_lot_threshold", value)}
                suffix="lots"
              />
              <NumberField
                label="Account exposure"
                value={settings.account_lot_threshold}
                onChange={(value) => update("account_lot_threshold", value)}
                suffix="lots"
              />
            </div>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="btn-primary mt-6 w-full py-2.5 text-xs"
            >
              {saving ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Save size={14} />
              )}{" "}
              Save controls
            </button>
          </section>
          <HelpCenter />
        </div>
      </div>

      <section className="mt-7 rounded-lg border border-red-500/25 bg-red-500/5 p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="settings-icon text-danger"><Trash2 size={18} /></span>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-danger">
                Danger zone
              </p>
              <h2 className="mt-1 text-sm font-semibold">Reset RMS database</h2>
              <p className="mt-1 max-w-2xl text-xs leading-5 text-muted">
                Clear cases, alerts, cached reports, CID evidence, P&amp;L
                history, book settings, automation data, and audit history.
                Administrator login accounts and database schema are preserved.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setResetOpen(true)}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-xs font-semibold text-danger hover:bg-red-500/15"
          >
            <Trash2 size={14} /> Delete database data
          </button>
        </div>
      </section>

      {resetOpen && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <form
            onSubmit={resetDatabase}
            className="w-full max-w-md rounded-2xl border border-red-500/30 bg-[rgb(var(--color-card))] p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-danger">
                  Permanent action
                </p>
                <h2 className="mt-1 text-lg font-semibold">
                  Delete all RMS data?
                </h2>
              </div>
              <button
                type="button"
                onClick={() => !resetting && setResetOpen(false)}
                className="btn-ghost p-2"
                aria-label="Close reset dialog"
              >
                <X size={16} />
              </button>
            </div>
            <p className="mt-3 text-xs leading-5 text-muted">
              This cannot be undone. MT5 source data is not deleted, but all
              data retained by this RMS will be cleared.
            </p>
            <label className="mt-4 block text-xs font-medium">
              Reset password
              <input
                type="password"
                value={resetPassword}
                onChange={(event) => setResetPassword(event.target.value)}
                className="field-input mt-1.5"
                autoComplete="off"
                required
              />
            </label>
            <label className="mt-4 block text-xs font-medium">
              Type DELETE ALL DATA
              <input
                value={resetConfirmation}
                onChange={(event) => setResetConfirmation(event.target.value)}
                className="field-input mt-1.5"
                autoComplete="off"
                required
              />
            </label>
            <button
              type="submit"
              disabled={
                resetting ||
                !resetPassword ||
                resetConfirmation !== "DELETE ALL DATA"
              }
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-3 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {resetting ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Trash2 size={14} />
              )}
              {resetting ? "Clearing database…" : "Permanently delete data"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
