import React, { useLayoutEffect, useRef } from "react";

const defaultFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

function observedNumber(value) {
  if (value == null || typeof value === "boolean" || (typeof value === "string" && !value.trim())) return null;
  if (!["number", "string"].includes(typeof value)) return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export default function AnimatedNumber({
  value,
  formatter,
  prefix = "",
  suffix = "",
  duration = 0.14,
  className = "",
  emptyLabel = "Unavailable",
}) {
  const numeric = observedNumber(value);
  const root = useRef(null);
  const previous = useRef(null);
  const format =
    typeof formatter === "function"
      ? formatter
      : formatter && typeof formatter.format === "function"
        ? (next) => formatter.format(next)
        : (next) => defaultFormatter.format(next);
  const text = numeric === null ? emptyLabel : `${prefix}${format(numeric)}${suffix}`;

  useLayoutEffect(() => {
    const now = performance.now();
    const before = previous.current;
    previous.current = { text, numeric, at: now };
    if (!before || before.text === text || before.numeric === null || numeric === null ||
        document.hidden || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ||
        !root.current?.animate || typeof KeyframeEffect === "undefined" ||
        !window.CSS?.supports("content", 'attr(data-previous) / ""')) return undefined;

    const direction = numeric >= before.numeric ? 1 : -1;
    // Retarget to each actual source amount. There is no numeric tween, queue
    // of skipped ticks, or animation-frame React update.
    const milliseconds = Math.max(0, Math.min(duration * 1000, 160, Math.max(60, (now - before.at) * .7)));
    if (!milliseconds) return undefined;
    const animations = [];
    const slots = [];
    const updates = [];
    const oldCharacters = Array.from(before.text);
    let cancelled = false;
    for (const slot of root.current.querySelectorAll("[data-digit-slot]")) {
      const offset = Number(slot.dataset.digitSlot);
      const old = oldCharacters.at(-1 - offset);
      if (!old || !/[0-9]/.test(old) || old === slot.textContent) continue;
      slot.dataset.previous = old;
      slots.push(slot);
      updates.push({ slot, current: slot.firstElementChild });
    }
    if (!updates.length) return undefined;
    // Resolve generated boxes once before targeting them. Batch the style
    // writes so many changing digits do not trigger one style flush each.
    void getComputedStyle(updates[0].slot, "::before").content;
    for (const { slot, current } of updates) {
      const oldEffect = new KeyframeEffect(slot, [
        { transform: "translateY(0)" },
        { transform: `translateY(${-direction * 100}%)` },
      ], { duration: milliseconds, easing: "cubic-bezier(.2,.65,.25,1)", fill: "forwards", pseudoElement: "::before" });
      if (oldEffect.pseudoElement !== "::before") {
        slot.removeAttribute("data-previous");
        continue;
      }
      // The outgoing glyph is CSS generated content with empty alternative
      // text. DOM text/accessibility always contain the new formatted value.
      const outgoing = new Animation(oldEffect, document.timeline);
      const incoming = current.animate([
        { transform: `translateY(${direction * 100}%)` },
        { transform: "translateY(0)" },
      ], { duration: milliseconds, easing: "cubic-bezier(.2,.65,.25,1)" });
      outgoing.play();
      animations.push(outgoing, incoming);
      void outgoing.finished.then(() => {
        if (cancelled) return;
        slot.removeAttribute("data-previous");
        outgoing.cancel();
      }).catch(() => {});
    }
    return () => {
      cancelled = true;
      for (const animation of animations) animation.cancel();
      for (const slot of slots) slot.removeAttribute("data-previous");
    };
  }, [text, numeric, duration]);

  const characters = Array.from(text);

  return (
    <span ref={root} data-observed-value={numeric ?? undefined} className={`observed-number rolling-number tabular-nums ${className}`}>
      {numeric === null ? text : characters.map((character, index) => /[0-9]/.test(character)
        ? <span key={characters.length - index} className="rolling-number-digit" data-digit-slot={characters.length - index - 1}><span className="rolling-number-current">{character}</span></span>
        : <span key={characters.length - index} className="rolling-number-static">{character}</span>)}
    </span>
  );
}
