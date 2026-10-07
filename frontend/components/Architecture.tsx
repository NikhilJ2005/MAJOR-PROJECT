import type { Snapshot, Spec } from "@/lib/api";

type Entity = Spec["entities"][number];

const table = (name: string) => {
  const snake = name.replace(/([a-z0-9])([A-Z])/g, "$1_$2").toLowerCase();
  if (/[^aeiou]y$/.test(snake)) return snake.slice(0, -1) + "ies";
  if (/(s|x|z|ch|sh)$/.test(snake)) return snake + "es";
  return snake + "s";
};

export function Architecture({ spec, plan }: { spec: Spec; plan: Snapshot["file_plan"] }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-sm text-zinc-100">{spec.project_name}</h3>
        {spec.auth && <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[11px] text-violet-300">JWT auth</span>}
        {(spec.features ?? []).map((f) => (
          <span key={f} className="rounded bg-zinc-800 px-1.5 py-0.5 text-[11px] text-zinc-400">{f}</span>
        ))}
        <span className="ml-auto text-[11px] uppercase tracking-wider text-zinc-500">FastAPI · SQLAlchemy 2.0 · Pydantic v2</span>
      </div>
      {spec.description && <p className="text-sm text-zinc-400">{spec.description}</p>}

      <Section title="Data model">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {spec.auth && (
            <EntityBox
              entity={{ name: "User", fields: [{ name: "email", type: "str", unique: true }, { name: "password_hash", type: "str" }] }}
              note="auth"
            />
          )}
          {spec.entities.map((e) => (
            <EntityBox key={e.name} entity={e} />
          ))}
        </div>
      </Section>

      <Section title="API endpoints">
        <ul className="grid gap-1 font-mono text-xs sm:grid-cols-2">
          {spec.auth &&
            ["POST /auth/register", "POST /auth/login", "GET /auth/me"].map((ep) => <Endpoint key={ep} ep={ep} />)}
          {spec.entities.flatMap((e) => {
            const t = table(e.name);
            const lock = spec.auth ? " 🔒" : "";
            return [`GET /${t}/`, `POST /${t}/${lock}`, `GET /${t}/{id}`, `PATCH /${t}/{id}${lock}`, `DELETE /${t}/{id}${lock}`].map((ep) => (
              <Endpoint key={ep} ep={ep} />
            ));
          })}
        </ul>
      </Section>

      {plan && plan.length > 0 && (
        <Section title={`File plan · ${plan.length} files in build order`}>
          <ol className="grid gap-x-6 gap-y-0.5 font-mono text-xs sm:grid-cols-2">
            {plan.map((f, i) => (
              <li key={f.path} className="flex gap-2 truncate">
                <span className="w-5 shrink-0 text-right text-zinc-600">{i + 1}</span>
                <span className={f.owner === "entity" ? "text-sky-300" : "text-zinc-300"}>{f.path}</span>
                <span className="truncate text-zinc-600">{f.purpose}</span>
              </li>
            ))}
          </ol>
          <p className="mt-2 text-[11px] text-zinc-500">
            <span className="text-sky-300">blue</span> = written by the AI code generator · white = verified templates
          </p>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-2 text-[11px] uppercase tracking-wider text-zinc-500">{title}</h4>
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
      <span className="text-zinc-300">{rest.join(" ")}</span>
    </li>
  );
}

function EntityBox({ entity, note }: { entity: Entity; note?: string }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1.5">
        <span className="font-mono text-sm text-sky-300">{entity.name}</span>
        {note && <span className="text-[10px] text-zinc-500">{note}</span>}
      </div>
      <ul className="px-3 py-2 font-mono text-xs">
        <li className="text-zinc-500">id · int · pk</li>
        {entity.fields.map((f) => (
          <li key={f.name} className="text-zinc-300">
            {f.name} <span className="text-zinc-500">· {f.type}</span>
            {f.references && <span className="text-violet-300"> → {f.references}</span>}
            {f.unique && <span className="text-amber-300/80"> · unique</span>}
            {f.required === false && <span className="text-zinc-500"> · optional</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
