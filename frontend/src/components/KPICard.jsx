import React, { useEffect, useMemo, useRef } from "react";
import gsap from "gsap";
import { TrendingUp, TrendingDown } from "lucide-react";
import { cn } from "../lib/utils";
import AnimatedNumber from "./AnimatedNumber";

export default function KPICard({
  title,
  value,
  prefix = "",
  suffix = "",
  trend,
  trendValue,
  icon: Icon,
  delay = 0,
  formatter: valueFormatter,
}) {
  const cardRef = useRef(null);
  const formatter = useMemo(
    () =>
      new Intl.NumberFormat("en-US", {
        maximumFractionDigits: prefix === "$" ? 2 : 0,
      }),
    [prefix],
  );

  useEffect(() => {
    if (!cardRef.current || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const tween = gsap.fromTo(
      cardRef.current,
      { y: 4 },
      { y: 0, duration: 0.18, delay: Math.min(delay, 0.2), ease: "power2.out", clearProps: "transform" },
    );
    return () => tween.kill();
  }, [delay]);

  const isPositive = trend === "up";

  return (
    <div ref={cardRef} className="glass-card p-5 relative overflow-hidden">
      {/* top shimmer line */}
      <div
        className="absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, rgb(var(--color-border)), transparent)",
        }}
      />

      <div className="flex justify-between items-start gap-3">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[rgb(var(--color-text-3))]">
          {title}
        </p>
        {Icon && (
          <div
            className="rounded-lg p-2 flex-shrink-0"
            style={{
              background: "var(--btn-ghost-bg)",
              border: "1px solid var(--inp-border)",
            }}
          >
            <Icon size={16} style={{ color: "rgb(var(--color-text-2))" }} />
          </div>
        )}
      </div>

      <div className="mt-3">
        <div
          className="text-2xl font-semibold tracking-tight"
          style={{ color: "rgb(var(--color-text))" }}
        >
          {typeof value === "string" ? (
            value
          ) : (
            <AnimatedNumber
              value={value}
              prefix={prefix}
              suffix={suffix}
              formatter={valueFormatter || formatter}
            />
          )}
        </div>

        {trend && (
          <div className="mt-2 flex items-center gap-1.5 text-xs">
            <span
              className={cn(
                "flex items-center gap-0.5 font-semibold",
                isPositive ? "text-success" : "text-danger",
              )}
            >
              {isPositive ? (
                <TrendingUp size={13} />
              ) : (
                <TrendingDown size={13} />
              )}
              {trendValue}
            </span>
            <span className="text-[rgb(var(--color-text-3))]">
              vs last month
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
