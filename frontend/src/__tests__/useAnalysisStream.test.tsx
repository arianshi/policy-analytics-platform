import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAnalysisStream } from "@/hooks/useAnalysisStream";
import { api } from "@/lib/api";
import { ev } from "./fixtures";

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  send(e: unknown) {
    this.onmessage?.({ data: JSON.stringify(e) });
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useAnalysisStream", () => {
  it("collects live events in order, ignores duplicates and pings, and reports completion", async () => {
    const { result } = renderHook(() => useAnalysisStream("a1"));
    const ws = FakeWS.instances[0]!;
    expect(ws.url).toMatch(/ws:\/\/.+\/ws\/analyses\/a1/);
    act(() => {
      ws.onopen?.();
      ws.send(ev(1, "coordinator", "thought", "plan"));
      ws.send(ev(1, "coordinator", "thought", "plan"));
      ws.send({ ...ev(2, "x", "ping", "") });
      ws.send(ev(3, "extraction", "action", "fetch"));
    });
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 3]);
    expect(result.current.transport).toBe("ws");
    expect(result.current.done).toBe(false);
    act(() => ws.send(ev(4, "system", "done", "finished", { status: "completed" })));
    expect(result.current.done).toBe(true);
  });

  it("falls back to polling when the socket drops mid-run, resuming after the last seen event", async () => {
    const getEvents = vi
      .spyOn(api, "getEvents")
      .mockResolvedValueOnce({ events: [ev(2, "analytics", "thought", "t")] })
      .mockResolvedValueOnce({ events: [ev(3, "system", "done", "d")] });
    const { result } = renderHook(() => useAnalysisStream("a1"));
    const ws = FakeWS.instances[0]!;
    act(() => ws.send(ev(1, "coordinator", "thought", "t")));
    act(() => ws.close());
    await waitFor(() => expect(result.current.done).toBe(true), { timeout: 4000 });
    expect(getEvents).toHaveBeenNthCalledWith(1, "a1", 1);
    expect(getEvents).toHaveBeenNthCalledWith(2, "a1", 2);
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(result.current.transport).toBe("polling");
  });

  it("never shows another analysis' events after the id changes, and closes the old socket", () => {
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useAnalysisStream(id), {
      initialProps: { id: "a1" as string | null },
    });
    act(() => FakeWS.instances[0]!.send(ev(1, "coordinator", "thought", "old")));
    expect(result.current.events).toHaveLength(1);
    rerender({ id: "a2" });
    expect(FakeWS.instances[0]!.closed).toBe(true);
    expect(result.current.events).toHaveLength(0);
    rerender({ id: null });
    expect(result.current.transport).toBe("idle");
  });
});
