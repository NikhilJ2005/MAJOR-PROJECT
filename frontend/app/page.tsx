"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Architecture } from "@/components/Architecture";
import { Files } from "@/components/Files";
import { ActivityFeed, Empty, SelfHealing } from "@/components/Ledger";
import { LiveApp } from "@/components/LiveApp";
import { Pipeline, type StageInfo } from "@/components/Pipeline";
import { Tests } from "@/components/Tests";
import { api, type Fault, type LedgerEntry, type RunEvent, type ServerConfig, type Snapshot } from "@/lib/api";

const EXAMPLES: [string, string][] = [
  ["Blog + auth", "A blog API with posts and comments. Users sign up and log in; only logged-in users can write. Posts are searchable by title."],
  ["E-commerce", "An e-commerce backend with categories, products (price, stock, sku), customers, orders with a status, order items with quantity, and product reviews with a rating. Customers log in to place orders."],
  ["Project tracker", "A Jira-like project tracker: projects, sprints with start and end dates, issues with title, description, status, priority and story points, comments on issues, and labels. Team members log in."],
  ["Hospital", "Hospital management with departments, doctors (specialty) in departments, patients, appointments between doctors and patients with a time and status, and prescriptions for appointments."],
  ["Library", "Library system tracking books (isbn, title, copies), members, and loans with due dates and a returned flag."],
  ["Events", "Event ticketing with venues (city, capacity), events at venues with a start time, ticket types with price, and bookings with quantity."],
];

const FAULTS: { value: Fault; label: string; hint: string }[] = [
  { value: "import", label: "Broken import", hint: "caught at gate 1: import" },
  { value: "status", label: "Wrong HTTP status", hint: "caught at gate 3: API tests" },
  { value: "none", label: "No bug", hint: "clean run" },
];

const TABS = ["live app", "architecture", "code", "self-healing", "tests"] as const;
type Tab = (typeof TABS)[number];

const NEXT: Record<string, string> = { parse_spec: "plan", plan: "generate", generate: "validate" };

const NOW: Record<string, string> = {
  parse_spec: "The Architect is turning your description into a data model and API design…",
  plan: "The Planner is ordering the files by dependency…",
  generate: "The Code Generator is writing the models, schemas and API routers…",
  validate: "The Sandbox Validator is importing, booting and API-testing the generated app…",
  classify: "The Error Classifier is diagnosing what failed and which file caused it…",
  reflect: "The Reflector is patching the faulty file…",
  package: "The Packager is bundling the project…",
};

function deriveStages(events: RunEvent[]) {
  const stages: Record<string, StageInfo> = {};
  const set = (id: string, patch: Partial<StageInfo>) => (stages[id] = { ...(stages[id] ?? { state: "idle" }), ...patch });
  let iteration = 0;
  let prevTs: number | null = null;
  for (const ev of events) {
    if (ev.type === "status" && ev.status === "running" && prevTs === null) {
      prevTs = ev.ts;
      set("parse_spec", { state: "active" });
    }
    if (ev.type === "error") for (const k of Object.keys(stages)) if (stages[k].state === "active") set(k, { state: "failed" });
    if (ev.type !== "node" || !ev.node) continue;
    const node = ev.node;
    const seconds = prevTs !== null ? ev.ts - prevTs : undefined;
    prevTs = ev.ts;
    const model = (ev.ledger ?? []).map((e) => e.model).find(Boolean);
    if (typeof ev.iteration === "number") iteration = ev.iteration;
    if (node === "validate") {
      const ok = ev.validation?.ok;
      set("validate", { state: ok ? "done" : "failed", seconds, detail: ok ? "all gates passed" : `failed at gate: ${ev.validation?.gate}` });
      if (ok) set("package", { state: "active" });
      else {
        set("classify", { state: "active", detail: undefined });
        set("reflect", { state: "idle" });
      }
      continue;
    }
    if (node === "classify") {
      const v = ev.validation;
      set("classify", { state: "done", seconds, detail: `${ev.error_class ?? ""} · ${v?.failing_file ?? "?"}` });
      set("reflect", { state: "active", detail: undefined });
      continue;
    }
    if (node === "reflect") {
      const patched = (ev.ledger ?? [])[0]?.files?.join(", ");
      set("reflect", { state: "done", seconds, model, detail: patched ? `patched ${patched}` : undefined });
      set("validate", { state: "active", detail: "re-validating…" });
      continue;
    }
    if (node === "failure_report") {
      set("failure_report", { state: "failed", seconds });
      if (stages.classify?.state === "active") set("classify", { state: "idle" });
      continue;
    }
    set(node, { state: "done", seconds, model: model ?? stages[node]?.model });
    if (node === "parse_spec") set("parse_spec", { detail: undefined });
    if (NEXT[node]) set(NEXT[node], { state: "active" });
  }
  return { stages, iteration };
}

