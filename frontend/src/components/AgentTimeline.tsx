"use client";

import { useEffect, useRef, useState } from "react";
import { AGENTS, type AgentEvent, type AgentName } from "@/lib/types";

export type AgentState = "idle" | "active" | "done";

export function agentStates(events: AgentEvent[], running: boolean): Record<AgentName, AgentState> {
  const out = Object.fromEntries(AGENTS.map((a) => [a, "idle"])) as Record<AgentName, AgentState>;
  for (const a of AGENTS) {
    const mine = events.filter((e) => e.agent === a);
    if (mine.some((e) => e.type === "result")) out[a] = "done";
    else if (mine.length && running) out[a] = "active";
    else if (mine.length) out[a] = "done";
  }
  // a delegating coordinator is idle while a sub-agent works
  if (running && AGENTS.slice(1).some((a) => out[a] === "active") && out.coordinator === "active") out.coordinator = "idle";
  return out;
}

const LABEL: Record<string, string> = {
  thought: "Thinks",
  action: "Acts",
  observation: "Observes",
  delegation: "Delegates",
  warning: "Warning",
  error: "Error",
  status: "Status",
  result: "Finished",
  llm: "LLM",
  done: "Done",
};

function Args({ data }: { data: Record<string, unknown> }) {
  const args = data.args as Record<string, unknown> | undefined;
  if (!args || !Object.keys(args).length) return null;
  const text = Object.entries(args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(", ");
  return (
    <code className="args" title={text}>
      {text.length > 140 ? `${text.slice(0, 138)}…` : text}
    </code>
  );
}

export function AgentTimeline({ events, running, transport }: { events: AgentEvent[]; running: boolean; transport?: string }) {
  const [showLlm, setShowLlm] = useState(false);
  const [only, setOnly] = useState<string>("all");
  const endRef = useRef<HTMLDivElement>(null);
  const states = agentStates(events, running);
  const visible = events.filter(
    (e) => (showLlm || e.type !== "llm") && e.type !== "ping" && (only === "all" || e.agent === only),
  );

  useEffect(() => {
    if (running) endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [events.length, running]);

  return (
    <section className="card timeline" aria-label="Agent activity">
      <div className="row between wrap">
        <h2>Agent activity</h2>
        <span className="muted small">{transport === "ws" ? "● live" : transport === "polling" ? "● polling" : ""}</span>
      </div>
      <ol className="agent-strip" aria-label="Agent status">
        {AGENTS.map((a) => (
          <li key={a}>
            <button
              type="button"
              className={`agent agent-${a} state-${states[a]} ${only === a ? "selected" : ""}`}
              onClick={() => setOnly(only === a ? "all" : a)}
              aria-pressed={only === a}
              title={`Show only ${a}`}
            >
              <span className="dot" aria-hidden />
              {a}
              <span className="sr-only"> {states[a]}</span>
            </button>
          </li>
        ))}
      </ol>
      <label className="small muted check">
        <input type="checkbox" checked={showLlm} onChange={(e) => setShowLlm(e.target.checked)} /> Show LLM calls
      </label>
      <ol className="events" data-testid="events">
        {visible.length === 0 && <li className="muted small">Waiting for the first agent step…</li>}
        {visible.map((e) => (
          <li
            key={e.seq}
            className={`event type-${e.type} agent-${e.agent} ${e.type === "observation" && e.data.ok === false ? "failed" : ""}`}
          >
            <span className="who">{e.agent}</span>
            <span className="kind">{LABEL[e.type] ?? e.type}</span>
            <span className="msg">
              {e.message}
              {e.type === "action" && <Args data={e.data} />}
            </span>
          </li>
        ))}
      </ol>
      <div ref={endRef} />
    </section>
  );
}
