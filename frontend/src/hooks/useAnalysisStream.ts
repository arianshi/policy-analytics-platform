"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AgentEvent } from "@/lib/types";

const POLL_MS = 1000;
type Transport = "ws" | "polling" | "idle";
interface State {
  id: string | null;
  events: AgentEvent[];
  done: boolean;
  transport: Transport;
}
const EMPTY: State = { id: null, events: [], done: false, transport: "idle" };

/**
 * Live agent events for one analysis. Uses the WebSocket; if it cannot connect or drops before the run
 * finishes, falls back to polling the events endpoint so the timeline never silently freezes.
 * State is keyed by analysis id, so switching analyses never shows another run's events.
 */
export function useAnalysisStream(id: string | null) {
  const [state, setState] = useState<State>(EMPTY);

  useEffect(() => {
    if (!id) return;
    let closed = false;
    let finished = false;
    let lastSeq = 0;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const update = (fn: (s: State) => State) => setState((prev) => fn(prev.id === id ? prev : { ...EMPTY, id }));
    const setTransport = (transport: Transport) => update((s) => ({ ...s, transport }));

    const accept = (ev: AgentEvent) => {
      if (ev.type === "ping" || ev.seq <= lastSeq) return;
      lastSeq = ev.seq;
      if (ev.type === "done") finished = true;
      update((s) => ({ ...s, events: [...s.events, ev], done: s.done || ev.type === "done" }));
    };

    const poll = async () => {
      if (closed || finished) return;
      try {
        const { events } = await api.getEvents(id, lastSeq);
        if (closed) return;
        setTransport("polling");
        events.forEach(accept);
      } catch {
        /* transient: retry on the next tick */
      }
      if (!closed && !finished) timer = setTimeout(poll, POLL_MS);
    };

    try {
      ws = new WebSocket(api.wsUrl(id));
      ws.onopen = () => setTransport("ws");
      ws.onmessage = (m) => {
        try {
          accept(JSON.parse(m.data as string) as AgentEvent);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onerror = () => {
        ws?.close();
      };
      ws.onclose = () => {
        if (!closed && !finished) void poll();
      };
    } catch {
      timer = setTimeout(poll, 0);
    }

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [id]);

  const current = state.id === id ? state : EMPTY;
  return { events: current.events, done: current.done, transport: id ? current.transport : "idle" };
}
