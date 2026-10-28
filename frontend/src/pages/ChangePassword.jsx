import React, { useState } from "react";
import { KeyRound, Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { changePassword } from "../lib/api";

export default function ChangePassword() {
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (newPassword !== confirmation) {
      toast.error("New passwords do not match");
      return;
    }
    setLoading(true);
    try {
      const result = await changePassword(currentPassword, newPassword);
      localStorage.setItem("user", JSON.stringify(result.user));
      toast.success("Password updated");
      navigate("/dealing-risk", { replace: true });
    } catch (error) {
      toast.error(error.message || "Unable to update password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[rgb(var(--color-bg))] p-4">
      <section className="w-full max-w-md rounded-3xl border border-[rgb(var(--color-border))] bg-[rgb(var(--color-card))] p-8 shadow-[var(--card-shadow)]">
        <div className="mb-6 flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--inp-border)] bg-[var(--btn-ghost-bg)]">
          <KeyRound size={20} />
        </div>
        <h1 className="text-2xl font-semibold">Secure your account</h1>
        <p className="mt-2 text-sm text-muted">
          Replace the temporary password before accessing brokerage data.
        </p>
        <form className="mt-6 space-y-4" onSubmit={submit}>
          <label className="block text-xs text-muted">
            Current password
            <input
              autoComplete="current-password"
              className="field-input mt-1.5"
              type="password"
              required
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
            />
          </label>
          <label className="block text-xs text-muted">
            New password
            <input
              autoComplete="new-password"
              className="field-input mt-1.5"
              type="password"
              required
              minLength={12}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
            />
          </label>
          <label className="block text-xs text-muted">
            Confirm new password
            <input
              autoComplete="new-password"
              className="field-input mt-1.5"
              type="password"
              required
              minLength={12}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <p className="text-[11px] text-muted">
            Use at least 12 characters and a password unique to this
            application.
          </p>
          <button
            className="btn-primary w-full justify-center"
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="animate-spin" size={16} />
            ) : (
              <KeyRound size={16} />
            )}
            Update password
          </button>
        </form>
      </section>
    </main>
  );
}
