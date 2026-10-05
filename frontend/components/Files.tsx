"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Empty } from "./Ledger";

export function Files({ runId, files, version }: { runId: string; files: string[]; version: number }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");

  const current = selected && files.includes(selected) ? selected : files.find((f) => f.startsWith("app/routers/") && !f.endsWith("__init__.py")) ?? files[0];

  useEffect(() => {
    if (!current) return;
    let cancelled = false;
    api.file(runId, current).then(
      (text) => !cancelled && setContent(text),
      () => !cancelled && setContent("// could not load file"),
    );
    return () => {
      cancelled = true;
    };
  }, [runId, current, version]);

  if (!files.length) return <Empty text="Generated files appear here." />;

  return (
    <div className="grid min-h-[420px] grid-cols-1 gap-3 md:grid-cols-[220px_minmax(0,1fr)]">
      <ul className="max-h-[520px] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950/60 p-1 font-mono text-xs">
        {files.map((f) => (
          <li key={f}>
            <button
              onClick={() => setSelected(f)}
              className={`w-full truncate rounded px-2 py-1 text-left ${f === current ? "bg-sky-500/15 text-sky-200" : "text-zinc-400 hover:bg-zinc-800/60"}`}
              title={f}
            >
              {f}
            </button>
          </li>
        ))}
      </ul>
      <pre className="max-h-[520px] overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-5 text-zinc-300">
        {content.split("\n").map((line, i) => (
          <div key={i} className="flex">
            <span className="mr-4 w-8 shrink-0 select-none text-right text-zinc-600">{i + 1}</span>
            <span className="whitespace-pre">{line}</span>
          </div>
        ))}
      </pre>
    </div>
  );
}
