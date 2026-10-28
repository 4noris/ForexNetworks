import { memo, useMemo, useRef, useState } from "react";
import { Minus, Plus, RotateCcw } from "lucide-react";
import land from "../data/world-land.json";

const INITIAL_VIEW = { scale: 1, x: 0, y: 0 };
const EMPTY_POINTS = [];
const coordinate = (value) => typeof value === "number" ? value
  : typeof value === "string" && value.trim() ? Number(value) : NaN;
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
function bounded(view) {
  return { ...view, x: clamp(view.x, 1000 * (1 - view.scale), 0), y: clamp(view.y, 500 * (1 - view.scale), 0) };
}

function WorldNetworkMap({ points = EMPTY_POINTS }) {
  const [view, setView] = useState(INITIAL_VIEW);
  const [activeId, setActiveId] = useState(null);
  const drag = useRef(null);
  const projected = useMemo(() => {
    const seen = new Set();
    return points.flatMap((point) => {
      const latitude = coordinate(point.latitude), longitude = coordinate(point.longitude);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180
          || typeof point.ip !== "string" || !point.ip.trim() || seen.has(point.ip)) return [];
      seen.add(point.ip);
      return [{ ...point, x: 12 + (longitude + 180) / 360 * 976, y: 12 + (90 - latitude) / 180 * 476 }];
    });
  }, [points]);
  const active = projected.find((point) => point.ip === activeId);
  const zoom = (amount) => setView((current) => {
    const scale = clamp(current.scale + amount, 1, 8);
    return bounded({ scale, x: 500 - (500 - current.x) / current.scale * scale, y: 250 - (250 - current.y) / current.scale * scale });
  });
  const startDrag = (event) => {
    if (view.scale === 1 || event.button !== 0 || event.target.closest("[data-location]")) return;
    drag.current = { pointer: event.pointerId, clientX: event.clientX, clientY: event.clientY, ...view };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const move = (event) => {
    const start = drag.current;
    if (!start || start.pointer !== event.pointerId) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const unitsPerPixel = 1 / Math.min(rect.width / 1000, rect.height / 500);
    setView(bounded({ scale: start.scale, x: start.x + (event.clientX - start.clientX) * unitsPerPixel,
      y: start.y + (event.clientY - start.clientY) * unitsPerPixel }));
  };
  const endDrag = () => { drag.current = null; };
  const keyboardPan = (event) => {
    if (view.scale === 1 || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    setView((current) => bounded({ ...current,
      x: current.x + (event.key === "ArrowLeft" ? 60 : event.key === "ArrowRight" ? -60 : 0),
      y: current.y + (event.key === "ArrowUp" ? 60 : event.key === "ArrowDown" ? -60 : 0) }));
  };
  return <div className="network-map-frame flex w-full flex-col overflow-hidden rounded-xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-bg))]">
    <div className="relative aspect-[2/1] w-full">
    <svg viewBox="0 0 1000 500" preserveAspectRatio="xMidYMid meet" className="absolute inset-0 block h-full w-full"
      role="group" aria-label="Client IP locations map" tabIndex={0}
      style={{ touchAction: view.scale > 1 ? "none" : "pan-y", cursor: view.scale > 1 ? "grab" : "default" }}
      onPointerDown={startDrag} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onKeyDown={keyboardPan}>
      <desc>Approximate last-login IP locations. Zoom using the buttons, then drag or use arrow keys to pan. Focus a marker for its IP and provider.</desc>
      <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
        <g stroke="rgb(var(--color-border))" strokeOpacity=".45" strokeWidth=".7" aria-hidden="true">
          {[0, 60, 120, 180, 240, 300, 360].map((longitude) => <line key={`x${longitude}`} x1={12 + longitude / 360 * 976} x2={12 + longitude / 360 * 976} y1="12" y2="488" />)}
          {[0, 30, 60, 90, 120, 150, 180].map((latitude) => <line key={`y${latitude}`} x1="12" x2="988" y1={12 + latitude / 180 * 476} y2={12 + latitude / 180 * 476} />)}
        </g>
        <path d={land.path} fill="rgb(var(--color-text-3))" fillOpacity=".23" stroke="rgb(var(--color-text-3))" strokeOpacity=".35" strokeWidth=".6" fillRule="evenodd" aria-hidden="true" />
        {projected.map((point) => {
          const color = point.vps_status === "detected" ? "#ef4444" : point.vps_status === "not_detected" ? "#22c55e" : "#f59e0b";
          const label = `${point.ip} · ${point.location || "Location unavailable"} · ${point.provider || "Provider unavailable"} · ${point.accounts ?? "Unknown"} account(s)`;
          return <g key={point.ip} data-location={point.ip} transform={`translate(${point.x} ${point.y})`} role="button" tabIndex={0} aria-label={label}
            onPointerEnter={() => setActiveId(point.ip)} onFocus={() => setActiveId(point.ip)} onClick={() => setActiveId(point.ip)}
            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setActiveId(point.ip); } }}>
            <title>{label}</title>
            <circle r={12 / view.scale} fill={color} opacity=".2" />
            <circle r={(point.ip === activeId ? 7 : 5) / view.scale} fill={color} stroke="rgb(var(--color-card))" strokeWidth={1.5 / view.scale} />
          </g>;
        })}
      </g>
      {!projected.length && <text x="500" y="250" textAnchor="middle" fill="rgb(var(--color-text-2))" fontSize="17">IP location data unavailable</text>}
    </svg>
    {active && <div role="status" className="pointer-events-none absolute bottom-2 left-2 max-w-[85%] rounded-lg border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-3 py-2 text-[10px] shadow-sm">
      <p className="font-semibold">{active.ip} · {active.accounts ?? "Unknown"} account(s)</p>
      <p className="truncate text-muted">{active.location || "Location unavailable"} · {active.provider || "Provider unavailable"}</p>
    </div>}
    </div>
    <div className="flex items-center justify-between gap-2 border-t border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] px-2 py-1">
      <span className="truncate text-[10px] text-muted">Zoom to explore locations</span>
      <div className="flex shrink-0 gap-1">
        <button type="button" aria-label="Zoom in map" title="Zoom in" disabled={view.scale >= 8} onClick={() => zoom(1)} className="flex h-8 w-8 items-center justify-center rounded text-secondary hover:bg-[var(--inp-bg)] disabled:opacity-30"><Plus size={15} /></button>
        <button type="button" aria-label="Zoom out map" title="Zoom out" disabled={view.scale === 1} onClick={() => zoom(-1)} className="flex h-8 w-8 items-center justify-center rounded text-secondary hover:bg-[var(--inp-bg)] disabled:opacity-30"><Minus size={15} /></button>
        <button type="button" aria-label="Reset map view" title="Show full world" onClick={() => { setView(INITIAL_VIEW); setActiveId(null); }} className="flex h-8 w-8 items-center justify-center rounded text-secondary hover:bg-[var(--inp-bg)]"><RotateCcw size={14} /></button>
      </div>
    </div>
  </div>;
}

export default memo(WorldNetworkMap);
