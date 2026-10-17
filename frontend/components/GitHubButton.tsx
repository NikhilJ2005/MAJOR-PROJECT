"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type GitHubConnection, type GitHubStatus } from "@/lib/api";

function GitHubMark() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

const CI_TEXT: Record<string, [string, string]> = {
  success: ["CI passing", "text-emerald-300"],
  failure: ["CI failing", "text-rose-300"],
  running: ["CI running", "text-signal-300"],
};

/** Push the current version to the user's GitHub repository, and show its CI status. */
export function GitHubButton({ projectId, currentVersion }: { projectId: string; currentVersion: number | null }) {
  const [conn, setConn] = useState<GitHubConnection | null>(null);
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [c, s] = await Promise.all([api.github(), api.githubStatus(projectId)]);
    setConn(c);
    setStatus(s);
    return s;
  }, [projectId]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stop = false;
    const tick = async () => {
      const s = await refresh().catch(() => null);
      // Follow the Actions run until it finishes.
      if (!stop && s?.ci && s.ci.status !== "completed") timer = setTimeout(tick, 15000);
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  if (!conn?.enabled) return null;
  if (!conn.connected)
    return (
      <a href={api.githubSignInUrl(`/?project=${projectId}`)} className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-sm text-ink-100 hover:border-ink-500">
        <GitHubMark /> Connect GitHub
      </a>
    );

  async function push() {
    setBusy(true);
    setError(null);
    try {
      await api.pushToGitHub(projectId);
      await refresh();
    } catch (e) {
      setError((e as Error).message.replace(/^\d+: /, ""));
    } finally {
      setBusy(false);
    }
  }

  const ci = status?.ci;
  const ciKey = ci ? (ci.status !== "completed" ? "running" : ci.conclusion === "success" ? "success" : "failure") : null;
  const upToDate = status?.version != null && status.version === currentVersion;
  return (
    <div className="flex items-center gap-2 text-sm">
      {status?.repo && (
        <a href={status.url} target="_blank" rel="noreferrer" className="hidden items-center gap-1.5 text-ink-300 hover:text-ink-50 xl:flex" title="Open the repository">
          <GitHubMark /> {status.repo}
          {status.version != null && <span className="text-ink-500">· v{status.version}</span>}
        </a>
      )}
      {ci && ciKey && (
        <a href={ci.url} target="_blank" rel="noreferrer" className={`hidden xl:inline ${CI_TEXT[ciKey][1]}`}>
          {CI_TEXT[ciKey][0]}
        </a>
      )}
      <button
        onClick={push}
        disabled={busy || currentVersion == null || upToDate}
        title={currentVersion == null ? "Push needs a working version" : upToDate ? "This version is already on GitHub" : undefined}
        className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-ink-100 hover:border-ink-500 disabled:opacity-50"
      >
        <GitHubMark /> {busy ? "Pushing…" : status?.repo ? (upToDate ? "Pushed" : `Push v${currentVersion}`) : "Push to GitHub"}
      </button>
      {error && <span className="max-w-60 truncate text-rose-300" title={error}>{error}</span>}
    </div>
  );
}
