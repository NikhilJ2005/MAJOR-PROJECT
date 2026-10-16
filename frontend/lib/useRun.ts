"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type RunEvent, type Snapshot } from "./api";

/** Follow one run: its snapshot, plus live events while it is running (Server-Sent Events). */
export function useRun(runId: string | null, onFinished?: (status: string) => void) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
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
      const first = await api.snapshot(runId).catch(() => null);
      if (closed) return;
      setSnap(first);
      if (!first || first.status !== "running") return;
      source = new EventSource(api.eventsUrl(runId, -1));
      const onEvent = (msg: MessageEvent) => {
        const ev = JSON.parse(msg.data) as RunEvent;
        if (ev.seq <= lastSeq.current) return;
        lastSeq.current = ev.seq;
        setEvents((prev) => [...prev, ev]);
        if (ev.type === "node" || ev.type === "error") refresh();
        if (ev.type === "status" && ev.status && ev.status !== "running") {
          source?.close();
          refresh();
          finished.current?.(ev.status);
        }
      };
      for (const t of ["node", "status", "error"]) source.addEventListener(t, onEvent as EventListener);
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

  return { snap, events, refresh };
}
