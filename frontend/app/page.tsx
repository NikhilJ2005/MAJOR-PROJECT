"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Architecture } from "@/components/Architecture";
import { Files } from "@/components/Files";
import { ActivityFeed, BugsAndHealing, Empty } from "@/components/Ledger";
import { LiveApp } from "@/components/LiveApp";
import { Login } from "@/components/Login";
import { Pipeline, type StageInfo } from "@/components/Pipeline";
import { Tests } from "@/components/Tests";
import {
  api,
  ApiError,
  getToken,
  setToken,
  type LedgerEntry,
  type RunEvent,
  type RunSummary,
  type ServerConfig,
  type Snapshot,
  type Stats,
} from "@/lib/api";

const EXAMPLES: [string, string][] = [
  ["Shop checkout (rules)", "An online shop. Admins manage products with a price and stock. Customers sign up and place orders made of order items. Checking out an order reduces stock for every item, fails with 409 if any product has too little stock, and marks the order paid. Customers only ever see their own orders. An order that is already paid cannot be checked out again."],
  ["Clinic booking (rules)", "Hospital appointment booking. Admins manage departments and doctors. Patients log in and book appointments with a doctor at a start time; a doctor cannot have two appointments at the same time (409). Patients see and cancel only their own appointments. Doctors have a daily appointment report."],
  ["Library loans (rules)", "Library loans. Librarians (admins) manage books with a number of copies. Members log in and borrow books: a member may have at most 3 open loans (409 otherwise) and a book cannot be borrowed when no copy is left. Returning a loan frees the copy. Members see only their own loans."],
  ["Blog + auth", "A blog API with posts and comments. Users sign up and log in; only logged-in users can write. Posts are searchable by title."],
  ["Project tracker", "A Jira-like project tracker: projects, sprints with start and end dates, issues with title, description, status, priority and story points, comments on issues, and labels. Team members log in."],
  ["Events", "Event ticketing with venues (city, capacity), events at venues with a start time, ticket types with price, and bookings with quantity."],
];

const STACKS = [
  { name: "fastapi", label: "FastAPI" },
  { name: "flask", label: "Flask" },
  { name: "django", label: "Django" },
];

const TABS = ["live app", "architecture", "code", "bugs & healing", "tests"] as const;
type Tab = (typeof TABS)[number];

const NEXT: Record<string, string> = { analyze: "parse_spec", parse_spec: "plan", plan: "write_tests", write_tests: "generate", generate: "validate" };

const NOW: Record<string, string> = {
  analyze: "The Analyst is splitting your description into testable requirements…",
  parse_spec: "The Architect is designing the data model, access rules and custom actions…",
  plan: "The Planner is ordering the files by dependency…",
  write_tests: "The Test Engineer is writing business-rule tests, before any code exists…",
  generate: "The API Engineer is writing models, schemas, services and routers…",
  validate: "The Validator is checking, importing, booting and testing the generated app…",
  bug_hunt: "The Bug Hunter is writing tests designed to break the app…",
  classify: "The Error Classifier is diagnosing what failed and which file caused it…",
  reflect: "The Reflector is patching the faulty file…",
  package: "The Packager is creating migrations and bundling the project…",
};

