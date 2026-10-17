export const API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type LedgerEntry = {
  agent: string;
  action: string;
  rationale?: string;
  files?: string[];
  tokens?: number;
  iteration?: number;
  model?: string;
  diff?: Record<string, string>;
  tools?: ToolCall[];
};

export type ToolCall = { tool: string; args: Record<string, unknown>; result: string };

export type Version = {
  run_id: string;
  version: number;
  prompt: string;
  status: string;
  parent_run_id?: string | null;
  created_at: number;
  finished_at?: number | null;
  repairs?: number;
  bugs_found?: number;
  bugs_fixed?: number;
  cost_usd?: number;
};
export type Project = {
  id: string;
  name: string;
  stack: string;
  current_run_id?: string | null;
  created_at: number;
  updated_at: number;
  versions?: number;
  latest_status?: string | null;
};
export type ProjectDetail = Omit<Project, "versions"> & { versions: Version[]; busy: boolean };

export type Field = {
  name: string;
  type: string;
  required?: boolean;
  unique?: boolean;
  references?: string | null;
  choices?: string[];
  min?: number | null;
  max?: number | null;
  transitions?: Record<string, string[]>;
  on_delete?: string | null;
  index?: boolean;
};
export type Entity = { name: string; fields: Field[]; write_access?: "any_user" | "owner" | "admin"; requirement_ids?: string[]; realtime?: boolean };
export type Action = {
  name: string;
  entity: string;
  method: "GET" | "POST";
  path: string;
  description?: string;
  rules?: string[];
  admin_only?: boolean;
  requirement_ids?: string[];
};
export type Requirement = { id: string; text: string; kind: "data" | "rule" | "permission" | "endpoint" };
export type Spec = {
  project_name: string;
  description?: string;
  stack?: string;
  auth?: boolean;
  features?: string[];
  entities: Entity[];
  actions?: Action[];
  requirements?: Requirement[];
};

export type Validation = { ok: boolean; gate: string; log: string; failing_file?: string | null; duration_s?: number };
export type Bug = { id: string; found_by: string; detail: string; fixed: boolean; open?: boolean };
export type ReviewNote = { path: string; line: number; rule: string; severity: string; message: string };

export type Snapshot = {
  run_id: string;
  project_id?: string | null;
  version?: number | null;
  base_run_id?: string | null;
  changes?: { added: string[]; changed: string[]; removed: string[] } | null;
  prompt?: string;
  status: string;
  options?: { stack?: string; codegen_mode?: string; plugins?: string[] };
  spec?: Spec;
  file_plan?: { path: string; owner: string; purpose: string }[];
  files?: string[];
  ledger?: LedgerEntry[];
  validation?: Validation | null;
  iteration?: number;
  usage?: { total_tokens?: number; cost_usd?: number; llm_calls?: number };
  requirements?: Requirement[];
  bugs?: Bug[];
  review?: ReviewNote[];
  stop_reason?: string | null;
  report?: string | null;
  has_artifact?: boolean;
  has_draft?: boolean;
};

export type PluginInfo = { name: string; description: string; hooks: string[]; default: boolean };

export type ServerConfig = {
  llm_enabled: boolean;
  fake_llm?: boolean;
  codegen_mode: string;
  sandbox: string;
  invite_required: boolean;
  provider?: string;
  max_heal_safety: number;
  max_runs_per_day: number;
  stacks: { name: string; label: string }[];
  plugins: PluginInfo[];
  models: { strong: string; cheap: string; fallback: string };
  github_enabled?: boolean;
};

export type GitHubConnection = { enabled: boolean; connected: boolean; login: string | null };
export type GitHubStatus = {
  repo: string | null;
  url?: string;
  version?: number | null;
  ci?: { status: string; conclusion: string | null; url: string } | null;
};

export type RunEvent = {
  seq: number;
  ts: number;
  type: "node" | "status" | "error" | "progress";
  node?: string;
  text?: string;
  status?: string;
  message?: string;
  ledger?: LedgerEntry[];
  validation?: Validation | null;
  error_class?: string | null;
  iteration?: number | null;
  bugs?: Bug[] | null;
  stop_reason?: string | null;
};

export type RunSummary = { run_id: string; prompt: string; stack: string; status: string; created_at: number };
export type Stats = { runs: number; succeeded: number; success_rate: number; avg_repairs: number; bugs_found: number; bugs_fixed: number; cost_usd: number };
export type PreviewInfo = { running: boolean; base: string | null; docs: string | null };
export type StartRun = { prompt: string; stack: string; codegen_mode?: string; plugins: string[] };

// --- login token (kept in this browser only) -------------------------------------------
const TOKEN_KEY = "vibestack-token";

export function getToken(): string {
  try {
    return typeof window === "undefined" ? "" : (localStorage.getItem(TOKEN_KEY) ?? "");
  } catch {
    return "";
  }
}

export function setToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {}
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json", ...((init?.headers as Record<string, string>) ?? {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new ApiError(res.status, `${res.status}: ${detail}`);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });
// Links the browser opens by itself (event stream, downloads) cannot send headers: pass the token in the URL.
const withToken = (url: string) => `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(getToken())}`;

export const api = {
  config: () => request<ServerConfig>("/api/config"),
  register: (email: string, password: string, invite_code?: string) =>
    request<{ token: string; email: string }>("/api/auth/register", post({ email, password, invite_code })),
  login: (email: string, password: string) => request<{ token: string; email: string }>("/api/auth/login", post({ email, password })),
  me: () => request<{ id: number; email: string; is_admin: boolean }>("/api/auth/me"),
  runs: () => request<RunSummary[]>("/api/runs"),
  stats: () => request<{ mine: Stats; everyone?: Stats }>("/api/stats"),
  snapshot: (id: string) => request<Snapshot>(`/api/runs/${id}`),
  start: (body: StartRun) => request<{ run_id: string; project_id: string }>("/api/projects", post(body)),
  projects: () => request<Project[]>("/api/projects"),
  project: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  message: (id: string, prompt: string, mode: "edit" | "fix" = "edit") =>
    request<{ run_id: string; project_id: string }>(`/api/projects/${id}/messages`, post({ prompt, mode })),
  github: () => request<GitHubConnection>("/api/auth/github"),
  githubStatus: (projectId: string) => request<GitHubStatus>(`/api/projects/${projectId}/github`),
  pushToGitHub: (projectId: string) =>
    request<{ repo: string; url: string; commit: string; version: number }>(`/api/projects/${projectId}/github`, post({})),
  githubSignInUrl: (next = "/") => `${API}/api/auth/github/start?next=${encodeURIComponent(next)}${getToken() ? `&token=${getToken()}` : ""}`,
  restore: (id: string, run_id: string) => request<{ current_run_id: string }>(`/api/projects/${id}/current`, post({ run_id })),
  file: async (id: string, path: string) => {
    const res = await fetch(`${API}/api/runs/${id}/files/${path}`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.text();
  },
  previewStatus: (id: string, draft = false) => request<PreviewInfo>(`/api/runs/${id}/preview${draft ? "?draft=true" : ""}`),
  startPreview: (id: string, draft = false) =>
    request<PreviewInfo>(`/api/runs/${id}/preview${draft ? "?draft=true" : ""}`, { method: "POST" }),
  eventsUrl: (id: string, after: number) => withToken(`${API}/api/runs/${id}/events?after=${after}`),
  downloadUrl: (id: string) => withToken(`${API}/api/runs/${id}/download`),
};
