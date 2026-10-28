import React, { useState } from "react";
import MarkoutPanel from "../components/MarkoutPanel";

const todayUtc = () => new Date().toISOString().slice(0, 10);

export default function MarkoutWorkspace({ selectedGroups, candidates = [], active = true }) {
  const [loginText, setLoginText] = useState("");
  const login = Number(loginText);
  const validLogin = Number.isSafeInteger(login) && login > 0;
  const accounts = candidates.filter((row) => row?.login || row?.Login).slice(0, 500);

  return <div className="space-y-4">
    <section className="flex flex-wrap items-end justify-between gap-3 rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-3 sm:px-4">
      <div><p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted">Execution quality</p><h2 className="mt-1 text-sm font-semibold">Markout analytics</h2><p className="mt-1 text-[11px] text-muted">Select a client to inspect verified post-execution price movement.</p></div>
      <label className="block w-full text-xs text-muted sm:w-64">Client login
        <input className="field-input mt-1 !py-2" type="number" min="1" step="1" list="markout-clients" value={loginText} onChange={(event) => setLoginText(event.target.value)} placeholder="MT5 client login" />
      </label>
      <datalist id="markout-clients">{accounts.map((row) => {
        const id = row.login || row.Login;
        return <option key={id} value={id}>{row.name || row.Name || `Account #${id}`}</option>;
      })}</datalist>
      {!selectedGroups.length && <p className="mt-3 text-xs text-amber-400">Select a live group above to scope the client.</p>}
    </section>
    {validLogin && <MarkoutPanel key={login} active={active} login={login} selectedGroups={selectedGroups} initialDate={todayUtc()} />}
  </div>;
}
