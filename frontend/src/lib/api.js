import { useMemo, useSyncExternalStore } from "react";
import { readLiveData, requestLiveData, clearLiveTransport, invalidateLiveData, refreshLiveSession, watchLiveData } from "./liveTransport";
import { isDataRead } from "./readRequests";

const API_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const GROUP_CACHE_PREFIX = "mt5Groups:";
const GROUP_CATALOG_TTL_MS = 60_000;
const PROFILE_STATE_EVENT = "mt5:profile-state-changed";
const PROFILE_REVISION_KEY = "selectedProfileRevision";
const RMS_DATA_EPOCH_KEY = "rmsDataEpoch";
const RMS_DATA_EPOCH = "2026-09-02-antifraud-scope-2";
export const BOOK_CONFIG_EVENT = "mt5:book-config-changed";
let bookConfigurationCache = null;
const groupCatalogCache = new Map();
const defaultLiveGroupCache = new Map();
const pendingGroupCatalogRequests = new Map();
const GET_DEDUPE_WINDOW_MS = 350;
const getResponseCache = new Map();
const pendingGetRequests = new Map();
let authRenewedAt = 0;
const SYSTEM_PROFILE = Object.freeze({
  id: "default",
  name: "Manager",
  server: "",
  mt5_login: 0,
  mt5_password: "",
});

// A server-side factory reset must also invalidate manager/group choices held
// by the browser. Preserve the authenticated user, but discard every cached
// MT5 scope so the configured server is the only source on the next load.
if (
  typeof window !== "undefined" &&
  localStorage.getItem(RMS_DATA_EPOCH_KEY) !== RMS_DATA_EPOCH
) {
  const staleKeys = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (
      key?.startsWith(GROUP_CACHE_PREFIX) ||
      key?.startsWith("antifraudSnapshot:")
    )
      staleKeys.push(key);
  }
  staleKeys.forEach((key) => localStorage.removeItem(key));
  localStorage.removeItem("selectedProfile");
  localStorage.removeItem("selectedProfileId");
  localStorage.removeItem(PROFILE_REVISION_KEY);
  localStorage.setItem(RMS_DATA_EPOCH_KEY, RMS_DATA_EPOCH);
}

function buildUrl(path) {
  if (!API_BASE) {
    return path;
  }
  return `${API_BASE}${path}`;
}

function emitProfileStateChange() {
  if (typeof window === "undefined") {
    return;
  }

  window.dispatchEvent(new Event(PROFILE_STATE_EVENT));
}

function readSelectedProfile() {
  try {
    const value = JSON.parse(localStorage.getItem("selectedProfile") || "null");
    return value?.id === SYSTEM_PROFILE.id ? value : SYSTEM_PROFILE;
  } catch {
    return SYSTEM_PROFILE;
  }
}

function touchProfileRevision() {
  if (typeof window === "undefined") {
    return;
  }

  const current = Number(localStorage.getItem(PROFILE_REVISION_KEY) || 0);
  localStorage.setItem(PROFILE_REVISION_KEY, String(current + 1));
}

function subscribeProfileState(callback) {
  if (typeof window === "undefined") {
    return () => {};
  }

  const handler = () => callback();
  window.addEventListener("storage", handler);
  window.addEventListener(PROFILE_STATE_EVENT, handler);

  return () => {
    window.removeEventListener("storage", handler);
    window.removeEventListener(PROFILE_STATE_EVENT, handler);
  };
}

