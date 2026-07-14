"""Unit tests for the `list` subcommand output formats in agent-queue.

Covers both the default human-readable table and the `--json` flag, which
emits a JSON array of queue items suitable for piping into tools like `jq`.
"""

import importlib.util
import json
from pathlib import Path
from unittest import mock

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


def _item(
    id: int,
    *,
    status="pending",
    priority="normal",
    tags=None,
    agent=None,
    title=None,
    depends_on=None,
):
    """Build a minimal queue item dict for testing."""
    return {
        "id": id,
        "title": title or f"task-{id}",
        "description": "",
        "tags": tags or [],
        "status": status,
        "priority": priority,
        "agent": agent,
        "assigned_to": None,
        "depends_on": depends_on or [],
        "branch": None,
        "reason": None,
        "source_file": None,
        "source_line": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
    }


def _run_list(items, *, status=None, tag=None, json_output=False) -> str:
    """Run cmd_list against *items* and return everything printed to stdout."""
    lines = []

    with mock.patch.object(aq, "load_queue", return_value=items):
        args = mock.MagicMock()
        args.project = "test"
        args.status = status
        args.tag = tag
        args.json_output = json_output

        with mock.patch(
            "builtins.print",
            side_effect=lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
        ):
            aq.cmd_list(args)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tests — --json output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def test_json_flag_emits_valid_json_array(self):
        items = [_item(1), _item(2, status="completed")]
        output = _run_list(items, json_output=True)
        parsed = json.loads(output)
        assert isinstance(parsed, list)
        assert [i["id"] for i in parsed] == [1, 2]

    def test_json_preserves_all_item_fields(self):
        items = [_item(1, tags=["backend"], agent="agent-1", status="in-progress")]
        parsed = json.loads(_run_list(items, json_output=True))
        assert parsed[0]["tags"] == ["backend"]
        assert parsed[0]["agent"] == "agent-1"
        assert parsed[0]["status"] == "in-progress"

    def test_json_empty_queue_is_empty_array(self):
        assert json.loads(_run_list([], json_output=True)) == []

    def test_json_prints_single_line(self):
        """JSON output should be one print call (one JSON document)."""
        items = [_item(1), _item(2)]
        output = _run_list(items, json_output=True)
        assert output.count("\n") == 0

    def test_json_respects_status_filter(self):
        items = [_item(1, status="pending"), _item(2, status="completed")]
        parsed = json.loads(_run_list(items, status="completed", json_output=True))
        assert [i["id"] for i in parsed] == [2]

    def test_json_respects_tag_filter(self):
        items = [_item(1, tags=["frontend"]), _item(2, tags=["backend"])]
        parsed = json.loads(_run_list(items, tag="backend", json_output=True))
        assert [i["id"] for i in parsed] == [2]


# ---------------------------------------------------------------------------
# Tests — default table output (unchanged by --json)
# ---------------------------------------------------------------------------


class TestTableOutput:
    def test_default_output_is_not_json(self):
        output = _run_list([_item(1, title="hello")])
        assert "#1" in output
        assert "hello" in output
        # A human table row is not parseable as a JSON document
        try:
            json.loads(output)
            is_json = True
        except (ValueError, json.JSONDecodeError):
            is_json = False
        assert not is_json

    def test_table_shows_status_marker_and_agent(self):
        output = _run_list([_item(1, status="in-progress", agent="agent-1")])
        assert "[>]" in output
        assert "[agent-1]" in output

    def test_table_shows_tags_and_deps(self):
        output = _run_list([_item(3, tags=["api"], depends_on=[1, 2])])
        assert "(api)" in output
        assert "depends:[1,2]" in output

    def test_non_tty_table_has_no_ansi(self):
        """The default (mocked-print, non-TTY) path emits zero escape codes."""
        output = _run_list([_item(1, status="failed", title="boom")])
        assert "\033[" not in output


# ---------------------------------------------------------------------------
# Tests — colored output
# ---------------------------------------------------------------------------


class _FakeStream:
    """Minimal stdout stand-in with a controllable isatty()."""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class TestColorGating:
    def test_use_color_true_for_tty(self):
        with mock.patch.dict("os.environ", clear=True):
            assert aq.use_color(False, stream=_FakeStream(tty=True)) is True

    def test_use_color_false_for_non_tty(self):
        assert aq.use_color(False, stream=_FakeStream(tty=False)) is False

    def test_use_color_false_when_no_color_flag(self):
        assert aq.use_color(True, stream=_FakeStream(tty=True)) is False

    def test_use_color_respects_no_color_env(self):
        with mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            assert aq.use_color(False, stream=_FakeStream(tty=True)) is False


class TestColoredRows:
    def test_row_colored_when_forced_on(self):
        row = aq._format_list_row(_item(1, status="completed"), colored=True)
        assert "\033[32m" in row  # green
        assert row.endswith(aq.ANSI_RESET)

    def test_row_uncolored_when_forced_off(self):
        row = aq._format_list_row(_item(1, status="completed"), colored=False)
        assert "\033[" not in row

    def test_distinct_style_per_status(self):
        statuses = [
            "pending",
            "in-progress",
            "in-review",
            "completed",
            "failed",
            "held",
        ]
        for status in statuses:
            row = aq._format_list_row(_item(1, status=status), colored=True)
            assert aq.STATUS_STYLES[status] in row

    def test_colored_row_preserves_fields(self):
        row = aq._format_list_row(
            _item(
                3,
                status="in-progress",
                agent="agent-1",
                tags=["api"],
                depends_on=[1, 2],
            ),
            colored=True,
        )
        assert "[>]" in row
        assert "[agent-1]" in row
        assert "(api)" in row
        assert "depends:[1,2]" in row
        assert "#3" in row


class TestJsonNeverColored:
    def test_json_output_has_no_ansi_even_when_forced(self):
        """--json must be escape-free regardless of any color state."""
        items = [_item(1, status="failed"), _item(2, status="completed")]
        output = _run_list(items, json_output=True)
        assert "\033[" not in output
        # Still valid JSON.
        assert [i["id"] for i in json.loads(output)] == [1, 2]