export default function Home() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [prompt, setPrompt] = useState(EXAMPLES[0][1]);
  const [fault, setFault] = useState<Fault>("import");
  const [mode, setMode] = useState<"llm" | "template">("llm");
  const [accessCode, setAccessCode] = useState(() => {
    try {
      return typeof window === "undefined" ? "" : (localStorage.getItem("vibestack-access") ?? "");
    } catch {
      return "";
    }
  });
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

  useEffect(() => {
    api.config().then(setConfig, () => setError("Backend not reachable. Is the API running?"));
    const id = new URLSearchParams(window.location.search).get("run");
    if (id) attach(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(async (id: string) => {
    try {
      setSnap(await api.snapshot(id));
    } catch {}
  }, []);

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
        if (ev.type === "node" && ev.node === "classify") setTab("self-healing");
        if (ev.type === "status" && ev.status && ev.status !== "running") {
          es.close();
          setEndedAt(Date.now());
          refresh(id);
          if (ev.status === "succeeded") setTab("live app");
          if (ev.status === "failed") setTab("tests");
        }
      };
      for (const t of ["node", "status", "error"]) es.addEventListener(t, onEvent as EventListener);
      es.onerror = () => {
        // The server closes the stream when the run finishes; EventSource would retry forever.
        es.close();
        refresh(id);
      };
    },
    [refresh],
  );

  async function attach(id: string) {
    setRunId(id);
    setEvents([]);
    lastSeq.current = -1;
    await refresh(id);
    connect(id);
  }

  async function start() {
    setError(null);
    try {
      localStorage.setItem("vibestack-access", accessCode);
    } catch {}
    try {
      const { run_id } = await api.start({ prompt, fault, codegen_mode: mode }, accessCode);
      window.history.replaceState(null, "", `?run=${run_id}`);
      setSnap(null);
      setTab("architecture");
      setStartedAt(Date.now());
      setEndedAt(null);
      await attach(run_id);
    } catch (e) {
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

  const { stages, iteration } = deriveStages(events);
  const errorEvent = events.find((e) => e.type === "error");
  const elapsed = startedAt ? ((endedAt ?? now) - startedAt) / 1000 : null;
  const activity: (LedgerEntry & { ts?: number })[] = events.flatMap((ev) => (ev.ledger ?? []).map((l) => ({ ...l, ts: ev.ts })));
  const activeIds = Object.entries(stages).filter(([, s]) => s.state === "active").map(([k]) => k);
  const nowText = running
    ? (activeIds.map((k) => NOW[k]).find(Boolean) ?? "Agents are starting…")
    : status === "succeeded"
      ? `✓ Built, tested${(snap?.iteration ?? 0) > 0 ? ` and self-healed (${snap?.iteration} fix)` : ""} in ${elapsed != null ? elapsed.toFixed(1) + "s" : "-"}. The app is running live below.`
      : status === "failed"
        ? "✗ The circuit breaker stopped the run after 3 repair attempts. See the Tests tab for the diagnosis."
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
            <p className="text-sm text-zinc-400">prompt → architecture → working, tested, self-healed app, running live</p>
          </div>
        </div>
        {config && (
          <div className="ml-auto flex flex-wrap gap-1.5 font-mono text-xs">
            <Badge label="orchestrator" value="LangGraph" />
            <Badge label="builder" value={config.models.strong} />
            <Badge label="healer" value={config.models.cheap} />
            <Badge label="sandbox" value={config.sandbox} />
            {config.fake_llm && <Badge label="llm" value="FAKE (offline demo)" warn />}
            {!config.llm_enabled && <Badge label="llm" value="not configured" warn />}
          </div>
        )}
      </header>

      <section className="mb-5 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div>
            <label className="mb-2 block text-xs font-semibold uppercase tracking-wider text-zinc-400">Describe your application</label>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              className="w-full resize-none rounded-xl border border-zinc-700 bg-zinc-950 p-3 text-base text-zinc-100 outline-none focus:border-sky-500"
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
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-zinc-400">Demo: plant a bug</label>
              <select value={fault} onChange={(e) => setFault(e.target.value as Fault)} className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm">
                {FAULTS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label} · {f.hint}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-zinc-500">Codegen</span>
              {(["llm", "template"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)} className={`rounded px-2 py-0.5 font-mono text-xs ${mode === m ? "bg-sky-500/20 text-sky-200" : "text-zinc-500 hover:text-zinc-300"}`}>
                  {m}
                </button>
              ))}
              {config?.access_required && (
                <input
                  type="password"
                  placeholder="Access code"
                  value={accessCode}
                  onChange={(e) => setAccessCode(e.target.value)}
                  className="ml-auto w-32 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm outline-none focus:border-sky-500"
                />
              )}
            </div>
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
          <div className="ml-auto flex flex-wrap gap-2">
            <Stat label="heal iterations" value={snap?.iteration ?? 0} accent={(snap?.iteration ?? 0) > 0} />
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
        <Pipeline stages={stages} iteration={iteration} maxIterations={config?.max_heal_iterations ?? 3} />
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
                {t === "live app" ? "▶ Live app" : t.replace("-", " ")}
                {t === "code" && snap?.files?.length ? <span className="ml-1 text-xs text-zinc-500">{snap.files.length}</span> : null}
                {t === "self-healing" && (snap?.iteration ?? 0) > 0 ? <span className="ml-1 rounded-full bg-amber-400/20 px-1.5 text-xs text-amber-300">{snap?.iteration}</span> : null}
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
                <LiveApp key={runId} runId={runId} spec={snap.spec} accessCode={accessCode} ready={status === "succeeded"} />
              ) : (
                <Empty text="After the app is built and verified, it is started here so you can use it live." />
              ))}
            {tab === "architecture" &&
              (snap?.spec ? (
                <Architecture spec={snap.spec} plan={snap.file_plan} />
              ) : (
                <Empty text={running ? "The Architect is designing the data model and API…" : "Describe an application and press Generate."} />
              ))}
            {tab === "code" && runId && <Files runId={runId} files={snap?.files ?? []} version={snap?.ledger?.length ?? 0} />}
            {tab === "code" && !runId && <Empty text="Generated source files appear here." />}
            {tab === "self-healing" && <SelfHealing entries={snap?.ledger ?? []} done={!running && !!snap?.validation} />}
            {tab === "tests" && <Tests validation={snap?.validation} report={snap?.report} />}
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

