"use client";

import { useEffect, useState } from "react";
import { PromptBuilder } from "@/components/PromptBuilder";
import { api, type Blueprint, type Project, type ServerConfig } from "@/lib/api";

const EXAMPLES: [string, string][] = [
  ["Shop with checkout", "An online shop. Admins manage products with a price and stock. Customers sign up and place orders made of order items. Checking out an order reduces stock for every item, fails with 409 if any product has too little stock, and marks the order paid. Customers only ever see their own orders."],
  ["Agent orchestrator", "A multi-agent orchestration backend. Users register agents with a role (planner, worker, critic) and a status that moves idle → busy → idle or offline. Task graphs store a JSON workflow definition and move draft → running → done or failed. Execution logs belong to a task graph (deleted with it), may point at an agent, and stream live to a dashboard."],
  ["Clinic booking", "Hospital appointment booking. Admins manage departments and doctors. Patients log in and book appointments with a doctor at a start time; a doctor cannot have two appointments at the same time. Patients see and cancel only their own appointments."],
  ["Library loans", "Library loans. Librarians manage books with a number of copies. Members log in and borrow books: at most 3 open loans per member, and a book cannot be borrowed when no copy is left. Returning a loan frees the copy."],
];

const STACK_NOTE: Record<string, string> = {
  fastapi: "Async-ready Python API",
  flask: "Lightweight and familiar",
  django: "Batteries-included ORM",
};

function ago(ts: number) {
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function StatusDot({ status }: { status?: string | null }) {
  const waiting = status === "awaiting_approval";
  const cls = status === "succeeded" ? "bg-emerald-400" : status === "running" || waiting ? "animate-pulse bg-signal-400" : status ? "bg-rose-400" : "bg-ink-600";
  const text = status === "succeeded" ? "Working" : status === "running" ? "Building" : waiting ? "Waiting for your approval" : status ? "Needs attention" : "Not built";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-300">
      <span className={`h-2 w-2 rounded-full ${cls}`} />
      {text}
    </span>
  );
}

