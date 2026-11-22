"""WebSocket fan-out to cockpits and phones.

Each client gets its own bounded queue, so one slow phone on bad mobile data
never holds up the cockpit. When a queue is full the oldest message is
dropped (live samples are worthless once stale); events are small and rare,
so they always get through.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("cockpit.hub")

COCKPIT = "cockpit"
PHONE = "phone"


@dataclass(eq=False)
class Client:
    ws: WebSocket
    kind: str
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=400))
    last_shake: float = 0.0


class Hub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()

    def counts(self) -> dict[str, int]:
        result = {COCKPIT: 0, PHONE: 0}
        for client in self._clients:
            result[client.kind] = result.get(client.kind, 0) + 1
        return result

    def add(self, ws: WebSocket, kind: str) -> Client:
        client = Client(ws=ws, kind=kind)
        self._clients.add(client)
        log.info("%s connected (%s total)", kind, self.counts())
        return client

    def remove(self, client: Client) -> None:
        if client in self._clients:
            self._clients.discard(client)
            log.info("%s disconnected (%s total)", client.kind, self.counts())

    def send(self, client: Client, message: dict[str, Any]) -> None:
        self._put(client, json.dumps(message, separators=(",", ":")))

    def broadcast(self, message: dict[str, Any], kinds: tuple[str, ...] = (COCKPIT,)) -> None:
        if not self._clients:
            return
        payload = json.dumps(message, separators=(",", ":"))
        for client in list(self._clients):
            if client.kind in kinds:
                self._put(client, payload)

    @staticmethod
    def _put(client: Client, payload: str) -> None:
        try:
            client.queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                client.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            client.queue.put_nowait(payload)

    async def pump(self, client: Client) -> None:
        """Send queued messages until the socket closes."""
        while True:
            payload = await client.queue.get()
            await client.ws.send_text(payload)
