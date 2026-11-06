"""Decision policies: what a competent agent does given its state.

Each ``decide_*`` is a pure function ``state -> {"thought", "action"|"final"}``. They are used (1) by the offline
provider as its answer to ``react_step`` requests and (2) as the guardrail when a live LLM produces an invalid or
looping action - so a workflow always makes progress. Real LLMs receive the same ``state`` and are free to deviate.
"""

from __future__ import annotations

from typing import Any


def act(thought: str, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"thought": thought, "action": {"tool": tool, "args": args or {}}}


def finish(thought: str, **final: Any) -> dict[str, Any]:
    return {"thought": thought, "final": final}


def decide_coordinator(s: dict[str, Any]) -> dict[str, Any]:
    if s["intent"] is None:
        return act("First I need to turn the free-text question into a structured intent (sectors, measures, period).", "interpret_query")
    if s["needs_clarification"]:
        if s.get("clarified"):
            return finish("Waiting for the user to clarify.", status="needs_clarification")
        return act("The question is too ambiguous to analyse reliably, so I will ask for clarification instead of guessing.", "request_clarification",
                   {"question": s["clarification"]})
    if not s["plan"]:
        return act("Next I plan the workflow: which datasets are needed and which analyses to run.", "plan_workflow")
    ex = s["extraction"]
    if ex is None:
        return act(f"Plan ready ({s['plan']['required']} required + {s['plan']['optional']} optional datasets). Delegating data acquisition to the Data Extraction agent.",
                   "delegate_extraction")
    if ex["status"] == "failed":
        return finish("Required data could not be obtained from any source, so the analysis cannot proceed.", status="failed", reason=ex.get("reason", "data unavailable"))
    if ex["skipped"] and not s["replanned"]:
        return act(f"{len(ex['skipped'])} optional dataset(s) were unavailable ({', '.join(ex['skipped'])}). Re-planning without the analyses that depended on them.",
                   "replan", {"reason": "optional datasets unavailable"})
    if s["analytics"] is None:
        return act("Data is validated and stored. Delegating statistical analysis to the Analytics agent.", "delegate_analytics")
    if s["analytics"]["status"] == "failed":
        return finish("The analytics agent produced no usable findings.", status="failed", reason="no analysable data for the requested scope")
    if s["reporting"] is None:
        return act(f"Analytics produced {s['analytics']['facts']} facts. Delegating narrative and report assembly (with hallucination checks) to the Reporter agent.",
                   "delegate_reporting")
    degraded = bool(ex["degraded"] or ex["skipped"] or s["analytics"]["status"] == "partial" or s["reporting"].get("template_fallback"))
    return finish("All stages are complete." + (" Some inputs were degraded; this is disclosed in the report." if degraded else ""),
                  status="degraded" if degraded else "completed")


def decide_extraction(s: dict[str, Any]) -> dict[str, Any]:
    if not s["searched"]:
        return act("I'll consult the dataset catalog to confirm the needed datasets exist and see if anything else is relevant.", "search_catalog",
                   {"query": s["need_text"]})
    if s["pending_fetch"]:
        return act(f"Fetching {len(s['pending_fetch'])} dataset(s) concurrently from their live sources.", "fetch_datasets", {"keys": s["pending_fetch"]})
    failed = s["failed"]
    if failed:
        retry = [k for k, f in failed.items() if f["retryable"] and f["attempts"] < 2 and f["code"] != "schema"]
        if retry:
            return act(f"{', '.join(retry)} failed with a transient error ({failed[retry[0]]['code']}). Retrying once after a short pause.", "fetch_datasets", {"keys": retry})
        snap = [k for k, f in failed.items() if f["has_snapshot"]]
        if snap:
            why = failed[snap[0]]["code"]
            return act(f"Live access to {', '.join(snap)} is not working ({why}). A bundled snapshot of the real data exists, so I'll fall back to it and flag the result as degraded.",
                       "use_snapshot", {"keys": snap})
        skip = [k for k, f in failed.items() if not f["required"]]
        if skip:
            return act(f"{', '.join(skip)} is optional and has no fallback; skipping it and noting the limitation.", "skip_datasets", {"keys": skip, "reason": "unavailable, no fallback"})
        return finish("A required dataset is unavailable and no fallback exists.", status="failed", reason="; ".join(f"{k}: {f['message']}" for k, f in failed.items()))
    if s["to_validate"]:
        return act(f"Validating and cleaning {len(s['to_validate'])} dataset(s): schema check, type coercion, duplicates, ranges, outliers.", "validate_and_clean",
                   {"keys": s["to_validate"]})
    if s["to_store"]:
        return act("Persisting the cleaned datasets to the database so results are reproducible.", "store_datasets", {"keys": s["to_store"]})
    return finish("All datasets are fetched, validated and stored.", status=s["final_status"], datasets=len(s["stored"]), degraded=s["degraded"], skipped=s["skipped"])


def decide_analytics(s: dict[str, Any]) -> dict[str, Any]:
    if not s["scope_checked"]:
        return act("Before analysing, I check what each dataset actually covers so I don't promise results the data can't support.", "check_scope")
    if s["todo"]:
        step = s["todo"][0]
        return act(f"{step['why'].capitalize()}.", step["tool"], step["args"])
    return finish("All planned analyses are done.", status=s["final_status"])


def decide_reporter(s: dict[str, Any]) -> dict[str, Any]:
    if s["draft"] is None:
        return act("Drafting findings and implications from the computed facts (the LLM may only narrate facts, not create numbers).", "draft_findings")
    if s["verified"] is None:
        return act("Checking the draft against the facts: every number, citation, year and source name must be grounded.", "verify_draft")
    if not s["verified"]["passed"]:
        if s["revisions"] == 0:
            return act(f"Verification found {s['verified']['errors']} problem(s). Asking the model to revise with the specific failures as feedback.", "revise_draft", {"mode": "llm"})
        if not s["template_used"]:
            return act("The revision still failed verification. Replacing the narrative with the rule-based draft built directly from facts.", "revise_draft", {"mode": "template"})
    if not s["composed"]:
        return act("Composing the executive summary and recommendations, then assembling the cited report.", "compose_report")
    return finish("Report assembled and verified.", status="ok", template_fallback=s["template_used"])


POLICIES = {"coordinator": decide_coordinator, "extraction": decide_extraction, "analytics": decide_analytics, "reporter": decide_reporter}