async function readResponseBody(response) {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function apiRequest(path, options = {}) {
  // Long MT5 history/report requests are allowed to finish. Fast live views
  // can still opt into a timeout by passing timeoutMs explicitly.
  const {
    timeoutMs = 0,
    dedupeMs = GET_DEDUPE_WINDOW_MS,
    ...fetchOptions
  } = options;
  const method = String(fetchOptions.method || "GET").toUpperCase();
  if (import.meta.env.VITE_LIVE_STREAM !== "false") {
    const live = readLiveData(path, fetchOptions, operationsWebSocketUrl().replace("/api/operations/ws", "/api/live/ws"));
    if (live !== undefined) return live;
  }
  const changesData = method !== "GET" && !isDataRead(path, fetchOptions);
  if (changesData && path.startsWith("/api/auth/")) clearLiveTransport();
  const canShareGet = method === "GET" && !fetchOptions.signal;
  const requestKey = canShareGet
    ? `${path}|${localStorage.getItem("user") || ""}`
    : "";
  if (canShareGet) {
    const cached = getResponseCache.get(requestKey);
    if (cached && performance.now() - cached.storedAt < dedupeMs)
      return cached.data;
    const pending = pendingGetRequests.get(requestKey);
    if (pending) return pending;
  } else if (method !== "GET") {
    getResponseCache.clear();
  }

  const execute = async () => {
    const customHeaders = { ...(options.headers || {}) };
    const accessToken = localStorage.getItem("accessToken");
    if (accessToken && !customHeaders.Authorization)
      customHeaders.Authorization = `Bearer ${accessToken}`;
    const controller = new AbortController();
    let timedOut = false;
    const timeout =
      timeoutMs > 0
        ? window.setTimeout(() => {
            timedOut = true;
            controller.abort();
          }, timeoutMs)
        : 0;
    if (fetchOptions.signal) {
      if (fetchOptions.signal.aborted) controller.abort();
      else
        fetchOptions.signal.addEventListener(
          "abort",
          () => controller.abort(),
          {
            once: true,
          },
        );
    }
    let response;
    let socketResponse;
    try {
      if (import.meta.env.VITE_LIVE_STREAM !== "false")
        try {
          socketResponse = await requestLiveData(path, { ...fetchOptions, signal: controller.signal },
            operationsWebSocketUrl().replace("/api/operations/ws", "/api/live/ws"));
        } catch (cause) {
          if (!cause?.wsRequest || controller.signal.aborted) throw cause;
          // Read-only queries can safely recover over authenticated HTTP while the socket reconnects.
        }
      response = socketResponse || await fetch(buildUrl(path), {
        ...fetchOptions,
        credentials: "include",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          ...customHeaders,
        },
      });
    } catch (error) {
      if (error?.wsRequest) throw error;
      if (error?.name === "AbortError")
        throw new Error(
          timedOut
            ? `Request timed out after ${Math.round(timeoutMs / 1000)} seconds`
            : "Request was cancelled",
        );
      throw new Error(
        "Unable to reach the server. Check the connection and try again.",
        { cause: error },
      );
    } finally {
      window.clearTimeout(timeout);
    }

    const data = socketResponse ? socketResponse.data : await readResponseBody(response);
    // WebSocket frames cannot refresh an HttpOnly session cookie. Retain the
    // existing sliding session through the dedicated HTTP auth endpoint.
    if (socketResponse && performance.now() - authRenewedAt > 300_000) {
      authRenewedAt = performance.now();
      void apiRequest("/api/auth/session", { dedupeMs: 0 }).then(refreshLiveSession).catch(() => {});
    }

    if (!response.ok) {
      let message =
        typeof data === "string"
          ? data
          : data?.detail || data?.message || "Request failed";
      if (
        response.status === 524 ||
        (typeof message === "string" &&
          /<!doctype\s+html|<html[\s>]/i.test(message))
      ) {
        message = "The server took too long to respond. Please try again.";
      }
      const error = new Error(message);
      error.status = response.status;
      error.body = data;
      if (response.status === 401 && path !== "/api/auth/login") {
        clearSession();
        window.location.assign("/login");
      }
      throw error;
    }

    if (changesData && !path.startsWith("/api/auth/")) invalidateLiveData();
    if (canShareGet)
      getResponseCache.set(requestKey, {
        data,
        storedAt: performance.now(),
      });
    return data;
  };

  const request = execute();
  if (canShareGet) pendingGetRequests.set(requestKey, request);
  try {
    return await request;
  } finally {
    if (canShareGet && pendingGetRequests.get(requestKey) === request)
      pendingGetRequests.delete(requestKey);
  }
}

