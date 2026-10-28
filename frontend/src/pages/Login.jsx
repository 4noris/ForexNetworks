import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import gsap from "gsap";
import { ArrowRight, Lock, User } from "lucide-react";
import { apiRequest, getStoredUser } from "../lib/api";
import { applyStoredTheme } from "../lib/theme";
import toast from "react-hot-toast";

export default function Login() {
  const containerRef = useRef(null);
  const formRef = useRef(null);
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    applyStoredTheme();
    const storedUser = getStoredUser();
    if (storedUser) {
      navigate(
        storedUser.must_change_pw ? "/change-password" : "/dealing-risk",
        { replace: true },
      );
      return;
    }

    const ctx = gsap.context(() => {
      gsap.from(".stagger-item", {
        opacity: 0,
        y: 18,
        stagger: 0.1,
        duration: 0.7,
        ease: "power3.out",
        delay: 0.15,
      });
    }, containerRef);

    return () => ctx.revert();
  }, [navigate]);

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const data = await apiRequest("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      localStorage.setItem("user", JSON.stringify(data.user));
      localStorage.removeItem("accessToken");
      gsap.to(formRef.current, {
        scale: 0.97,
        opacity: 0,
        duration: 0.35,
        ease: "power2.inOut",
        onComplete: () =>
          navigate(
            data.user?.must_change_pw ? "/change-password" : "/dealing-risk",
          ),
      });
    } catch (err) {
      toast.error(err.message || "Login failed");
      setLoading(false);
    }
  };

  return (
    <div
      ref={containerRef}
      className="min-h-screen w-full flex items-center justify-center relative overflow-hidden"
      style={{ background: "rgb(var(--color-bg))" }}
    >
      {/* Subtle background accent */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 0%, rgb(var(--color-accent)/0.04), transparent)",
        }}
      />

      <div ref={formRef} className="w-full max-w-[420px] mx-4 relative z-10">
        {/* Card */}
        <div
          className="rounded-3xl p-8"
          style={{
            background: "rgb(var(--color-card))",
            border: "1px solid rgb(var(--color-border))",
            boxShadow: "var(--card-shadow)",
          }}
        >
          {/* Header */}
          <div className="mb-8 stagger-item">
            <h1
              className="text-2xl font-semibold tracking-tight"
              style={{ color: "rgb(var(--color-text))" }}
            >
              Welcome back
            </h1>
            <p
              className="mt-1 text-sm"
              style={{ color: "rgb(var(--color-text-3))" }}
            >
              Sign in to the brokerage suite
            </p>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            {/* Username */}
            <div className="stagger-item">
              <label
                htmlFor="login-username"
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.2em]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                Username
              </label>
              <div className="relative">
                <User
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                  style={{ color: "rgb(var(--color-text-3))" }}
                />
                <input
                  id="login-username"
                  type="text"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="field-input pl-9"
                  placeholder="admin"
                />
              </div>
            </div>

            {/* Password */}
            <div className="stagger-item">
              <label
                htmlFor="login-password"
                className="mb-1.5 block text-[11px] font-semibold uppercase tracking-[0.2em]"
                style={{ color: "rgb(var(--color-text-3))" }}
              >
                Password
              </label>
              <div className="relative">
                <Lock
                  size={16}
                  className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
                  style={{ color: "rgb(var(--color-text-3))" }}
                />
                <input
                  id="login-password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="field-input pl-9"
                  placeholder="Enter password"
                />
              </div>
            </div>

            {/* Submit */}
            <div className="stagger-item pt-1">
              <button
                type="submit"
                disabled={loading}
                className="btn-primary w-full"
              >
                {loading ? (
                  <div
                    className="h-5 w-5 rounded-full border-2 border-t-transparent animate-spin"
                    style={{
                      borderColor: "rgb(var(--color-bg))",
                      borderTopColor: "transparent",
                    }}
                  />
                ) : (
                  <>
                    <span>Sign In</span>
                    <ArrowRight size={16} />
                  </>
                )}
              </button>
            </div>
          </form>
        </div>

        {/* Footer */}
        <p
          className="mt-5 text-center text-xs"
          style={{ color: "rgb(var(--color-text-3))" }}
        >
          MT5 Brokerage Suite · Secure Login
        </p>
      </div>
    </div>
  );
}
