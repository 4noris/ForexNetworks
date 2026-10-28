import { useEffect, useRef, useState } from "react";
import { BellRing, Save, Send } from "lucide-react";
import toast from "react-hot-toast";
import { apiRequest } from "../lib/api";

export default function NewsPushSettings() {
  const [settings, setSettings] = useState(null);
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testLogin, setTestLogin] = useState("");
  const [testMessage, setTestMessage] = useState("RMS push notification test.");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const testRequest = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest("/api/notifications/news-push", { signal: controller.signal, timeoutMs: 10000 })
      .then((result) => { if (!controller.signal.aborted) { setStatus(result); setSettings(result.settings); } })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!status?.settings?.enabled) return undefined;
    let stopped = false;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      apiRequest("/api/notifications/news-push", { signal: controller.signal, timeoutMs: 10000, dedupeMs: 0 })
        .then((value) => { if (!stopped) setStatus(value); }).catch(() => {});
    }, 15000);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); };
  }, [status?.settings?.enabled]);
  const update = (key, value) => setSettings((current) => ({ ...current, [key]: value }));
  const save = async (event) => {
    event.preventDefault();
    if (!settings || saving) return;
    setSaving(true);
    setError("");
    try {
      const result = await apiRequest("/api/notifications/news-push", {
        method: "PUT", timeoutMs: 15000,
        body: JSON.stringify({ enabled: settings.enabled, minimum_profit: Number(settings.minimum_profit),
          currency: settings.currency, message: settings.message }),
      });
      setStatus(result);
      setSettings(result.settings);
      toast.success(result.settings.enabled ? "News-trading push notifications enabled" : "News-trading push notifications disabled");
    } catch (cause) { setError(cause.message || "Unable to save push settings"); }
    finally { setSaving(false); }
  };
  const sendTest = async (event) => {
    event.preventDefault();
    if (testing || !/^[1-9][0-9]*$/.test(testLogin.trim()) || !testMessage.trim()) return;
    const login = testLogin.trim(), message = testMessage.trim();
    if (!testRequest.current || testRequest.current.login !== login || testRequest.current.message !== message)
      testRequest.current = { request_id: crypto.randomUUID(), login, message };
    setTesting(true);
    setTestResult(null);
    try {
      const result = await apiRequest("/api/notifications/news-push/test", {
        method: "POST", timeoutMs: 30000, body: JSON.stringify(testRequest.current),
      });
      setTestResult(result);
      if (result.status !== "sending") testRequest.current = null;
      if (result.status === "accepted") toast.success(`Test accepted by MT5 for #${login}`);
      else if (result.status !== "sending") toast.error(result.detail || "Test was not accepted by MT5");
    } catch (cause) {
      setTestResult({ login, status: "unknown", detail: `${cause.message || "Connection interrupted"}. Retry with the same login and message to check this test without sending it again.` });
    } finally { setTesting(false); }
  };
  const lastTest = testResult || status?.last_test;
  return <section className="mt-7 rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-5" aria-label="News-trading push notifications">
    <div className="flex items-center justify-between gap-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><BellRing size={17} />News-trading push notifications</h2>
      <span className={`badge ${status?.settings?.enabled ? "badge-success" : ""}`}>{status?.settings ? status.settings.enabled ? "Enabled" : "Disabled" : "Loading"}</span>
    </div>
    <p className="mt-2 text-xs leading-5 text-muted">Send MT5 mobile messages to clients when a verified news-trading match exceeds the realized profit threshold. Applies to monitored anti-fraud scopes. Only trades opened after enabling are eligible.</p>
    {error && <p role="alert" className="mt-3 text-xs text-danger">{error}</p>}
    {settings && <form onSubmit={save} className="mt-4 space-y-4">
      <label className="flex cursor-pointer items-center justify-between gap-4 text-sm font-medium">
        Send push notifications to news traders
        <input type="checkbox" role="switch" aria-label="Send push notifications to news traders" checked={settings.enabled} disabled={saving}
          onChange={(event) => update("enabled", event.target.checked)} className="h-4 w-4 accent-black" />
      </label>
      <div className="grid gap-4 sm:grid-cols-[1fr_160px]">
        <label className="text-xs text-muted">Minimum realized profit per trade
          <input className="field-input mt-1.5" aria-label="Minimum news-trade profit" type="number" min="0.01" step="0.01" required value={settings.minimum_profit}
            onChange={(event) => update("minimum_profit", event.target.value)} />
        </label>
        <label className="text-xs text-muted">Account currency
          <input className="field-input mt-1.5 uppercase" aria-label="News push account currency" value={settings.currency} required minLength={3} maxLength={6} pattern="[A-Z]{3,6}"
            onChange={(event) => update("currency", event.target.value.toUpperCase())} />
        </label>
      </div>
      <label className="block text-xs text-muted">Client message
        <textarea aria-label="News-trading client message" className="field-input mt-1.5 min-h-24 w-full" value={settings.message} required maxLength={1024}
          onChange={(event) => update("message", event.target.value)} />
      </label>
      <p className="text-[11px] leading-5 text-muted">Fields: {"{login}, {symbol}, {position_id}, {profit}, {currency}, {news_count}"}. Maximum 1024 characters after substitution. Each trade/news-release match is sent once. The client needs a MetaQuotes ID, and the manager needs notification rights.</p>
      <button type="submit" disabled={saving} className="btn-primary inline-flex min-w-40 items-center justify-center gap-2 px-4 py-2 text-xs"><Save size={14} />{saving ? "Saving…" : "Save push settings"}</button>
      <p className="text-[11px] text-muted">Disabling cancels queued messages and stops future sends. MT5 acceptance does not confirm delivery to the device.</p>
    </form>}
    {settings && <form onSubmit={sendTest} aria-label="Send test MT5 push notification" className="mt-5 space-y-3 border-t border-[rgb(var(--color-border))] pt-4">
      <h3 className="text-sm font-semibold">Send a test push</h3>
      <p className="text-xs text-muted">Sends one message to the entered MT5 login, even when automatic news-trading alerts are disabled. Test messages are sent as typed.</p>
      <div className="grid gap-3 sm:grid-cols-[180px_1fr]">
        <label className="text-xs text-muted">MT5 login
          <input aria-label="Test push MT5 login" className="field-input mt-1.5" inputMode="numeric" pattern="[1-9][0-9]*" required value={testLogin} disabled={testing}
            onChange={(event) => setTestLogin(event.target.value)} placeholder="Enter MT5 login" />
        </label>
        <label className="text-xs text-muted">Test message
          <textarea aria-label="Test push message" className="field-input mt-1.5 min-h-20 w-full" required maxLength={1024} value={testMessage} disabled={testing}
            onChange={(event) => setTestMessage(event.target.value)} />
        </label>
      </div>
      <button type="submit" disabled={testing || !testLogin.trim() || !testMessage.trim()} className="btn-secondary inline-flex min-w-40 items-center justify-center gap-2 px-4 py-2 text-xs"><Send size={14} />{testing ? "Sending test?" : "Send test notification"}</button>
      {lastTest && <p role="status" className={`text-xs ${lastTest.status === "accepted" ? "text-success" : "text-amber-500"}`}>Last test #{lastTest.login}: {lastTest.status} ? {lastTest.detail || "Test is in progress; retry checks its status without sending again."}</p>}
    </form>}
    {Boolean(status?.recent?.length) && <div className="mt-5 max-h-44 overflow-auto border-t border-[rgb(var(--color-border))] pt-3">
      <h3 className="mb-2 text-xs font-semibold">Recent notifications</h3>
      {status.recent.map((item) => <div key={item.id} className="flex flex-wrap justify-between gap-2 py-1.5 text-xs">
        <span>#{item.login} · {item.symbol} · {item.profit.toFixed(2)} {item.currency}</span>
        <span title={item.detail} className={item.status === "accepted" ? "text-success" : "text-muted"}>{item.status}{["failed", "skipped", "unknown"].includes(item.status) ? ` · ${item.detail}` : ""}</span>
      </div>)}
    </div>}
  </section>;
}
