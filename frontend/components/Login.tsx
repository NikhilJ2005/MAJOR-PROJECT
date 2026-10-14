"use client";

import { useState } from "react";
import { api, setToken } from "@/lib/api";

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

  const input = "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-base outline-none focus:border-sky-500";
  return (
    <div className="mx-auto mt-16 max-w-md px-4">
      <div className="mb-6 flex items-center gap-3">
        <div className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-sky-400 to-violet-500 font-mono text-base font-bold text-zinc-950">
          VS
        </div>
        <div>
          <h1 className="text-2xl font-bold leading-tight">VibeStack</h1>
          <p className="text-sm text-zinc-400">describe a backend · agents build, test, bug-hunt and self-heal it</p>
        </div>
      </div>
      <form onSubmit={submit} className="space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex gap-1 rounded-lg bg-zinc-950 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              type="button"
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md py-1.5 ${mode === m ? "bg-zinc-800 font-semibold text-white" : "text-zinc-400 hover:text-zinc-200"}`}
            >
              {m === "login" ? "Log in" : "Create account"}
            </button>
          ))}
        </div>
        <label className="block text-sm text-zinc-400">
          Email
          <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={`mt-1 ${input}`} />
        </label>
        <label className="block text-sm text-zinc-400">
          Password <span className="text-zinc-600">(8+ characters)</span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={`mt-1 ${input}`}
          />
        </label>
        {mode === "register" && inviteRequired && (
          <label className="block text-sm text-zinc-400">
            Invite code
            <input required value={invite} onChange={(e) => setInvite(e.target.value)} className={`mt-1 ${input}`} />
          </label>
        )}
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <button
          disabled={busy}
          className="w-full rounded-xl bg-gradient-to-r from-sky-500 to-violet-500 py-2.5 text-base font-bold text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? "…" : mode === "login" ? "Log in" : "Create account"}
        </button>
        <p className="text-xs text-zinc-500">Your runs, generated code and live previews are private to your account.</p>
      </form>
    </div>
  );
}
