# agent-queue

Multi-agent work queue with tag-based coordination, merge locking, and dependency ordering.

Coordinate multiple AI agents working on the same repo in parallel. Agents claim tasks from a shared queue, work in isolated clones, and merge back through a serialized lock.

## Scripts

All scripts live in `scripts/` — no dependencies beyond Python 3.12+ stdlib.

| Script | Purpose |
|--------|---------|
| `agent-queue` | Queue management: init, add, claim, complete, fail, status |
| `agent-merge` | Serialized merge with `fcntl.flock` — one agent merges at a time |
| `agent-orchestrate` | Monitoring loop: re-queues retryable failures, reports plan |

## Quick Start

```bash
AQ=~/development/github.com/jschell12/agent-queue/scripts

# Initialize queue
"$AQ/agent-queue" init -p my-project

# Add tasks (inline or from markdown)
"$AQ/agent-queue" add -p my-project "Add login page" --tags frontend
"$AQ/agent-queue" add-file -p my-project tasks.md

# Worker loop: clone → claim → work → merge → complete
CLONE_INFO=$("$AQ/agent-queue" clone git@github.com:org/repo.git agent-1 --parent /tmp)
"$AQ/agent-queue" claim -p my-project --agent agent-1
# ... do work, commit ...
"$AQ/agent-merge" merge my-branch --delete-branch
"$AQ/agent-queue" complete -p my-project 1 --branch my-branch
```

## Subcommands

The three core subcommands for the claim → work → complete cycle:

```bash
# add — enqueue a task (title required; description and tags optional)
"$AQ/agent-queue" add -p my-project "Add login page" "OAuth + session cookie" --tags frontend

# claim — atomically hand the next ready task to an agent (prints the item as JSON)
"$AQ/agent-queue" claim -p my-project --agent agent-1

# complete — mark a claimed task done, recording the branch that delivered it
"$AQ/agent-queue" complete -p my-project 1 --branch my-branch
```

```bash
# health — check queue integrity; exit 0 = healthy, non-zero = degraded/unhealthy
"$AQ/agent-queue" health -p my-project
"$AQ/agent-queue" health -p my-project --stale-minutes 15
```

`health` is read-only and prints a JSON report (`status`, per-check results,
status `counts`, and a `stale_in_progress` count). It exits 0 when the queue is
`healthy`, 2 when `degraded` (an `in-progress` item has been untouched past
`--stale-minutes`, default 30), and 1 when `unhealthy` (queue never initialized
or `queue.json` is corrupt), so it drops into monitoring and `&&` chains.

Run `"$AQ/agent-queue" <subcommand> --help` for the full flag list. Other subcommands
include `init`, `add-file`, `fail`, `review`, `status`, `health`, `list`, and `clone`.

## Dependencies

Tasks can declare dependencies so agents work in the right order:

```bash
# Task 3 depends on tasks 1 and 2
"$AQ/agent-queue" add -p my-project "Deploy service" --depends-on 1,2

# In markdown: reference IDs with [N]
# - [ ] Deploy service [1] [2]
```

`claim` will not hand out a task until all its dependencies are completed.

## Queue Storage

Data lives at `~/.agent-queue/<project>/queue.json`. Locking uses `fcntl.flock` — held for milliseconds during reads/writes, auto-released on crash.

## Merge Lock

`agent-merge` holds a global lock during fetch → rebase → push. 5-minute timeout. Only one agent merges at a time, preventing push races.

## Development

Contributions welcome. The project targets **Python 3.12+** and has no runtime
dependencies beyond the standard library; the only dev dependencies are
[`ruff`](https://docs.astral.sh/ruff/) (lint + format) and
[`pytest`](https://docs.pytest.org/) (tests).

### Setup

```bash
# From a clean checkout, create a virtualenv and install the dev tools
python3 -m venv .venv
source .venv/bin/activate
pip install ruff pytest
```

### Make targets

The `Makefile` wraps the common workflows:

| Command | What it does |
|---------|--------------|
| `make test` | Run the test suite with `pytest` |
| `make lint` | Lint `scripts/` and `tests/` with `ruff check` |
| `make fmt`  | Format `scripts/` and `tests/` with `ruff format` |

Ruff is configured (in `pyproject.toml`) to include the extensionless scripts
(`scripts/agent-queue`, `scripts/agent-merge`, `scripts/agent-orchestrate`) in
its file selection, so lint and format cover them too.

Run the checks before opening a PR:

```bash
make fmt    # auto-format
make lint   # style + static checks
make test   # run tests
```

### Continuous integration

GitHub Actions runs `make lint` and `make test` on every pull request (and on
pushes to `main`) via `.github/workflows/ci.yml`, so PRs must be lint-clean and
green before merge.
