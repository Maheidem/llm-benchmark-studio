"""Tests for job_registry.py — JobRegistry unit tests.

Tests the JobRegistry class methods using mocked DB and WebSocket layers.
Focuses on: handler registration, slot accounting, cancel logic, and
utility accessors. Does NOT test the full submit->run->complete lifecycle
(that requires an event loop with real DB).
"""

import asyncio
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from job_registry import JobRegistry


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def reg():
    """Fresh JobRegistry instance per test."""
    return JobRegistry()


@pytest.fixture
def mock_ws():
    """Mock WebSocket manager with send_to_user."""
    ws = MagicMock()
    ws.send_to_user = AsyncMock()
    return ws


# ── Handler registration ────────────────────────────────────────────

class TestRegisterHandler:
    def test_register_stores_handler(self, reg):
        handler = AsyncMock()
        reg.register_handler("benchmark", handler)
        assert "benchmark" in reg._handlers
        assert reg._handlers["benchmark"] is handler

    def test_register_multiple_types(self, reg):
        h1 = AsyncMock()
        h2 = AsyncMock()
        reg.register_handler("benchmark", h1)
        reg.register_handler("param_tune", h2)
        assert reg._handlers["benchmark"] is h1
        assert reg._handlers["param_tune"] is h2

    def test_register_overwrites_existing(self, reg):
        h1 = AsyncMock()
        h2 = AsyncMock()
        reg.register_handler("benchmark", h1)
        reg.register_handler("benchmark", h2)
        assert reg._handlers["benchmark"] is h2


# ── WebSocket manager ───────────────────────────────────────────────

class TestSetWsManager:
    def test_set_ws_manager(self, reg, mock_ws):
        reg.set_ws_manager(mock_ws)
        assert reg._ws_manager is mock_ws

    def test_ws_manager_initially_none(self, reg):
        assert reg._ws_manager is None


# ── Utility accessors ───────────────────────────────────────────────

class TestAccessors:
    def test_is_job_running_false_initially(self, reg):
        assert reg.is_job_running("nonexistent") is False

    def test_is_job_running_true_when_present(self, reg):
        reg._running["job123"] = MagicMock()
        assert reg.is_job_running("job123") is True

    def test_get_active_count_zero_initially(self, reg):
        assert reg.get_active_count("user1") == 0

    def test_get_active_count_after_manual_set(self, reg):
        reg._user_slots["user1"] = 3
        assert reg.get_active_count("user1") == 3

    def test_get_cancel_event_none_when_not_running(self, reg):
        assert reg.get_cancel_event("job123") is None

    def test_get_cancel_event_returns_event(self, reg):
        event = asyncio.Event()
        reg._cancel_events["job123"] = event
        assert reg.get_cancel_event("job123") is event


