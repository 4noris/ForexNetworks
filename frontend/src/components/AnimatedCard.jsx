import React, { useEffect, useRef } from "react";
import gsap from "gsap";
import { cn } from "../lib/utils";

export default function AnimatedCard({ className = "", delay = 0, children }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const tween = gsap.fromTo(
      ref.current,
      { y: 4 },
      { y: 0, duration: 0.18, delay: Math.min(delay, 0.2), ease: "power2.out", clearProps: "transform" },
    );
    return () => tween.kill();
  }, [delay]);

  return (
    <div ref={ref} className={cn("glass-card", className)}>
      {children}
    </div>
  );
}
