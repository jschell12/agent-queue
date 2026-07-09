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
