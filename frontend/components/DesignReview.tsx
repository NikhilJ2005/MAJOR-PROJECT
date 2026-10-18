"use client";

import { useMemo, useState } from "react";
import { api, FEATURES, type Entity, type Spec } from "@/lib/api";

/** The design, shown before any code exists: approve it, edit it here, or ask for a change in words. */
export function DesignReview({ runId, spec: original, onSent }: { runId: string; spec: Spec; onSent: () => void }) {
  const [spec, setSpec] = useState<Spec>(original);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const edited = JSON.stringify(spec) !== JSON.stringify(original);

  async function send(body: { spec?: Spec; instruction?: string }) {
    setBusy(true);
    setError(null);
    try {
      await api.approve(runId, body);
      onSent();
    } catch (e) {
      setError((e as Error).message.replace(/^\d+: /, ""));
      setBusy(false);
    }
  }

  const setEntity = (name: string, change: (e: Entity) => Entity) =>
    setSpec((s) => ({ ...s, entities: s.entities.map((e) => (e.name === name ? change(e) : e)) }));
  const toggleFeature = (f: string) =>
    setSpec((s) => ({ ...s, features: (s.features ?? []).includes(f) ? (s.features ?? []).filter((x) => x !== f) : [...(s.features ?? []), f] }));

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-signal-400/40 bg-signal-400/5 p-4">
        <h2 className="font-display text-lg text-ink-50">Check the design before any code is written</h2>
        <p className="mt-1 text-sm text-ink-300">
          The Architect designed {spec.entities.length} tables{spec.actions?.length ? ` and ${spec.actions.length} actions` : ""}. Change permissions and
          features here, or describe a change in words. Nothing is built until you approve.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            disabled={busy}
            onClick={() => send(edited ? { spec } : {})}
            className="rounded-lg bg-signal-400 px-4 py-2 text-sm font-semibold text-ink-950 hover:bg-signal-300 disabled:opacity-40"
          >
            {edited ? "Approve with my changes" : "Approve and build"}
          </button>
          {edited && (
            <button onClick={() => setSpec(original)} className="rounded-lg px-3 py-2 text-sm text-ink-300 hover:text-ink-50">
              Undo my changes
            </button>
          )}
          <form
            className="flex min-w-[260px] flex-1 gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (words.trim().length >= 3) send({ instruction: words.trim() });
            }}
          >
            <input
              value={words}
              onChange={(e) => setWords(e.target.value)}
              placeholder="Or change it in words: “add a Review table with a 1–5 rating”"
              className="min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-950 px-3 py-2 text-sm text-ink-100 outline-none focus:border-ink-500"
            />
            <button disabled={busy || words.trim().length < 3} className="rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-100 hover:border-ink-400 disabled:opacity-40">
              Send to Architect
            </button>
          </form>
        </div>
        {error && <p className="mt-2 text-sm text-rose-300">{error}</p>}
      </div>

      <Block title="How the data connects">
        <EntityDiagram spec={spec} />
      </Block>

      <Block title="Who can change what" note="Click a cell to allow or block a role. Admins (ADMIN_EMAILS) can always do everything.">
        <PermissionMatrix spec={spec} setEntity={setEntity} />
      </Block>

      {spec.auth && (
        <Block title="Production features" note="Verified, tested code: switching one on adds its endpoints and tests.">
          <div className="flex flex-wrap gap-2">
            {Object.entries(FEATURES).map(([key, label]) => {
              const on = (spec.features ?? []).includes(key);
              return (
                <button
                  key={key}
                  onClick={() => toggleFeature(key)}
                  aria-pressed={on}
                  className={`rounded-full border px-3 py-1.5 text-sm ${on ? "border-signal-400 bg-signal-400/15 text-signal-200" : "border-ink-700 text-ink-400 hover:text-ink-100"}`}
                >
                  {on ? "✓ " : ""}{label}
                </button>
              );
            })}
          </div>
        </Block>
      )}

      {(spec.actions ?? []).length > 0 && (
        <Block title="Actions and their rules">
          <ul className="space-y-2">
            {spec.actions!.map((a) => (
              <li key={`${a.entity}.${a.name}`} className="rounded-lg border border-ink-800 p-3">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono text-ink-100">{a.method} /{tableName(a.entity)}{a.path}</span>
                  {a.admin_only && <Chip cls="bg-amber-500/15 text-amber-300">admins only</Chip>}
                  {(a.roles ?? []).map((r) => <Chip key={r} cls="bg-violet-500/15 text-violet-300">{r}</Chip>)}
                  {a.locks && <Chip cls="bg-sky-500/15 text-sky-300">row locked</Chip>}
                </div>
                {a.description && <p className="mt-1 text-sm text-ink-300">{a.description}</p>}
                {(a.rules ?? []).length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-xs text-ink-400">{a.rules!.map((r) => <li key={r}>{r}</li>)}</ul>
                )}
              </li>
            ))}
          </ul>
        </Block>
      )}

      <StateMachines spec={spec} />

      {(spec.plugins ?? []).length > 0 && (
        <Block title="Plugins the Architect wants">
          <ul className="space-y-1 text-sm">
            {spec.plugins!.map((p) => (
              <li key={p.name}><span className="font-mono text-ink-100">{p.name}</span> <span className="text-ink-400">— {p.reason}</span></li>
            ))}
          </ul>
        </Block>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------- pieces

function Block({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-sm font-medium text-ink-100">{title}</h3>
      {note && <p className="mb-2 text-xs text-ink-500">{note}</p>}
      <div className={note ? "" : "mt-2"}>{children}</div>
    </section>
  );
}

function Chip({ cls, children }: { cls: string; children: React.ReactNode }) {
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${cls}`}>{children}</span>;
}

function tableName(entity: string) {
  const snake = entity.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
  if (/[^aeiou]y$/.test(snake)) return snake.slice(0, -1) + "ies";
  return /(s|x|z|ch|sh)$/.test(snake) ? snake + "es" : snake + "s";
}

/** Tables in columns by how deep their foreign keys go (parents left, children right), with labelled links. */
function EntityDiagram({ spec }: { spec: Spec }) {
  const layout = useMemo(() => {
    const entities = [...(spec.auth ? [{ name: "User", fields: [{ name: "email", type: "str" }] } as Entity] : []), ...spec.entities];
    const byName = Object.fromEntries(entities.map((e) => [e.name, e]));
    const depth: Record<string, number> = {};
    const depthOf = (name: string, seen: Set<string> = new Set()): number => {
      if (depth[name] !== undefined) return depth[name];
      if (seen.has(name) || !byName[name]) return 0;
      seen.add(name);
      const parents = byName[name].fields.map((f) => f.references).filter((r): r is string => !!r && r !== name);
      depth[name] = parents.length ? 1 + Math.max(...parents.map((p) => depthOf(p, seen))) : 0;
      return depth[name];
    };
    entities.forEach((e) => depthOf(e.name));
    const W = 168, ROW = 18, GAPX = 70, GAPY = 22;
    const columns: Entity[][] = [];
    entities.forEach((e) => (columns[depth[e.name]] ??= []).push(e));
    const boxes: Record<string, { x: number; y: number; h: number; e: Entity }> = {};
    let height = 0;
    columns.forEach((col, ci) => {
      let y = 0;
      col.forEach((e) => {
        const shown = Math.min(e.fields.length, 6);
        const h = 30 + shown * ROW + (e.fields.length > 6 ? ROW : 0);
        boxes[e.name] = { x: ci * (W + GAPX), y, h, e };
        y += h + GAPY;
      });
      height = Math.max(height, y);
    });
    return { boxes, width: columns.length * (W + GAPX) - GAPX, height, W, ROW };
  }, [spec]);
  const { boxes, width, height, W, ROW } = layout;
  const links = Object.values(boxes).flatMap(({ e }) =>
    e.fields.filter((f) => f.references && boxes[f.references]).map((f) => ({ from: e.name, to: f.references!, label: f.on_delete ?? "restrict" })),
  );
  return (
    <div className="overflow-x-auto rounded-xl border border-ink-800 bg-ink-950 p-4">
      <svg width={Math.max(width, 200)} height={Math.max(height, 60)} role="img" aria-label="Entity relationship diagram">
        {links.map((l, i) => {
          const a = boxes[l.from], b = boxes[l.to];
          const x1 = a.x, y1 = a.y + 15, x2 = b.x + W, y2 = b.y + 15;
          const mid = (x1 + x2) / 2;
          return (
            <g key={i}>
              <path d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none" stroke="var(--color-ink-600)" strokeWidth={1.5} />
              <circle cx={x2} cy={y2} r={3} fill="var(--color-ink-500)" />
              <text x={mid} y={(y1 + y2) / 2 - 4} textAnchor="middle" fontSize={10} fill="var(--color-ink-500)">{l.label}</text>
            </g>
          );
        })}
        {Object.values(boxes).map(({ x, y, h, e }) => (
          <g key={e.name} transform={`translate(${x},${y})`}>
            <rect width={W} height={h} rx={8} fill="var(--color-ink-900)" stroke="var(--color-ink-700)" />
            <text x={10} y={20} fontSize={13} fontWeight={600} fill="var(--color-ink-50)">{e.name}</text>
            {e.fields.slice(0, 6).map((f, i) => (
              <text key={f.name} x={10} y={42 + i * ROW} fontSize={11} fill={f.references ? "var(--color-signal-300)" : "var(--color-ink-300)"} fontFamily="monospace">
                {f.name}: {f.references ? `→ ${f.references}` : f.choices?.length ? "choice" : f.type}
              </text>
            ))}
            {e.fields.length > 6 && <text x={10} y={42 + 6 * ROW} fontSize={11} fill="var(--color-ink-500)">+{e.fields.length - 6} more</text>}
          </g>
        ))}
      </svg>
    </div>
  );
}

const ACCESS_LABEL = { any_user: "every logged-in user", owner: "only the row's owner", admin: "admins only" } as const;

function PermissionMatrix({ spec, setEntity }: { spec: Spec; setEntity: (name: string, change: (e: Entity) => Entity) => void }) {
  const roles = (spec.roles ?? []).map((r) => r.name);
  if (!spec.auth) return <p className="text-sm text-ink-400">This app has no logins, so anyone can change any table.</p>;

  function toggle(e: Entity, role: string) {
    const allowed = e.write_roles?.length ? e.write_roles : roles;
    const next = allowed.includes(role) ? allowed.filter((r) => r !== role) : [...allowed, role];
    setEntity(e.name, (x) =>
      next.length === 0
        ? { ...x, write_access: "admin", write_roles: [] }
        : { ...x, write_access: x.write_access === "admin" ? "any_user" : x.write_access, write_roles: next.length === roles.length ? [] : next },
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-ink-800">
      <table className="w-full text-sm">
        <thead className="border-b border-ink-800 text-left text-xs text-ink-400">
          <tr>
            <th className="px-3 py-2 font-medium">Table</th>
            <th className="px-3 py-2 font-medium">Rows belong to</th>
            {roles.map((r) => <th key={r} className="px-3 py-2 text-center font-medium">{r}</th>)}
            <th className="px-3 py-2 text-center font-medium">admin</th>
          </tr>
        </thead>
        <tbody>
          {spec.entities.map((e) => {
            const adminOnly = e.write_access === "admin";
            return (
              <tr key={e.name} className="border-b border-ink-800/60 last:border-0">
                <td className="px-3 py-2 font-mono text-ink-100">{e.name}</td>
                <td className="px-3 py-2">
                  <select
                    value={e.write_access ?? "any_user"}
                    onChange={(ev) => setEntity(e.name, (x) => ({ ...x, write_access: ev.target.value as Entity["write_access"], write_roles: ev.target.value === "admin" ? [] : x.write_roles }))}
                    className="rounded border border-ink-700 bg-ink-950 px-2 py-1 text-xs text-ink-200"
                    aria-label={`Who may change ${e.name}`}
                  >
                    {Object.entries(ACCESS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </td>
                {roles.map((r) => {
                  const can = !adminOnly && (!e.write_roles?.length || e.write_roles.includes(r));
                  return (
                    <td key={r} className="px-3 py-2 text-center">
                      <button
                        onClick={() => toggle(e, r)}
                        aria-pressed={can}
                        aria-label={`${r} may change ${e.name}`}
                        className={`h-7 w-7 rounded-md text-sm ${can ? "bg-emerald-500/20 text-emerald-300" : "bg-ink-800 text-ink-600"} hover:ring-1 hover:ring-ink-500`}
                      >
                        {can ? "✓" : "–"}
                      </button>
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-center text-emerald-300">✓</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StateMachines({ spec }: { spec: Spec }) {
  const machines = spec.entities.flatMap((e) =>
    e.fields.filter((f) => f.transitions && Object.keys(f.transitions).length).map((f) => ({ entity: e.name, field: f })),
  );
  if (!machines.length) return null;
  return (
    <Block title="Status flows" note="Any change not shown here is refused (409).">
      <div className="space-y-3">
        {machines.map(({ entity, field }) => (
          <div key={`${entity}.${field.name}`}>
            <p className="mb-1 font-mono text-xs text-ink-400">{entity}.{field.name}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
              {Object.entries(field.transitions!).map(([from, tos]) => (
                <span key={from} className="text-ink-200">
                  <span className="rounded bg-ink-800 px-1.5 py-0.5">{from}</span> → {tos.map((t) => <span key={t} className="mr-1 rounded bg-ink-800 px-1.5 py-0.5">{t}</span>)}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Block>
  );
}
