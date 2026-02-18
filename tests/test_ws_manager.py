"""Tests for ws_manager.py — WebSocket ConnectionManager.

Uses a mock WebSocket to test connect, disconnect, send_to_user,
broadcast_to_admins, and connection limits without a real ASGI server.
"""

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from ws_manager import ConnectionManager, MAX_CONNECTIONS_PER_USER


class FakeWebSocket:
    """Minimal WebSocket mock with accept/close/send_json."""

    def __init__(self, fail_send=False):
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent: list[dict] = []
        self._fail_send = fail_send

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, data: dict):
        if self._fail_send:
            raise ConnectionError("send failed")
        self.sent.append(data)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def mgr():
    return ConnectionManager()


@pytest.fixture
def ws():
    return FakeWebSocket()


# ── Connect / Disconnect ────────────────────────────────────────────

class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_accepts_websocket(self, mgr, ws):
        result = await mgr.connect("u1", "user", ws)
        assert result is True
        assert ws.accepted is True

    @pytest.mark.asyncio
    async def test_connect_registers_user(self, mgr, ws):
        await mgr.connect("u1", "user", ws)
        assert "u1" in mgr.get_connected_user_ids()

    @pytest.mark.asyncio
    async def test_connect_multiple_tabs(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u1", "user", ws2)
        assert mgr.get_connection_count() == 2

    @pytest.mark.asyncio
    async def test_connect_rejects_over_limit(self, mgr):
        sockets = []
        for i in range(MAX_CONNECTIONS_PER_USER):
            ws = FakeWebSocket()
            await mgr.connect("u1", "user", ws)
            sockets.append(ws)

        extra = FakeWebSocket()
        result = await mgr.connect("u1", "user", extra)
        assert result is False
        assert extra.closed is True
        assert extra.close_code == 4008

    @pytest.mark.asyncio
    async def test_connect_different_users_independent(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u2", "admin", ws2)
        assert mgr.get_connection_count() == 2
        assert set(mgr.get_connected_user_ids()) == {"u1", "u2"}


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, mgr, ws):
        await mgr.connect("u1", "user", ws)
        await mgr.disconnect("u1", ws)
        assert mgr.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_disconnect_last_tab_removes_user(self, mgr, ws):
        await mgr.connect("u1", "user", ws)
        await mgr.disconnect("u1", ws)
        assert "u1" not in mgr.get_connected_user_ids()

    @pytest.mark.asyncio
    async def test_disconnect_one_of_many_tabs(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u1", "user", ws2)
        await mgr.disconnect("u1", ws1)
        assert mgr.get_connection_count() == 1
        assert "u1" in mgr.get_connected_user_ids()

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_user_no_error(self, mgr, ws):
        # Should not raise
        await mgr.disconnect("nobody", ws)

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_ws_no_error(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        # ws2 was never connected
        await mgr.disconnect("u1", ws2)
        assert mgr.get_connection_count() == 1


# ── send_to_user ────────────────────────────────────────────────────

class TestSendToUser:
    @pytest.mark.asyncio
    async def test_send_to_single_tab(self, mgr, ws):
        await mgr.connect("u1", "user", ws)
        await mgr.send_to_user("u1", {"type": "hello"})
        assert ws.sent == [{"type": "hello"}]

    @pytest.mark.asyncio
    async def test_send_to_multiple_tabs(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u1", "user", ws2)
        await mgr.send_to_user("u1", {"type": "ping"})
        assert ws1.sent == [{"type": "ping"}]
        assert ws2.sent == [{"type": "ping"}]

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_user(self, mgr):
        # Should not raise
        await mgr.send_to_user("nobody", {"type": "test"})

    @pytest.mark.asyncio
    async def test_send_removes_dead_connections(self, mgr):
        good_ws = FakeWebSocket()
        bad_ws = FakeWebSocket(fail_send=True)
        await mgr.connect("u1", "user", good_ws)
        await mgr.connect("u1", "user", bad_ws)
        assert mgr.get_connection_count() == 2

        await mgr.send_to_user("u1", {"type": "test"})

        # good_ws should have received it
        assert good_ws.sent == [{"type": "test"}]
        # bad_ws should have been removed
        assert mgr.get_connection_count() == 1

    @pytest.mark.asyncio
    async def test_send_does_not_cross_users(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u2", "user", ws2)
        await mgr.send_to_user("u1", {"type": "private"})
        assert ws1.sent == [{"type": "private"}]
        assert ws2.sent == []


# ── broadcast_to_admins ─────────────────────────────────────────────

class TestBroadcastToAdmins:
    @pytest.mark.asyncio
    async def test_broadcast_reaches_admin(self, mgr):
        ws_admin = FakeWebSocket()
        await mgr.connect("a1", "admin", ws_admin)
        await mgr.broadcast_to_admins({"type": "alert"})
        assert ws_admin.sent == [{"type": "alert"}]

    @pytest.mark.asyncio
    async def test_broadcast_skips_non_admin(self, mgr):
        ws_user = FakeWebSocket()
        ws_admin = FakeWebSocket()
        await mgr.connect("u1", "user", ws_user)
        await mgr.connect("a1", "admin", ws_admin)
        await mgr.broadcast_to_admins({"type": "alert"})
        assert ws_user.sent == []
        assert ws_admin.sent == [{"type": "alert"}]

    @pytest.mark.asyncio
    async def test_broadcast_multiple_admins(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("a1", "admin", ws1)
        await mgr.connect("a2", "admin", ws2)
        await mgr.broadcast_to_admins({"type": "system"})
        assert ws1.sent == [{"type": "system"}]
        assert ws2.sent == [{"type": "system"}]

    @pytest.mark.asyncio
    async def test_broadcast_no_admins(self, mgr):
        ws = FakeWebSocket()
        await mgr.connect("u1", "user", ws)
        # Should not raise
        await mgr.broadcast_to_admins({"type": "alert"})
        assert ws.sent == []


# ── Utility methods ─────────────────────────────────────────────────

class TestUtilities:
    @pytest.mark.asyncio
    async def test_get_connected_user_ids_empty(self, mgr):
        assert mgr.get_connected_user_ids() == []

    @pytest.mark.asyncio
    async def test_get_connection_count_empty(self, mgr):
        assert mgr.get_connection_count() == 0

    @pytest.mark.asyncio
    async def test_connection_count_after_connect_disconnect(self, mgr):
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        await mgr.connect("u1", "user", ws1)
        await mgr.connect("u2", "admin", ws2)
        assert mgr.get_connection_count() == 2
        await mgr.disconnect("u1", ws1)
        assert mgr.get_connection_count() == 1
