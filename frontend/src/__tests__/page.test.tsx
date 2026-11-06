import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { api, ApiError } from "@/lib/api";
import { record, result } from "./fixtures";

class FakeWS {
  onopen: (() => void) | null = null; onmessage: ((m: { data: string }) => void) | null = null; onerror: (() => void) | null = null; onclose: (() => void) | null = null;
  close() {} constructor(public url: string) {}
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeWS);
  vi.spyOn(api, "samples").mockResolvedValue({ queries: ["Analyse employment trends in the technology sector from 2020-2024"] });
  vi.spyOn(api, "providers").mockResolvedValue({ providers: [{ name: "offline", model: "offline", configured: true, circuit: "closed", calls: 0, failures: 0, skipped: 0, cost_usd: 0, avg_latency_ms: 0, last_error: "" }] });
  vi.spyOn(api, "listAnalyses").mockResolvedValue({ items: [] });
  vi.spyOn(api, "getEvents").mockResolvedValue({ events: [] });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Home page", () => {
  it("submits a question, then shows the finished report", async () => {
    const create = vi.spyOn(api, "createAnalysis").mockResolvedValue({ id: "a1", status: "queued" });
    vi.spyOn(api, "getAnalysis").mockResolvedValue(record());
    render(<Home />);
    await screen.findByText("offline");
    await userEvent.click(await screen.findByRole("button", { name: /analyse employment trends in the technology/i }));
    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(create).toHaveBeenCalledWith("Analyse employment trends in the technology sector from 2020-2024", []);
    expect(await screen.findByRole("heading", { name: /ICT: employment trends, 2020-2024/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /agent activity/i })).toBeInTheDocument();
    expect(screen.getByText("Completed")).toBeInTheDocument();
  });

  it("explains rate limiting in plain language", async () => {
    vi.spyOn(api, "createAnalysis").mockRejectedValue(new ApiError(429, "rate_limited", "Too many analyses"));
    render(<Home />);
    await userEvent.type(await screen.findByLabelText(/research question/i), "some valid question");
    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/too quickly/i);
  });

  it("asks for clarification instead of guessing and lets the user pick a suggestion", async () => {
    const create = vi.spyOn(api, "createAnalysis").mockResolvedValueOnce({ id: "a1", status: "queued" }).mockResolvedValueOnce({ id: "a2", status: "queued" });
    vi.spyOn(api, "getAnalysis").mockResolvedValue(record({ status: "needs_clarification", result: result({ status: "needs_clarification", report: null,
      clarification: { question: "Which sector should I analyse?", suggestions: ["Analyse tech employment 2020-2024"] } }) }));
    render(<Home />);
    await userEvent.type(await screen.findByLabelText(/research question/i), "tell me something");
    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(await screen.findByText("Which sector should I analyse?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Analyse tech employment 2020-2024" }));
    await waitFor(() => expect(create).toHaveBeenLastCalledWith("Analyse tech employment 2020-2024", []));
  });

  it("surfaces failures and disclosed fallbacks", async () => {
    vi.spyOn(api, "createAnalysis").mockResolvedValue({ id: "a1", status: "queued" });
    vi.spyOn(api, "getAnalysis").mockResolvedValue(record({ status: "failed", error: "Required data unavailable", result: result({ status: "failed", report: null }) }));
    render(<Home />);
    await userEvent.type(await screen.findByLabelText(/research question/i), "some valid question");
    await userEvent.click(screen.getByRole("button", { name: /run analysis/i }));
    expect(await screen.findByText(/analysis failed: required data unavailable/i)).toBeInTheDocument();
  });

  it("reports an unreachable backend", async () => {
    vi.spyOn(api, "listAnalyses").mockRejectedValue(new ApiError(0, "network_error", "Cannot reach the API at http://localhost:8000. Is the backend running?"));
    render(<Home />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot reach the api/i);
  });
});
