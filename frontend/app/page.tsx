"use client";

import { useCallback, useEffect, useState } from "react";
import { Home } from "@/components/Home";
import { Login, Logo } from "@/components/Login";
import { Workspace } from "@/components/Workspace";
import { api, getToken, setToken, type Project, type ServerConfig } from "@/lib/api";

/** One page: log in, then either the home screen (?) or a project workspace (?project=<id>). */
export default function Page() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [user, setUser] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);

  const go = useCallback((id: string | null) => {
    setProjectId(id);
    window.history.pushState(null, "", id ? `?project=${id}` : window.location.pathname);
  }, []);

  useEffect(() => {
    api.config().then(setConfig, () => undefined);
    const query = new URLSearchParams(window.location.search);
    const fromGitHub = query.get("login");  // back from "Sign in with GitHub"
    if (fromGitHub) setToken(fromGitHub);
    const loginProblem = query.get("login_error");
    if (fromGitHub || loginProblem) {
      query.delete("login");
      query.delete("login_error");
      window.history.replaceState(null, "", query.toString() ? `?${query}` : window.location.pathname);
    }
    (async () => {
      if (getToken()) {
        try {
          setUser((await api.me()).email);
        } catch {
          setToken(""); // expired: show the login screen
        }
      }
      setLoginError(loginProblem);
      setProjectId(new URLSearchParams(window.location.search).get("project"));
      setChecked(true);
    })();
    const onBack = () => setProjectId(new URLSearchParams(window.location.search).get("project"));
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, []);

  useEffect(() => {
    if (!user || projectId) return;
    (async () => setProjects(await api.projects().catch(() => [])))();
  }, [user, projectId]);

  if (!checked) return null;
  if (!user)
    return <Login inviteRequired={!!config?.invite_required} github={!!config?.github_enabled} initialError={loginError} onDone={setUser} />;

  async function create(prompt: string, stack: string, plugins: string[]) {
    const { project_id } = await api.start({ prompt, stack, plugins });
    go(project_id);
  }

  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center gap-4 border-b border-ink-800 bg-ink-950/90 px-5 backdrop-blur">
        <button onClick={() => go(null)} aria-label="Home">
          <Logo />
        </button>
        <span className="text-xs text-ink-500" title="Version of this UI build">v{process.env.NEXT_PUBLIC_UI_VERSION?.split(".")[0]}</span>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {config?.fake_llm && <span className="rounded-md bg-signal-400/15 px-2 py-1 text-xs text-signal-300">Offline demo model</span>}
          {config && !config.llm_enabled && <span className="rounded-md bg-rose-500/15 px-2 py-1 text-xs text-rose-300">No AI provider configured</span>}
          {config?.provider && config.llm_enabled && !config.fake_llm && <span className="text-xs text-ink-400">Models by {config.provider}</span>}
          <span className="hidden text-ink-300 sm:inline">{user}</span>
          <button
            onClick={() => {
              setToken("");
              setUser(null);
              go(null);
            }}
            className="rounded-md px-2 py-1 text-ink-400 hover:bg-ink-800 hover:text-ink-50"
          >
            Log out
          </button>
        </div>
      </header>
      {projectId ? (
        <Workspace key={projectId} projectId={projectId} onHome={() => go(null)} />
      ) : (
        <Home config={config} projects={projects} onCreate={create} onOpen={go} />
      )}
    </div>
  );
}
