import type { Action, Entity, Field, Snapshot, Spec } from "@/lib/api";

export const table = (name: string) => {
  const snake = name.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
  if (/[^aeiou]y$/.test(snake)) return snake.slice(0, -1) + "ies";
  if (/(s|x|z|ch|sh)$/.test(snake)) return snake + "es";
  return snake + "s";
};

const STACK_LABEL: Record<string, string> = {
  fastapi: "FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2",
  flask: "Flask 3, SQLAlchemy 2.0, Alembic, Pydantic v2",
  django: "Django 5 ORM, Django migrations, Pydantic v2",
};

const ACCESS: Record<string, { text: string; cls: string }> = {
  owner: { text: "private to owner", cls: "bg-violet-500/15 text-violet-300" },
  admin: { text: "admin writes", cls: "bg-amber-500/15 text-amber-300" },
  any_user: { text: "logged-in writes", cls: "bg-ink-800 text-ink-400" },
};

const KIND: Record<string, string> = {
  data: "text-sky-300",
  rule: "text-amber-300",
  permission: "text-violet-300",
  endpoint: "text-emerald-300",
};

export function Architecture({ spec, plan }: { spec: Spec; plan: Snapshot["file_plan"] }) {
  const coveredBy = (id: string) => [
    ...spec.entities.filter((e) => e.requirement_ids?.includes(id)).map((e) => e.name),
    ...(spec.actions ?? []).filter((a) => a.requirement_ids?.includes(id)).map((a) => `${a.entity}.${a.name}`),
  ];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm text-ink-100">{spec.project_name}</h3>
        {spec.auth && <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[11px] text-violet-300">JWT auth</span>}
        {(spec.features ?? []).map((f) => (
          <span key={f} className="rounded bg-ink-800 px-1.5 py-0.5 text-[11px] text-ink-400">{f}</span>
        ))}
        <span className="ml-auto text-[11px] text-ink-500">{STACK_LABEL[spec.stack ?? "fastapi"]}</span>
      </div>
      {spec.description && <p className="text-sm text-ink-400">{spec.description}</p>}

      {(spec.requirements ?? []).length > 0 && (
        <Section title={`Requirements (${spec.requirements!.length})`}>
          <ul className="space-y-1 text-sm">
            {spec.requirements!.map((r) => {
              const by = coveredBy(r.id);
              return (
                <li key={r.id} className="flex gap-2">
                  <span className={`shrink-0 ${by.length ? "text-emerald-400" : "text-ink-600"}`}>{by.length ? "✓" : "○"}</span>
                  <span className="w-8 shrink-0 font-mono text-xs leading-5 text-ink-500">{r.id}</span>
                  <span className={`w-20 shrink-0 text-xs leading-5 ${KIND[r.kind] ?? ""}`}>{r.kind}</span>
                  <span className="text-ink-200">
                    {r.text}
                    {by.length > 0 && <span className="ml-2 font-mono text-[11px] text-ink-500">→ {by.join(", ")}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
        </Section>
      )}

      <Section title="Data model">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {spec.auth && (
            <EntityBox
              entity={{ name: "User", fields: [{ name: "email", type: "str", unique: true }, { name: "password_hash", type: "str" }] }}
              note="auth"
            />
          )}
          {spec.entities.map((e) => (
            <EntityBox key={e.name} entity={e} auth={!!spec.auth} />
          ))}
        </div>
      </Section>

      {(spec.actions ?? []).length > 0 && (
        <Section title="Custom actions (business logic)">
          <ul className="space-y-2">
            {spec.actions!.map((a) => (
              <ActionRow key={`${a.entity}.${a.name}`} action={a} />
            ))}
          </ul>
        </Section>
      )}

      <Section title="API endpoints">
        <ul className="grid gap-1 font-mono text-xs sm:grid-cols-2">
          {spec.auth && ["POST /auth/register", "POST /auth/login", "GET /auth/me"].map((ep) => <Endpoint key={ep} ep={ep} />)}
          {spec.entities.flatMap((e) => {
            const t = table(e.name);
            const lock = spec.auth ? (e.write_access === "admin" ? " 🔒admin" : " 🔒") : "";
            const read = e.write_access === "owner" ? " 🔒own" : "";
            return [`GET /${t}/${read}`, `POST /${t}/${lock}`, `GET /${t}/{id}${read}`, `PATCH /${t}/{id}${lock}`, `DELETE /${t}/{id}${lock}`].map((ep) => (
              <Endpoint key={ep} ep={ep} />
            ));
          })}
          {(spec.actions ?? []).map((a) => (
            <Endpoint key={`${a.entity}.${a.name}`} ep={`${a.method} /${table(a.entity)}${a.path} ⚙`} />
          ))}
        </ul>
      </Section>

      {plan && plan.length > 0 && (
        <Section title={`File plan, ${plan.length} files in build order`}>
          <ol className="grid gap-x-6 gap-y-0.5 font-mono text-xs sm:grid-cols-2">
            {plan.map((f, i) => (
              <li key={f.path} className="flex gap-2 truncate">
                <span className="w-5 shrink-0 text-right text-ink-600">{i + 1}</span>
                <span className={f.owner === "entity" ? "text-sky-300" : f.owner.startsWith("plugin:") ? "text-lime-300" : "text-ink-300"}>{f.path}</span>
                <span className="truncate text-ink-600">{f.purpose}</span>
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[11px] text-ink-500">
            <span className="text-sky-300">blue</span> = written by the Code Generator · white = verified templates ·{" "}
            <span className="text-lime-300">green</span> = added by plugins
          </p>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-2 font-display text-sm font-medium text-ink-200">{title}</h4>
      {children}
    </div>
  );
}

function Endpoint({ ep }: { ep: string }) {
  const [method, ...rest] = ep.split(" ");
  const color: Record<string, string> = { GET: "text-emerald-300", POST: "text-sky-300", PATCH: "text-amber-300", DELETE: "text-rose-300" };
  return (
    <li className="flex gap-2">
      <span className={`w-14 ${color[method] ?? ""}`}>{method}</span>
      <span className="text-ink-300">{rest.join(" ")}</span>
    </li>
  );
}

function ActionRow({ action }: { action: Action }) {
  return (
    <li className="rounded-lg border border-ink-800 bg-ink-950/60 px-3 py-2">
      <div className="flex flex-wrap items-baseline gap-2 font-mono text-xs">
        <span className={action.method === "GET" ? "text-emerald-300" : "text-sky-300"}>{action.method}</span>
        <span className="text-ink-200">/{table(action.entity)}{action.path}</span>
        {action.admin_only && <span className="rounded bg-amber-500/15 px-1 text-amber-300">admin</span>}
        <span className="font-sans text-sm text-ink-400">{action.description}</span>
      </div>
      {(action.rules ?? []).length > 0 && (
        <ul className="mt-1 list-inside list-disc text-xs text-amber-200/80">
          {action.rules!.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function fieldNote(f: Field) {
  const parts: string[] = [];
  if (f.choices?.length) parts.push(f.choices.join("|"));
  if (f.min != null) parts.push(`≥${f.min}`);
  if (f.max != null) parts.push(`≤${f.max}`);
  return parts.join(" ");
}

function EntityBox({ entity, note, auth }: { entity: Entity; note?: string; auth?: boolean }) {
  const access = auth && entity.write_access ? ACCESS[entity.write_access] : null;
  return (
    <div className="rounded-lg border border-ink-800 bg-ink-950/60">
      <div className="flex items-center justify-between gap-2 border-b border-ink-800 px-3 py-1.5">
        <span className="font-mono text-sm text-sky-300">{entity.name}</span>
        {note && <span className="text-[10px] text-ink-500">{note}</span>}
        {access && <span className={`rounded px-1.5 text-[10px] ${access.cls}`}>{access.text}</span>}
      </div>
      <ul className="px-3 py-2 font-mono text-xs">
        <li className="text-ink-500">id · int · pk</li>
        {entity.write_access === "owner" && <li className="text-ink-500">owner_id → User · automatic</li>}
        {entity.fields.map((f) => (
          <li key={f.name} className="text-ink-300">
            {f.name} <span className="text-ink-500">· {f.choices?.length ? "enum" : f.type}</span>
            {f.references && <span className="text-violet-300"> → {f.references}</span>}
            {f.unique && <span className="text-amber-300/80"> · unique</span>}
            {f.required === false && <span className="text-ink-500"> · optional</span>}
            {fieldNote(f) && <span className="text-teal-300/80"> · {fieldNote(f)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
