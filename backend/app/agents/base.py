"""ReAct agent loop: Reason (thought) -> Act (tool call) -> Observe (tool result), repeated until a final answer.

Failure handling built into the loop:
  * invalid / unknown actions from the LLM are rejected by the router's validator (-> next provider) and, if the LLM
    layer is exhausted, replaced by the agent's rule-based policy;
  * tool exceptions and timeouts become *observations* the agent can reason about, not crashes;
  * repeated identical actions (loops) and premature ``final`` answers are overridden by the policy;
  * a hard step budget turns runaway agents into an ``incomplete`` result instead of hanging.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..llm.router import parse_json_object
from ..llm.types import LLMError, LLMRequest
from .context import RunContext

log = logging.getLogger(__name__)
TOOL_TIMEOUT_S = 120.0
OBS_LIMIT = 1400


class ToolError(Exception):
    """A tool failed in an expected way; the message is shown to the agent as an observation."""


@dataclass
class Tool:
    name: str
    description: str
    params: str  # human/LLM-readable argument description
    fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class AgentResult:
    status: str  # ok | incomplete | failed (+ whatever the agent's final.status says)
    final: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    error: str = ""


class ReActAgent:
    name = "agent"
    role = ""

    def __init__(self, ctx: RunContext, *, max_steps: int | None = None):
        self.ctx = ctx
        self.max_steps = max_steps or ctx.settings.agent_max_steps
        self.tools: dict[str, Tool] = {}
        self.scratch: list[dict[str, Any]] = []
        self.premature_finals = 0
        self.llm_unavailable = False

    # --- to override -------------------------------------------------------
    def goal(self) -> str:
        raise NotImplementedError

    def state(self) -> dict[str, Any]:
        raise NotImplementedError

    def policy(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def can_finish(self, state: dict[str, Any], final: dict[str, Any]) -> str | None:
        """Return a reason string if the agent must NOT finish yet."""
        return None

    # --- helpers -------------------------------------------------------------
    def emit(self, type_: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.ctx.emit(self.name, type_, message, data)

    def add_tool(self, name: str, description: str, params: str, fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> None:
        self.tools[name] = Tool(name, description, params, fn)

    def _system(self) -> str:
        return (f"You are the {self.name} agent in a multi-agent policy-analytics system. {self.role}\n"
                "Use the ReAct pattern: reason about the current STATE, then take exactly ONE action. "
                'Reply ONLY with JSON: {"thought": "<short reasoning>", "action": {"tool": "<name>", "args": {...}}} '
                'or, when the goal is complete, {"thought": "...", "final": {...}}.')

    def _prompt(self, state: dict[str, Any]) -> str:
        tools = "\n".join(f"- {t.name}({t.params}): {t.description}" for t in self.tools.values())
        recent = "\n".join(f"[{i}] thought: {e['thought']}\n    action: {e['tool']}({json.dumps(e['args'])[:160]})\n    observation: {e['obs'][:400]}"
                           for i, e in enumerate(self.scratch[-4:], max(1, len(self.scratch) - 3)))
        return f"GOAL: {self.goal()}\nTOOLS:\n{tools}\nSTATE:\n{json.dumps(state, default=str)[:3000]}\nRECENT STEPS:\n{recent or '(none yet)'}"

    def _parse_decision(self, text: str) -> dict[str, Any]:
        obj = parse_json_object(text)
        if not isinstance(obj.get("thought"), str):
            raise ValueError("missing 'thought'")
        if "final" in obj:
            if not isinstance(obj["final"], dict):
                raise ValueError("'final' must be an object")
            return obj
        act = obj.get("action")
        if not isinstance(act, dict) or act.get("tool") not in self.tools:
            raise ValueError(f"unknown tool {act.get('tool') if isinstance(act, dict) else act!r}; valid: {list(self.tools)}")
        if not isinstance(act.get("args", {}), dict):
            raise ValueError("'args' must be an object")
        act.setdefault("args", {})
        return obj

    async def _next_action(self, state: dict[str, Any]) -> dict[str, Any]:
        if self.llm_unavailable:
            return self.policy(state)
        req = LLMRequest(task="react_step", system=self._system(), prompt=self._prompt(state), max_tokens=500,
                         context={"agent": self.name, "state": state}, validator=self._parse_decision)
        try:
            resp = await self.ctx.llm(self.name, req)
            return resp.parsed
        except LLMError as exc:
            self.llm_unavailable = True
            self.ctx.warn(self.name, f"No LLM provider could plan the next step ({exc}); using the rule-based policy for the rest of this task")
            return self.policy(state)

    # --- main loop -----------------------------------------------------------
    async def run(self) -> AgentResult:
        last_sig, repeats = "", 0
        for step in range(1, self.max_steps + 1):
            state = self.state()
            decision = await self._next_action(state)
            thought = decision.get("thought", "")
            self.emit("thought", thought, {"step": step})

            if "final" in decision:
                reason = self.can_finish(state, decision["final"])
                if reason:
                    self.premature_finals += 1
                    self.emit("warning", f"Cannot finish yet: {reason}", {"step": step})
                    if self.premature_finals >= 2:
                        decision = self.policy(state)
                        if "final" in decision:
                            return AgentResult(decision["final"].get("status", "ok"), decision["final"], step)
                    else:
                        self.scratch.append({"thought": thought, "tool": "final", "args": {}, "obs": f"REJECTED: {reason}"})
                        continue
                else:
                    final = decision["final"]
                    self.emit("result", f"{self.name} finished: {final.get('status', 'ok')}", {"final": final, "steps": step})
                    return AgentResult(final.get("status", "ok"), final, step)

            action = decision["action"]
            tool, args = action["tool"], action.get("args", {})
            sig = tool + json.dumps(args, sort_keys=True, default=str)
            repeats = repeats + 1 if sig == last_sig else 0
            last_sig = sig
            if repeats >= 2:  # looping: hand control to the policy for this step
                self.emit("warning", f"Loop detected on {tool}; overriding with the rule-based policy", {"step": step})
                alt = self.policy(state)
                if "final" in alt:
                    return AgentResult(alt["final"].get("status", "ok"), alt["final"], step)
                tool, args = alt["action"]["tool"], alt["action"].get("args", {})
                repeats = 0
            self.emit("action", f"{tool}({_brief(args)})", {"tool": tool, "args": args, "step": step})
            obs, ok = await self._execute(tool, args)
            summary = json.dumps(obs, default=str)
            self.emit("observation", _observation_text(tool, obs, ok), {"tool": tool, "ok": ok, "result": json.loads(summary[:OBS_LIMIT]) if len(summary) <= OBS_LIMIT else {"truncated": summary[:OBS_LIMIT]}, "step": step})
            self.scratch.append({"thought": thought, "tool": tool, "args": args, "obs": summary[:OBS_LIMIT]})
        self.emit("warning", f"{self.name} hit its step budget ({self.max_steps}) without finishing", {})
        return AgentResult("incomplete", {"status": "incomplete"}, self.max_steps, "step budget exhausted")

    async def _execute(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        try:
            return await asyncio.wait_for(self.tools[tool].fn(args), timeout=TOOL_TIMEOUT_S), True
        except ToolError as exc:
            return {"error": str(exc)}, False
        except TimeoutError:
            return {"error": f"tool {tool} timed out after {TOOL_TIMEOUT_S:.0f}s"}, False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # unexpected bug in a tool: surface it to the agent, keep the workflow alive
            log.exception("tool %s crashed", tool)
            return {"error": f"{exc.__class__.__name__}: {exc}"}, False


def _brief(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={json.dumps(v, default=str)[:60]}" for k, v in args.items())


def _observation_text(tool: str, obs: dict[str, Any], ok: bool) -> str:
    if not ok:
        return f"{tool} failed: {obs.get('error', 'error')}"
    return obs.get("summary") or f"{tool} ok"
