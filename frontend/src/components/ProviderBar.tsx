import type { ProviderStatus } from "@/lib/types";

export function ProviderBar({ providers }: { providers: ProviderStatus[] }) {
  return (
    <ul className="providers" aria-label="LLM providers">
      {providers.map((p) => (
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
