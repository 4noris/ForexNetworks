import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import GroupTreeSelect from "../components/GroupTreeSelect";
import RiskEventPanel from "../components/RiskEventPanel";
import AnimatedNumber from "../components/AnimatedNumber";
import {
  apiRequest, fetchCompleteMt5Groups, getCachedGroupsForProfile,
  getBookGroupSettings, getDefaultLiveGroupForProfile, operationsWebSocketUrl,
  storeCachedGroupsForProfile, useStoredProfile,
} from "../lib/api";

import { initialBookGroups } from "../lib/bookDefaults";

const formatNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const moneyNumber = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const lotNumber = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 });
const formatMoney = (value, currency) => value == null || !currency ? "Unavailable" : `${moneyNumber.format(value)} ${currency}`;
const formatCount = (value) => value == null ? "Unavailable" : formatNumber.format(value);

export default function DealerCommandCenter() {
  const profile = useStoredProfile();
  const pageActive = useLocation().pathname === "/command-center";
  const [groups, setGroups] = useState([]);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(null);

  useEffect(() => {
    if (!profile) return undefined;
    let active = true;
    const cached = getCachedGroupsForProfile(profile.id);
    setGroups(cached);
    Promise.all([fetchCompleteMt5Groups(profile), getBookGroupSettings()]).then(([available, books]) => {
      if (!active) return;
      setGroups(available);
      storeCachedGroupsForProfile(profile.id, available);
      setSelectedGroups((current) => {
        const retained = current.filter((group) => available.includes(group));
        if (retained.length) return retained;
        const preferred = getDefaultLiveGroupForProfile(profile.id);
        return initialBookGroups(available, books, "", preferred);
      });
    }).catch((cause) => { if (active) setError(cause.message || "Group catalog unavailable"); });
    return () => { active = false; };
  }, [profile]);

  const refresh = useCallback(async () => {
    if (!pageActive || !selectedGroups.length || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setLoading(true);
    try {
      const result = await apiRequest("/api/dealer-command-center", {
        method: "POST", timeoutMs: 60_000, signal: controller.signal,
        body: JSON.stringify({ selected_groups: selectedGroups, excluded_logins: [], group_filter_rules: "" }),
      });
      if (controller.signal.aborted || pending.current !== controller) return;
      setData(result);
      setError("");
    } catch (cause) {
      if (!controller.signal.aborted && pending.current === controller)
        setError(cause.message || "Command Center unavailable");
    } finally {
      if (pending.current === controller) {
        pending.current = null;
        setLoading(false);
      }
    }
  }, [selectedGroups, pageActive]);

  useEffect(() => {
    if (!pageActive || !selectedGroups.length) return undefined;
    let socket;
    let lastMarker;
    refresh();
    const timer = window.setInterval(() => { if (!document.hidden) refresh(); }, 30_000);
    try {
      socket = new WebSocket(operationsWebSocketUrl());
      socket.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type !== "manager_tick" || !message.source_updated_at) return;
        if (lastMarker !== undefined && lastMarker !== message.source_updated_at && !document.hidden) refresh();
        lastMarker = message.source_updated_at;
      };
    } catch { /* Timed refresh remains available. */ }
    return () => {
      window.clearInterval(timer);
      pending.current?.abort();
      pending.current = null;
      if (socket) {
        socket.onmessage = null;
        if (socket.readyState === WebSocket.CONNECTING)
          socket.onopen = () => socket.close();
        else socket.close();
      }
    };
  }, [refresh, selectedGroups, pageActive]);

  const kpis = data?.kpis;
  const stats = [
    ["Total Accounts", kpis?.total_accounts, false], ["Active Accounts", kpis?.active_accounts, false],
    ["Total Equity", kpis?.total_equity, true], ["Total Balance", kpis?.total_balance, true],
    ["Total Credit", kpis?.total_credit, true],
    ["Floating P&L", kpis?.floating_pnl, true], ["Margin Used", kpis?.margin_used, true],
    ["Free Margin", kpis?.free_margin, true], ["Gross Lots", kpis?.gross_lots, false], ["Net Lots", kpis?.net_lots, false],
    ["Open Positions", kpis?.open_positions, false], ["Critical Accounts", kpis?.critical_accounts, false],
    ["High Risk Accounts", kpis?.high_risk_accounts, false], ["Open Cases", kpis?.open_cases, false],
  ];

  return <div className="space-y-5 p-4 md:p-6">
    <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[.2em] text-muted">Broker operations</p>
          <h1 className="mt-1 text-2xl font-semibold">Dealer Command Center</h1>
          <p className="mt-2 text-xs text-muted">{data?.status === "connected" ? "MT5 connected" : data?.status === "stale" ? "MT5 data stale" : "MT5 data unavailable"}</p>
          <p className="mt-1 text-xs text-muted">Scope {data?.selected_groups?.length ?? selectedGroups.length} groups | Excluded {data?.excluded_logins ?? 0} accounts | Age {data?.data_age_seconds != null ? `${formatNumber.format(data.data_age_seconds)}s` : "Unavailable"}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <GroupTreeSelect groups={groups} value={selectedGroups} onChange={setSelectedGroups} label="Live Groups" emptyMeansAll={false} className="min-w-[220px]" />
          <button type="button" className="btn-secondary h-9 w-28 shrink-0 whitespace-nowrap px-3 py-2 text-xs" onClick={refresh} disabled={loading || !selectedGroups.length}>{loading ? "Refreshing..." : "Refresh"}</button>
        </div>
      </div>
      {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
      {(data?.warnings || []).map((warning, index) => <p className="mt-2 text-xs text-amber-400" key={`${index}-${warning}`}>{warning}</p>)}
    </section>
    <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6" aria-label="Verified KPIs">
      {stats.map(([label, value, financial]) => <div key={label} className="min-h-[92px] min-w-0 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4"><p className="text-[10px] uppercase tracking-wide text-muted">{label}</p><p className="mt-2 overflow-hidden text-lg font-semibold tabular-nums whitespace-nowrap"><AnimatedNumber key={financial ? data?.currency : label} value={financial && !data?.currency ? null : value} formatter={financial ? moneyNumber : label.includes("Lots") ? lotNumber : formatNumber} suffix={financial && data?.currency ? ` ${data.currency}` : ""} /></p></div>)}
    </section>
    <RiskEventPanel events={data?.risk_events || []} onUpdated={refresh} />
    <section className="grid gap-4 xl:grid-cols-2">
      <div className="overflow-hidden rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))]"><h2 className="p-4 text-sm font-semibold">Live symbol exposure</h2><div className="scrollbar-hidden max-h-[480px] overflow-auto"><table className="w-full min-w-[760px] text-left text-xs"><thead className="sticky top-0 bg-[rgb(var(--color-card))] text-muted"><tr>{["Symbol","Buy","Sell","Net","Positions","Floating P&L","Accounts","Gross Notional","Concentration","Status"].map((label) => <th key={label} className="px-3 py-2">{label}</th>)}</tr></thead><tbody>{(data?.symbol_exposure || []).map((row) => <tr key={row.symbol} className="border-t border-[rgb(var(--color-border))]"><td className="px-3 py-2 font-medium">{row.symbol}</td><td className="px-3 py-2">{formatCount(row.buy_lots)}</td><td className="px-3 py-2">{formatCount(row.sell_lots)}</td><td className="px-3 py-2">{formatCount(row.net_lots)}</td><td className="px-3 py-2">{formatCount(row.open_positions)}</td><td className="px-3 py-2">{formatMoney(row.floating_pnl, data?.currency)}</td><td className="px-3 py-2">{row.accounts}</td><td className="px-3 py-2">{formatMoney(row.gross_notional, data?.currency)}</td><td className="px-3 py-2">{row.concentration_pct == null ? "N/A" : `${row.concentration_pct}%`}</td><td className="px-3 py-2">{row.risk_status === "data_required" ? "Data required" : "Measured"}</td></tr>)}</tbody></table>{data?.symbol_exposure?.length === 0 && <p className="p-4 text-xs text-muted">No open positions in the selected scope.</p>}</div></div>
      <div className="overflow-hidden rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))]"><h2 className="p-4 text-sm font-semibold">Top risk accounts</h2><div className="scrollbar-hidden max-h-[480px] overflow-auto"><table className="w-full min-w-[860px] text-left text-xs"><thead className="sticky top-0 bg-[rgb(var(--color-card))] text-muted"><tr>{["Login","Name","Group","Balance","Credit","Equity","Margin","Free","Margin %","Floating P&L","Lots","Severity"].map((label) => <th key={label} className="px-3 py-2">{label}</th>)}</tr></thead><tbody>{(data?.top_risk_accounts || []).map((row) => <tr key={row.login} className="border-t border-[rgb(var(--color-border))]"><td className="px-3 py-2"><Link className="underline" to={`/dealing-risk?tab=analyser&login=${row.login}&group=${encodeURIComponent(row.group)}`}>{row.login}</Link></td><td className="px-3 py-2">{row.name || "Unavailable"}</td><td className="px-3 py-2">{row.group}</td><td className="px-3 py-2">{formatMoney(row.balance, data?.currency)}</td><td className="px-3 py-2">{formatMoney(row.credit, data?.currency)}</td><td className="px-3 py-2">{formatMoney(row.equity, data?.currency)}</td><td className="px-3 py-2">{formatMoney(row.margin, data?.currency)}</td><td className="px-3 py-2">{formatMoney(row.margin_free, data?.currency)}</td><td className="px-3 py-2">{formatCount(row.margin_level)}</td><td className="px-3 py-2">{formatMoney(row.floating_pnl, data?.currency)}</td><td className="px-3 py-2">{formatCount(row.gross_lots)}</td><td className="px-3 py-2">{row.severity}</td></tr>)}</tbody></table>{data?.top_risk_accounts?.length === 0 && <p className="p-4 text-xs text-muted">No flagged accounts in the selected scope.</p>}</div></div>
    </section>
    <section className="rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-4"><h2 className="text-sm font-semibold">What changed?</h2><p className="mt-1 text-xs text-muted">Recorded dealer and configuration actions from the audit log.</p><div className="scrollbar-hidden mt-3 max-h-36 space-y-2 overflow-y-auto overscroll-contain pr-1" tabIndex={0} role="region" aria-label="Recent changes">{(data?.what_changed || []).map((event) => <div key={event.event_id} className="rounded-lg border border-[rgb(var(--color-border))] p-3 text-xs"><span className="text-muted">{event.timestamp}</span> | {event.description}</div>)}{data?.what_changed?.length === 0 && <p className="text-xs text-muted">No new events.</p>}</div></section>
  </div>;
}

