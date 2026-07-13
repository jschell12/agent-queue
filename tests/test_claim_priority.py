"""Unit tests for the claim priority logic in agent-queue.

Tests the scoring function used by `cmd_claim` to determine which pending
item an agent should claim next.  Priority factors (from highest to lowest):

  1. assigned_to — items assigned to the claiming agent come first
  2. priority   — high > normal > low
  3. tag overlap — prefer items whose tags don't overlap with other active agents
  4. tag affinity — prefer items whose tags the agent has already worked on
  5. item ID     — lower ID wins as tiebreaker
"""

import importlib.util
import json
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


def _item(
    id: int,
    *,
    status="pending",
    priority="normal",
    tags=None,
    agent=None,
    assigned_to=None,
    depends_on=None,
):
    """Build a minimal queue item dict for testing."""
    return {
        "id": id,
        "title": f"task-{id}",
        "description": "",
        "tags": tags or [],
        "status": status,
        "priority": priority,
        "agent": agent,
        "assigned_to": assigned_to,
        "depends_on": depends_on or [],
        "branch": None,
        "reason": None,
        "source_file": None,
        "source_line": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "completed_at": None,
    }


def _run_claim(items: list[dict], agent_id: str = "agent-1") -> dict:
    """Run the claim logic against *items* and return the chosen item.

    Patches load_queue/save_queue so nothing touches disk.
    """
    captured = {}

    def fake_save(_project, saved_items):
        captured["items"] = saved_items

    with (
        mock.patch.object(aq, "load_queue", return_value=items),
        mock.patch.object(aq, "save_queue", side_effect=fake_save),
        mock.patch.object(aq, "with_lock", side_effect=lambda _p, fn: fn()),
    ):
        args = mock.MagicMock()
        args.project = "test"
        args.agent = agent_id

        # Capture the JSON output
        with mock.patch("builtins.print") as mock_print:
            aq.cmd_claim(args)
            output = mock_print.call_args[0][0]
            return json.loads(output)


# ---------------------------------------------------------------------------
# Tests — basic claim
# ---------------------------------------------------------------------------


class TestClaimBasic:
    def test_claims_only_pending_item(self):
        items = [_item(1)]
        result = _run_claim(items)
        assert result["id"] == 1
        assert result["status"] == "in-progress"
        assert result["agent"] == "agent-1"

    def test_skips_non_pending_statuses(self):
        items = [
            _item(1, status="completed"),
            _item(2, status="in-progress", agent="agent-2"),
            _item(3, status="failed"),
            _item(4, status="pending"),
        ]
        result = _run_claim(items)
        assert result["id"] == 4

    def test_empty_queue_exits(self):
        with pytest.raises(SystemExit):
            _run_claim([])

    def test_all_completed_exits(self):
        items = [_item(1, status="completed"), _item(2, status="completed")]
        with pytest.raises(SystemExit):
            _run_claim(items)


# ---------------------------------------------------------------------------
# Tests — priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_high_before_normal(self):
        items = [
            _item(1, priority="normal"),
            _item(2, priority="high"),
        ]
        result = _run_claim(items)
        assert result["id"] == 2

    def test_normal_before_low(self):
        items = [
            _item(1, priority="low"),
            _item(2, priority="normal"),
        ]
        result = _run_claim(items)
        assert result["id"] == 2

    def test_high_before_low(self):
        items = [
            _item(1, priority="low"),
            _item(2, priority="high"),
        ]
        result = _run_claim(items)
        assert result["id"] == 2

    def test_same_priority_uses_id_tiebreak(self):
        items = [
            _item(3, priority="normal"),
            _item(1, priority="normal"),
            _item(2, priority="normal"),
        ]
        result = _run_claim(items)
        assert result["id"] == 1


