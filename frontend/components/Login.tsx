"use client";

import { useState } from "react";
import { api, setToken } from "@/lib/api";

export function Logo() {
  return (
    <span className="flex items-center gap-2">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
        <rect x="1" y="1" width="20" height="20" rx="5" fill="none" stroke="var(--color-signal-400)" strokeWidth="2" />
        <path d="M6 8l5 6 5-6" fill="none" stroke="var(--color-signal-400)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="font-display text-lg font-bold tracking-tight text-ink-50">VibeStack</span>
    </span>
  );
}

export function Login({ inviteRequired, onDone }: { inviteRequired: boolean; onDone: (email: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = mode === "login" ? await api.login(email, password) : await api.register(email, password, invite || undefined);
      setToken(res.token);
      onDone(res.email);
    } catch (err) {
      setError((err as Error).message.replace(/^\d+: /, ""));
    } finally {
      setBusy(false);
    }
  }

  const input = "mt-1.5 w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2.5 text-ink-50 outline-none focus:border-signal-400";
  return (
    <main className="blueprint grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <Logo />
        <h1 className="mt-8 font-display text-3xl font-bold leading-tight text-ink-50">
          {mode === "login" ? "Welcome back" : "Start building"}
        </h1>
        <p className="mt-2 text-ink-300">Describe an app. Eight agents design it, build it, test it and run it for you.</p>
        <form onSubmit={submit} className="mt-8 space-y-4 rounded-2xl border border-ink-700 bg-ink-900 p-6">
          <label className="block text-sm text-ink-200">
            Email
            <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={input} />
          </label>
          <label className="block text-sm text-ink-200">
            Password
            <input
              type="password"
              required
              minLength={8}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={input}
            />
            <span className="mt-1 block text-xs text-ink-400">At least 8 characters</span>
          </label>
          {mode === "register" && inviteRequired && (
            <label className="block text-sm text-ink-200">
              Invite code
              <input required value={invite} onChange={(e) => setInvite(e.target.value)} className={input} />
            </label>
          )}
          {error && <p className="text-sm text-rose-300">{error}</p>}
          <button disabled={busy} className="w-full rounded-lg bg-signal-400 py-2.5 font-semibold text-ink-950 hover:bg-signal-300 disabled:opacity-50">
            {busy ? "One moment…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>
        <button onClick={() => setMode(mode === "login" ? "register" : "login")} className="mt-4 text-sm text-ink-300 hover:text-ink-50">
          {mode === "login" ? "New here? Create an account" : "Already have an account? Log in"}
        </button>
      </div>
    </main>
  );
}