export async function apiDownload(path, options = {}) {
  const { timeoutMs = 0, ...fetchOptions } = options;
  const customHeaders = { ...(options.headers || {}) };
  const accessToken = localStorage.getItem("accessToken");
  if (accessToken && !customHeaders.Authorization)
    customHeaders.Authorization = `Bearer ${accessToken}`;

  const controller = new AbortController();
  const timeout =
    timeoutMs > 0 ? window.setTimeout(() => controller.abort(), timeoutMs) : 0;
  let response;
  try {
    response = await fetch(buildUrl(path), {
      ...fetchOptions,
      credentials: "include",
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...customHeaders },
    });
  } catch (error) {
    if (error?.name === "AbortError")
      throw new Error("Download timed out. Narrow the report range and retry.");
    throw new Error("Unable to reach the server.", { cause: error });
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const data = await readResponseBody(response);
    const message =
      typeof data === "string"
        ? data
        : data?.detail || data?.message || "Request failed";

    throw new Error(message);
  }

  const blob = await response.blob();
  const contentDisposition = response.headers.get("content-disposition") || "";
  const match = contentDisposition.match(
    /filename\*?=(?:UTF-8''|")?([^";]+)"?/i,
  );
  const filename = match ? decodeURIComponent(match[1]) : null;

  return { blob, filename };
}

