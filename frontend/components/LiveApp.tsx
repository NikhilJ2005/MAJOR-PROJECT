"use client";

import { useCallback, useEffect, useState } from "react";
import { table } from "@/components/Architecture";
import { API, api, type Action, type Entity, type Field, type Spec } from "@/lib/api";
import { Empty } from "./Ledger";

type Row = Record<string, unknown> & { id: number };
type Me = { id: number; email: string };

// The live preview makes this account an admin (ADMIN_EMAILS in the sandbox environment).
const DEMO_ADMIN = "admin@vibestack.dev";
const DEMO_CUSTOMER = "customer@vibestack.dev";
const DEMO_PASSWORD = "demo-pass-123";

async function call(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = body?.detail;
    throw new Error(`${res.status} ${typeof d === "string" ? d : d ? JSON.stringify(d).slice(0, 160) : res.statusText}`);
  }
  return body;
}

export function LiveApp({ runId, spec, ready }: { runId: string; spec: Spec; ready: boolean }) {
  const [state, setState] = useState<"idle" | "starting" | "running" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [base, setBase] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<Record<string, Row[]>>({});
  const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};

  const load = useCallback(
    async (url: string | null = base, headers: Record<string, string> = authHeaders) => {
      if (!url) return;
      const out: Record<string, Row[]> = {};
      for (const e of spec.entities) {
        try {
          out[e.name] = await call(`${url}/${table(e.name)}/?limit=100`, { headers });
        } catch {
          out[e.name] = [];
        }
      }
      setRows(out);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [base, spec.entities, token],
  );

  const start = useCallback(async () => {
    setState("starting");
    setError(null);
    try {
      const info = await api.startPreview(runId);
      const url = `${API}${info.base}`;
      setBase(url);
      setToken(null);
      setMe(null);
      setState("running");
      await load(url, {});
    } catch (e) {
      setState("error");
      setError((e as Error).message);
    }
  }, [runId, load]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const s = await api.previewStatus(runId);
        if (cancelled) return;
        if (s.running && s.base) {
          const url = `${API}${s.base}`;
          setBase(url);
          setState("running");
          await load(url, {});
          return;
        }
      } catch {}
      if (!cancelled) await start();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, runId]);

  if (!ready) return <Empty text="When the app passes validation, VibeStack starts it here so you can use it live." />;

  async function onLogin(t: string, user: Me) {
    setToken(t);
    setMe(user);
    await load(base, { Authorization: `Bearer ${t}` });
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/5 px-4 py-3">
        <span className={`flex items-center gap-2 text-base font-semibold ${state === "running" ? "text-emerald-300" : state === "error" ? "text-rose-300" : "text-sky-300"}`}>
          <span className={`h-2.5 w-2.5 rounded-full ${state === "running" ? "bg-emerald-400" : state === "error" ? "bg-rose-400" : "animate-pulse bg-sky-400"}`} />
          {state === "running" ? "Your generated app is running live" : state === "error" ? "Preview failed to start" : "Starting your generated app…"}
        </span>
        <span className="text-xs text-zinc-400">private link · only people you share it with can open it</span>
        <div className="ml-auto flex gap-2">
          <a
            href={base ? `${base}/docs` : "#"}
            target="_blank"
            rel="noreferrer"
            className={`rounded-lg bg-emerald-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-emerald-400 ${state !== "running" ? "pointer-events-none opacity-40" : ""}`}
          >
            Open API docs ↗
          </a>
          <button onClick={start} className="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800">
            Restart
          </button>
        </div>
      </div>
      {error && <pre className="max-h-48 overflow-auto rounded-lg bg-rose-500/10 p-3 font-mono text-xs text-rose-300">{error}</pre>}

      {state === "running" && base && (
        <>
          {spec.auth && <AuthCard base={base} me={me} onLogin={onLogin} />}
          {spec.entities.map((e) => (
            <EntityPanel
              key={e.name}
              entity={e}
              actions={(spec.actions ?? []).filter((a) => a.entity === e.name)}
              url={`${base}/${table(e.name)}/`}
              rows={rows[e.name] ?? []}
              allRows={rows}
              me={me}
              headers={authHeaders}
              needsAuth={!!spec.auth && !token}
              onChange={() => load()}
            />
          ))}
        </>
      )}
    </div>
  );
}

