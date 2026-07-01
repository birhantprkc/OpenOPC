"""Concurrency tests for the _config_lock pattern used in P2.

The real WSHandler requires a full engine/store/ws stack to boot, which makes
a pure-unit concurrency test prohibitively heavy. Instead we test the *pattern*
applied in P2 — a single asyncio.Lock serializing any number of mixed
`add_role` and `reorg_decide` style coroutines — and assert:

  - Every operation completes (20 ops in our case).
  - `asyncio.Lock` never produces a RuntimeError ("lock already held") — which
    would indicate same-task reentry.
  - The log shows strictly interleaved entries (no two tasks recorded an
    "inside-lock" line between each other's enter/exit).
  - The whole batch completes well under the 10s budget.

Evidence: ws_handler.py:175 defines `self._config_lock = asyncio.Lock()`.
P2 wraps `_handle_reorg_decide`, `_handle_set_mode`, and the `company_profile`
swap under this same lock. The proof of no deadlock is static (D4 in the plan);
this test is a dynamic smoke that the cooperative-locking behavior holds.
"""
from __future__ import annotations

import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_add_role_and_reorg_decide_serialized():
    """20 mixed ops share one lock; no exceptions, no deadlock, finishes fast."""
    lock = asyncio.Lock()
    # Event log records enter/exit around the critical section so we can verify
    # the lock serialized the body. If two tasks ever interleaved between an
    # enter and its matching exit, the test fails.
    log: list[tuple[str, str]] = []  # (op, phase)

    async def guarded(op: str) -> None:
        async with lock:
            log.append((op, "enter"))
            # Yield to event loop so other tasks get a chance to observe
            # whether they see an "inside-lock" window (they should not).
            await asyncio.sleep(0)
            log.append((op, "exit"))

    started = time.monotonic()
    tasks = []
    for i in range(10):
        tasks.append(asyncio.create_task(guarded(f"add_role:{i}")))
        tasks.append(asyncio.create_task(guarded(f"reorg_decide:{i}")))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - started

    exceptions = [r for r in results if isinstance(r, BaseException)]
    assert exceptions == [], f"Unexpected exceptions: {exceptions}"

    # Every op must produce exactly one enter + one exit.
    assert len(log) == 40, f"Expected 40 log entries (20 ops × 2), got {len(log)}"

    # Verify lock-mutual-exclusion: iterating the log, each enter must be
    # immediately followed by the SAME op's exit (no interleave).
    i = 0
    while i < len(log):
        assert log[i][1] == "enter", f"Expected enter at index {i}, got {log[i]}"
        assert log[i + 1] == (log[i][0], "exit"), (
            f"Enter for {log[i][0]} was not immediately followed by its exit; "
            f"saw {log[i + 1]} — indicates lock was not held across the body."
        )
        i += 2

    # Counts must balance.
    add_role_count = sum(1 for (op, phase) in log if phase == "enter" and op.startswith("add_role"))
    reorg_count = sum(1 for (op, phase) in log if phase == "enter" and op.startswith("reorg_decide"))
    assert add_role_count == 10
    assert reorg_count == 10

    # 10s budget; a correct cooperative-lock implementation finishes in <1s.
    assert elapsed < 10.0, f"Test took {elapsed:.2f}s, exceeded 10s budget"


@pytest.mark.asyncio
async def test_lock_is_not_reentrant_from_same_task():
    """asyncio.Lock must NOT be re-acquirable from the same task (would deadlock).

    This is the invariant that the P2 plan relied on (D4 deadlock proof): if any
    code path under `_handle_reorg_decide` re-acquired `_config_lock` we'd
    deadlock. Here we verify the Lock primitive's behavior — the test passes
    only when a re-acquire attempt blocks forever (we force it to time out).
    """
    lock = asyncio.Lock()

    async def try_reentrant():
        async with lock:
            # Attempting to acquire again from the same task must block.
            try:
                await asyncio.wait_for(lock.acquire(), timeout=0.2)
            except asyncio.TimeoutError:
                return "blocked_as_expected"
            # Only reach here if stdlib ever became reentrant (it is not).
            lock.release()
            return "unexpected_reentry"

    outcome = await try_reentrant()
    assert outcome == "blocked_as_expected", (
        f"Expected asyncio.Lock to block on same-task re-acquire, got {outcome}"
    )