function deriveStages(events: RunEvent[], huntEnabled: boolean) {
  const stages: Record<string, StageInfo> = {};
  const set = (id: string, patch: Partial<StageInfo>) => (stages[id] = { ...(stages[id] ?? { state: "idle" }), ...patch });
  let iteration = 0;
  let prevTs: number | null = null;
  for (const ev of events) {
    if (ev.type === "status" && ev.status === "running" && prevTs === null) {
      prevTs = ev.ts;
      set("analyze", { state: "active" });
    }
    if (ev.type === "error") for (const k of Object.keys(stages)) if (stages[k].state === "active") set(k, { state: "failed" });
    if (ev.type !== "node" || !ev.node) continue;
    const node = ev.node;
    const seconds = prevTs !== null ? ev.ts - prevTs : undefined;
    prevTs = ev.ts;
    const first = (ev.ledger ?? [])[0];
    const model = (ev.ledger ?? []).map((e) => e.model).find(Boolean);
    if (typeof ev.iteration === "number") iteration = ev.iteration;
    if (node === "validate") {
      const ok = ev.validation?.ok;
      set("validate", { state: ok ? "done" : "failed", seconds, detail: ok ? "all gates passed" : `failed at gate: ${ev.validation?.gate}` });
      const hunter = (ev.ledger ?? []).find((e) => e.agent === "bug_hunter");
      if (hunter) set("bug_hunt", { detail: hunter.action });
      if (ok) {
        const hunted = stages.bug_hunt && stages.bug_hunt.state !== "idle";
        set(huntEnabled && !hunted ? "bug_hunt" : "package", { state: "active" });
      } else {
        set("classify", { state: "active", detail: undefined });
        set("reflect", { state: "idle" });
      }
      continue;
    }
    if (node === "bug_hunt") {
      set("bug_hunt", { state: "done", seconds, model, detail: first?.action });
      set("validate", { state: "active", detail: "running the Bug Hunter's tests…" });
      continue;
    }
    if (node === "hunt_giveup") {
      set("bug_hunt", { state: "done", detail: first?.action });
      set("validate", { state: "active", detail: "re-validating the last passing code…" });
      continue;
    }
    if (node === "classify") {
      const v = ev.validation;
      set("classify", { state: "done", seconds, detail: `${ev.error_class ?? ""} · ${v?.failing_file ?? "?"}` });
      set("reflect", { state: "active", detail: undefined });
      continue;
    }
    if (node === "reflect") {
      const patched = first?.files?.join(", ");
      set("reflect", { state: "done", seconds, model, detail: patched ? `patched ${patched}` : first?.rationale });
      set("validate", { state: "active", detail: "re-validating…" });
      continue;
    }
    if (node === "failure_report") {
      set("failure_report", { state: "failed", seconds, detail: ev.stop_reason ?? undefined });
      if (stages.classify?.state === "active") set("classify", { state: "idle" });
      continue;
    }
    set(node, { state: "done", seconds, model: model ?? stages[node]?.model, detail: first?.action });
    if (NEXT[node]) set(NEXT[node], { state: "active" });
  }
  return { stages, iteration };
}

