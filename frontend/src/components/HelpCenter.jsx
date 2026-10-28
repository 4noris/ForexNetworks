import React, { useMemo, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  HelpCircle,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";

const TOPICS = [
  {
    id: "data-trust",
    category: "Getting started",
    title: "Data accuracy and status labels",
    summary:
      "How RMS distinguishes live, cached, stale, collecting, and unavailable data.",
    sections: [
      [
        "Live",
        "The value comes from the connected MT5 Manager cache and has a current source timestamp. Live views update from background snapshots; opening a page does not trigger a full MT5 recalculation.",
      ],
      [
        "Verified cached",
        "The value was previously obtained from MT5 and retained by RMS. Historical reports and rankings use stored snapshots so they load quickly and remain auditable.",
      ],
      [
        "Stale",
        "The most recent verified snapshot is displayed while Manager connectivity recovers. The timestamp remains visible. A stale value is never labelled live.",
      ],
      [
        "Collecting",
        "More than one real observation is required, for example floating-P&L delta. RMS waits for sufficient observations instead of inventing a baseline.",
      ],
      [
        "Unavailable",
        "MT5 returned no authoritative value, the required permission is missing, or the cache has not published a complete generation. Dashes and Unavailable are intentional—not zero.",
      ],
      [
        "Currency and time",
        "Financial dashboards report USD unless a page explicitly states otherwise. Cent accounts are converted using the configured divisor. Historical period boundaries use UTC unless the report states MT5 server time.",
      ],
    ],
  },
  {
    id: "dealing-monitor",
    category: "Dealing & Risk",
    title: "Live Monitor",
    summary:
      "Broker-wide live risk, network evidence, alerts, positions, and symbol exposure.",
    sections: [
      [
        "KPI cards",
        "Floating P&L, equity, margin used, free margin, open positions, gross lots, flagged accounts, and critical accounts are calculated from the selected MT5 groups in the latest complete cache generation.",
      ],
      [
        "P&L movement",
        "A rolling browser-session series of verified floating P&L snapshots. It starts collecting when the page is open; it is not backfilled with synthetic points.",
      ],
      [
        "Symbol exposure",
        "Buy lots, sell lots, net lots, position count, and floating P&L are aggregated from current open MT5 positions.",
      ],
      [
        "Client network map",
        "Uses verified public login IP evidence and provider geolocation. Hosting/VPS classification is evidence-based and may be unavailable when the provider lookup cannot verify an IP.",
      ],
      [
        "Risk & abuse alerts",
        "Scores current account conditions such as margin proximity, floating-loss ratio, concentration, grid-like exposure, shared IP, and verified hosting evidence. Mark Noticed hides the same activity for 48 hours.",
      ],
      [
        "Group selector",
        "Limits every calculation on the page to the selected MT5 groups. All Groups means every group returned by the connected Manager.",
      ],
    ],
  },
  {
    id: "pnl-leaders",
    category: "Dealing & Risk",
    title: "P&L Leaders",
    summary: "Live floating rankings and realized rankings by period.",
    sections: [
      [
        "Live winners and losers",
        "Ranks current account floating P&L from the live position/account cache.",
      ],
      [
        "Daily, weekly, monthly",
        "Realized P&L uses closing trade deals inside the corresponding UTC boundary.",
      ],
      [
        "Overall",
        "Uses the configured rolling history window. Profit, commission, fee, and swap/storage are included for verified closing deals.",
      ],
      [
        "Background cache",
        "History is calculated in the background and stored. Preparing means an authoritative MT5 history scan is still running; missing history is not shown as zero.",
      ],
    ],
  },
  {
    id: "client-analyser",
    category: "Dealing & Risk",
    title: "Client Analyser",
    summary:
      "Account-level trading behavior, execution, exposure, funding, and network evidence.",
    sections: [
      [
        "Account selection",
        "Search an MT5 login from the currently selected group scope.",
      ],
      [
        "Live account summary",
        "Balance, equity, floating P&L, margin, free margin, leverage, and current positions come from current Manager account and position records.",
      ],
      [
        "Trade lifecycle analysis",
        "Pairs verified opening and closing MT5 deals by position identifier to calculate duration, realized P&L, win rate, and strategy-pattern evidence.",
      ],
      [
        "Network evidence",
        "Ticket-linked journal IP evidence is labelled verified. Last-login IP fallback is explicitly labelled inferred and must not be treated as proof of trade origin.",
      ],
      [
        "Pattern flags",
        "Scalping, burst execution, reversal, grid, martingale-like escalation, and concentration are descriptive signals requiring dealer review—not proof of wrongdoing.",
      ],
    ],
  },
  {
    id: "exposure",
    category: "Dealing & Risk",
    title: "Exposure",
    summary:
      "Open risk by symbol and account, concentration, realized P&L, and position ageing.",
    sections: [
      [
        "Mark-to-market",
        "Sum of current floating profit and swap for scoped open positions/accounts.",
      ],
      [
        "Gross open notional",
        "Absolute lots × MT5 symbol contract size × current price. Contract specifications are taken from the live MT5 symbol cache.",
      ],
      [
        "Delta floating",
        "Difference between two consecutive verified live snapshots. Collecting is displayed until two snapshots exist.",
      ],
      [
        "Symbol concentration",
        "The share of an account’s total open lots held in one symbol. Example: 0.80 XAUUSD lots out of 1.00 total lot equals 80% concentration.",
      ],
      [
        "Account concentration",
        "Groups positions by account and symbol, then compares symbol lots with the account’s total open lots.",
      ],
      [
        "VWAP",
        "Volume-weighted average opening price for the account-symbol position group.",
      ],
      [
        "Overnight and aged",
        "Overnight means at least 24 hours open. Aged means at least five days, based on the MT5 position creation timestamp.",
      ],
    ],
  },
  {
    id: "book-suggestions",
    category: "Dealing & Risk",
    title: "Book Suggestions",
    summary:
      "Advisory A-Book, B-Book, or Hybrid classification with transparent evidence.",
    sections: [
      [
        "Inputs",
        "Uses only trading evidence: selected-period realized P&L, live floating P&L, equity-relative performance, lots, open positions, symbol concentration, and live risk score.",
      ],
      [
        "A-Book suggestion",
        "Indicates observed profitable or materially risky flow that may justify externalizing more market risk.",
      ],
      [
        "B-Book suggestion",
        "Indicates observed negative performance, limited live volume, or lower current risk under the configured scoring rules.",
      ],
      [
        "Hybrid suggestion",
        "Used for mixed evidence, close scores, or incomplete realized history. Missing history is never treated as zero and caps confidence.",
      ],
      [
        "Periods",
        "Daily, weekly, monthly, and rolling 365-day Overall change the realized-P&L evidence used by the classifier.",
      ],
      [
        "Important",
        "Suggestions never move an account. Dealer, liquidity, compliance, contractual, and market review is required before changing book assignment.",
      ],
    ],
  },
  {
    id: "anti-fraud",
    category: "Dealing & Risk",
    title: "Anti-Fraud",
    summary:
      "Background analysis of verified deals, positions, ticks, funding, and network evidence.",
    sections: [
      [
        "Detectors",
        "Includes latency candidates, scalping/HFT sequences, sharp deals, news timing, low-deposit/high-profit behavior, hedge accounts, failed stop-outs, large volume, funding churn, and profit concentration.",
      ],
      [
        "Evidence quality",
        "Every detector declares its source and availability. A candidate is not labelled confirmed when required ticks, journals, or account history are unavailable.",
      ],
      [
        "Real-time behavior",
        "Current position/account signals use the live cache. Expensive deal and tick scans run in a background cache to avoid blocking the UI.",
      ],
      [
        "Notifications",
        "Eligible high-severity events can route to Telegram according to Controls settings and alert-specific channels.",
      ],
    ],
  },
  {
    id: "manager-market",
    category: "Core pages",
    title: "Manager and Market Watch",
    summary:
      "Account health, MT5 totals, symbol quotes, spreads, and margin exposure.",
    sections: [
      [
        "Manager",
        "Shows account totals, balance, equity, margin, free margin, asset exposure, margin alerts, and top account exposure from the Manager cache.",
      ],
      [
        "Market Watch",
        "Shows live bid/ask, spread, contract settings, quote freshness, and top margin accounts. A quote is live only when MT5 supplies a current tick.",
      ],
      [
        "Search and scope",
        "Search filters currently loaded authoritative records; it does not generate symbols or accounts absent from MT5.",
      ],
    ],
  },
  {
    id: "operations",
    category: "Operations",
    title: "Operations Center tabs",
    summary:
      "Alerts, cases, related accounts, limits, intelligence, reconciliation, channels, and audit.",
    sections: [
      [
        "Alert Center",
        "Verified live operational and exposure alerts. Acknowledgement is retained with actor and timestamp.",
      ],
      [
        "Cases",
        "Create and track investigations with priority, owner, status, notes, and evidence history.",
      ],
      [
        "Related Accounts",
        "Displays verified shared IP attributes, coordinated timing candidates, and CID journal audit tools.",
      ],
      [
        "Exposure Limits",
        "Configure account, group, symbol, or global thresholds. A breach compares a verified current metric against the configured threshold.",
      ],
      [
        "Intelligence",
        "Historical client activity, profitability comparison, coordinated-timing candidates, latency candidates, and deposit/profit anomalies.",
      ],
      [
        "Reconciliation",
        "Compares retained reporting metrics and source timestamps to expose missing or stale reporting periods.",
      ],
      [
        "Channels",
        "Configure operational delivery destinations and minimum severity.",
      ],
      [
        "Audit Log",
        "Records sensitive administrative actions, case changes, settings updates, exports, and database reset activity.",
      ],
      [
        "Live channel",
        "A WebSocket sends lightweight Manager update notifications. The UI then reads the latest complete HTTP snapshot; the socket itself does not carry fabricated metrics.",
      ],
    ],
  },
  {
    id: "cid",
    category: "Operations",
    title: "Computer ID and login journal audit",
    summary:
      "How CID evidence is obtained, parsed, retained, searched, and flagged.",
    sections: [
      [
        "Source",
        "CID is not read from the MT5 user database. It is obtained from current OnlineGetArray sessions or Server Journal client-login messages returned by LoggerServerRequest.",
      ],
      [
        "Parsed fields",
        "Login, device type, terminal build, CID, access server, ping, country, city, ISP, journal timestamp, and journal IP when supplied.",
      ],
      [
        "Check CID",
        "Checks live sessions, retained evidence, then a bounded seven-day journal query. Historical results require Manager server-log permission.",
      ],
      [
        "CID audit",
        "Runs the selected journal range, stores deduplicated events, and supports partial case-insensitive Login, CID, and IP search plus device, country, build, and flagged filters.",
      ],
      [
        "Risk rules",
        "Shared CID: two or more logins. Shared IP: three or more distinct CIDs. Multiple CIDs: two or more CIDs for one login. Multiple countries: one CID observed in two or more countries.",
      ],
      [
        "Limitations",
        "A timeout or No data result means MT5 returned no authoritative journal evidence. RMS does not reconstruct or guess a CID.",
      ],
    ],
  },
  {
    id: "book-shifting",
    category: "Books and reporting",
    title: "Book Shifting",
    summary: "Detects account movement between configured groups and books.",
    sections: [
      [
        "Detection",
        "Compares consecutive verified account-group snapshots and records login, client, old/new group, old/new book, equity, floating P&L, and source timestamp.",
      ],
      [
        "P&L correction",
        "When an account moves into or out of B-Book or Cent Book, floating P&L at the movement time is used to prevent the move itself from distorting daily book performance.",
      ],
      [
        "Configuration",
        "A, B, Cent, and Hybrid group membership comes from reusable group presets in Controls.",
      ],
    ],
  },
  {
    id: "book-pnl",
    category: "Books and reporting",
    title: "A-Book / B-Book P&L",
    summary:
      "Database-backed daily book performance with auditable components.",
    sections: [
      [
        "Formula",
        "Book P&L = Closed P&L + Swaps + Commissions + (Closing Floating − Opening Floating) + Shift Corrections. IB payout is shown separately where configured.",
      ],
      [
        "Daily table",
        "One retained row per reporting day with opening/closing floating, realized components, corrections, account count, and total.",
      ],
      [
        "Date selection",
        "Returns stored dates inside the chosen inclusive range. Missing dates remain missing rather than being filled with zero.",
      ],
      [
        "Background refresh",
        "Snapshots and historical backfill run in the background. Existing verified rows stay visible while refresh occurs.",
      ],
      [
        "Reconciliation",
        "Use identical MT5 groups, dates, timezone, and cent conversion when comparing against MT5 Administrator Summary and Segregated reports.",
      ],
    ],
  },
  {
    id: "reports",
    category: "Books and reporting",
    title: "PnL and Brokerage reports",
    summary: "Detailed deal/account reporting and broker-level aggregation.",
    sections: [
      [
        "PnL report",
        "Builds account and group results from verified MT5 deals for the selected dates and groups, with export support.",
      ],
      [
        "Brokerage",
        "Compares opening and closing equity snapshots, deposits, withdrawals, account P&L, LP P&L, and net brokerage for an auditable date range.",
      ],
      [
        "Exports",
        "Generated from the same response data displayed in the UI so downloaded totals match the visible report.",
      ],
    ],
  },
  {
    id: "automation",
    category: "Automation",
    title: "Risk Automation",
    summary:
      "Rules, simulations, approvals, manual overrides, and execution audit.",
    sections: [
      [
        "Rules",
        "Define scope, metric, condition, threshold, action, cooldown, and approval requirement.",
      ],
      [
        "Simulation",
        "Evaluates a rule against current verified clients without applying its action.",
      ],
      [
        "Approvals",
        "Risk actions configured for approval remain pending until an authorized administrator approves or rejects them.",
      ],
      [
        "Manual override",
        "Records an explicit operator action with actor and reason. It must not be confused with an automatic rule result.",
      ],
      [
        "Safety",
        "Automation does not broaden MT5 permissions. Failed Manager actions remain failed and are recorded rather than represented as successful.",
      ],
    ],
  },
  {
    id: "controls",
    category: "Configuration",
    title: "System Controls",
    summary:
      "Manager status, book presets, Telegram delivery, thresholds, and database reset.",
    sections: [
      [
        "Manager status",
        "Connected means the persistent Manager session is established. Pages still wait for a complete cache generation before presenting live totals.",
      ],
      [
        "Book presets",
        "Assign each MT5 group to at most one of A-Book, B-Book, Cent, or Hybrid. All Groups is dynamically refreshed from Manager.",
      ],
      [
        "Telegram master control",
        "Enables or disables all automatic Telegram delivery.",
      ],
      [
        "Risk and anti-fraud alerts",
        "Enable category-specific delivery independently of the master switch.",
      ],
      [
        "Channel routing",
        "Enter a Telegram chat ID per alert category or leave blank to use the default configured chat.",
      ],
      [
        "Profit thresholds",
        "Profit alert and critical profit are percentage thresholds used by eligible alert rules.",
      ],
      [
        "Position and account exposure",
        "Position size is the individual-lot threshold; account exposure is the aggregate open-lot threshold.",
      ],
      [
        "Reset RMS database",
        "Requires administrator permission, reset password, typed DELETE ALL DATA, and browser confirmation. It clears retained RMS data and caches but preserves login accounts, schema, and all source data on MT5.",
      ],
    ],
  },
  {
    id: "troubleshooting",
    category: "Support",
    title: "Troubleshooting and permissions",
    summary:
      "What to check when data is stale, missing, slow, or permission-restricted.",
    sections: [
      [
        "Reconnecting",
        "Confirm the backend is ready, Manager credentials are valid, and the server is reachable. The last verified snapshot may remain visible as stale.",
      ],
      [
        "WebSocket unavailable",
        "HTTP data remains authoritative. Confirm the reverse proxy supports WebSocket upgrade for /api/operations/ws and that the session cookie is valid.",
      ],
      [
        "CID not found",
        "Confirm LoggerServerRequest exists, the Manager login has server-journal permission, the server retains the requested dates, and a matching client-login event exists.",
      ],
      [
        "Historical report missing",
        "Confirm selected groups, inclusive dates, UTC/server-time interpretation, Manager history permission, and whether the background cache has completed.",
      ],
      [
        "Zero versus unavailable",
        "Zero is shown only when an authoritative dataset exists and its calculated total is actually zero. Otherwise RMS displays Unavailable, a dash, Preparing, or Collecting.",
      ],
    ],
  },
];

function Topic({ topic, open, onToggle }) {
  return (
    <section className="overflow-hidden rounded-xl border border-[rgb(var(--color-border))] bg-[var(--inp-bg)]">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start justify-between gap-4 p-4 text-left"
      >
        <span>
          <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-muted">
            {topic.category}
          </span>
          <span className="mt-1 block text-sm font-semibold">
            {topic.title}
          </span>
          <span className="mt-1 block text-xs leading-5 text-muted">
            {topic.summary}
          </span>
        </span>
        <ChevronDown
          size={16}
          className={`mt-1 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-3 border-t border-[rgb(var(--color-border))] p-4">
          {topic.sections.map(([title, detail]) => (
            <div key={title}>
              <h4 className="text-xs font-semibold">{title}</h4>
              <p className="mt-1 text-xs leading-5 text-secondary">{detail}</p>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export default function HelpCenter() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [expanded, setExpanded] = useState(new Set(["data-trust"]));
  const categories = useMemo(
    () => ["All", ...new Set(TOPICS.map((topic) => topic.category))],
    [],
  );
  const results = useMemo(() => {
    const search = query.trim().toLowerCase();
    return TOPICS.filter((topic) => {
      if (category !== "All" && topic.category !== category) return false;
      if (!search) return true;
      return [
        topic.title,
        topic.summary,
        topic.category,
        ...topic.sections.flat(),
      ]
        .join(" ")
        .toLowerCase()
        .includes(search);
    });
  }, [category, query]);

  const toggle = (id) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <>
      <section className="rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5">
        <div className="flex items-start gap-3">
          <HelpCircle size={18} />
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-[rgb(var(--color-text-3))]">
              Help &amp; documentation
            </p>
            <h2 className="mt-1 text-sm font-semibold">RMS feature guide</h2>
            <p className="mt-1 text-xs leading-5 text-muted">
              Detailed explanations for every page, tab, calculation,
              configuration, data source, status, and permission.
            </p>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
          {[
            "Data accuracy",
            "Dealing & Risk",
            "Operations & CID",
            "Books & P&L",
            "Automation",
            "Configuration",
          ].map((label) => (
            <div
              key={label}
              className="rounded-lg border border-[rgb(var(--color-border))] bg-[var(--inp-bg)] px-3 py-2 text-secondary"
            >
              {label}
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="btn-primary mt-4 w-full py-2.5 text-xs"
        >
          <BookOpen size={14} /> Open Help Center
        </button>
      </section>

      {open && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/80 p-3 backdrop-blur-sm sm:p-6">
          <div className="flex h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-bg))] shadow-2xl">
            <header className="flex items-start justify-between gap-4 border-b border-[rgb(var(--color-border))] p-4 sm:p-5">
              <div className="flex gap-3">
                <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 p-2.5 text-success">
                  <ShieldCheck size={20} />
                </div>
                <div>
                  <h2 className="text-lg font-semibold">MT5 RMS Help Center</h2>
                  <p className="mt-1 text-xs text-muted">
                    Operational reference and configuration guide ·{" "}
                    {TOPICS.length} topics
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="btn-ghost p-2"
                aria-label="Close Help Center"
              >
                <X size={17} />
              </button>
            </header>
            <div className="border-b border-[rgb(var(--color-border))] p-3 sm:p-4">
              <div className="flex flex-col gap-2 lg:flex-row">
                <label className="relative min-w-0 flex-1">
                  <Search
                    size={15}
                    className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
                  />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="field-input py-2.5 pl-9 text-xs"
                    placeholder="Search features, calculations, data sources, permissions…"
                    autoFocus
                  />
                </label>
                <select
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  className="field-input py-2.5 text-xs lg:w-52"
                  aria-label="Help category"
                >
                  {categories.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </select>
              </div>
            </div>
            <main className="min-h-0 flex-1 overflow-y-auto p-3 scrollbar-hidden sm:p-4">
              <div className="space-y-3">
                {results.map((topic) => (
                  <Topic
                    key={topic.id}
                    topic={topic}
                    open={expanded.has(topic.id) || Boolean(query.trim())}
                    onToggle={() => toggle(topic.id)}
                  />
                ))}
                {!results.length && (
                  <div className="p-12 text-center text-sm text-muted">
                    No help topic matches this search.
                  </div>
                )}
              </div>
            </main>
          </div>
        </div>
      )}
    </>
  );
}
