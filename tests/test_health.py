"""Unit tests for the `health` subcommand in agent-queue.

`cmd_health` inspects the per-project queue file at
`~/.agent-queue/<project>/queue.json` and reports a machine-readable status
with a monitoring-friendly exit code:

  * healthy   → exit 0
  * degraded  → exit 2 (e.g. stale in-progress claims)
  * unhealthy → exit 1 (missing queue, corrupt JSON, malformed structure)

The command is read-only and never mutates the queue file.
"""

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Import agent-queue as a module (it has no .py extension)
# ---------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "agent-queue"
_loader = importlib.machinery.SourceFileLoader("agent_queue", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("agent_queue", _loader, origin=str(_SCRIPT))
aq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aq)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _item(id: int, *, status="pending", agent=None, updated_at=None):
    """Build a minimal queue item dict for testing."""
    return {
        "id": id,
        "title": f"task-{id}",
        "status": status,
        "agent": agent,
        "updated_at": updated_at or "2026-01-01T00:00:00+00:00",
    }


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _run_health(project: str, *, stale_minutes: float = 30.0):
    """Run cmd_health for *project* and return (result_dict, exit_code).

    Assumes the queue file already exists on disk (or not) under a tmp HOME.
    """
    args = mock.MagicMock()
    args.project = project
    args.stale_minutes = stale_minutes
    args.json_output = False

    with mock.patch("builtins.print") as mock_print:
        with pytest.raises(SystemExit) as exc:
            aq.cmd_health(args)
        output = mock_print.call_args[0][0]
        return json.loads(output), exc.value.code


@pytest.fixture
def queue_home(tmp_path, monkeypatch):
    """Point the queue storage at a temp dir and return a writer helper."""
    monkeypatch.setattr(aq, "QUEUE_DIR", tmp_path)

    def write_queue(project: str, content):
        d = tmp_path / project
        d.mkdir(parents=True, exist_ok=True)
        f = d / "queue.json"
        if isinstance(content, str):
            f.write_text(content)
        else:
            f.write_text(json.dumps(content, indent=2) + "\n")
        return f

    return write_queue


# ---------------------------------------------------------------------------
# Tests — healthy queue
# ---------------------------------------------------------------------------


class TestHealthy:
    def test_healthy_queue_exits_zero(self, queue_home):
        queue_home(
            "proj",
            [
                _item(1, status="completed"),
                _item(2, status="pending"),
                _item(
                    3, status="in-progress", agent="a1", updated_at=_iso_minutes_ago(1)
                ),
            ],
        )
        result, code = _run_health("proj")
        assert code == 0
        assert result["status"] == "healthy"
        assert result["total"] == 3
        assert result["counts"] == {"completed": 1, "pending": 1, "in-progress": 1}
        assert result["stale_in_progress"] == 0
        assert result["checks"]["queue_file_exists"] is True
        assert result["checks"]["valid_json"] is True
        assert result["checks"]["no_stale_in_progress"] is True

    def test_empty_but_initialized_queue_is_healthy(self, queue_home):
        queue_home("proj", [])
        result, code = _run_health("proj")
        assert code == 0
        assert result["status"] == "healthy"
        assert result["total"] == 0
        assert result["counts"] == {}


# ---------------------------------------------------------------------------
# Tests — missing queue (never initialized)
# ---------------------------------------------------------------------------


class TestMissingQueue:
    def test_missing_queue_is_unhealthy(self, queue_home):
        # No write_queue call → queue.json does not exist.
        result, code = _run_health("never-inited")
        assert code == 1
        assert result["status"] == "unhealthy"
        assert result["reason"] == "not_initialized"
        assert result["checks"]["queue_file_exists"] is False

    def test_missing_queue_not_treated_as_empty_healthy(self, queue_home):
        result, code = _run_health("never-inited")
        # Distinct from an empty-but-initialized healthy queue.
        assert result["status"] != "healthy"
        assert code != 0


# ---------------------------------------------------------------------------
# Tests — corrupt JSON
# ---------------------------------------------------------------------------


class TestCorruptJson:
    def test_corrupt_json_is_unhealthy(self, queue_home):
        queue_home("proj", "{ this is not valid json ]")
        result, code = _run_health("proj")
        assert code == 1
        assert result["status"] == "unhealthy"
        assert result["reason"] == "corrupt_json"
        assert result["checks"]["valid_json"] is False

    def test_corrupt_json_does_not_raise(self, queue_home):
        queue_home("proj", "not json at all")
        # Should exit cleanly (SystemExit), never an unhandled exception.
        result, code = _run_health("proj")
        assert code == 1

    def test_non_list_structure_is_unhealthy(self, queue_home):
        queue_home("proj", {"unexpected": "object"})
        result, code = _run_health("proj")
        assert code == 1
        assert result["status"] == "unhealthy"
        assert result["reason"] == "malformed_structure"
        assert result["checks"]["is_list"] is False


# ---------------------------------------------------------------------------
# Tests — stale in-progress claims → degraded
# ---------------------------------------------------------------------------


class TestStaleClaims:
    def test_stale_in_progress_is_degraded(self, queue_home):
        queue_home(
            "proj",
            [
                _item(
                    1, status="in-progress", agent="a1", updated_at=_iso_minutes_ago(90)
                ),
                _item(2, status="pending"),
            ],
        )
        result, code = _run_health("proj")
        assert code == 2
        assert result["status"] == "degraded"
        assert result["stale_in_progress"] == 1
        assert result["stale_items"][0]["id"] == 1
        assert result["stale_items"][0]["agent"] == "a1"
        assert result["checks"]["no_stale_in_progress"] is False

    def test_fresh_in_progress_is_healthy(self, queue_home):
        queue_home(
            "proj",
            [
                _item(
                    1, status="in-progress", agent="a1", updated_at=_iso_minutes_ago(5)
                )
            ],
        )
        result, code = _run_health("proj")
        assert code == 0
        assert result["status"] == "healthy"
        assert result["stale_in_progress"] == 0

    def test_stale_threshold_is_configurable(self, queue_home):
        queue_home(
            "proj",
            [
                _item(
                    1, status="in-progress", agent="a1", updated_at=_iso_minutes_ago(10)
                )
            ],
        )
        # 10 minutes old, default threshold 30 → healthy
        result, code = _run_health("proj", stale_minutes=30)
        assert result["status"] == "healthy"
        # ... but with a 5-minute threshold it is stale → degraded
        result, code = _run_health("proj", stale_minutes=5)
        assert result["status"] == "degraded"
        assert result["stale_in_progress"] == 1

    def test_completed_items_never_stale(self, queue_home):
        queue_home(
            "proj",
            [
                _item(
                    1, status="completed", agent="a1", updated_at=_iso_minutes_ago(9999)
                )
            ],
        )
        result, code = _run_health("proj")
        assert code == 0
        assert result["status"] == "healthy"


# ---------------------------------------------------------------------------
# Tests — read-only guarantee
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_health_does_not_mutate_queue_file(self, queue_home):
        f = queue_home(
            "proj",
            [
                _item(
                    1, status="in-progress", agent="a1", updated_at=_iso_minutes_ago(90)
                ),
            ],
        )
        before = f.read_text()
        _run_health("proj")
        assert f.read_text() == before