export default function Home() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [user, setUser] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [prompt, setPrompt] = useState(EXAMPLES[0][1]);
  const [stack, setStack] = useState("fastapi");
  const [mode, setMode] = useState<"llm" | "template">("llm");
  const [bugHunt, setBugHunt] = useState(true);
  const [plugins, setPlugins] = useState<string[] | null>(null);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<Tab>("architecture");
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [endedAt, setEndedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const lastSeq = useRef(-1);
  const source = useRef<EventSource | null>(null);

  const refreshAccount = useCallback(async () => {
    try {
      const [runs, s] = await Promise.all([api.runs(), api.stats()]);
      setHistory(runs);
      setStats(s.mine);
    } catch {}
  }, []);

  const logout = useCallback(() => {
    setToken("");
    source.current?.close();
    setUser(null);
    setRunId(null);
    setSnap(null);
    setEvents([]);
  }, []);

  const refresh = useCallback(
    async (id: string) => {
      try {
        setSnap(await api.snapshot(id));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) logout();
      }
    },
    [logout],
  );

  const connect = useCallback(
    (id: string) => {
      source.current?.close();
      const es = new EventSource(api.eventsUrl(id, lastSeq.current));
      source.current = es;
      const onEvent = (msg: MessageEvent) => {
        const ev = JSON.parse(msg.data) as RunEvent;
        if (ev.seq <= lastSeq.current) return;
        lastSeq.current = ev.seq;
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "node" || ev.type === "error") refresh(id);
        if (ev.type === "node" && ev.node === "classify") setTab("bugs & healing");
        if (ev.type === "status" && ev.status && ev.status !== "running") {
          es.close();
          setEndedAt(Date.now());
          refresh(id);
          refreshAccount();
          if (ev.status === "succeeded") setTab("live app");
          if (ev.status === "failed") setTab("bugs & healing");
        }
      };
      for (const t of ["node", "status", "error"]) es.addEventListener(t, onEvent as EventListener);
      es.onerror = () => {
        // The server closes the stream when the run finishes; EventSource would retry forever.
        es.close();
        refresh(id);
      };
    },
    [refresh, refreshAccount],
  );

  const attach = useCallback(
    async (id: string) => {
      setRunId(id);
      setEvents([]);
      setStartedAt(null);
      setEndedAt(null);
      lastSeq.current = -1;
      window.history.replaceState(null, "", `?run=${id}`);
      await refresh(id);
      connect(id);
    },
    [refresh, connect],
  );

  useEffect(() => {
    api.config().then(setConfig, () => setError("Backend not reachable. Is the API running?"));
    (async () => {
      if (getToken()) {
        try {
          setUser((await api.me()).email);
        } catch {
          setToken(""); // expired or invalid: show the login screen
        }
      }
      setChecked(true);
    })();
  }, []);

  useEffect(() => {
    if (!user) return;
    (async () => {
      await refreshAccount();
      const id = new URLSearchParams(window.location.search).get("run");
      if (id) await attach(id);
    })();
  }, [user, refreshAccount, attach]);

  const chosenPlugins = plugins ?? (config?.plugins ?? []).filter((p) => p.default).map((p) => p.name);

  async function start() {
    setError(null);
    try {
      const { run_id } = await api.start({ prompt, stack, codegen_mode: mode, bug_hunt: bugHunt, plugins: chosenPlugins });
      setSnap(null);
      setTab("architecture");
      await attach(run_id);
      setStartedAt(Date.now());
      refreshAccount();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) logout();
      setError((e as Error).message);
    }
  }

  const status = snap?.status ?? (runId ? "running" : "idle");
  const running = status === "running";

  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [running]);

  if (!checked) return null;
  if (!user) return <Login inviteRequired={!!config?.invite_required} onDone={setUser} />;

  const { stages, iteration } = deriveStages(events, snap?.options?.bug_hunt ?? bugHunt);
  const bugs = snap?.bugs ?? [];
  const errorEvent = events.find((e) => e.type === "error");
  const elapsed = startedAt ? ((endedAt ?? now) - startedAt) / 1000 : null;
  const activity: (LedgerEntry & { ts?: number })[] = events.flatMap((ev) => (ev.ledger ?? []).map((l) => ({ ...l, ts: ev.ts })));
  const activeIds = Object.entries(stages).filter(([, s]) => s.state === "active").map(([k]) => k);
  const fixed = bugs.filter((b) => b.fixed).length;
  const nowText = running
    ? (activeIds.map((k) => NOW[k]).find(Boolean) ?? "Agents are starting…")
    : status === "succeeded"
      ? `✓ Built, tested and bug-hunted${bugs.length ? `: ${bugs.length} bug${bugs.length === 1 ? "" : "s"} found by the agents, ${fixed} fixed` : ""}${elapsed != null ? ` in ${elapsed.toFixed(1)}s` : ""}. The app is running live below.`
      : status === "failed"
        ? `✗ Self-healing stopped: ${snap?.stop_reason ?? "no more progress"}. See Bugs & healing for the diagnosis.`
        : status === "error"
          ? "✗ The run crashed. See the error below."
          : "Describe an application and press Generate. Watch the agents work in real time.";

  return (
    <main className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-sky-400 to-violet-500 font-mono text-base font-bold text-zinc-950">
            VS
          </div>
          <div>
            <h1 className="text-2xl font-bold leading-tight">VibeStack</h1>
            <p className="text-sm text-zinc-400">prompt → requirements → FastAPI / Flask / Django app, tested, bug-hunted and self-healed</p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1.5 font-mono text-xs">
          {config && <Badge label="builder" value={config.models.strong} />}
          {config && <Badge label="healer" value={config.models.cheap} />}
          {config?.fake_llm && <Badge label="llm" value="FAKE (offline demo)" warn />}
          {config && !config.llm_enabled && <Badge label="llm" value="not configured" warn />}
          {stats && <Badge label="my runs" value={`${stats.runs} · ${Math.round(stats.success_rate * 100)}% ok · ${stats.bugs_fixed} bugs fixed`} />}
          {history.length > 0 && (
            <select
              value={runId ?? ""}
              onChange={(e) => e.target.value && attach(e.target.value)}
              className="max-w-56 rounded-md border border-zinc-800 bg-zinc-950 px-2 py-1 text-zinc-300"
            >
              <option value="">history…</option>
              {history.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.status === "succeeded" ? "✓" : r.status === "running" ? "…" : "✗"} {r.stack} · {r.prompt.slice(0, 40)}
                </option>
              ))}
            </select>
          )}
          <span className="rounded-md border border-zinc-800 px-2 py-1 text-zinc-300">{user}</span>
          <button onClick={logout} className="rounded-md border border-zinc-800 px-2 py-1 text-zinc-400 hover:text-zinc-200">log out</button>
        </div>
      </header>

      <section className="mb-5 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-zinc-400">Describe your application</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              className="w-full resize-y rounded-xl border border-zinc-700 bg-zinc-950 p-3 text-base text-zinc-100 outline-none focus:border-sky-500"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {EXAMPLES.map(([name, text]) => (
                <button
                  key={name}
                  onClick={() => setPrompt(text)}
                  className={`rounded-full border px-3 py-1 text-xs ${prompt === text ? "border-sky-500 text-sky-200" : "border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200"}`}
                >
                  {name}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-zinc-400">Framework</label>
              <div className="grid grid-cols-3 gap-1 rounded-lg bg-zinc-950 p-1">
                {(config?.stacks ?? STACKS).map((s) => (
                  <button
                    key={s.name}
                    onClick={() => setStack(s.name)}
                    className={`rounded-md py-1.5 text-sm ${stack === s.name ? "bg-sky-500/20 font-semibold text-sky-200" : "text-zinc-400 hover:text-zinc-200"}`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
              <label className="flex items-center gap-2 text-zinc-300">
                <input type="checkbox" checked={bugHunt} onChange={(e) => setBugHunt(e.target.checked)} className="accent-violet-400" />
                Bug Hunter
              </label>
              <span className="flex items-center gap-1">
                <span className="text-zinc-500">code</span>
                {(["llm", "template"] as const).map((m) => (
                  <button key={m} onClick={() => setMode(m)} className={`rounded px-2 py-0.5 font-mono text-xs ${mode === m ? "bg-sky-500/20 text-sky-200" : "text-zinc-500 hover:text-zinc-300"}`}>
                    {m}
                  </button>
                ))}
              </span>
            </div>
            {(config?.plugins ?? []).length > 0 && (
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-zinc-400">Plugins</label>
                <div className="flex flex-wrap gap-1.5">
                  {config!.plugins.map((p) => {
                    const on = chosenPlugins.includes(p.name);
                    return (
                      <button
                        key={p.name}
                        title={p.description}
                        onClick={() => setPlugins(on ? chosenPlugins.filter((n) => n !== p.name) : [...chosenPlugins, p.name])}
                        className={`rounded-full border px-2.5 py-0.5 text-xs ${on ? "border-lime-400/60 bg-lime-400/10 text-lime-200" : "border-zinc-700 text-zinc-500 hover:text-zinc-300"}`}
                      >
                        {on ? "✓ " : ""}{p.name}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            <button
              onClick={start}
              disabled={running || prompt.trim().length < 10}
              className="mt-auto w-full rounded-xl bg-gradient-to-r from-sky-500 to-violet-500 py-3 text-base font-bold text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? "Agents working…" : "Generate application"}
            </button>
            {error && <p className="text-sm text-rose-400">{error}</p>}
          </div>
        </div>
      </section>

      <section className="mb-5 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">LangGraph agent pipeline</h2>
          <StatusPill status={status} />
          {snap?.spec?.stack && <span className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-zinc-300">{snap.spec.stack}</span>}
          <div className="ml-auto flex flex-wrap gap-2">
            <Stat label="requirements" value={snap?.requirements?.length ?? 0} />
            <Stat label="bugs found / fixed" value={`${bugs.length} / ${fixed}`} accent={bugs.length > 0} />
            <Stat label="repairs" value={snap?.iteration ?? 0} accent={(snap?.iteration ?? 0) > 0} />
            <Stat label="tokens" value={(snap?.usage?.total_tokens ?? 0).toLocaleString()} />
            <Stat label="cost" value={`$${(snap?.usage?.cost_usd ?? 0).toFixed(4)}`} />
            <Stat label="elapsed" value={elapsed != null ? `${elapsed.toFixed(1)}s` : "-"} />
          </div>
        </div>
        <div
          className={`mb-4 flex items-center gap-3 rounded-xl border px-4 py-3 text-lg font-medium ${
            running
              ? "border-sky-500/40 bg-sky-500/10 text-sky-100"
              : status === "succeeded"
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-100"
                : status === "failed" || status === "error"
                  ? "border-rose-500/40 bg-rose-500/10 text-rose-100"
                  : "border-zinc-800 text-zinc-400"
          }`}
        >
          {running && <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-sky-300 border-t-transparent" />}
          <span>{nowText}</span>
        </div>
        <Pipeline stages={stages} iteration={iteration} bugsFound={bugs.length} stopReason={snap?.stop_reason} />
      </section>

      {errorEvent && <div className="mb-5 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 font-mono text-sm text-rose-300">{errorEvent.message}</div>}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="min-w-0 rounded-2xl border border-zinc-800 bg-zinc-900/40">
          <div className="flex items-center gap-1 overflow-x-auto border-b border-zinc-800 px-2">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`-mb-px shrink-0 border-b-2 px-4 py-3 text-base capitalize ${tab === t ? "border-sky-400 font-semibold text-white" : "border-transparent text-zinc-400 hover:text-zinc-200"}`}
              >
                {t === "live app" ? "▶ Live app" : t}
                {t === "code" && snap?.files?.length ? <span className="ml-1 text-xs text-zinc-500">{snap.files.length}</span> : null}
                {t === "bugs & healing" && bugs.length > 0 ? <span className="ml-1 rounded-full bg-amber-400/20 px-1.5 text-xs text-amber-300">{bugs.length}</span> : null}
              </button>
            ))}
            {snap?.has_artifact && runId && (
              <a href={api.downloadUrl(runId)} className="my-1.5 ml-auto shrink-0 rounded-lg border border-emerald-500/50 px-3 py-1.5 text-sm font-semibold text-emerald-300 hover:bg-emerald-500/10">
                Download .zip
              </a>
            )}
          </div>
          <div className="p-5">
            {tab === "live app" &&
              (runId && snap?.spec ? (
                <LiveApp key={runId} runId={runId} spec={snap.spec} ready={status === "succeeded"} />
              ) : (
                <Empty text="After the app is built and verified, it is started here so you can use it live." />
              ))}
            {tab === "architecture" &&
              (snap?.spec ? (
                <Architecture spec={snap.spec} plan={snap.file_plan} />
              ) : (
                <Empty text={running ? "The Analyst and Architect are designing the application…" : "Describe an application and press Generate."} />
              ))}
            {tab === "code" && runId && <Files runId={runId} files={snap?.files ?? []} version={snap?.ledger?.length ?? 0} />}
            {tab === "code" && !runId && <Empty text="Generated source files appear here." />}
            {tab === "bugs & healing" && (
              <BugsAndHealing entries={snap?.ledger ?? []} bugs={bugs} notes={snap?.review ?? []} done={!running && !!snap?.validation} stopReason={snap?.stop_reason} />
            )}
            {tab === "tests" && <Tests validation={snap?.validation} report={snap?.report} files={snap?.files ?? []} />}
          </div>
        </section>
        <aside className="xl:sticky xl:top-4 xl:h-[640px]">
          <ActivityFeed entries={activity} running={running} />
        </aside>
      </div>
    </main>
  );
}

function Badge({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <span className={`rounded-md border px-2 py-1 ${warn ? "border-amber-500/40 text-amber-300" : "border-zinc-800 text-zinc-300"}`}>
      <span className="text-zinc-500">{label}:</span> {value}
    </span>
  );
}

function Stat({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className={`rounded-lg border px-3 py-1.5 ${accent ? "border-amber-400/40" : "border-zinc-800"}`}>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className={`font-mono text-lg leading-tight ${accent ? "text-amber-300" : "text-zinc-100"}`}>{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    idle: "bg-zinc-800 text-zinc-400",
    running: "bg-sky-500/15 text-sky-300",
    succeeded: "bg-emerald-500/15 text-emerald-300",
    failed: "bg-rose-500/15 text-rose-300",
    error: "bg-rose-500/15 text-rose-300",
  };
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${map[status] ?? map.running}`}>{status}</span>;
}
