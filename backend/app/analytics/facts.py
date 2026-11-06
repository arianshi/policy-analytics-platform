"""Facts: the only place numbers enter the narrative.

The analytics engine computes statistics deterministically and records each as a :class:`Fact` with a
ready-made sentence and the exact numeric values behind it. LLMs may only *narrate* facts (citing ids);
:mod:`app.reporting.verify` checks every number in a narrative against these values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# value kinds understood by the verifier
PCT, COUNT, MONEY_M, RATIO, YEAR = "pct", "count", "money_m", "ratio", "year"


@dataclass
class FactValue:
    v: float
    kind: str  # pct | count | money_m | ratio | year

    def to_dict(self) -> dict[str, Any]:
        return {"v": self.v, "kind": self.kind}


@dataclass
class Fact:
    id: str
    kind: str  # growth | peak | trend | latest | comparison | share | correlation | anomaly | productivity | pipeline | forecast | context | limitation
    sector: str | None
    metric: str | None
    period: tuple[int, int] | None
    statement: str
    values: dict[str, FactValue] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)  # dataset keys
    caveats: list[str] = field(default_factory=list)
    importance: float = 0.5
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["values"] = {k: fv.to_dict() for k, fv in self.values.items()}
        d["period"] = list(self.period) if self.period else None
        return d


class FactStore:
    def __init__(self) -> None:
        self._facts: list[Fact] = []

    def add(self, kind: str, statement: str, *, sector: str | None = None, metric: str | None = None,
            period: tuple[int, int] | None = None, values: dict[str, FactValue] | None = None,
            sources: list[str] | None = None, caveats: list[str] | None = None, importance: float = 0.5,
            mock: bool = False) -> Fact:
        fact = Fact(f"F{len(self._facts) + 1}", kind, sector, metric, period, statement, values or {},
                    sources or [], caveats or [], importance, mock)
        self._facts.append(fact)
        return fact

    def all(self) -> list[Fact]:
        return list(self._facts)

    def get(self, fact_id: str) -> Fact | None:
        return next((f for f in self._facts if f.id == fact_id), None)

    def ids(self) -> set[str]:
        return {f.id for f in self._facts}

    def __len__(self) -> int:
        return len(self._facts)


# ---------------------------------------------------------------------------- formatting
def fmt_count(v: float) -> str:
    return f"{v:,.0f}"


def fmt_pct(v: float, signed: bool = False, digits: int = 1) -> str:
    return f"{v:+.{digits}f}%" if signed else f"{v:.{digits}f}%"


def fmt_pp(v: float) -> str:
    return f"{v:+.1f} percentage points"


def fmt_money_m(v: float) -> str:
    """S$ million -> 'S$42.8 billion' / 'S$950 million'."""
    return f"S${v / 1000:,.1f} billion" if abs(v) >= 1000 else f"S${v:,.0f} million"


def fmt_ratio(v: float) -> str:
    return f"{v:.2f}"


def fmt_by_unit(v: float, unit: str) -> str:
    if unit == "%":
        return fmt_pct(v)
    if unit.startswith("S$ million"):
        return fmt_money_m(v)
    if unit == "S$ thousand":
        return f"S${v:,.1f} thousand"
    if unit == "ratio":
        return fmt_ratio(v)
    return fmt_count(v)


def kind_for_unit(unit: str) -> str:
    return PCT if unit == "%" else MONEY_M if unit.startswith("S$ million") else RATIO if unit == "ratio" else COUNT
