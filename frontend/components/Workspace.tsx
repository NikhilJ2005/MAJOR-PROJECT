"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Architecture } from "@/components/Architecture";
import { AgentSteps, deriveSteps } from "@/components/AgentSteps";
import { DesignReview } from "@/components/DesignReview";
import { Files } from "@/components/Files";
import { LiveCode } from "@/components/LiveCode";
import { StatusDot } from "@/components/Home";
import { BugsAndHealing, Empty } from "@/components/Ledger";
import { Tests } from "@/components/Tests";
import { API, api, type ProjectDetail, type Snapshot, type Version } from "@/lib/api";
import { useRun } from "@/lib/useRun";

const TABS = ["Live", "Preview", "Code", "Design", "Tests", "Quality"] as const;
type Tab = (typeof TABS)[number];

export function Workspace({ projectId, onHome }: { projectId: string; onHome: () => void }) {
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [chosenTab, setChosenTab] = useState<Tab>("Preview");
  const [leftReview, setLeftReview] = useState<string | null>(null);

  const load = useCallback(async () => {
    const p = await api.project(projectId);
    setProject(p);
    return p;
  }, [projectId]);

  useEffect(() => {
    (async () => {
      const p = await load();
      const latest = p.versions[p.versions.length - 1];
      setSelected((s) => s ?? (latest?.status === "running" ? latest.run_id : p.current_run_id ?? latest?.run_id ?? null));
      if (latest?.status === "running") setChosenTab("Live");  // watch the agents write
    })();
  }, [load]);

  const running = project?.versions.find((v) => v.status === "running") ?? null;
  const live = useRun(running?.run_id ?? null, async () => {
    const p = await load();
    setSelected(p.versions[p.versions.length - 1]?.run_id ?? null);
    setChosenTab("Preview");
  });
  const shown = useRun(selected === running?.run_id ? null : selected);
  const snap = selected === running?.run_id ? live.snap : shown.snap;
  const reviewing = live.snap?.status === "awaiting_approval" && selected === running?.run_id;
  // While the build waits for the user's approval, open the design, unless they picked another tab since.
  const tab = reviewing && leftReview !== running?.run_id ? "Design" : chosenTab;
  const setTab = (t: Tab) => {
    setChosenTab(t);
    if (reviewing) setLeftReview(running?.run_id ?? null);
  };

  async function send(prompt: string, mode: "edit" | "fix" = "edit") {
    const { run_id } = await api.message(projectId, prompt, mode);
    setTab("Live");
    await load();
    setSelected(run_id);
  }

  async function restore(runId: string) {
    await api.restore(projectId, runId);
    await load();
  }

  if (!project) return <div className="grid h-[calc(100vh-56px)] place-items-center text-ink-400">Opening project…</div>;

  return (
    <div className="grid h-[calc(100vh-56px)] grid-cols-1 lg:grid-cols-[400px_minmax(0,1fr)]">
      <ChatPanel
        project={project}
        selected={selected}
        onSelect={setSelected}
        onSend={send}
        onRestore={restore}
        onHome={onHome}
        liveSteps={running ? deriveSteps(live.events, live.snap) : null}
        selectedSnap={snap}
      />
      <section className="flex min-h-0 flex-col border-l border-ink-800 bg-ink-900/40">
        <div className="flex items-center gap-1 border-b border-ink-800 px-3">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`-mb-px border-b-2 px-3 py-3 text-sm ${tab === t ? "border-signal-400 font-medium text-ink-50" : "border-transparent text-ink-400 hover:text-ink-100"}`}
            >
              {t}
              {t === "Design" && reviewing && <span className="ml-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-signal-400" aria-label="waiting for your approval" />}
              {t === "Quality" && (snap?.bugs?.length ?? 0) > 0 && <span className="ml-1.5 rounded-full bg-signal-400/20 px-1.5 text-xs text-signal-300">{snap?.bugs?.length}</span>}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {snap?.has_artifact && selected && (
              <a href={api.downloadUrl(selected)} className="rounded-lg border border-ink-700 px-3 py-1.5 text-sm text-ink-100 hover:border-ink-500">
                Download code
              </a>
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {tab === "Preview" && <PreviewPane runId={selected} snap={snap} />}
          {tab === "Live" && (
            <LiveCode
              live={selected === running?.run_id ? live.live : {}}
              events={selected === running?.run_id ? live.events : []}
              running={selected === running?.run_id}
            />
          )}
          <div className={tab === "Preview" || tab === "Live" ? "hidden" : "p-5"}>
            {tab === "Code" && selected && <Files runId={selected} files={snap?.files ?? []} version={snap?.ledger?.length ?? 0} />}
            {tab === "Design" &&
              (reviewing && snap?.spec && selected ? (
                <DesignReview key={JSON.stringify(snap.spec)} runId={selected} spec={snap.spec} onSent={() => { live.refresh(); setTab("Live"); }} />
              ) : snap?.spec ? (
                <Architecture spec={snap.spec} plan={snap.file_plan} plugins={snap.plugin_choices} />
              ) : (
                <Empty text="The Architect's design appears here." />
              ))}
            {tab === "Tests" && <Tests validation={snap?.validation} report={snap?.report} files={snap?.files ?? []} />}
            {tab === "Quality" && (
              <BugsAndHealing entries={snap?.ledger ?? []} bugs={snap?.bugs ?? []} notes={snap?.review ?? []} done={snap?.status !== "running"} stopReason={snap?.stop_reason} />
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------- the conversation

function ChatPanel({
  project, selected, onSelect, onSend, onRestore, onHome, liveSteps, selectedSnap,
}: {
  project: ProjectDetail;
  selected: string | null;
  onSelect: (id: string) => void;
  onSend: (prompt: string, mode?: "edit" | "fix") => Promise<void>;
  onRestore: (id: string) => Promise<void>;
  onHome: () => void;
  liveSteps: ReturnType<typeof deriveSteps> | null;
  selectedSnap: Snapshot | null;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const busy = project.versions.some((v) => v.status === "running");
  const canEdit = project.versions.length > 0 && !busy;
  const latest = project.versions[project.versions.length - 1];
  const lastFailed = !busy && !!latest && latest.status !== "succeeded";

  const progress = liveSteps?.map((s) => s.state).join() ?? "";
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [project.versions.length, progress]);

  async function send(prompt: string, mode: "edit" | "fix" = "edit") {
    setError(null);
    try {
      await onSend(prompt, mode);
      setText("");
    } catch (err) {
      setError((err as Error).message.replace(/^\d+: /, ""));
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    send(text.trim());
  }

  return (
    <aside className="flex min-h-0 flex-col bg-ink-950">
      <div className="flex items-center gap-2 border-b border-ink-800 px-4 py-3">
        <button onClick={onHome} className="text-sm text-ink-400 hover:text-ink-50" aria-label="All projects">All projects</button>
        <span className="text-ink-600">/</span>
        <span className="truncate font-medium text-ink-50">{project.name}</span>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-5">
        {project.versions.map((v) => (
          <Message
            key={v.run_id}
            version={v}
            isCurrent={v.run_id === project.current_run_id}
            isSelected={v.run_id === selected}
            steps={v.status === "running" ? liveSteps : v.run_id === selected ? deriveSteps([], selectedSnap) : null}
            snap={v.run_id === selected ? selectedSnap : null}
            onSelect={() => onSelect(v.run_id)}
            onRestore={() => onRestore(v.run_id)}
          />
        ))}
        <div ref={end} />
      </div>
      <form onSubmit={submit} className="border-t border-ink-800 p-3">
        {lastFailed && (
          <div className="mb-2 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => send("Keep fixing the current version.", "fix")}
              title="Continue healing this version's code: no new design, no new code"
              className="rounded-full bg-signal-400 px-3 py-1 text-sm font-semibold text-ink-950 hover:bg-signal-300"
            >
              Keep fixing
            </button>
            <button
              type="button"
              onClick={() => send("Rebuild this app from its design and fix whatever stopped it.")}
              title="Write the business logic again from the same design"
              className="rounded-full border border-ink-600 px-3 py-1 text-sm text-ink-200 hover:border-ink-400 hover:text-ink-50"
            >
              Rebuild from scratch
            </button>
          </div>
        )}
        <div className="rounded-xl border border-ink-700 bg-ink-900 p-2 focus-within:border-signal-400">
          <label htmlFor="followup" className="sr-only">Ask for a change</label>
          <textarea
            id="followup"
            rows={2}
            value={text}
            disabled={!canEdit}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && text.trim().length >= 3 && canEdit) submit(e);
            }}
            placeholder={busy ? "The agents are working…" : lastFailed ? "Describe a change, or press Keep fixing" : "Ask for a change, e.g. “add product reviews with a 1–5 rating”"}
            className="w-full resize-none bg-transparent px-1.5 py-1 text-sm text-ink-50 outline-none placeholder:text-ink-500 disabled:opacity-60"
          />
          <div className="flex items-center justify-between">
            <span className="px-1.5 text-xs text-ink-500">Enter to send, Shift+Enter for a new line</span>
            <button disabled={!canEdit || text.trim().length < 3} className="rounded-lg bg-signal-400 px-3 py-1.5 text-sm font-semibold text-ink-950 disabled:opacity-40">
              Send
            </button>
          </div>
        </div>
        {error && <p className="mt-2 text-sm text-rose-300">{error}</p>}
      </form>
    </aside>
  );
}

function Message({
  version, isCurrent, isSelected, steps, snap, onSelect, onRestore,
}: {
  version: Version;
  isCurrent: boolean;
  isSelected: boolean;
  steps: ReturnType<typeof deriveSteps> | null;
  snap: Snapshot | null;
  onSelect: () => void;
  onRestore: () => void;
}) {
  const seconds = version.finished_at ? version.finished_at - version.created_at : null;
  const changes = snap?.changes;
  return (
    <div className="space-y-2">
      <div className="ml-8 rounded-2xl rounded-tr-sm bg-ink-800 px-4 py-2.5 text-sm leading-relaxed text-ink-50">{version.prompt}</div>
      <div
        className={`rounded-2xl rounded-tl-sm border px-4 py-3 ${isSelected ? "border-ink-600 bg-ink-900" : "border-ink-800 bg-ink-900/50"}`}
      >
        <div className="flex items-center gap-2">
          <button onClick={onSelect} className="font-display font-bold text-ink-50 hover:text-signal-300">Version {version.version}</button>
          <StatusDot status={isSelected && snap?.status === "awaiting_approval" ? snap.status : version.status} />
          {isCurrent && <span className="rounded-full bg-ink-700 px-2 py-0.5 text-xs text-ink-200">current</span>}
          {!isCurrent && version.status === "succeeded" && (
            <button onClick={onRestore} className="ml-auto text-xs text-ink-400 hover:text-signal-300">Make current</button>
          )}
        </div>
        {version.status !== "running" && (
          <p className="mt-1 text-sm text-ink-300">
            {version.status === "succeeded" ? "Built and tested" : "Stopped before it worked"}
            {seconds != null && ` in ${Math.round(seconds)}s`}
            {(version.bugs_found ?? 0) > 0 && `. The agents found ${version.bugs_found} bug${version.bugs_found === 1 ? "" : "s"} and fixed ${version.bugs_fixed}`}
            .
          </p>
        )}
        {snap && version.status !== "running" && version.status !== "succeeded" && (
          <FailureSummary snap={snap} />
        )}
        {changes && (changes.added.length + changes.changed.length + changes.removed.length > 0) && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {changes.added.map((n) => <span key={n} className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">+ {n}</span>)}
            {changes.changed.map((n) => <span key={n} className="rounded bg-signal-400/15 px-1.5 py-0.5 text-signal-300">~ {n}</span>)}
            {changes.removed.map((n) => <span key={n} className="rounded bg-rose-500/15 px-1.5 py-0.5 text-rose-300">− {n}</span>)}
          </div>
        )}
        {steps && (
          <div className="mt-3">
            <AgentSteps steps={steps} compact={version.status !== "running"} />
          </div>
        )}
        {!steps && (
          <button onClick={onSelect} className="mt-1 text-xs text-ink-400 hover:text-ink-100">Show what the agents did</button>
        )}
      </div>
    </div>
  );
}

/** Why a version stopped, in plain words: the open problems first, the log behind "Details". */
function FailureSummary({ snap }: { snap: Snapshot }) {
  const open = (snap.bugs ?? []).filter((b) => !b.fixed).slice(0, 3);
  return (
    <div className="mt-2 rounded-lg border border-rose-400/30 bg-rose-500/5 px-3 py-2 text-sm">
      {snap.stop_reason && <p className="text-rose-200">Stopped: {snap.stop_reason}</p>}
      {open.length > 0 && (
        <ul className="mt-1 space-y-1 text-ink-200">
          {open.map((b) => (
            <li key={b.id} className="truncate" title={b.detail}>
              <span className="text-ink-400">{b.found_by}:</span> <span className="font-mono text-xs">{b.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {snap.validation?.log && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-ink-400 hover:text-ink-100">Details</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-ink-400">{snap.validation.log.slice(-2000)}</pre>
        </details>
      )}
      <p className="mt-1 text-xs text-ink-400">Press Keep fixing to continue on this code, or describe a change.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------- the live app

function PreviewPane({ runId, snap }: { runId: string | null; snap: Snapshot | null }) {
  const [base, setBase] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mobile, setMobile] = useState(false);
  const [reload, setReload] = useState(0);
  const ready = snap?.status === "succeeded" && snap.run_id === runId;
  // While the agents work (or after a failed build), the verified template build runs as a draft.
  const draft = !ready && !!snap?.has_draft && snap.run_id === runId;

  useEffect(() => {
    if ((!ready && !draft) || !runId) return;
    let cancelled = false;
    (async () => {
      setError(null);
      try {
        const status = await api.previewStatus(runId, draft);
        const info = status.running ? status : await api.startPreview(runId, draft);
        if (!cancelled) setBase(info.base);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
      setBase(null);
    };
  }, [ready, draft, runId, reload]);

  if (!snap || (snap.status === "running" && !draft))
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div>
          <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-ink-700 border-t-signal-400" />
          <p className="font-display text-lg text-ink-100">Your app is being designed</p>
          <p className="mt-1 text-sm text-ink-400">A first draft opens here in about a minute.</p>
        </div>
      </div>
    );
  if (!ready && !draft)
    return <div className="p-8"><Empty text={`This version did not pass validation${snap.stop_reason ? ` (${snap.stop_reason})` : ""}. See Quality for what the agents tried, or press Keep fixing.`} /></div>;

  const url = base ? `${API}${base}/` : null;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2">
        <button onClick={() => setReload((n) => n + 1)} className="rounded-md px-2 py-1 text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50" title="Restart the app">
          Restart
        </button>
        <div className="min-w-0 flex-1 truncate rounded-md bg-ink-950 px-3 py-1.5 font-mono text-xs text-ink-300">
          {url ? (typeof window !== "undefined" ? new URL(url, window.location.href).href : url) : "Starting the app…"}
        </div>
        <div role="radiogroup" aria-label="Screen size" className="flex rounded-md bg-ink-950 p-0.5 text-xs">
          {[["Desktop", false], ["Phone", true]].map(([label, value]) => (
            <button key={label as string} role="radio" aria-checked={mobile === value} onClick={() => setMobile(value as boolean)}
              className={`rounded px-2 py-1 ${mobile === value ? "bg-ink-700 text-ink-50" : "text-ink-400"}`}>{label as string}</button>
          ))}
        </div>
        {url && (
          <a href={url} target="_blank" rel="noreferrer" className="rounded-md bg-signal-400 px-3 py-1.5 text-xs font-semibold text-ink-950 hover:bg-signal-300">
            Open in new tab
          </a>
        )}
      </div>
      {draft && (
        <p className="border-b border-ink-800 bg-signal-400/10 px-4 py-2 text-sm text-signal-200">
          {snap.status === "running"
            ? "Draft: every screen already works. The agents are still writing and testing the business rules (custom actions)."
            : `Draft of a version that did not pass validation${snap.stop_reason ? ` (${snap.stop_reason})` : ""}: screens work, custom actions do not yet.`}
        </p>
      )}
      {error && <pre className="m-4 whitespace-pre-wrap rounded-lg bg-rose-500/10 p-3 font-mono text-xs text-rose-300">{error}</pre>}
      <div className="flex min-h-0 flex-1 justify-center bg-ink-950 p-3">
        {url && (
          <iframe
            key={url}
            src={url}
            title="Your generated app"
            className={`h-full rounded-lg border border-ink-800 bg-white ${mobile ? "w-[390px]" : "w-full"}`}
          />
        )}
      </div>
    </div>
  );
}
