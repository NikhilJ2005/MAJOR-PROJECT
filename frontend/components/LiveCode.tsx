"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { RunEvent } from "@/lib/api";
import type { LiveItem } from "@/lib/useRun";

const AGENT_NAME: Record<string, string> = {
  architect: "Architect",
  code_generator: "Code Generator",
  reflector: "Reflector",
};

/** What the agents are writing, as they write it: one item per file or document, plus recent activity. */
export function LiveCode({ live, events, running }: { live: Record<string, LiveItem>; events: RunEvent[]; running: boolean }) {
  const items = useMemo(() => Object.values(live).sort((a, b) => a.updated - b.updated), [live]);
  const [picked, setPicked] = useState<string | null>(null);
  // Follow whatever is being written right now, until the user picks an item themselves.
  const newest = items.filter((i) => !i.done).at(-1) ?? items.at(-1);
  const shown = (picked && live[picked]) || newest;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!shown?.done) end.current?.scrollIntoView({ block: "end" });
  }, [shown?.text, shown?.done]);

  const activity = events
    .filter((e) => (e.type === "progress" && e.text) || (e.type === "node" && e.ledger?.length))
    .slice(-6)
    .map((e) => (e.type === "progress" ? e.text : e.ledger?.[0]?.action) ?? "");

  if (!items.length)
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div>
          <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-ink-700 border-t-signal-400" />
          <p className="font-display text-lg text-ink-100">{running ? "The Architect is reading your request" : "Nothing was written live in this version"}</p>
          <p className="mt-1 text-sm text-ink-400">{running ? "The design, the tests and the code appear here as they are written." : "Open Code to read the files."}</p>
        </div>
      </div>
    );

  // Show the code itself: the reply's wrapping (### FILE headers, ``` fences, RATIONALE) is left out.
  const lines = (shown?.text ?? "").split("\n").filter((l) => !/^(```|### FILE:|RATIONALE:|DIAGNOSIS:)/.test(l.trim()));
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1">
        <ol className="w-64 shrink-0 space-y-0.5 overflow-y-auto border-r border-ink-800 p-2" aria-label="What the agents are writing">
          {items.map((item) => (
            <li key={item.label}>
              <button
                onClick={() => setPicked(item.label)}
                className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left ${shown?.label === item.label ? "bg-ink-800" : "hover:bg-ink-900"}`}
              >
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.done ? "bg-emerald-400" : "animate-pulse bg-signal-400"}`} />
                <span className="min-w-0">
                  <span className="block truncate font-mono text-xs text-ink-100">{item.label}</span>
                  <span className="block text-[11px] text-ink-500">{AGENT_NAME[item.agent] ?? item.agent} · {item.done ? "done" : "writing…"}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
        <div className="min-w-0 flex-1 overflow-auto bg-ink-950">
          <div className="sticky top-0 flex items-center gap-2 border-b border-ink-800 bg-ink-950/95 px-4 py-2 text-xs">
            <span className="font-mono text-ink-200">{shown?.label}</span>
            <span className="text-ink-500">{lines.length} lines</span>
            {!shown?.done && <span className="ml-auto text-signal-300">{AGENT_NAME[shown?.agent ?? ""] ?? "An agent"} is writing</span>}
          </div>
          <pre className="px-0 py-2 font-mono text-[12.5px] leading-5 text-ink-100">
            {lines.map((line, i) => (
              <div key={i} className="flex">
                <span className="w-12 shrink-0 select-none pr-3 text-right text-ink-600">{i + 1}</span>
                <span className="whitespace-pre-wrap break-all pr-4">{line || " "}</span>
              </div>
            ))}
            {!shown?.done && <span className="ml-12 inline-block h-4 w-2 animate-pulse bg-signal-400 align-middle" />}
          </pre>
          <div ref={end} />
        </div>
      </div>
      {activity.length > 0 && (
        <ol className="space-y-0.5 border-t border-ink-800 bg-ink-900/60 px-4 py-2 font-mono text-xs text-ink-400" aria-label="Recent agent activity">
          {activity.map((text, i) => (
            <li key={i} className={i === activity.length - 1 && running ? "text-ink-100" : ""}>{text}</li>
          ))}
        </ol>
      )}
    </div>
  );
}