function AuthCard({ base, me, onLogin }: { base: string; me: Me | null; onLogin: (t: string, u: Me) => void }) {
  const [email, setEmail] = useState(DEMO_CUSTOMER);
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [msg, setMsg] = useState<string | null>(null);
  const json = { "Content-Type": "application/json" };

  async function login(address: string, pw: string) {
    setMsg(null);
    try {
      const creds = JSON.stringify({ email: address, password: pw });
      await fetch(`${base}/auth/register`, { method: "POST", headers: json, body: creds }); // 201, or 409 if it exists
      const { access_token } = await call(`${base}/auth/login`, { method: "POST", headers: json, body: creds });
      const user = await call(`${base}/auth/me`, { headers: { Authorization: `Bearer ${access_token}` } });
      onLogin(access_token, user);
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-500/5 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold uppercase tracking-wider text-violet-300">Generated JWT login</span>
        {me && <span className="text-sm text-emerald-300">✓ logged in as {me.email} (user #{me.id})</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => login(DEMO_ADMIN, DEMO_PASSWORD)} className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-amber-400">
          Log in as admin
        </button>
        <button onClick={() => login(DEMO_CUSTOMER, DEMO_PASSWORD)} className="rounded-lg bg-violet-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-violet-400">
          Log in as customer
        </button>
        <span className="text-xs text-zinc-500">or</span>
        <input value={email} onChange={(e) => setEmail(e.target.value)} className="min-w-48 flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm" />
        <input value={password} type="password" onChange={(e) => setPassword(e.target.value)} className="w-36 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm" />
        <button onClick={() => login(email, password)} className="rounded-lg border border-violet-500/50 px-3 py-1.5 text-sm text-violet-200 hover:bg-violet-500/10">
          Log in
        </button>
      </div>
      <p className="mt-2 text-xs text-zinc-500">Switch users to see the access rules work: private rows disappear, admin-only writes return 403.</p>
      {msg && <p className="mt-2 text-sm text-rose-300">{msg}</p>}
    </div>
  );
}

function inputFor(f: Field, value: string, set: (v: string) => void, options?: Row[]) {
  const cls = "w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm";
  if (f.references || f.choices?.length) {
    const items = f.choices?.length
      ? f.choices.map((c) => ({ value: c, label: c }))
      : (options ?? []).map((r) => ({
          value: String(r.id),
          label: `#${r.id} ${String(Object.entries(r).find(([k, v]) => typeof v === "string" && k !== "created_at")?.[1] ?? "")}`,
        }));
    return (
      <select value={value} onChange={(e) => set(e.target.value)} className={cls}>
        <option value="">{f.name}…</option>
        {items.map((i) => (
          <option key={i.value} value={i.value}>{i.label}</option>
        ))}
      </select>
    );
  }
  if (f.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm text-zinc-300">
        <input type="checkbox" checked={value === "true"} onChange={(e) => set(e.target.checked ? "true" : "false")} className="accent-sky-400" /> {f.name}
      </label>
    );
  }
  const numeric = ["int", "float", "decimal"].includes(f.type);
  const type = numeric ? "number" : f.type === "datetime" ? "datetime-local" : f.type === "date" ? "date" : "text";
  return (
    <input
      type={type}
      step={f.type === "int" ? undefined : "any"}
      min={f.min ?? undefined}
      max={f.max ?? undefined}
      placeholder={f.name}
      value={value}
      onChange={(e) => set(e.target.value)}
      className={cls}
    />
  );
}

function convert(f: Field, raw: string): unknown {
  if (f.type === "bool") return raw === "true";
  if (raw === "") return undefined;
  if (f.type === "int" || f.references) return parseInt(raw, 10);
  if (f.type === "float" || f.type === "decimal") return parseFloat(raw);
  if (f.type === "datetime") return raw.length === 16 ? `${raw}:00` : raw;
  return raw;
}

function EntityPanel({
  entity, actions, url, rows, allRows, me, headers, needsAuth, onChange,
}: {
  entity: Entity;
  actions: Action[];
  url: string;
  rows: Row[];
  allRows: Record<string, Row[]>;
  me: Me | null;
  headers: Record<string, string>;
  needsAuth: boolean;
  onChange: () => void;
}) {
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const fields = entity.fields;

  const optionsFor = (f: Field): Row[] | undefined => {
    if (!f.references) return undefined;
    if (f.references === "User") return me ? [{ id: me.id, email: me.email }] : [];
    return allRows[f.references];
  };

  async function create() {
    setMsg(null);
    const body: Record<string, unknown> = {};
    for (const f of fields) {
      const v = convert(f, form[f.name] ?? (f.type === "bool" ? "false" : ""));
      if (v !== undefined) body[f.name] = v;
    }
    try {
      await call(url, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
      setForm({});
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function remove(id: number) {
    try {
      await call(`${url}${id}`, { method: "DELETE", headers });
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }

  async function run(action: Action, id?: number) {
    setMsg(null);
    const path = action.path.replace("{item_id}", String(id ?? "")).replace(/^\//, "");
    try {
      const init: RequestInit = { method: action.method, headers: { "Content-Type": "application/json", ...headers } };
      if (action.method === "POST") init.body = "{}";
      setResult(`${action.name}: ${JSON.stringify(await call(`${url}${path}`, init), null, 1)}`);
      onChange();
    } catch (e) {
      setResult(`${action.name}: ${(e as Error).message}`);
    }
  }

  const itemActions = actions.filter((a) => a.path.startsWith("/{item_id}"));
  const listActions = actions.filter((a) => !a.path.startsWith("/{item_id}"));

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/40">
      <div className="flex flex-wrap items-center gap-3 border-b border-zinc-800 px-4 py-2.5">
        <span className="text-lg font-semibold text-sky-300">{entity.name}</span>
        <span className="font-mono text-xs text-zinc-500">/{table(entity.name)}/</span>
        {entity.write_access === "owner" && <span className="rounded bg-violet-500/15 px-1.5 text-[11px] text-violet-300">private rows</span>}
        {entity.write_access === "admin" && <span className="rounded bg-amber-500/15 px-1.5 text-[11px] text-amber-300">admin writes</span>}
        {listActions.map((a) => (
          <button key={a.name} onClick={() => run(a)} className="rounded-md border border-teal-500/40 px-2 py-0.5 text-xs text-teal-200 hover:bg-teal-500/10" title={a.description}>
            ⚙ {a.name}
          </button>
        ))}
        <span className="ml-auto text-sm text-zinc-400">{rows.length} record{rows.length === 1 ? "" : "s"}</span>
      </div>
      <div className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-4">
        {fields.map((f) => (
          <div key={f.name}>{inputFor(f, form[f.name] ?? "", (v) => setForm((s) => ({ ...s, [f.name]: v })), optionsFor(f))}</div>
        ))}
        <button onClick={create} className="rounded-md bg-sky-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-sky-400">
          + Create {entity.name}
        </button>
      </div>
      {needsAuth && <p className="px-4 pb-2 text-xs text-amber-300">Writes need a login: use the buttons above (this shows the generated auth working).</p>}
      {msg && <p className="px-4 pb-2 font-mono text-xs text-rose-300">{msg}</p>}
      {result && <pre className="mx-4 mb-3 max-h-40 overflow-auto rounded bg-zinc-950 p-2 font-mono text-xs text-teal-200">{result}</pre>}
      {rows.length > 0 && (
        <div className="overflow-x-auto border-t border-zinc-800">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-4 py-2">id</th>
                {fields.map((f) => (
                  <th key={f.name} className="px-4 py-2">{f.name}</th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-zinc-800/60">
                  <td className="px-4 py-2 font-mono text-zinc-500">{r.id}</td>
                  {fields.map((f) => (
                    <td key={f.name} className="max-w-64 truncate px-4 py-2 text-zinc-200">{String(r[f.name] ?? "")}</td>
                  ))}
                  <td className="space-x-3 whitespace-nowrap px-4 py-2 text-right">
                    {itemActions.map((a) => (
                      <button key={a.name} onClick={() => run(a, r.id)} className="text-xs text-teal-300 hover:text-teal-200" title={a.description}>
                        {a.name}
                      </button>
                    ))}
                    <button onClick={() => remove(r.id)} className="text-xs text-rose-400 hover:text-rose-300">delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
