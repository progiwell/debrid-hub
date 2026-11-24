"""In-memory Publish/Subscribe event bus for real-time SSE job updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class JobEventBus:
    """Thread-safe and async-safe in-memory pub/sub event bus."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._last_progress_broadcast: dict[str, float] = {}

    def subscribe(self) -> asyncio.Queue[str]:
        """Register a new SSE client subscriber queue."""
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        logger.debug("New SSE subscriber registered (total: %d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        """Unregister a subscriber queue on client disconnect."""
        self._subscribers.discard(q)
        logger.debug("SSE subscriber disconnected (remaining: %d)", len(self._subscribers))

    def publish(
        self,
        event_type: str,
        data: Any,
        job_id: str | None = None,
        is_progress: bool = False,
    ) -> None:
        """Publish an event to all connected subscribers.

        - If is_progress is True, throttles to at most 1 update per second per job.
        - State transitions (is_progress=False) are broadcast immediately (0ms delay).
        """
        now = time.monotonic()
        if is_progress and job_id:
            last = self._last_progress_broadcast.get(job_id, 0.0)
            if now - last < 1.0:
                return
            self._last_progress_broadcast[job_id] = now
        elif job_id and not is_progress:
            self._last_progress_broadcast.pop(job_id, None)

        payload_str = json.dumps(data) if not isinstance(data, str) else data
        sse_message = f"event: {event_type}\ndata: {payload_str}\n\n"

        for q in list(self._subscribers):
            try:
                q.put_nowait(sse_message)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(sse_message)
                except asyncio.QueueFull:
                    pass


EVENT_BUS = JobEventBus()
