"""WebSocket stream of agent events: replay what already happened, then push live events until 'done'."""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
TERMINAL = {"completed", "degraded", "failed", "needs_clarification", "cancelled"}


@router.websocket("/ws/analyses/{analysis_id}")
async def analysis_events(ws: WebSocket, analysis_id: str):
    svc = ws.app.state.service
    await ws.accept()
    rec = svc.db.get_analysis(analysis_id)
    if not rec:
        await ws.send_json({"type": "error", "message": "analysis not found"})
        await ws.close(code=4404)
        return
    q = svc.bus.subscribe(analysis_id)  # subscribe BEFORE replay so nothing is missed between the two
    try:
        last = 0
        for ev in svc.db.get_events(analysis_id):
            await ws.send_json(ev)
            last = ev["seq"]
        if svc.db.get_analysis(analysis_id)["status"] in TERMINAL and not svc.tasks.get(analysis_id):
            await ws.send_json({"type": "done", "seq": last + 1, "agent": "system", "message": "already finished", "data": {}})
            return
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=25)
            except TimeoutError:
                await ws.send_json({"type": "ping", "seq": last, "agent": "system", "message": "", "data": {}})
                continue
            if ev.seq <= last:
                continue
            last = ev.seq
            await ws.send_json(ev.to_dict())
            if ev.type == "done":
                return
    except WebSocketDisconnect:
        pass
    finally:
        svc.bus.unsubscribe(analysis_id, q)
        with contextlib.suppress(Exception):
            await ws.close()