export function operationsWebSocketUrl() {
  if (API_BASE) return `${API_BASE.replace(/^http/, "ws")}/api/operations/ws`;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/operations/ws`;
}

export function subscribeLiveData(path, body, listener) {
  if (import.meta.env.VITE_LIVE_STREAM === "false") return () => {};
  return watchLiveData(path, { method: "POST", body: JSON.stringify(body) }, listener,
    operationsWebSocketUrl().replace("/api/operations/ws", "/api/live/ws"));
}

export async function fetchAnalyticsReport(payload) {
  return apiRequest("/api/analytics", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchBrokerageReport(payload, options = {}) {
  return apiRequest("/api/reports/brokerage", {
    ...options,
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function downloadBrokerageExport(payload) {
  return apiDownload("/api/reports/brokerage/export", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function downloadDealReport(payload) {
  return apiDownload("/api/reports/deal/export", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function clampNonNegative(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
}

function cloneAndClampEquityRows(rows, fields) {
  if (!Array.isArray(rows)) {
    return [];
  }

  return rows.map((row) => {
    const next = { ...row };
    for (const field of fields) {
      if (Object.prototype.hasOwnProperty.call(next, field)) {
        next[field] = clampNonNegative(next[field]);
      }
    }
    return next;
  });
}

function normalizeDiffReport(data) {
  const openingReport = cloneAndClampEquityRows(data?.opening_report, [
    "Equity",
    "equity",
    "ProfitEquity",
    "profit_equity",
  ]);
  const closingReport = cloneAndClampEquityRows(data?.closing_report, [
    "Equity",
    "equity",
    "ProfitEquity",
    "profit_equity",
  ]);
  const openingTotal = openingReport.reduce(
    (total, row) =>
      total +
      clampNonNegative(
        row?.Equity ?? row?.equity ?? row?.ProfitEquity ?? row?.profit_equity,
      ),
    0,
  );
  const closingTotal = closingReport.reduce(
    (total, row) =>
      total +
      clampNonNegative(
        row?.Equity ?? row?.equity ?? row?.ProfitEquity ?? row?.profit_equity,
      ),
    0,
  );
  const difference = closingTotal - openingTotal;

  const brokerageTotals = {
    ...(data?.brokerage_totals || {}),
    opening_equity: openingTotal,
    closing_equity: closingTotal,
    difference,
  };
  brokerageTotals.net_brokerage =
    difference -
    brokerageTotals.deposit -
    brokerageTotals.net_credit -
    brokerageTotals.additional +
    brokerageTotals.withdrawal;

  const dailySummary = Array.isArray(data?.daily_summary)
    ? data.daily_summary.map((row) => {
        const next = { ...row };
        next["Opening Equity"] = clampNonNegative(next["Opening Equity"]);
        next["Closing Equity"] = clampNonNegative(next["Closing Equity"]);
        next["Broker PnL"] =
          next["Closing Equity"] -
          next["Opening Equity"] -
          next["Deposit"] -
          next["Credit"] -
          next["Additional"] +
          next["Withdraw"];
        next["Net PnL %"] =
          next["Opening Equity"] > 0
            ? (next["Broker PnL"] / next["Opening Equity"]) * 100
            : 0;
        return next;
      })
    : [];

  return {
    ...data,
    opening_report: openingReport,
    closing_report: closingReport,
    daily_summary: dailySummary,
    brokerage_totals: brokerageTotals,
    profile_name: data?.profile_name || data?.profileName || "",
    opening_equity_total: openingTotal,
    closing_equity_total: closingTotal,
    equity_difference: difference,
    total_lp_pnl: brokerageTotals.total_lp_pnl,
    client_a_book_pnl: brokerageTotals.client_a_book_pnl,
    net_brokerage: brokerageTotals.net_brokerage,
  };
}

export async function fetchDiffReport(payload, options = {}) {
  try {
    return await apiRequest("/api/reports/diff", {
      ...options,
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (error?.status !== 404 && error?.status !== 405) {
      throw error;
    }

    const brokerageReport = await fetchBrokerageReport(payload, options);
    return normalizeDiffReport(brokerageReport);
  }
}

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem("user") || "null");
  } catch {
    return null;
  }
}

export function getSelectedProfile() {
  return readSelectedProfile();
}

function getSelectedProfileSnapshot() {
  if (typeof window === "undefined") {
    return null;
  }

  return JSON.stringify({
    profile: localStorage.getItem("selectedProfile"),
    revision: localStorage.getItem(PROFILE_REVISION_KEY),
  });
}

export function useStoredProfile() {
  const snapshot = useSyncExternalStore(
    subscribeProfileState,
    getSelectedProfileSnapshot,
    () => null,
  );
  // JSON.parse creates a new object. Memoize it against the primitive storage
  // snapshot so effects depending on `profile` do not restart every render.
  return useMemo(() => {
    try {
      const storageState = JSON.parse(snapshot || "null");
      const value = JSON.parse(storageState?.profile || "null");
      return value?.id === SYSTEM_PROFILE.id ? value : SYSTEM_PROFILE;
    } catch {
      return SYSTEM_PROFILE;
    }
  }, [snapshot]);
}

export function getCachedGroupsForProfile(profileId) {
  if (!profileId) {
    return [];
  }

  try {
    const raw = localStorage.getItem(`${GROUP_CACHE_PREFIX}${profileId}`);
    const parsed = JSON.parse(raw || "[]");
    return Array.isArray(parsed)
      ? parsed.map((group) => String(group).trim()).filter(Boolean)
      : [];
  } catch {
    return [];
  }
}

export function storeCachedGroupsForProfile(profileId, groups) {
  if (!profileId) {
    return;
  }

  const normalized = Array.from(
    new Set(
      (groups || []).map((group) => String(group).trim()).filter(Boolean),
    ),
  );

  if (normalized.length === 0) {
    localStorage.removeItem(`${GROUP_CACHE_PREFIX}${profileId}`);
    return;
  }

  localStorage.setItem(
    `${GROUP_CACHE_PREFIX}${profileId}`,
    JSON.stringify(normalized),
  );
}

export function clearCachedGroupsForProfile(profileId) {
  if (!profileId) {
    return;
  }

  localStorage.removeItem(`${GROUP_CACHE_PREFIX}${profileId}`);
  groupCatalogCache.delete(String(profileId));
  defaultLiveGroupCache.delete(String(profileId));
}

export function getDefaultLiveGroupForProfile(profileId) {
  return defaultLiveGroupCache.get(String(profileId || "default")) || "";
}

export function clearAllCachedGroups() {
  if (typeof window === "undefined") {
    return;
  }

  const keys = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key && key.startsWith(GROUP_CACHE_PREFIX)) {
      keys.push(key);
    }
  }

  keys.forEach((key) => localStorage.removeItem(key));
  groupCatalogCache.clear();
  defaultLiveGroupCache.clear();
  pendingGroupCatalogRequests.clear();
}

export function storeSelectedProfile(profile) {
  if (!profile) {
    localStorage.removeItem("selectedProfile");
    localStorage.removeItem("selectedProfileId");
    touchProfileRevision();
    emitProfileStateChange();
    return;
  }
  localStorage.setItem("selectedProfile", JSON.stringify(profile));
  localStorage.setItem("selectedProfileId", String(profile.id));
  touchProfileRevision();
  emitProfileStateChange();
}

export function disconnectSelectedProfile(profileId) {
  clearCachedGroupsForProfile(profileId);
  localStorage.removeItem("selectedProfile");
  localStorage.removeItem("selectedProfileId");
  touchProfileRevision();
  emitProfileStateChange();
}

export function clearSession() {
  clearLiveTransport();
  fetch(buildUrl("/api/auth/logout"), {
    method: "POST",
    credentials: "include",
  }).catch(() => {});
  localStorage.removeItem("user");
  localStorage.removeItem("accessToken");
  localStorage.removeItem("selectedProfile");
  localStorage.removeItem("selectedProfileId");
  localStorage.removeItem(PROFILE_REVISION_KEY);
  clearAllCachedGroups();
  emitProfileStateChange();
}

export function changePassword(currentPassword, newPassword) {
  return apiRequest("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}

export async function syncMt5Groups(profile) {
  return fetchCompleteMt5Groups(profile, { force: true });
}

export async function fetchCompleteMt5Groups(profile, { force = false } = {}) {
  const profileId = String(profile?.id || "default");
  const cached = groupCatalogCache.get(profileId);
  if (!force && cached && Date.now() - cached.storedAt < GROUP_CATALOG_TTL_MS)
    return cached.groups;

  const pending = pendingGroupCatalogRequests.get(profileId);
  if (pending) return pending;

  const request = apiRequest("/api/mt5/groups", {
    method: "POST",
    body: JSON.stringify({
      server: profile.server,
      login: profile.mt5_login,
      password: profile.mt5_password,
    }),
  })
    .then((data) => {
      const groups = Array.from(
        new Set(
          (Array.isArray(data?.groups) ? data.groups : [])
            .map((group) => String(group || "").trim())
            .filter(Boolean),
        ),
      ).sort((left, right) =>
        left.localeCompare(right, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      );
      if (groups.length > 0) {
        const defaultGroup = String(data?.default_group || "").trim();
        if (defaultGroup && groups.includes(defaultGroup))
          defaultLiveGroupCache.set(profileId, defaultGroup);
        groupCatalogCache.set(profileId, { groups, storedAt: Date.now() });
        storeCachedGroupsForProfile(profileId, groups);
        return groups;
      }

      // A connected Manager can still be publishing its first generation.
      // Never turn that short startup state into a cached empty catalog.
      return getCachedGroupsForProfile(profileId);
    })
    .finally(() => pendingGroupCatalogRequests.delete(profileId));

  pendingGroupCatalogRequests.set(profileId, request);
  return request;
}

export async function testMt5Connection(profile) {
  return apiRequest("/api/mt5/connect", {
    method: "POST",
    body: JSON.stringify({
      server: profile.server,
      login: profile.mt5_login,
      password: profile.mt5_password,
    }),
  });
}

export async function getDashboard() {
  return apiRequest("/api/manager/dashboard");
}

export async function getMarketWatch(params = {}) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.limit) query.set("limit", String(params.limit));

  const queryString = query.toString();
  return apiRequest(
    `/api/manager/market-watch${queryString ? `?${queryString}` : ""}`,
    { dedupeMs: 75 },
  );
}

export async function getAccounts(params = {}) {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.enabled_only)
    query.set("enabled_only", String(params.enabled_only));

  const queryString = query.toString();
  return apiRequest(
    `/api/manager/accounts${queryString ? `?${queryString}` : ""}`,
  );
}

export async function connectManager(credentials) {
  return apiRequest("/api/manager/connect", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
}

export async function disconnectManager() {
  return apiRequest("/api/manager/disconnect", {
    method: "POST",
  });
}

export async function getManagerStatus() {
  return apiRequest("/api/manager/status");
}

export async function getManagers() {
  return apiRequest("/api/managers", { dedupeMs: 0 });
}

export async function getBookGroupSettings({ refresh = false } = {}) {
  if (!refresh && bookConfigurationCache) return bookConfigurationCache;
  bookConfigurationCache = await apiRequest("/api/settings/books");
  return bookConfigurationCache;
}

export async function updateBookGroupSettings(settings) {
  bookConfigurationCache = await apiRequest("/api/settings/books", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
  if (typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(BOOK_CONFIG_EVENT, { detail: bookConfigurationCache }),
    );
  }
  return bookConfigurationCache;
}

export async function resetRmsDatabase(password, confirmation) {
  const result = await apiRequest("/api/settings/reset-database", {
    method: "POST",
    timeoutMs: 60_000,
    body: JSON.stringify({ password, confirmation }),
  });
  bookConfigurationCache = null;
  return result;
}

export async function getBookShiftEvents(limit = 100) {
  return apiRequest(`/api/settings/book-shifts?limit=${Number(limit)}`, {
    dedupeMs: 500,
  });
}

export async function clearBookShiftEvents() {
  return apiRequest("/api/settings/book-shifts/clear", {
    method: "POST",
    body: JSON.stringify({ confirmation: "CLEAR BOOK SHIFTS" }),
  });
}

export async function getTelegramStatus() {
  return apiRequest("/api/notifications/telegram/status");
}

export async function sendTelegramTest(message, route = "default") {
  return apiRequest("/api/notifications/telegram/test", {
    method: "POST",
    body: JSON.stringify({
      message: message || "MT5 Brokerage Telegram notifications are connected.",
      route,
    }),
  });
}

export async function updateTelegramSettings(settings) {
  return apiRequest("/api/notifications/telegram/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function syncGoogleSheets(liveSnapshot) {
  return apiRequest("/api/integrations/google-sheets/sync", {
    method: "POST",
    timeoutMs: 60_000,
    body: JSON.stringify({ live_snapshot: liveSnapshot }),
  });
}

export async function getRiskAutomationOverview(
  limit = 250,
  { includeClients = false, includeAudit = false, clientLimit = 500 } = {},
) {
  const query = new URLSearchParams({
    limit: String(limit),
    include_clients: String(includeClients),
    include_audit: String(includeAudit),
    client_limit: String(clientLimit),
  });
  return apiRequest(`/api/risk-automation/overview?${query}`);
}

export async function createRiskAutomationRule(rule) {
  return apiRequest("/api/risk-automation/rules", {
    method: "POST",
    body: JSON.stringify(rule),
  });
}

export async function updateRiskAutomationRule(ruleId, rule) {
  return apiRequest(`/api/risk-automation/rules/${ruleId}`, {
    method: "PUT",
    body: JSON.stringify(rule),
  });
}

export async function deleteRiskAutomationRule(ruleId) {
  return apiRequest(`/api/risk-automation/rules/${ruleId}`, {
    method: "DELETE",
  });
}

export async function simulateRiskAutomationRule(ruleId) {
  return apiRequest(`/api/risk-automation/rules/${ruleId}/simulate`, {
    method: "POST",
  });
}

export async function runRiskAutomation(ruleId = null) {
  return apiRequest(
    `/api/risk-automation/run${ruleId ? `?rule_id=${ruleId}` : ""}`,
    { method: "POST" },
  );
}

export async function decideRiskApproval(approvalId, decision, note = "") {
  return apiRequest(`/api/risk-automation/approvals/${approvalId}`, {
    method: "POST",
    body: JSON.stringify({ decision, note }),
  });
}

export async function applyRiskManualOverride(payload) {
  return apiRequest("/api/risk-automation/manual-override", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
