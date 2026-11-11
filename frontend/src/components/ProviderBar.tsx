import type { ProviderStatus } from "@/lib/types";

// Production builds set NEXT_PUBLIC_HIDE_OFFLINE=true: the rule-based fallback still runs, but is not advertised in the header.
const HIDE_OFFLINE = process.env.NEXT_PUBLIC_HIDE_OFFLINE === "true";

export function ProviderBar({ providers, hideOffline = HIDE_OFFLINE }: { providers: ProviderStatus[]; hideOffline?: boolean }) {
  const shown = hideOffline ? providers.filter((p) => p.name !== "offline") : providers;
  return (
    <ul className="providers" aria-label="LLM providers">
      {shown.map((p) => (
        <li
          key={p.name}
          className={`prov ${p.configured ? (p.circuit === "closed" ? "ok" : "warn") : "off"}`}
          title={`${p.model} · ${p.calls} calls · ${p.failures} failures · circuit ${p.circuit}${p.last_error ? ` · last error: ${p.last_error}` : ""}`}
        >
          <span className="dot" aria-hidden />
          {p.name}
          <span className="sr-only">{p.configured ? `configured, circuit ${p.circuit}` : "not configured"}</span>
        </li>
      ))}
    </ul>
  );
}
