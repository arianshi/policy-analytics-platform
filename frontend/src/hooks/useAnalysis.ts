"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { TERMINAL, type AnalysisRecord } from "@/lib/types";

interface State { id: string | null; record: AnalysisRecord | null; error: string | null }

/** Loads the analysis record; refreshes when the stream reports completion and polls slowly as a safety net. */
export function useAnalysis(id: string | null, streamDone: boolean) {
  const [state, setState] = useState<State>({ id: null, record: null, error: null });

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const record = await api.getAnalysis(id);
      setState({ id, record, error: null });
    } catch (e) {
      setState((s) => ({ id, record: s.id === id ? s.record : null, error: e instanceof ApiError ? e.message : "Failed to load the analysis" }));
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (streamDone) void load(); }, [streamDone, load]);

  const current = state.id === id ? state : { id, record: null, error: null };
  const terminal = current.record ? TERMINAL.includes(current.record.status) : false;
  useEffect(() => {
    if (!id || terminal) return;
    const t = setInterval(() => void load(), 3000);
    return () => clearInterval(t);
  }, [id, terminal, load]);

  return { record: current.record, error: current.error, reload: load };
}
