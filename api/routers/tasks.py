"""Background tasks and their progress events."""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

from api.context import Api
from api.schemas import TaskStatus


def register(api: Api) -> None:
    app, not_found, tasks = api.app, api.not_found, api.tasks

    # --- Tasks ---------------------------------------------------------------------------------------------
    @app.get("/tasks/{task_id}", response_model=TaskStatus, tags=["tasks"])
    def task_status(task_id: str):
        return tasks.get(task_id) or not_found("Task")

    @app.get("/tasks/{task_id}/events", tags=["tasks"])
    async def task_events(task_id: str, request: Request):
        """Server-Sent Events: one `progress` event per step, then `done` or `error`."""
        if tasks.get(task_id) is None:
            not_found("Task")

        async def stream():
            seq = 0
            while True:
                for e in tasks.events_after(task_id, seq):
                    seq = e["seq"]
                    kind = e["status"] if e["status"] in ("done", "error") else "progress"
                    yield f"event: {kind}\ndata: {json.dumps(e)}\n\n"
                    if kind != "progress":
                        return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.3)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

