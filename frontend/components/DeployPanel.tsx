"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Deployment } from "@/lib/api";

const DOT: Record<string, string> = {
  waiting: "border border-ink-600",
  running: "animate-pulse bg-signal-400",
  done: "bg-emerald-400",
  failed: "bg-rose-400",
};

/** Put the current version live on the Kubernetes cluster, and follow each step until it answers at its URL. */
export function DeployButton({ projectId, canDeploy, currentRunId }: { projectId: string; canDeploy: boolean; currentRunId?: string | null }) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [dep, setDep] = useState<Deployment | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => setDep(await api.deployment(projectId).catch(() => null)), [projectId]);
  useEffect(() => {
    (async () => {
      const config = await api.config().catch(() => null);
      setEnabled(!!config?.deploy_enabled);
      if (config?.deploy_enabled) await load();
    })();
  }, [load]);
  useEffect(() => {
    if (dep?.status !== "deploying") return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [dep?.status, load]);

  if (!enabled) return null;
  const outdated = dep && currentRunId && dep.run_id !== currentRunId;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await load();
    } catch (e) {
      setError((e as Error).message.replace(/^\d+: /, ""));
    }
  }

  const label = dep?.status === "live" ? (outdated ? "Update live app" : "Live") : dep?.status === "deploying" ? "Deploying…" : "Deploy";
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className={`rounded-lg px-3 py-1.5 text-sm font-medium ${dep?.status === "live" && !outdated ? "border border-emerald-500/50 text-emerald-300" : "bg-signal-400 text-ink-950 hover:bg-signal-300"}`}
      >
        {dep?.status === "live" && !outdated && <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-emerald-400" />}
        {label}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-[380px] rounded-xl border border-ink-700 bg-ink-900 p-4 shadow-2xl">
          <h3 className="font-display text-base text-ink-50">Deploy to your Kubernetes cluster</h3>
          <p className="mt-1 text-xs text-ink-400">
            Its own namespace, a Postgres database, two replicas and a public address. The live URL is tested before it is marked live.
          </p>
          {dep && (
            <ol className="mt-3 space-y-2">
              {dep.steps.map((s) => (
                <li key={s.id} className="flex gap-2.5 text-sm">
                  <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[s.state]}`} />
                  <span className="min-w-0">
                    <span className={s.state === "waiting" ? "text-ink-500" : "text-ink-100"}>{s.label}</span>
                    {s.detail && s.state !== "failed" && <span className="block truncate text-xs text-ink-500" title={s.detail}>{s.detail}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
          {dep?.status === "live" && (
            <a href={dep.url} target="_blank" rel="noreferrer" className="mt-3 block truncate rounded-lg bg-emerald-500/10 px-3 py-2 font-mono text-sm text-emerald-300 hover:underline">
              {dep.url}
            </a>
          )}
          {dep?.status === "failed" && dep.message && (
            <p className="mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{dep.message}</p>
          )}
          {error && <p className="mt-3 text-sm text-rose-300">{error}</p>}
          <div className="mt-4 flex items-center gap-2">
            {dep?.status !== "deploying" && (
              <button
                disabled={!canDeploy}
                title={canDeploy ? "" : "Only a version that passed all its tests can be deployed"}
                onClick={() => run(() => api.deploy(projectId))}
                className="rounded-lg bg-signal-400 px-3 py-1.5 text-sm font-semibold text-ink-950 hover:bg-signal-300 disabled:opacity-40"
              >
                {!dep ? "Deploy this version" : dep.status === "failed" ? "Try again" : outdated ? "Deploy the current version" : "Redeploy"}
              </button>
            )}
            {dep && dep.status !== "deploying" && (
              <button onClick={() => confirm("Delete the live app and its database?") && run(() => api.undeploy(projectId))}
                className="ml-auto text-sm text-ink-400 hover:text-rose-300">
                Undeploy
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
