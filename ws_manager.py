"""WebSocket connection manager for LLM Benchmark Studio.

Manages per-user WebSocket connections supporting multiple tabs.
Used by the JobRegistry to broadcast real-time job status updates.
"""

import asyncio
import json
from fastapi import WebSocket


MAX_CONNECTIONS_PER_USER = 5


class ConnectionManager:
    """Manages WebSocket connections per user, supporting multiple tabs."""

    def __init__(self):
        # user_id -> set of WebSocket connections
        self._connections: dict[str, set[WebSocket]] = {}
        # user_id -> role (for admin broadcast)
        self._user_roles: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, role: str, ws: WebSocket) -> bool:
        """Accept a WebSocket connection and register it for a user.

        Returns False and closes the socket if the user already has
        MAX_CONNECTIONS_PER_USER connections (prevents reconnect storms).
        """
        async with self._lock:
            existing = self._connections.get(user_id, set())
            if len(existing) >= MAX_CONNECTIONS_PER_USER:
                await ws.close(code=4008, reason="Too many connections")
                return False

        await ws.accept()
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = set()
            self._connections[user_id].add(ws)
            self._user_roles[user_id] = role
        return True

    async def disconnect(self, user_id: str, ws: WebSocket):
        """Remove a WebSocket connection for a user."""
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    del self._connections[user_id]
                    self._user_roles.pop(user_id, None)

    async def send_to_user(self, user_id: str, message: dict):
        """Send a JSON message to ALL tabs of a specific user."""
        conns = self._connections.get(user_id, set()).copy()
        dead = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)

    async def broadcast_to_admins(self, message: dict):
        """Send a message to all connected admin users."""
        admin_ids = [
            uid for uid, role in self._user_roles.items() if role == "admin"
        ]
        for uid in admin_ids:
            await self.send_to_user(uid, message)

    def get_connected_user_ids(self) -> list[str]:
        """Return list of currently connected user IDs."""
        return list(self._connections.keys())

    def get_connection_count(self) -> int:
        """Return total number of active WebSocket connections."""
        return sum(len(conns) for conns in self._connections.values())
