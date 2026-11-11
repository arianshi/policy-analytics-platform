import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AgentTimeline, agentStates } from "@/components/AgentTimeline";
import { ChartCard } from "@/components/ChartCard";
import { FactRefs } from "@/components/FactRefs";
import { HistoryList } from "@/components/HistoryList";
import { ProviderBar } from "@/components/ProviderBar";
import { FAILURE_OPTIONS, QueryForm } from "@/components/QueryForm";
import { ResultTabs } from "@/components/ResultTabs";
import { ev, fact, record, result } from "./fixtures";

describe("QueryForm", () => {
  it("disables submit until the question is long enough and passes trimmed text", async () => {
    const onSubmit = vi.fn();
    render(<QueryForm busy={false} samples={["Sample question about tech"]} onSubmit={onSubmit} />);
    const run = screen.getByRole("button", { name: /run analysis/i });
    expect(run).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/research question/i), "  hi there  ");
    expect(run).toBeEnabled();
    await userEvent.click(run);
    expect(onSubmit).toHaveBeenCalledWith("hi there", []);
  });
  it("fills the textarea from a sample chip and sends the chosen simulated failure", async () => {
    const onSubmit = vi.fn();
    render(<QueryForm busy={false} samples={["Analyse tech employment"]} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: /analyse tech employment/i }));
    expect(screen.getByLabelText(/research question/i)).toHaveValue("Analyse tech employment");
    await userEvent.click(screen.getByText(/simulate a failure/i));
    await userEvent.selectOptions(screen.getByLabelText(/inject a fault/i), FAILURE_OPTIONS[1]!.value);
    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(onSubmit).toHaveBeenCalledWith("Analyse tech employment", ["source:mom:rate_limited"]);
  });
  it("is locked while an analysis is running", () => {
    render(<QueryForm busy samples={[]} onSubmit={vi.fn()} initialQuery="valid question" />);
    expect(screen.getByRole("button", { name: /analysing/i })).toBeDisabled();
  });
});

describe("AgentTimeline", () => {
  const events = [
    ev(1, "coordinator", "thought", "Plan the workflow"),
    ev(2, "coordinator", "delegation", "Delegating to the Data Extraction agent"),
    ev(3, "extraction", "action", "fetch_datasets(keys=[...])", { args: { keys: ["mom.job_vacancies"] } }),
    ev(4, "extraction", "observation", "fetch_datasets failed: rate_limited", { ok: false }),
    ev(5, "extraction", "llm", "react_step answered by openai"),
    ev(6, "extraction", "warning", "using bundled snapshot"),
  ];
  it("derives agent states", () => {
    const s = agentStates(events, true);
    expect(s).toMatchObject({ coordinator: "idle", extraction: "active", analytics: "idle", reporter: "idle" });
    expect(agentStates([...events, ev(7, "extraction", "result", "done")], true).extraction).toBe("done");
  });
  it("renders events, hides LLM calls by default and filters by agent", async () => {
    render(<AgentTimeline events={events} running transport="ws" />);
    const list = screen.getByTestId("events");
    expect(within(list).getByText(/Plan the workflow/)).toBeInTheDocument();
    expect(within(list).queryByText(/answered by openai/)).not.toBeInTheDocument();
    expect(within(list).getByText(/mom.job_vacancies/)).toBeInTheDocument();
    expect(list.querySelector(".failed")).not.toBeNull();
    await userEvent.click(screen.getByLabelText(/show llm calls/i));
    expect(within(list).getByText(/answered by openai/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /extraction/i }));
    expect(within(list).queryByText(/Plan the workflow/)).not.toBeInTheDocument();
    expect(screen.getByText("● live")).toBeInTheDocument();
  });
  it("shows a waiting message before the first event", () => {
    render(<AgentTimeline events={[]} running />);
    expect(screen.getByText(/waiting for the first agent step/i)).toBeInTheDocument();
  });
});

