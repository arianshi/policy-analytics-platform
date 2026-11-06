"""Event model shared by agents, the service and the WebSocket stream."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# thought | action | observation | delegation | status | warning | error | llm | result | done
EventType = str


@dataclass
class Event:
    analysis_id: str
    agent: str
    type: EventType
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Emit = Callable[[str, str, str, dict[str, Any] | None], None]  # (agent, type, message, data)


class EventBus:
    """Fan-out of events to live subscribers (WebSocket connections); persistence is done by the sink."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[Event]]] = {}

    def subscribe(self, analysis_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subs.setdefault(analysis_id, set()).add(q)
        return q

    def unsubscribe(self, analysis_id: str, q: asyncio.Queue[Event]) -> None:
        subs = self._subs.get(analysis_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(analysis_id, None)

    def publish(self, event: Event) -> None:
        for q in list(self._subs.get(event.analysis_id, ())):
            q.put_nowait(event)
