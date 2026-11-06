import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { pivotSeries } from "@/lib/charts";
import { compactNumber, formatCost, formatDuration, timeAgo } from "@/lib/format";

afterEach(() => vi.restoreAllMocks());

describe("format", () => {
  it("formats durations, costs and numbers", () => {
    expect(formatDuration(420)).toBe("420 ms");
    expect(formatDuration(18587)).toBe("18.6 s");
    expect(formatDuration(null)).toBe("-");
    expect(formatCost(0)).toBe("$0.00");
    expect(formatCost(0.0034)).toBe("$0.0034");
    expect(formatCost(1.5)).toBe("$1.50");
    expect(compactNumber(139200)).toBe("139k");
    expect(compactNumber(2_500_000)).toBe("2.5M");
    expect(compactNumber(5.67)).toBe("5.7");
    expect(compactNumber(250)).toBe("250");
  });
  it("renders relative times", () => {
    const now = new Date("2026-10-30T12:00:00Z").getTime();
    expect(timeAgo("2026-10-30T11:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-10-30T11:30:00Z", now)).toBe("30 min ago");
    expect(timeAgo("2026-10-30T09:00:00Z", now)).toBe("3 h ago");
    expect(timeAgo("2026-10-28T12:00:00Z", now)).toBe("2 d ago");
  });
});

describe("pivotSeries", () => {
  it("merges series by x, sorts numerically and tracks flagged points", () => {
    const { rows, flagged } = pivotSeries([
      { name: "a", kind: "line", data: [{ x: 2022, y: 3, flag: true }, { x: 2020, y: 1 }] },
      { name: "b", kind: "line", data: [{ x: 2020, y: 10 }, { x: 2021, y: 20 }] },
    ]);
    expect(rows).toEqual([{ x: 2020, a: 1, b: 10 }, { x: 2021, b: 20 }, { x: 2022, a: 3 }]);
    expect(flagged.has("a|2022")).toBe(true);
    expect(flagged.size).toBe(1);
  });
  it("sorts categorical x values alphabetically", () => {
    expect(pivotSeries([{ name: "s", kind: "bar", data: [{ x: "B", y: 1 }, { x: "A", y: 2 }] }]).rows.map((r) => r.x)).toEqual(["A", "B"]);
  });
});

describe("api client", () => {
  const mockFetch = (res: Partial<Response> & { json?: () => Promise<unknown> }) => vi.spyOn(globalThis, "fetch").mockResolvedValue(res as Response);

  it("posts analyses with the simulated failures", async () => {
    const spy = mockFetch({ ok: true, status: 202, json: async () => ({ id: "x", status: "queued" }) });
    expect(await api.createAnalysis("hello world", ["source:mom"])).toEqual({ id: "x", status: "queued" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/api\/analyses$/);
    expect(JSON.parse(init.body as string)).toEqual({ query: "hello world", simulate_failures: ["source:mom"] });
  });
  it("turns the error envelope into an ApiError", async () => {
    mockFetch({ ok: false, status: 422, json: async () => ({ error: { code: "validation_error", message: "Invalid request", details: [1] } }) });
    await expect(api.getAnalysis("x")).rejects.toMatchObject({ name: "ApiError", status: 422, code: "validation_error", message: "Invalid request" });
  });
  it("handles non-JSON error bodies and unreachable servers", async () => {
    mockFetch({ ok: false, status: 502, json: async () => { throw new Error("html"); } });
    await expect(api.listAnalyses()).rejects.toMatchObject({ status: 502, code: "http_error" });
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const err = await api.listAnalyses().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.code).toBe("network_error");
    expect(err.message).toMatch(/Cannot reach the API/);
  });
  it("returns undefined for 204 and builds export / websocket URLs", async () => {
    mockFetch({ ok: true, status: 204 });
    expect(await api.remove("x")).toBeUndefined();
    expect(api.exportUrl("abc", "md")).toMatch(/\/api\/analyses\/abc\/export\?format=md$/);
    expect(api.wsUrl("abc")).toMatch(/^ws:\/\/.+\/ws\/analyses\/abc$/);
  });
});
