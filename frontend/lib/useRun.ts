"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type RunEvent, type Snapshot } from "./api";

/** What a model is writing right now, one entry per file or document (e.g. "app/services/order.py"). */
export type LiveItem = { label: string; agent: string; text: string; done: boolean; updated: number };

/** Follow one run: its snapshot, plus live events while it is running (Server-Sent Events). */
export function useRun(runId: string | null, onFinished?: (status: string) => void) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState<Record<string, LiveItem>>({});
  const lastSeq = useRef(-1);
  const finished = useRef(onFinished);
  useEffect(() => {
    finished.current = onFinished;
  }, [onFinished]);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      setSnap(await api.snapshot(runId));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setSnap(null);
    }
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let source: EventSource | null = null;
    let closed = false;
    lastSeq.current = -1;
    (async () => {
      setEvents([]);
      setLive({});
      const first = await api.snapshot(runId).catch(() => null);
      if (closed) return;
      setSnap(first);
      // A run waiting at the design review is still going: keep listening, it continues after approval.
      if (!first || (first.status !== "running" && first.status !== "awaiting_approval")) return;
      source = new EventSource(api.eventsUrl(runId, -1));
      const onEvent = (msg: MessageEvent) => {
        const ev = JSON.parse(msg.data) as RunEvent;
        if (ev.seq <= lastSeq.current) return;
        lastSeq.current = ev.seq;
        if (ev.type === "stream" && ev.label) {
          // Text arrives in pieces: append it to its item instead of keeping every piece as an event.
          const label = ev.label;
          setLive((prev) => {
            const item = prev[label];
            const text = (item && !item.done ? item.text : "") + (ev.delta ?? "");
            return { ...prev, [label]: { label, agent: ev.agent ?? "", text, done: !!ev.done, updated: ev.ts } };
          });
          return;
        }
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "node" || ev.type === "error") refresh();
        if (ev.type === "status" && ev.status === "stopping") return; // Stop was pressed: the final status follows
        if (ev.type === "status" && ev.status === "awaiting_approval") {
          refresh();
          return;
        }
        if (ev.type === "status" && ev.status && ev.status !== "running") {
          source?.close();
          refresh();
          finished.current?.(ev.status);
        }
      };
      for (const t of ["node", "status", "error", "progress", "stream"]) source.addEventListener(t, onEvent as EventListener);
      source.onerror = () => {
        source?.close(); // the server closes the stream when the run ends
        refresh();
      };
    })();
    return () => {
      closed = true;
      source?.close();
    };
  }, [runId, refresh]);

  return { snap, events, live, refresh };
}
