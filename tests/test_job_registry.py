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

from job_registry import JobRegistry, JobStatus, VALID_TRANSITIONS, validate_transition


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
        job = {"id": "j1", "status": "running"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "done", result_ref="ref123")
        args = mock_update.call_args
        assert args[0][0] == "j1"
        assert args[0][1] == "done"
        assert args[0][2] is not None  # completed_at set

    @pytest.mark.asyncio
    async def test_non_terminal_status_no_completed_at(self, reg):
        job = {"id": "j1", "status": "pending"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "running")
        args = mock_update.call_args
        assert args[0][2] is None  # completed_at not set

    @pytest.mark.asyncio
    async def test_failed_status_passes_error(self, reg):
        job = {"id": "j1", "status": "running"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "failed", error_msg="boom")
        args = mock_update.call_args
        assert args[0][4] == "boom"

    @pytest.mark.asyncio
    async def test_invalid_transition_logs_warning(self, reg):
        """Transitioning from a terminal state should log a warning but still proceed."""
        job = {"id": "j1", "status": "done"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update, \
             patch("job_registry.logger") as mock_logger:
            await reg._update_status("j1", "running")
        # Warning logged for invalid transition
        mock_logger.warning.assert_called_once()
        assert "Invalid job transition" in mock_logger.warning.call_args[0][0]
        # But update still proceeded (defensive)
        mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_transition_no_warning(self, reg):
        """A valid transition should not log a warning."""
        job = {"id": "j1", "status": "running"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock), \
             patch("job_registry.logger") as mock_logger:
            await reg._update_status("j1", "done")
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_not_found_skips_validation(self, reg):
        """If job not found in DB, skip validation but still update."""
        with patch("db.get_job", new_callable=AsyncMock, return_value=None), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
            await reg._update_status("j1", "done")
        mock_update.assert_called_once()


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
        job = {"id": "j1", "status": "pending"}
        with patch("db.get_job", new_callable=AsyncMock, return_value=job), \
             patch("db.update_job_status", new_callable=AsyncMock) as mock_update:
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


# ── JobStatus enum ─────────────────────────────────────────────────

class TestJobStatus:
    def test_all_values_present(self):
        expected = {"pending", "queued", "running", "done", "failed", "cancelled", "interrupted"}
        assert {s.value for s in JobStatus} == expected

    def test_string_comparison(self):
        """JobStatus members compare equal to their string values."""
        assert JobStatus.PENDING == "pending"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.DONE == "done"

    def test_construct_from_string(self):
        assert JobStatus("running") is JobStatus.RUNNING

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            JobStatus("nonexistent")


# ── VALID_TRANSITIONS map ─────────────────────────────────────────

class TestValidTransitions:
    def test_pending_can_go_to_running(self):
        assert JobStatus.RUNNING in VALID_TRANSITIONS[JobStatus.PENDING]

    def test_pending_can_go_to_queued(self):
        assert JobStatus.QUEUED in VALID_TRANSITIONS[JobStatus.PENDING]

    def test_pending_can_go_to_cancelled(self):
        assert JobStatus.CANCELLED in VALID_TRANSITIONS[JobStatus.PENDING]

    def test_queued_can_go_to_running(self):
        assert JobStatus.RUNNING in VALID_TRANSITIONS[JobStatus.QUEUED]

    def test_queued_can_go_to_cancelled(self):
        assert JobStatus.CANCELLED in VALID_TRANSITIONS[JobStatus.QUEUED]

    def test_running_can_go_to_done(self):
        assert JobStatus.DONE in VALID_TRANSITIONS[JobStatus.RUNNING]

    def test_running_can_go_to_failed(self):
        assert JobStatus.FAILED in VALID_TRANSITIONS[JobStatus.RUNNING]

    def test_running_can_go_to_cancelled(self):
        assert JobStatus.CANCELLED in VALID_TRANSITIONS[JobStatus.RUNNING]

    def test_running_can_go_to_interrupted(self):
        assert JobStatus.INTERRUPTED in VALID_TRANSITIONS[JobStatus.RUNNING]

    def test_terminal_states_have_no_transitions(self):
        for status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INTERRUPTED):
            assert VALID_TRANSITIONS[status] == set(), f"{status} should have no valid transitions"

    def test_all_statuses_have_entry(self):
        for status in JobStatus:
            assert status in VALID_TRANSITIONS


# ── validate_transition() ─────────────────────────────────────────

class TestValidateTransition:
    def test_valid_pending_to_running(self):
        assert validate_transition("pending", "running") is True

    def test_valid_pending_to_queued(self):
        assert validate_transition("pending", "queued") is True

    def test_valid_queued_to_running(self):
        assert validate_transition("queued", "running") is True

    def test_valid_running_to_done(self):
        assert validate_transition("running", "done") is True

    def test_valid_running_to_failed(self):
        assert validate_transition("running", "failed") is True

    def test_valid_running_to_cancelled(self):
        assert validate_transition("running", "cancelled") is True

    def test_valid_running_to_interrupted(self):
        assert validate_transition("running", "interrupted") is True

    def test_invalid_done_to_running(self):
        assert validate_transition("done", "running") is False

    def test_invalid_failed_to_done(self):
        assert validate_transition("failed", "done") is False

    def test_invalid_cancelled_to_running(self):
        assert validate_transition("cancelled", "running") is False

    def test_invalid_interrupted_to_done(self):
        assert validate_transition("interrupted", "done") is False

    def test_invalid_pending_to_done(self):
        assert validate_transition("pending", "done") is False

    def test_invalid_queued_to_done(self):
        assert validate_transition("queued", "done") is False

    def test_invalid_current_status_string(self):
        assert validate_transition("bogus", "running") is False

    def test_invalid_new_status_string(self):
        assert validate_transition("running", "bogus") is False

    def test_both_invalid_strings(self):
        assert validate_transition("foo", "bar") is False

    def test_same_status_transition(self):
        """Transitioning to the same status is not in the valid set."""
        for status in JobStatus:
            assert validate_transition(status.value, status.value) is False
