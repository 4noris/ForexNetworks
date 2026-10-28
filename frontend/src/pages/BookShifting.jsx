import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRightLeft, Download, RefreshCw, Search } from "lucide-react";
import toast from "react-hot-toast";
import AnimatedCard from "../components/AnimatedCard";
import { getBookShiftEvents, getManagerStatus } from "../lib/api";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const BOOK_LABELS = {
  a_book: "A Book",
  b_book: "B Book",
  cent: "Cent",
  hybrid: "Hybrid",
  unassigned: "Unassigned",
};
const bookLabel = (value) => BOOK_LABELS[value] || value || "Unassigned";

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

export default function BookShifting() {
  const [events, setEvents] = useState([]);
  const [source, setSource] = useState("");
  const [manager, setManager] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastSynced, setLastSynced] = useState(null);
  const [query, setQuery] = useState("");
  const [fromBook, setFromBook] = useState("all");
  const [toBook, setToBook] = useState("all");

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    try {
      const [result, status] = await Promise.all([
        getBookShiftEvents(1000),
        getManagerStatus(),
      ]);
      setEvents(result?.items || []);
      setSource(result?.equity_source || "");
      setManager(status);
      setError("");
      setLastSynced(new Date());
    } catch (err) {
      setError(err.message || "Unable to load book-shift history");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    load();
    const timer = window.setInterval(() => {
      if (active && !document.hidden) load({ quiet: true });
    }, 15_000);
    const onCleared = () => load({ quiet: true });
    window.addEventListener("book-shifts-cleared", onCleared);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("book-shifts-cleared", onCleared);
    };
  }, [load]);

  const filtered = useMemo(() => {
    const token = query.trim().toLowerCase();
    return events.filter((row) => {
      if (fromBook !== "all" && row.from_book !== fromBook) return false;
      if (toBook !== "all" && row.to_book !== toBook) return false;
      if (!token) return true;
      return [
        row.login,
        row.manager_login,
        row.client_name,
        row.from_group,
        row.to_group,
      ].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(token),
      );
    });
  }, [events, query, fromBook, toBook]);

  const aToB = events.filter(
    (row) => row.from_book === "a_book" && row.to_book === "b_book",
  ).length;
  const bToA = events.filter(
    (row) => row.from_book === "b_book" && row.to_book === "a_book",
  ).length;
  const lastEvent = events[0];

  const download = () => {
    if (!filtered.length) return toast.error("No movement rows to export");
    const headers = [
      "Detected UTC",
      "Manager Login",
      "Client Login",
      "Client Name",
      "From Group",
      "To Group",
      "From Book",
      "To Book",
      "Equity At Shift",
      "MT5 Generation Epoch",
    ];
    const rows = filtered.map((row) => [
      row.detected_at,
      row.manager_login,
      row.login,
      row.client_name,
      row.from_group,
      row.to_group,
      bookLabel(row.from_book),
      bookLabel(row.to_book),
      row.equity_at_shift,
      row.source_updated_at,
    ]);
    const blob = new Blob(
      [
        [headers, ...rows]
          .map((row) => row.map(csvCell).join(","))
          .join("\r\n"),
      ],
      { type: "text/csv;charset=utf-8" },
    );
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `book-shifts-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  return (
    <div className="mx-auto max-w-[1440px] space-y-5 px-4 pb-10 pt-5 md:px-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-[0.36em] text-muted">
            Broker operations
          </p>
          <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold">
            <ArrowRightLeft size={20} /> Book Shifting
          </h1>
          <p className="mt-1 text-xs text-muted">
            Durable audit of client group and execution-book movements with
            equity captured at detection.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="badge">
            Synced {lastSynced ? lastSynced.toLocaleTimeString() : "—"}
          </span>
          <span
            className={
              manager?.status === "connected"
                ? "badge badge-success"
                : "badge badge-danger"
            }
          >
            {manager?.status || "checking"}
          </span>
          <button
            className="btn-ghost text-xs"
            type="button"
            onClick={() => load()}
            disabled={loading}
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />{" "}
            Refresh
          </button>
          <button
            className="btn-ghost text-xs"
            type="button"
            onClick={download}
          >
            <Download size={14} /> Export CSV
          </button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Captured movements", events.length],
          ["A Book → B Book", aToB],
          ["B Book → A Book", bToA],
          ["Latest detection (UTC)", lastEvent?.detected_at || "None"],
        ].map(([label, value]) => (
          <AnimatedCard key={label} className="p-4">
            <p className="text-[10px] uppercase tracking-[.16em] text-muted">
              {label}
            </p>
            <p className="mt-2 text-lg font-semibold">{value}</p>
          </AnimatedCard>
        ))}
      </div>

      <AnimatedCard className="p-3">
        <div className="grid gap-2 md:grid-cols-[1fr_190px_190px]">
          <label className="relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              size={14}
            />
            <input
              className="field-input pl-9"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search login, client or group"
            />
          </label>
          {[
            ["From book", fromBook, setFromBook],
            ["To book", toBook, setToBook],
          ].map(([label, value, setter]) => (
            <select
              key={label}
              aria-label={label}
              className="field-input"
              value={value}
              onChange={(event) => setter(event.target.value)}
            >
              <option value="all">{label}: All</option>
              {Object.entries(BOOK_LABELS).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
          ))}
        </div>
      </AnimatedCard>

      {error && (
        <AnimatedCard className="p-4 text-xs text-danger">{error}</AnimatedCard>
      )}
      <AnimatedCard className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[rgb(var(--color-border))] p-4">
          <h2 className="text-sm font-semibold">Movement history</h2>
          <span className="text-[10px] text-muted">
            {source} · showing {filtered.length} of {events.length}
          </span>
        </div>
        <div className="max-h-[620px] overflow-auto">
          <table className="w-full min-w-[1050px] text-xs">
            <thead className="sticky top-0 bg-[rgb(var(--color-card))] text-left text-muted">
              <tr>
                {[
                  "Detected (UTC)",
                  "Manager",
                  "Client",
                  "From",
                  "To",
                  "Book movement",
                  "Equity at shift",
                  "Floating at shift",
                  "Destination correction",
                  "Source generation",
                ].map((head) => (
                  <th key={head} className="px-4 py-3">
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr
                  key={row.id}
                  className="border-t border-[rgb(var(--color-border))]"
                >
                  <td className="px-4 py-3">{row.detected_at}</td>
                  <td className="px-4 py-3">#{row.manager_login}</td>
                  <td className="px-4 py-3">
                    <div className="font-medium">
                      {row.client_name || "Unnamed client"}
                    </div>
                    <div className="text-muted">#{row.login}</div>
                  </td>
                  <td className="px-4 py-3">{row.from_group}</td>
                  <td className="px-4 py-3">{row.to_group}</td>
                  <td className="px-4 py-3">
                    {bookLabel(row.from_book)} → {bookLabel(row.to_book)}
                  </td>
                  <td className="px-4 py-3 font-semibold">
                    {money.format(row.equity_at_shift)}
                  </td>
                  <td className="px-4 py-3 font-semibold">
                    {money.format(row.floating_pnl_at_shift || 0)}
                  </td>
                  <td className="px-4 py-3 font-semibold">
                    {money.format(row.to_book_correction || 0)}
                  </td>
                  <td className="px-4 py-3 text-muted">
                    {new Date(
                      Number(row.source_updated_at) * 1000,
                    ).toISOString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && !filtered.length && (
            <p className="p-12 text-center text-xs text-muted">
              No movements match the current filters.
            </p>
          )}
          {loading && !events.length && (
            <p className="p-12 text-center text-xs text-muted">
              Loading verified movement history…
            </p>
          )}
        </div>
      </AnimatedCard>
      <AnimatedCard className="p-4 text-xs leading-5 text-muted">
        <strong className="text-primary">Accuracy rule:</strong> equity and
        floating P&amp;L come from the first complete atomic MT5 Manager cache
        generation that shows the destination group. Book P&amp;L correction
        negates incoming floating and restores outgoing floating, so moving the
        account cannot create artificial daily performance.
      </AnimatedCard>
    </div>
  );
}