describe("FactRefs", () => {
  it("reveals the exact computed statement, caveats and sources on click", async () => {
    render(<FactRefs ids={["F1", "F9"]} facts={[fact("F1")]} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /F1/ }));
    const note = screen.getByRole("note");
    expect(within(note).getByText(/111,600 in 2020/)).toBeInTheDocument();
    expect(within(note).getByText(/Residents only/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /F1/ }));
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
  it("renders nothing without ids", () => {
    const { container } = render(<FactRefs ids={[]} facts={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("HistoryList", () => {
  const items = [
    {
      id: "a1",
      query: "q1",
      title: "ICT trends",
      status: "degraded" as const,
      error: null,
      facts: 37,
      duration_ms: 1,
      created_at: "2026-10-30T11:00:00Z",
      completed_at: null,
    },
  ];
  it("selects and deletes analyses", async () => {
    const onSelect = vi.fn();
    const onDelete = vi.fn();
    render(
      <HistoryList
        items={items}
        selected="a1"
        onSelect={onSelect}
        onDelete={onDelete}
        now={new Date("2026-10-30T12:00:00Z").getTime()}
      />,
    );
    expect(screen.getByText("Completed with fallbacks")).toBeInTheDocument();
    expect(screen.getByText(/1 h ago · 37 facts/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("ICT trends"));
    expect(onSelect).toHaveBeenCalledWith("a1");
    await userEvent.click(screen.getByRole("button", { name: /delete analysis: ICT trends/i }));
    expect(onDelete).toHaveBeenCalledWith("a1");
  });
  it("shows an empty state", () => {
    render(<HistoryList items={[]} selected={null} onSelect={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText(/no analyses yet/i)).toBeInTheDocument();
  });
});

describe("ProviderBar", () => {
  it("distinguishes configured, tripped and unconfigured providers accessibly", () => {
    const base = { model: "m", calls: 0, failures: 0, skipped: 0, cost_usd: 0, avg_latency_ms: 0, last_error: "" };
    render(
      <ProviderBar
        providers={[
          { ...base, name: "openai", configured: true, circuit: "open" },
          { ...base, name: "gemini", configured: false, circuit: "closed" },
          { ...base, name: "offline", configured: true, circuit: "closed" },
        ]}
      />,
    );
    expect(screen.getByText(/configured, circuit open/)).toBeInTheDocument();
    expect(screen.getByText("not configured")).toBeInTheDocument();
    expect(screen.getByText("openai").closest("li")).toHaveClass("warn");
    expect(screen.getByText("offline").closest("li")).toHaveClass("ok");
  });
});

describe("ChartCard", () => {
  it("renders the title, source note and an accessible data table", async () => {
    render(<ChartCard spec={result().charts[0]!} />);
    expect(screen.getByRole("figure", { name: "Employed residents" })).toBeInTheDocument();
    expect(screen.getByText(/Source: mom.employed_residents/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/view data table/i));
    expect(screen.getByRole("cell", { name: "132,900" })).toBeInTheDocument();
  });
});

describe("ResultTabs", () => {
  const r = record().result!;
  it("shows the summary with assumptions, findings and fact citations", () => {
    render(<ResultTabs id="a1" result={r} />);
    expect(screen.getByRole("heading", { name: /ICT: employment trends/ })).toBeInTheDocument();
    expect(screen.getByText(/assumed the latest 5 years/)).toBeInTheDocument();
    expect(screen.getByText("Employed residents rose 24.7%.")).toBeInTheDocument();
  });
  it("switches between tabs and exposes exports", async () => {
    render(<ResultTabs id="a1" result={r} />);
    expect(screen.getByRole("link", { name: "MD" })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/analyses/a1/export?format=md"),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Data quality" }));
    expect(screen.getByText(/5,376 → 224 rows/)).toBeInTheDocument();
    expect(screen.getByText(/1 suppressed value/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Sources" }));
    expect(screen.getByText("fallback snapshot")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Trust & cost" }));
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText(/openai ×12, offline ×8/)).toBeInTheDocument();
    expect(screen.getByText("$0.0123")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Facts" }));
    await userEvent.selectOptions(screen.getByLabelText(/filter by kind/i), "comparison");
    expect(screen.queryByText(/Statement for F1/)).not.toBeInTheDocument();
    expect(screen.getByText(/Statement for F2/)).toBeInTheDocument();
  });
  it("flags MOCK facts", async () => {
    render(<ResultTabs id="a1" result={result({ facts: [fact("F7", { mock: true })] })} />);
    await userEvent.click(screen.getByRole("tab", { name: "Facts" }));
    expect(screen.getByText("MOCK")).toBeInTheDocument();
  });
});
