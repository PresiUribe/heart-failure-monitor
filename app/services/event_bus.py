from __future__ import annotations
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncIterator

_queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
_lock = asyncio.Lock()


async def subscribe(user_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    async with _lock:
        _queues[user_id].add(q)
    return q


async def unsubscribe(user_id: str, q: asyncio.Queue) -> None:
    async with _lock:
        _queues[user_id].discard(q)
        if not _queues[user_id]:
            _queues.pop(user_id, None)


async def publish(user_ids, event: dict) -> None:
    payload = dict(event)
    payload.setdefault("fecha", datetime.now(timezone.utc).isoformat())
    async with _lock:
        targets = [(uid, list(_queues.get(str(uid), set()))) for uid in user_ids]
    for _, queues in targets:
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass


def sse_encode(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
