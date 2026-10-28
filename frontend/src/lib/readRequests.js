// POST is also used for report filters. Only these data queries may use RPC;
// trading, approvals, settings changes and session actions stay on HTTP.
export const READ_POST_PATHS = new Set([
  "/api/analytics", "/api/book-performance", "/api/mt5/groups",
  "/api/dealer-command-center", "/api/risk/live", "/api/risk/exposure", "/api/risk/performance",
  "/api/risk/markout", "/api/risk/analyse", "/api/risk/client-live",
  "/api/risk/antifraud", "/api/risk/stress", "/api/reports/deal",
  "/api/reports/brokerage", "/api/reports/brokerage/audited", "/api/reports/diff",
  "/api/operations/live", "/api/operations/cid-audit", "/api/operations/intelligence",
]);

export function isDataRead(path, options = {}) {
  try {
    const url = new URL(path, "http://rms.local");
    const decoded = decodeURIComponent(url.pathname);
    if (!path.startsWith("/api/") || !decoded.startsWith("/api/") ||
        decoded.includes("\\") || decoded.includes("//") ||
        decoded.split("/").some((part) => part === "." || part === "..") ||
        url.origin !== "http://rms.local" || url.hash ||
        decoded.startsWith("/api/auth/") || /\/(export|download)$|\.(mq5|ex5)$/.test(decoded)) return false;
    const method = (options.method || "GET").toUpperCase();
    return method === "GET" || (method === "POST" && READ_POST_PATHS.has(decoded));
  } catch { return false; }
}