# ---------------------------------------------------------------------------
# Tests — assigned_to takes precedence over priority
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_assigned_item_beats_higher_priority(self):
        items = [
            _item(1, priority="high"),
            _item(2, priority="low", assigned_to="agent-1"),
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 2

    def test_assignment_ignored_for_other_agent(self):
        items = [
            _item(1, priority="normal"),
            _item(2, priority="normal", assigned_to="agent-2"),
        ]
        result = _run_claim(items, agent_id="agent-1")
        # agent-1 is not assigned to item 2, so no boost — lower ID wins
        assert result["id"] == 1

    def test_assigned_among_multiple(self):
        items = [
            _item(1, priority="high"),
            _item(2, priority="normal", assigned_to="agent-1"),
            _item(3, priority="high"),
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 2


# ---------------------------------------------------------------------------
# Tests — tag overlap avoidance
# ---------------------------------------------------------------------------


class TestTagOverlap:
    def test_avoids_tags_used_by_other_agents(self):
        items = [
            _item(1, tags=["frontend"], status="in-progress", agent="agent-2"),
            _item(2, tags=["frontend"]),  # overlaps with agent-2
            _item(3, tags=["backend"]),  # no overlap
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 3

    def test_overlap_with_multiple_tags(self):
        items = [
            _item(1, tags=["api", "auth"], status="in-progress", agent="agent-2"),
            _item(2, tags=["api"]),  # 1 tag overlap
            _item(3, tags=["api", "auth"]),  # 2 tag overlap
            _item(4, tags=["docs"]),  # 0 tag overlap
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 4

    def test_own_in_progress_tags_not_counted_as_overlap(self):
        """An agent's own in-progress items should not penalize its next claim."""
        items = [
            _item(1, tags=["backend"], status="in-progress", agent="agent-1"),
            _item(2, tags=["backend"]),  # same tag as agent-1's own work
            _item(3, tags=["frontend"]),
        ]
        result = _run_claim(items, agent_id="agent-1")
        # agent-1's own tags are not "other_active_tags", so no penalty
        # lower ID wins
        assert result["id"] == 2

    def test_priority_beats_overlap(self):
        """High priority wins even if there is tag overlap."""
        items = [
            _item(1, tags=["frontend"], status="in-progress", agent="agent-2"),
            _item(2, tags=["frontend"], priority="high"),  # overlaps but high prio
            _item(3, tags=["backend"], priority="low"),  # no overlap but low prio
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 2


# ---------------------------------------------------------------------------
# Tests — tag affinity (prefer agent's previously completed tags)
# ---------------------------------------------------------------------------


class TestTagAffinity:
    def test_prefers_tags_agent_completed_before(self):
        items = [
            _item(1, tags=["frontend"], status="completed", agent="agent-1"),
            _item(2, tags=["backend"]),  # no affinity
            _item(3, tags=["frontend"]),  # affinity match
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 3

    def test_more_affinity_tags_win(self):
        items = [
            _item(1, tags=["api"], status="completed", agent="agent-1"),
            _item(2, tags=["auth"], status="completed", agent="agent-1"),
            _item(3, tags=["api", "auth"]),  # 2 affinity matches
            _item(4, tags=["api"]),  # 1 affinity match
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 3

    def test_affinity_from_other_agent_ignored(self):
        items = [
            _item(1, tags=["frontend"], status="completed", agent="agent-2"),
            _item(2, tags=["frontend"]),
            _item(3, tags=["backend"]),
        ]
        result = _run_claim(items, agent_id="agent-1")
        # No affinity for agent-1, so lower ID wins
        assert result["id"] == 2

    def test_overlap_beats_affinity(self):
        """Tag overlap avoidance is more important than tag affinity."""
        items = [
            _item(1, tags=["frontend"], status="completed", agent="agent-1"),
            _item(5, tags=["frontend"], status="in-progress", agent="agent-2"),
            _item(2, tags=["frontend"]),  # affinity but overlaps with agent-2
            _item(3, tags=["backend"]),  # no affinity, no overlap
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 3


# ---------------------------------------------------------------------------
# Tests — dependency checking
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_skips_items_with_unmet_deps(self):
        items = [
            _item(1, depends_on=[99]),  # dep 99 not completed
            _item(2),  # no deps
        ]
        result = _run_claim(items)
        assert result["id"] == 2

    def test_allows_items_with_met_deps(self):
        items = [
            _item(1, status="completed"),
            _item(2, depends_on=[1]),
        ]
        result = _run_claim(items)
        assert result["id"] == 2

    def test_multiple_deps_all_must_be_met(self):
        items = [
            _item(1, status="completed"),
            _item(2, status="pending"),
            _item(3, depends_on=[1, 2]),  # dep 2 not completed
            _item(4),
        ]
        result = _run_claim(items)
        # Item 2 is claimable (no deps), item 3 is blocked, item 4 is claimable
        assert result["id"] == 2

    def test_all_items_blocked_exits(self):
        items = [
            _item(1, depends_on=[2]),
            _item(2, depends_on=[1]),  # circular — both blocked
        ]
        with pytest.raises(SystemExit):
            _run_claim(items)


# ---------------------------------------------------------------------------
# Tests — combined scoring
# ---------------------------------------------------------------------------


class TestCombinedScoring:
    def test_full_priority_chain(self):
        """Assignment > priority > overlap > affinity > id."""
        items = [
            # Agent-2 working on "frontend"
            _item(10, tags=["frontend"], status="in-progress", agent="agent-2"),
            # Agent-1 completed "backend" before
            _item(11, tags=["backend"], status="completed", agent="agent-1"),
            # Candidates:
            _item(1, priority="normal", tags=["frontend"]),  # overlap
            _item(2, priority="high", tags=["backend"]),  # high prio + affinity
            _item(3, priority="normal", tags=["backend"]),  # affinity
            _item(4, priority="normal", tags=["docs"]),  # neutral
            _item(5, priority="normal", assigned_to="agent-1"),  # assigned
        ]
        result = _run_claim(items, agent_id="agent-1")
        # assigned_to wins over everything
        assert result["id"] == 5

    def test_without_assignment_priority_wins(self):
        items = [
            _item(10, tags=["frontend"], status="in-progress", agent="agent-2"),
            _item(11, tags=["backend"], status="completed", agent="agent-1"),
            _item(1, priority="normal", tags=["frontend"]),
            _item(2, priority="high", tags=["backend"]),
            _item(3, priority="normal", tags=["backend"]),
            _item(4, priority="normal", tags=["docs"]),
        ]
        result = _run_claim(items, agent_id="agent-1")
        # high priority wins
        assert result["id"] == 2

    def test_same_priority_overlap_breaks_tie(self):
        items = [
            _item(10, tags=["frontend"], status="in-progress", agent="agent-2"),
            _item(1, priority="normal", tags=["frontend"]),  # 1 overlap
            _item(2, priority="normal", tags=["backend"]),  # 0 overlap
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 2

    def test_same_priority_same_overlap_affinity_breaks_tie(self):
        items = [
            _item(10, tags=["backend"], status="completed", agent="agent-1"),
            _item(1, priority="normal", tags=["frontend"]),
            _item(2, priority="normal", tags=["backend"]),  # affinity
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 2

    def test_all_equal_id_breaks_tie(self):
        items = [
            _item(5, priority="normal"),
            _item(3, priority="normal"),
            _item(7, priority="normal"),
        ]
        result = _run_claim(items, agent_id="agent-1")
        assert result["id"] == 3


# ---------------------------------------------------------------------------
# Tests — score function directly
# ---------------------------------------------------------------------------


class TestScoreFunction:
    """Test the score tuple values directly to verify ordering semantics."""

    def _compute_score(
        self, item, agent_id="agent-1", other_active_tags=None, my_completed_tags=None
    ):
        """Reproduce the score() logic from cmd_claim."""
        if other_active_tags is None:
            other_active_tags = set()
        if my_completed_tags is None:
            my_completed_tags = set()

        priority_order = {"high": 0, "normal": 1, "low": 2}
        assigned = 1 if item.get("assigned_to") == agent_id else 0
        prio = priority_order.get(item.get("priority", "normal"), 1)
        overlap = len(set(item.get("tags", [])) & other_active_tags)
        affinity = len(set(item.get("tags", [])) & my_completed_tags)
        return (-assigned, prio, overlap, -affinity, item["id"])

    def test_assigned_produces_negative_one(self):
        item = _item(1, assigned_to="agent-1")
        score = self._compute_score(item, "agent-1")
        assert score[0] == -1

    def test_unassigned_produces_zero(self):
        item = _item(1)
        score = self._compute_score(item, "agent-1")
        assert score[0] == 0

    def test_high_priority_score(self):
        item = _item(1, priority="high")
        score = self._compute_score(item)
        assert score[1] == 0

    def test_normal_priority_score(self):
        item = _item(1, priority="normal")
        score = self._compute_score(item)
        assert score[1] == 1

    def test_low_priority_score(self):
        item = _item(1, priority="low")
        score = self._compute_score(item)
        assert score[1] == 2

    def test_overlap_count(self):
        item = _item(1, tags=["a", "b", "c"])
        score = self._compute_score(item, other_active_tags={"a", "c"})
        assert score[2] == 2

    def test_affinity_count_negative(self):
        item = _item(1, tags=["x", "y"])
        score = self._compute_score(item, my_completed_tags={"x", "y", "z"})
        assert score[3] == -2

    def test_id_tiebreaker(self):
        item = _item(42)
        score = self._compute_score(item)
        assert score[4] == 42

    def test_score_ordering(self):
        """Verify that score tuples sort correctly for a realistic scenario."""
        assigned_low = self._compute_score(
            _item(5, priority="low", assigned_to="agent-1"), "agent-1"
        )
        high_prio = self._compute_score(_item(1, priority="high"), "agent-1")
        normal_no_overlap = self._compute_score(_item(2, priority="normal"), "agent-1")
        normal_with_overlap = self._compute_score(
            _item(3, priority="normal", tags=["x"]), "agent-1", other_active_tags={"x"}
        )

        scores = [assigned_low, high_prio, normal_no_overlap, normal_with_overlap]
        sorted_scores = sorted(scores)
        assert sorted_scores == [
            assigned_low,
            high_prio,
            normal_no_overlap,
            normal_with_overlap,
        ]