# ── Cancel logic ────────────────────────────────────────────────────

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_job_returns_false(self, reg):
        with patch("db.get_job", new_callable=AsyncMock, return_value=None):
            result = await reg.cancel("no-such-job", "user1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_wrong_user_returns_false(self, reg):
        job = {"id": "j1", "user_id": "user1", "status": "running"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job):
            result = await reg.cancel("j1", "user2")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_wrong_user_admin_allowed(self, reg):
        job = {"id": "j1", "user_id": "user1", "status": "pending"}
        reg.set_ws_manager(MagicMock(send_to_user=AsyncMock()))
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock):
            result = await reg.cancel("j1", "admin_user", is_admin=True)
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, reg, mock_ws):
        reg.set_ws_manager(mock_ws)
        job = {"id": "j1", "user_id": "user1", "status": "pending"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            result = await reg.cancel("j1", "user1")
        assert result is True
        mock_update.assert_called_once()
        # Should broadcast job_cancelled
        mock_ws.send_to_user.assert_called_once()
        msg = mock_ws.send_to_user.call_args[0][1]
        assert msg["type"] == "job_cancelled"

    @pytest.mark.asyncio
    async def test_cancel_queued_job(self, reg, mock_ws):
        reg.set_ws_manager(mock_ws)
        job = {"id": "j2", "user_id": "user1", "status": "queued"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock):
            result = await reg.cancel("j2", "user1")
        assert result is True

    @pytest.mark.asyncio
    async def test_cancel_running_job_sets_event(self, reg):
        cancel_event = asyncio.Event()
        reg._cancel_events["j3"] = cancel_event
        job = {"id": "j3", "user_id": "user1", "status": "running"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job):
            result = await reg.cancel("j3", "user1")
        assert result is True
        assert cancel_event.is_set()

    @pytest.mark.asyncio
    async def test_cancel_terminal_job_returns_false(self, reg):
        job = {"id": "j4", "user_id": "user1", "status": "done"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job):
            result = await reg.cancel("j4", "user1")
        assert result is False


# ── _broadcast ──────────────────────────────────────────────────────

class TestBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_with_ws_manager(self, reg, mock_ws):
        reg.set_ws_manager(mock_ws)
        await reg._broadcast("user1", {"type": "test"})
        mock_ws.send_to_user.assert_called_once_with("user1", {"type": "test"})

    @pytest.mark.asyncio
    async def test_broadcast_without_ws_manager(self, reg):
        # Should not raise
        await reg._broadcast("user1", {"type": "test"})


# ── _update_status ──────────────────────────────────────────────────

class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_terminal_status_sets_completed_at(self, reg):
        with patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "done", result_ref="ref123")
        args = mock_update.call_args
        assert args[0][0] == "j1"
        assert args[0][1] == "done"
        assert args[0][2] is not None  # completed_at set

    @pytest.mark.asyncio
    async def test_non_terminal_status_no_completed_at(self, reg):
        with patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "running")
        args = mock_update.call_args
        assert args[0][2] is None  # completed_at not set

    @pytest.mark.asyncio
    async def test_failed_status_passes_error(self, reg):
        with patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "failed", error_msg="boom")
        args = mock_update.call_args
        assert args[0][4] == "boom"


# ── _get_user_limit ─────────────────────────────────────────────────

class TestGetUserLimit:
    @pytest.mark.asyncio
    async def test_default_limit_when_no_row(self, reg):
        with patch("db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            limit = await reg._get_user_limit("user1")
        assert limit == 1

    @pytest.mark.asyncio
    async def test_uses_db_limit(self, reg):
        with patch("db.get_user_rate_limit", new_callable=AsyncMock, return_value={"max_concurrent": 5}):
            limit = await reg._get_user_limit("user1")
        assert limit == 5

    @pytest.mark.asyncio
    async def test_default_when_key_missing(self, reg):
        with patch("db.get_user_rate_limit", new_callable=AsyncMock, return_value={}):
            limit = await reg._get_user_limit("user1")
        assert limit == 1


# ── _start_job with no handler ──────────────────────────────────────

class TestStartJobNoHandler:
    @pytest.mark.asyncio
    async def test_no_handler_marks_failed(self, reg, mock_ws):
        reg.set_ws_manager(mock_ws)
        with patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._start_job("j1", "user1", "unknown_type", {}, 3600)
        # Should call update_job_status with "failed"
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1] == "failed"
        # Should broadcast job_failed
        mock_ws.send_to_user.assert_called_once()
        msg = mock_ws.send_to_user.call_args[0][1]
        assert msg["type"] == "job_failed"


# ── _startup_recovery ───────────────────────────────────────────────

class TestStartupRecovery:
    @pytest.mark.asyncio
    async def test_marks_interrupted_on_startup(self, reg):
        with patch("db.mark_interrupted_jobs", new_callable=AsyncMock, return_value=3) as mock_mark:
            await reg._startup_recovery()
        mock_mark.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_orphans_no_warning(self, reg):
        with patch("db.mark_interrupted_jobs", new_callable=AsyncMock, return_value=0) as mock_mark:
            await reg._startup_recovery()
        mock_mark.assert_called_once()
