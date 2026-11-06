"""The ReAct loop's failure handling, tested with a minimal agent and scripted LLMs."""

import json

from app.agents.base import ReActAgent, ToolError
from app.agents.policies import act, finish
from tests.conftest import ScriptedProvider, make_ctx


class Demo(ReActAgent):
    name = "demo"

    def __init__(self, ctx, **kw):
        super().__init__(ctx, **kw)
        self.log = []
        self.allow_finish = True
        self.add_tool("echo", "echo", "text: str", self._echo)
        self.add_tool("boom", "crashes", "", self._boom)
        self.add_tool("bad", "tool error", "", self._bad)

    async def _echo(self, args):
        self.log.append(args)
        return {"summary": f"echoed {args}"}

    async def _boom(self, args):
        raise RuntimeError("kaboom")

    async def _bad(self, args):
        raise ToolError("bad arguments")

    def goal(self):
        return "demo"

    def state(self):
        return {"calls": len(self.log)}

    def policy(self, state):
        return finish("policy says done", status="ok") if self.log else act("policy acts", "echo", {"text": "from-policy"})

    def can_finish(self, state, final):
        return None if self.allow_finish else "not yet"


def llm_says(*decisions):
    it = iter(decisions)
    return lambda req: json.dumps(next(it))


def kinds(ctx):
    return [e[1] for e in ctx.events]


async def test_normal_react_cycle(settings):
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", llm_says(
        {"thought": "t1", "action": {"tool": "echo", "args": {"text": "hi"}}}, {"thought": "t2", "final": {"status": "ok"}}))])
    agent = Demo(ctx)
    res = await agent.run()
    assert res.status == "ok" and res.steps == 2 and agent.log == [{"text": "hi"}]
    assert [k for k in kinds(ctx) if k in ("thought", "action", "observation", "result")] == ["thought", "action", "observation", "thought", "result"]


async def test_unknown_tool_is_rejected_and_policy_takes_over(settings):
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", llm_says({"thought": "x", "action": {"tool": "hack", "args": {}}}))])
    agent = Demo(ctx)
    res = await agent.run()
    assert res.status == "ok" and agent.log == [{"text": "from-policy"}]
    assert any("rule-based policy" in e[2] for e in ctx.events if e[1] == "warning")


async def test_malformed_json_falls_back_to_policy(settings):
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", lambda r: "I think we should echo something")])
    assert (await Demo(ctx).run()).status == "ok"


async def test_tool_exceptions_become_observations_not_crashes(settings):
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", llm_says(
        {"thought": "a", "action": {"tool": "boom", "args": {}}}, {"thought": "b", "action": {"tool": "bad", "args": {}}},
        {"thought": "c", "action": {"tool": "echo", "args": {"text": "recovered"}}}, {"thought": "d", "final": {"status": "ok"}}))])
    agent = Demo(ctx)
    res = await agent.run()
    assert res.status == "ok" and agent.log == [{"text": "recovered"}]
    obs = [e for e in ctx.events if e[1] == "observation"]
    assert obs[0][3]["ok"] is False and "kaboom" in obs[0][2] and "bad arguments" in obs[1][2]


async def test_looping_agent_is_overridden_by_policy(settings):
    same = {"thought": "again", "action": {"tool": "echo", "args": {"text": "x"}}}
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", lambda r: json.dumps(same))])
    agent = Demo(ctx)
    agent.policy = lambda s: finish("loop broken", status="ok")  # type: ignore[method-assign]
    res = await agent.run()
    assert res.status == "ok" and any("Loop detected" in e[2] for e in ctx.events)


async def test_premature_final_is_rejected(settings):
    ctx = make_ctx(settings, providers=[ScriptedProvider("s", lambda r: json.dumps({"thought": "done?", "final": {"status": "ok"}}))])
    agent = Demo(ctx)
    agent.allow_finish = False
    agent.policy = lambda s: finish("policy finish", status="ok")  # type: ignore[method-assign]
    res = await agent.run()
    assert res.status == "ok" and any("Cannot finish yet" in e[2] for e in ctx.events)


async def test_step_budget_yields_incomplete_not_a_hang(settings):
    n = {"i": 0}

    def varied(req):
        n["i"] += 1
        return json.dumps({"thought": "more", "action": {"tool": "echo", "args": {"text": str(n["i"])}}})

    ctx = make_ctx(settings, providers=[ScriptedProvider("s", varied)])
    res = await Demo(ctx, max_steps=3).run()
    assert res.status == "incomplete" and res.steps == 3 and res.error == "step budget exhausted"


async def test_prompt_contains_goal_tools_state_and_recent_steps(settings):
    p = ScriptedProvider("s", llm_says({"thought": "t", "action": {"tool": "echo", "args": {"text": "hi"}}}, {"thought": "t", "final": {"status": "ok"}}))
    await Demo(make_ctx(settings, providers=[p])).run()
    second = p.calls[1].prompt
    assert "GOAL: demo" in second and "- echo(text: str)" in second and "STATE:" in second and "observation: " in second
