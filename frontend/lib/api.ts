export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type LedgerEntry = {
  agent: string;
  action: string;
  rationale?: string;
  files?: string[];
  tokens?: number;
  iteration?: number;
};

export type Finding = { severity: "high" | "medium" | "low" | "info"; file: string; issue: string; source?: string };

export type Field = { name: string; type: string; required?: boolean; unique?: boolean; references?: string | null };
export type Entity = { name: string; fields: Field[] };
export type Spec = { project_name: string; description?: string; auth?: boolean; features?: string[]; entities: Entity[] };

export type Validation = { ok: boolean; gate: string; log: string; failing_file?: string | null; duration_s?: number };

export type Snapshot = {
  run_id: string;
  prompt?: string;
  status: string;
  spec?: Spec;
  files?: string[];
  ledger?: LedgerEntry[];
  review?: Finding[];
  validation?: Validation | null;
  iteration?: number;
  usage?: { total_tokens?: number; cost_usd?: number; llm_calls?: number };
  report?: string | null;
  has_artifact?: boolean;
};

export type ServerConfig = {
  llm_enabled: boolean;
  codegen_mode: string;
  sandbox: string;
  access_required: boolean;
  max_heal_iterations: number;
  models: { strong: string; cheap: string; fallback: string };
};

export type RunEvent = {
  seq: number;
  type: "node" | "interrupt" | "status" | "error";
  node?: string;
  status?: string;
  message?: string;
  ledger?: LedgerEntry[];
  validation?: Validation | null;
  iteration?: number | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

const headers = (code: string) => ({ "Content-Type": "application/json", ...(code ? { "X-Access-Code": code } : {}) });

export const api = {
  config: () => request<ServerConfig>("/api/config"),
  snapshot: (id: string) => request<Snapshot>(`/api/runs/${id}`),
  start: (body: { prompt: string; inject_fault: boolean; codegen_mode?: string }, code: string) =>
    request<{ run_id: string }>("/api/runs", { method: "POST", headers: headers(code), body: JSON.stringify(body) }),
  approve: (id: string, body: { approved: boolean; spec?: Spec; reason?: string }, code: string) =>
    request<{ status: string }>(`/api/runs/${id}/approve`, { method: "POST", headers: headers(code), body: JSON.stringify(body) }),
  file: async (id: string, path: string) => {
    const res = await fetch(`${API}/api/runs/${id}/files/${path}`);
    if (!res.ok) throw new Error(`${res.status}`);
    return res.text();
  },
  eventsUrl: (id: string, after: number) => `${API}/api/runs/${id}/events?after=${after}`,
  downloadUrl: (id: string) => `${API}/api/runs/${id}/download`,
};