export function Home({
  config,
  projects,
  onCreate,
  onBlueprint,
  onOpen,
}: {
  config: ServerConfig | null;
  projects: Project[];
  onCreate: (prompt: string, stack: string, plugins: string[], pluginsOff: string[]) => Promise<void>;
  onBlueprint: (id: string, prompt: string, stack: string) => Promise<void>;
  onOpen: (id: string) => void;
}) {
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  useEffect(() => {
    api.blueprints().then(setBlueprints, () => setBlueprints([]));
  }, []);
  const [prompt, setPrompt] = useState("");
  const [stack, setStack] = useState("fastapi");
  // Plugins are chosen by the agents; the user may force one on or off.
  const [force, setForce] = useState<Record<string, "on" | "off">>({});
  const [busy, setBusy] = useState(false);
  const [guided, setGuided] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plugins = config?.plugins ?? [];
  const forced = (mode: "on" | "off") => Object.keys(force).filter((n) => force[n] === mode);
  const cycle = (name: string) =>
    setForce((f) => {
      const next = { ...f };
      if (!f[name]) next[name] = "on";
      else if (f[name] === "on") next[name] = "off";
      else delete next[name];
      return next;
    });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onCreate(prompt.trim(), stack, forced("on"), forced("off"));
    } catch (err) {
      setError((err as Error).message.replace(/^\d+: /, ""));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-5 pb-20">
      <section className="blueprint -mx-5 rounded-b-3xl px-5 pb-12 pt-14 sm:pt-20">
        <h1 className="max-w-3xl font-display text-4xl font-bold leading-[1.08] tracking-tight text-ink-50 sm:text-6xl">
          Describe an app. Watch it get built, tested and fixed.
        </h1>
        <p className="mt-4 max-w-2xl text-lg text-ink-300">
          Eight agents turn your words into a working web app with a FastAPI, Flask or Django backend. They find their own bugs and repair them before you see it.
        </p>
        <form onSubmit={submit} className="mt-8 rounded-2xl border border-ink-600 bg-ink-900/95 p-3 shadow-[0_24px_60px_-30px_rgba(0,0,0,0.8)] focus-within:border-signal-400">
          <label htmlFor="prompt" className="sr-only">Describe your app</label>
          <textarea
            id="prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && prompt.trim().length >= 10) submit(e);
            }}
            rows={4}
            placeholder="A booking app for a yoga studio: classes with a capacity, members who book and cancel, a waitlist when a class is full…"
            className="w-full resize-none bg-transparent px-2 py-1.5 text-base leading-relaxed text-ink-50 outline-none placeholder:text-ink-500"
          />
          <div className="flex flex-wrap items-center gap-2 border-t border-ink-700 pt-3">
            <div role="radiogroup" aria-label="Backend framework" className="flex rounded-lg bg-ink-950 p-0.5">
              {(config?.stacks ?? [{ name: "fastapi", label: "FastAPI" }, { name: "flask", label: "Flask" }, { name: "django", label: "Django" }]).map((s) => (
                <button
                  type="button"
                  role="radio"
                  aria-checked={stack === s.name}
                  key={s.name}
                  title={STACK_NOTE[s.name]}
                  onClick={() => setStack(s.name)}
                  className={`rounded-md px-3 py-1.5 text-sm ${stack === s.name ? "bg-ink-700 font-medium text-ink-50" : "text-ink-400 hover:text-ink-100"}`}
                >
                  {s.label}
                </button>
              ))}
            </div>
            {plugins.length > 0 && (
              <details className="relative">
                <summary className="cursor-pointer list-none rounded-lg px-3 py-1.5 text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-50">
                  Plugins: {Object.keys(force).length ? `agents + ${Object.keys(force).length} set by you` : "chosen by the agents"}
                </summary>
                <div className="absolute left-0 z-10 mt-2 w-80 space-y-1 rounded-xl border border-ink-700 bg-ink-900 p-2 shadow-xl">
                  <p className="px-2 pb-1 text-xs text-ink-400">The Architect picks the plugins your app needs and says why. Click one to force it on or off.</p>
                  {plugins.map((p) => {
                    const mode = force[p.name] ?? (p.always ? "always" : "auto");
                    const chip = { on: "bg-signal-400 text-ink-950", off: "bg-rose-500/20 text-rose-200", always: "bg-ink-700 text-ink-200", auto: "bg-ink-800 text-ink-300" }[mode];
                    return (
                      <button type="button" key={p.name} onClick={() => cycle(p.name)} className="flex w-full items-start gap-2 rounded-lg p-2 text-left hover:bg-ink-800">
                        <span className={`mt-0.5 w-14 shrink-0 rounded px-1.5 py-0.5 text-center text-[11px] font-medium ${chip}`}>{mode}</span>
                        <span>
                          <span className="block text-sm text-ink-100">{p.name}</span>
                          <span className="block text-xs text-ink-400">{p.description}</span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </details>
            )}
            <button
              disabled={busy || prompt.trim().length < 10}
              className="ml-auto rounded-lg bg-signal-400 px-5 py-2 font-semibold text-ink-950 hover:bg-signal-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "Starting…" : "Build it"}
            </button>
          </div>
        </form>
        {error && <p className="mt-3 text-sm text-rose-300">{error}</p>}
        {blueprints.length > 0 && (
          <div className="mt-8">
            <div className="flex items-baseline gap-3">
              <h2 className="font-display text-lg text-ink-50">Verified blueprints</h2>
              <span className="text-sm text-ink-400">Complex apps with demo data that build in under a minute, every test passing first time.</span>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {blueprints.map((b) => (
                <button
                  key={b.id}
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError(null);
                    try {
                      await onBlueprint(b.id, b.prompt, stack);
                    } catch (err) {
                      setError((err as Error).message.replace(/^\d+: /, ""));
                      setBusy(false);
                    }
                  }}
                  className="rounded-xl border border-ink-700 bg-ink-900/80 p-4 text-left hover:border-signal-400 disabled:opacity-50"
                >
                  <span className="block font-medium text-ink-50">{b.title}</span>
                  <span className="mt-1 block text-sm text-ink-400">{b.summary}</span>
                  <span className="mt-2 block text-xs text-signal-300">{b.entities} tables · {b.actions} actions · build with {stack}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {guided && <PromptBuilder onClose={() => setGuided(false)} onUse={(text) => { setPrompt(text); setGuided(false); }} />}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button onClick={() => setGuided(true)} className="rounded-full bg-signal-400/15 px-3 py-1 text-sm font-medium text-signal-200 hover:bg-signal-400/25">
            Build it step by step
          </button>
          <span className="text-sm text-ink-400">or try:</span>
          {EXAMPLES.map(([name, text]) => (
            <button
              key={name}
              onClick={() => setPrompt(text)}
              className="rounded-full border border-ink-700 bg-ink-900 px-3 py-1 text-sm text-ink-200 hover:border-ink-500 hover:text-ink-50"
            >
              {name}
            </button>
          ))}
        </div>
      </section>

      <section className="mt-12">
        <h2 className="font-display text-xl font-bold text-ink-50">Your projects</h2>
        {projects.length === 0 ? (
          <p className="mt-3 text-ink-400">Nothing yet. Describe your first app above and it will appear here.</p>
        ) : (
          <ul className="mt-4 divide-y divide-ink-800 overflow-hidden rounded-2xl border border-ink-800">
            {projects.map((p) => (
              <li key={p.id}>
                <button onClick={() => onOpen(p.id)} className="grid w-full grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1 bg-ink-900/60 px-5 py-4 text-left hover:bg-ink-800 sm:grid-cols-[1fr_120px_110px_130px]">
                  <span className="truncate font-medium text-ink-50">{p.name}</span>
                  <span className="text-sm text-ink-400 sm:order-none">{config?.stacks.find((s) => s.name === p.stack)?.label ?? p.stack}</span>
                  <span className="text-sm text-ink-400">
                    {p.versions ?? 0} version{p.versions === 1 ? "" : "s"}
                  </span>
                  <span className="flex items-center justify-between gap-3 sm:justify-end">
                    <StatusDot status={p.latest_status} />
                    <span className="text-xs text-ink-500">{ago(p.updated_at)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
