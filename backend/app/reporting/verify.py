"""Hallucination detection: ground every claim in a narrative against the computed facts.

Checks (each yields an :class:`Issue`):
  * ``unknown_fact``      - a cited fact id does not exist
  * ``uncited_claim``     - a narrative item cites no fact at all
  * ``unsupported_number``- a number that cannot be matched (by type, scale and rounding) to the cited facts
  * ``unsupported_year``  - a year outside the data coverage, or (warning) not in the cited facts
  * ``unsupported_source``- an agency/dataset that was never used (e.g. citing IRAS or the IMF)
  * ``causal_claim``      - causal language where the evidence is descriptive/correlational (warning)
  * ``mock_unlabelled``   - a MOCK-data fact narrated without saying it is synthetic

An item with any *error* fails verification; warnings are reported but pass.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any

from ..analytics.facts import COUNT, MONEY_M, PCT, RATIO, YEAR, Fact, FactStore

_FACT_REF = re.compile(r"\[?\bF\d+\b\]?")
_NUM = re.compile(
    r"""(?P<money>S?\$\s*)?(?P<sign>[-+−–]?)\s?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
        (?:\s*(?P<unit>%|per\s?cent\b|percent(?:age)?(?:\s+points?)?\b|pp\b|k\b|thousand\b|million\b|billion\b|bn\b|m\b))?""",
    re.IGNORECASE | re.VERBOSE,
)
_OTHER_AGENCIES = re.compile(
    r"\b(IRAS|Inland Revenue|CPF Board|IMDA|MAS|Monetary Authority|URA|HDB|EDB|Economic Development Board|World Bank|IMF|OECD|"
    r"Ministry of Finance|MOF|Statista|Bloomberg|Reuters|LinkedIn|Glassdoor|Gartner|McKinsey)\b"
)
_CAUSAL = re.compile(
    r"\b(caused|causes|causing|because of|due to|as a result of|resulting from|driven by|led to|leading to|thanks to|attributable to|"
    r"proves?|proven|demonstrates? that)\b",
    re.IGNORECASE,
)
_MOCK_OK = re.compile(r"mock|synthetic|illustrative", re.IGNORECASE)


@dataclass
class Issue:
    severity: str  # error | warning | info
    code: str
    item: str
    text: str
    detail: str = ""


@dataclass
class VerificationResult:
    passed: bool
    items_checked: int
    numbers_checked: int
    grounding_score: float
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["error_count"] = len(self.errors)
        d["warning_count"] = sum(1 for i in self.issues if i.severity == "warning")
        return d

    def feedback(self, limit: int = 6) -> str:
        return "; ".join(f"[{i.code}] {i.item}: {i.text!r} {i.detail}".strip() for i in self.errors[:limit])


@dataclass
class VerifyContext:
    window: tuple[int, int]
    coverage: tuple[int, int]  # earliest/latest year present in the data (+ forecast horizon)


@dataclass
class Item:
    label: str
    text: str
    fact_ids: list[str]


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def _candidates(fact: Fact) -> list[tuple[float, str, bool]]:
    """(value, kind, derived) for a fact: its stated values plus simple derivations (differences, % changes)."""
    base = [(abs(fv.v), fv.kind, False) for fv in fact.values.values()]
    out = list(base)
    nums = [fv for fv in fact.values.values() if fv.kind not in (YEAR,)]
    for a, b in combinations(nums, 2):
        if a.kind == b.kind and a.kind in (COUNT, MONEY_M, RATIO):
            out.append((abs(a.v - b.v), a.kind, True))
            if a.v:
                out.append((abs((b.v - a.v) / a.v * 100.0), PCT, True))
                out.append((abs((a.v - b.v) / b.v * 100.0) if b.v else 0.0, PCT, True))
        if a.kind == b.kind == PCT:
            out.append((abs(a.v - b.v), PCT, True))
    return out


def _interpretations(n: float, d: int, unit: str | None, money: bool) -> list[tuple[float, set[str], float]]:
    half = 0.5 * 10 ** (-d)
    u = (unit or "").lower().replace(" ", "")
    if u.startswith(("%", "per", "pp")):  # %, percent, per cent, percentage points, pp
        return [(n, {PCT}, half)]
    if u in ("k", "thousand"):
        return [(n * 1e3, {COUNT}, half * 1e3), (n, {COUNT}, half)]  # "139.2k" persons or "S$236.0 thousand"
    if u in ("million", "m"):
        return [(n * 1e6, {COUNT}, half * 1e6), (n, {MONEY_M}, half)]
    if u in ("billion", "bn"):
        return [(n * 1e9, {COUNT}, half * 1e9), (n * 1e3, {MONEY_M}, half * 1e3)]
    return [(n, {COUNT, RATIO, MONEY_M, PCT}, half)]


def _matches(n: float, d: int, unit: str | None, money: bool, facts: list[Fact]) -> str | None:
    """Return 'direct', 'derived' or None."""
    interps = _interpretations(n, d, unit, money)
    derived_hit = False
    for fact in facts:
        for value, kind, derived in _candidates(fact):
            for target, kinds, tol in interps:
                if kind in kinds and abs(value - target) <= tol + 1e-9:
                    if not derived:
                        return "direct"
                    derived_hit = True
    return "derived" if derived_hit else None


def _years_in_fact(f: Fact) -> set[int]:
    ys = {int(fv.v) for fv in f.values.values() if fv.kind == YEAR}
    if f.period:
        ys.update(range(f.period[0], f.period[1] + 1))
    ys.update(int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", f.statement))
    return ys


def verify_items(items: list[Item], store: FactStore, ctx: VerifyContext) -> VerificationResult:
    issues: list[Issue] = []
    numbers_checked = 0
    for it in items:
        cited = [store.get(fid) for fid in it.fact_ids]
        for fid, f in zip(it.fact_ids, cited, strict=True):
            if f is None:
                issues.append(Issue("error", "unknown_fact", it.label, fid, "cited fact does not exist"))
        facts = [f for f in cited if f is not None]
        if not it.fact_ids:
            issues.append(Issue("error", "uncited_claim", it.label, it.text[:80], "no supporting fact cited"))
        text = _FACT_REF.sub(" ", it.text)
        if any(f.mock for f in facts) and not _MOCK_OK.search(text):
            issues.append(
                Issue("error", "mock_unlabelled", it.label, it.text[:80], "narrates MOCK data without labelling it as synthetic")
            )
        cited_years = set().union(*[_years_in_fact(f) for f in facts]) if facts else set()
        for m in _NUM.finditer(text):
            raw, unit, money = m.group("num"), m.group("unit"), bool(m.group("money"))
            n = float(raw.replace(",", ""))
            d = _decimals(raw)
            snippet = text[max(0, m.start() - 12) : m.end() + 12].strip()
            if unit is None and not money and "," not in raw and d == 0 and 1900 <= n <= 2100:  # a year
                y = int(n)
                lo, hi = ctx.coverage
                if not (lo <= y <= hi) and y not in range(ctx.window[0], ctx.window[1] + 1):
                    issues.append(Issue("error", "unsupported_year", it.label, str(y), f"outside data coverage {lo}-{hi}"))
                elif y not in cited_years and y not in range(ctx.window[0], ctx.window[1] + 1):
                    issues.append(Issue("warning", "unsupported_year", it.label, str(y), "year not found in the cited facts"))
                continue
            if unit is None and not money and d == 0 and n <= 12:  # small counts/ordinals ("top 3", "two years") are not claims
                continue
            numbers_checked += 1
            hit = _matches(n, d, unit, money, facts)
            if hit is None:
                issues.append(Issue("error", "unsupported_number", it.label, snippet, "not found in the cited facts"))
            elif hit == "derived":
                issues.append(
                    Issue("info", "derived_number", it.label, snippet, "computed from cited values; not stated directly")
                )
        for m in _OTHER_AGENCIES.finditer(text):
            issues.append(
                Issue("error", "unsupported_source", it.label, m.group(0), "agency/source was not used in this analysis")
            )
        for m in _CAUSAL.finditer(text):
            issues.append(
                Issue(
                    "warning", "causal_claim", it.label, m.group(0), "causal wording; the analysis is descriptive/correlational"
                )
            )
    errors = sum(1 for i in issues if i.severity == "error")
    denom = max(1, numbers_checked + len(items))
    return VerificationResult(errors == 0, len(items), numbers_checked, round(max(0.0, 1 - errors / denom), 3), issues)
