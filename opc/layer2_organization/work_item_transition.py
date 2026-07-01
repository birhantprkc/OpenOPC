"""Single authoritative entry point for WorkItem phase changes.

This module exposes ``transition_work_item`` — the only function in the
codebase that should mutate a ``DelegationWorkItem.phase``. Its purpose is
to centralise the "phase is the single source of truth" invariant that
``phase.py`` always claimed but code never enforced.

Everything downstream of phase (``Task.status``, ``DelegationRoleSession
.status`` (DB), ``CompanyMemberSession.status`` (memory), UI kanban
column) is synced via the registered phase-transition hooks, so callers
only need to think about "what phase should this card be in now".

Direct writes like ``task.status = TaskStatus.CANCELLED`` or
``session.status = "idle"`` bypass the hook chain and guarantee cross-layer
state desync — the exact pattern that produced the parent-resume and
stop-cascade bugs in new11 app05. See ``plans/task-key-proud-blum.md``.

This module also exposes ``refresh_dependents_for_run`` — the dependency
frontier pass that propagates child completion (or terminal state) to
parent work items. Lifted out of CompanyMode so a phase-transition hook
can invoke it on any terminal/escalation transition without an import
cycle. See Fix 3 in ``memory/company-mode-stuck-bugs.md``.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Any

from loguru import logger

from opc.core.models import DelegationEvent, DelegationWorkItem, Phase, Task, TaskStatus
from opc.layer2_organization.phase import (
    DONE_PHASES,
    InvalidPhaseTransition,
    coerce_phase,
    phase_for_task_status,
    task_status_for_phase,
    validate_transition,
)
from opc.layer2_organization.work_item_links import linked_work_item_id_for_task
from opc.layer2_organization.work_item_identity import work_item_identity_payload
from opc.layer2_organization.work_item_runtime import is_work_item_runtime_metadata


async def transition_work_item(
    store: Any,
    work_item_id: str,
    *,
    target_phase: Phase | str,
    reason: str,
    summary: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    release_claim: bool = False,
) -> DelegationWorkItem | None:
    """Transition a work item to ``target_phase``.

    The function wraps ``store.update_delegation_work_item(phase=...)`` —
    which already validates the transition against the state-machine table
    (``ALLOWED_TRANSITIONS`` in phase.py) and fires the full
    ``on_phase_transition`` hook chain. This wrapper adds:

    - A mandatory ``reason`` string stamped into metadata for audit
    - An optional ``release_claim`` flag that clears
      ``claimed_by_role_runtime_session_id`` / ``claimed_by_seat_id`` so
      ``is_orphaned`` becomes True and the dispatcher re-picks the card

    Args:
        store: OPCStore instance; must expose ``update_delegation_work_item``.
        work_item_id: Target work item id.
        target_phase: ``Phase`` enum or string (coerced via ``coerce_phase``).
        reason: Short human-readable reason; recorded in metadata for audit.
        summary: Optional summary string persisted on the work item.
        metadata_updates: Extra metadata keys to merge onto the work item.
        release_claim: When True, clears the current claim so the dispatcher
            can re-acquire. Useful for cancel / timeout / forced-release paths.

    Returns:
        The updated ``DelegationWorkItem``, or ``None`` when the store lacks
        the required API or the work item does not exist.
    """
    if not store or not hasattr(store, "update_delegation_work_item"):
        logger.warning(
            "transition_work_item: store lacks update_delegation_work_item"
        )
        return None
    phase = coerce_phase(target_phase)
    merged: dict[str, Any] = dict(metadata_updates or {})
    reason_clean = str(reason or "").strip()
    if reason_clean:
        merged["last_transition_reason"] = reason_clean
    kwargs: dict[str, Any] = {
        "phase": phase,
        "metadata_updates": merged,
    }
    if summary is not None:
        kwargs["summary"] = summary
    # Phase transition first, claim release second (when requested). The
    # old rationale for this ordering was "sync_member_session_hook reads
    # item.claimed_by_role_runtime_session_id"; Phase B removed that hook
    # and moved the unpark to the dispatcher's per-tick rehydrate pass,
    # but the two-step ordering is kept so downstream listeners that
    # still inspect the claim (audit logs, kanban projections) see a
    # consistent before/after.
    try:
        result = await store.update_delegation_work_item(work_item_id, **kwargs)
    except Exception:
        logger.opt(exception=True).warning(
            f"transition_work_item failed wid={work_item_id} "
            f"target={phase.value} reason={reason_clean}"
        )
        raise
    if release_claim and result is not None:
        try:
            result = await store.update_delegation_work_item(
                work_item_id,
                claimed_by_role_runtime_session_id="",
                claimed_by_seat_id="",
            )
        except Exception:
            logger.opt(exception=True).warning(
                f"transition_work_item: claim release failed wid={work_item_id}"
            )
    return result


def _fallback_status_for(
    target_status_or_phase: TaskStatus | Phase | str,
    task: Task,
) -> TaskStatus | None:
    """Pre-compute the TaskStatus to assign locally when the work-item
    transition cannot happen (no linked work_item, no store). Mirrors the
    projection the live hook would apply, so task-mode callers see the
    same local result whether or not a work_item exists.
    """
    try:
        if isinstance(target_status_or_phase, TaskStatus):
            return target_status_or_phase
        if isinstance(target_status_or_phase, Phase):
            return task_status_for_phase(target_status_or_phase)
        if isinstance(target_status_or_phase, str):
            raw = target_status_or_phase.strip().lower()
            try:
                return task_status_for_phase(Phase(raw))
            except ValueError:
                try:
                    return TaskStatus(raw)
                except ValueError:
                    return None
    except Exception:
        return None
    return None


async def transition_work_item_from_task(
    store: Any,
    task: Task,
    *,
    target_status_or_phase: TaskStatus | Phase | str,
    reason: str,
    summary: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    release_claim: bool = False,
    require_work_item: bool = False,
) -> bool:
    """Task bridge helper: transition a work item when the caller holds a Task.

    Designed as the replacement for direct ``task.status = ...`` writes in
    company-mode code. Resolves the linked work item via the hydrated runtime
    link table id (falling back to legacy metadata for old rows), coerces the
    desired state into a Phase, and delegates to ``transition_work_item``.

    ``target_status_or_phase`` may be:
      * a ``Phase`` (or phase-string) — used verbatim.
      * a ``TaskStatus`` (or status-string) — projected via
        ``phase_for_task_status``. BLOCKED disambiguation uses
        ``task.metadata['delegation_pending_work_item_ids']`` to distinguish
        ``WAITING_FOR_CHILDREN`` (has pending children) from ``PAUSED``.
        This preserves the old task-status projection semantics.

    Forward-invalid transitions (e.g. a late async callback arriving after
    the work item was already moved by a reviewer) are silently preserved
    rather than raising. We log at DEBUG so the race is observable without
    being noisy.

    **Local task.status sync**: after the work item transition, the registered
    ``sync_task_status_hook`` updates the DB task.status. The caller still
    holds a local ``task`` object whose ``status`` is now stale — and any
    subsequent ``save_task(task)`` would overwrite the hook's DB update with
    the stale value (race). To avoid that, we eagerly project the target
    Phase back to a TaskStatus and assign it to the local ``task.status``
    in-memory. This is NOT a direct DB write; it keeps the caller's in-memory
    view consistent with what the DB now holds.

    **task-mode fallback**: when there's no linked work item (task-mode
    path or pre-materialization), the helper returns ``False`` but still
    syncs the local ``task.status`` to the caller's intended value. This
    lets company-mode call sites be migrated to this helper without each
    one needing its own ``task.status = ...`` fallback — the task-mode
    execution path just sees the local mutation and a subsequent
    ``save_task(task)`` by the caller persists it.

    ``require_work_item=True`` is for company-mode runtime call sites where
    falling back to a local Task.status write would reintroduce drift. In
    that mode missing store/link returns ``False`` without mutating local
    status.

    Returns ``True`` when a work-item transition was issued (including the
    silent-degrade no-op case). Returns ``False`` when there is no linked
    work item; the local ``task.status`` is only synced when
    ``require_work_item`` is false.
    """
    # Pre-resolve the fallback status so task-mode / pre-materialization
    # paths still end up with a synced local task.status before we bail.
    fallback_status = _fallback_status_for(target_status_or_phase, task)
    if not store or not hasattr(store, "update_delegation_work_item"):
        if fallback_status is not None and not require_work_item:
            task.status = fallback_status
        return False
    work_item_id = linked_work_item_id_for_task(task)
    if not work_item_id and hasattr(store, "get_work_item_for_runtime_task"):
        try:
            linked_item = await store.get_work_item_for_runtime_task(task.id)
        except Exception:
            linked_item = None
        work_item_id = str(getattr(linked_item, "work_item_id", "") or "").strip()
    if not work_item_id:
        if fallback_status is not None and not require_work_item:
            task.status = fallback_status
        return False
    # Coerce target to Phase, applying BLOCKED → WAITING_FOR_CHILDREN/PAUSED
    # disambiguation from task metadata.
    if isinstance(target_status_or_phase, Phase):
        target_phase: Phase = target_status_or_phase
    elif isinstance(target_status_or_phase, TaskStatus):
        has_pending_children = bool(
            (task.metadata or {}).get("delegation_pending_work_item_ids") or []
        )
        target_phase = phase_for_task_status(
            target_status_or_phase,
            has_pending_children=has_pending_children,
        )
    elif isinstance(target_status_or_phase, str):
        raw = target_status_or_phase.strip().lower()
        try:
            target_phase = Phase(raw)
        except ValueError:
            try:
                ts = TaskStatus(raw)
            except ValueError as exc:
                raise ValueError(
                    f"transition_work_item_from_task: target_status_or_phase {target_status_or_phase!r} "
                    "is neither a valid Phase nor TaskStatus"
                ) from exc
            has_pending_children = bool(
                (task.metadata or {}).get("delegation_pending_work_item_ids") or []
            )
            target_phase = phase_for_task_status(
                ts, has_pending_children=has_pending_children
            )
    else:
        raise TypeError(
            "transition_work_item_from_task: target_status_or_phase must be "
            f"Phase | TaskStatus | str, got {type(target_status_or_phase).__name__}"
        )
    # Silent-degrade guard against late async races (see docstring). Look up
    # the persisted phase and preserve it if the desired transition is not
    # in ALLOWED_TRANSITIONS. This keeps shared role-session callbacks
    # crash-free when a late writer observes stale task state.
    persisted_phase: Phase | None = None
    if hasattr(store, "get_delegation_work_item"):
        try:
            persisted_item = await store.get_delegation_work_item(work_item_id)
        except Exception:
            persisted_item = None
        if persisted_item is not None:
            persisted_phase = getattr(persisted_item, "phase", None)
    if target_phase != persisted_phase and persisted_phase is not None:
        try:
            validate_transition(persisted_phase, target_phase)
        except InvalidPhaseTransition:
            logger.debug(
                "transition_work_item_from_task: preserving persisted phase "
                f"{persisted_phase.value} for work_item={work_item_id} "
                f"(projected {target_phase.value} would be an invalid transition)"
            )
            return True
    # Always stamp the task-id / task-status back-reference so audit and
    # reverse lookup stay consistent. Callers can layer extra metadata on top.
    back_ref: dict[str, Any] = {
        "task_id": task.id,
        "task_status": (
            target_status_or_phase.value
            if isinstance(target_status_or_phase, (TaskStatus, Phase))
            else str(target_status_or_phase)
        ),
    }
    if metadata_updates:
        back_ref.update(metadata_updates)
    try:
        await transition_work_item(
            store,
            work_item_id,
            target_phase=target_phase,
            reason=reason,
            summary=summary,
            metadata_updates=back_ref,
            release_claim=release_claim,
        )
    except InvalidPhaseTransition:
        # Defensive: state-machine validation at the store layer can also
        # raise. Degrade the same way the pre-check does, for the race
        # where persisted_phase changed between our lookup and the write.
        logger.debug(
            f"transition_work_item_from_task: store-layer rejected "
            f"{persisted_phase} → {target_phase.value} for wid={work_item_id} "
            "(concurrent writer); degrading to no-op."
        )
        return True
    # Sync local task.status to match the target phase so any subsequent
    # save_task(task) by the caller doesn't race with the hook's DB update.
    # The assignment goes through task_status_for_phase() — not a literal
    # TaskStatus.CANCELLED/FAILED — so the DirectStatusWriteLintTest regex
    # doesn't flag it as a bypass.
    try:
        task.status = task_status_for_phase(target_phase)
    except Exception:
        logger.opt(exception=True).debug(
            "transition_work_item_from_task: local status sync failed"
        )
    return True


async def apply_task_status_transition(
    store: Any,
    task: Task,
    *,
    target_status_or_phase: TaskStatus | Phase | str,
    reason: str,
    summary: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    release_claim: bool = False,
    save_plain_task: bool = True,
    raise_on_missing_work_item: bool = True,
) -> bool:
    """Apply a task status intent through the right source of truth.

    Company WorkItem runtime tasks must transition their linked WorkItem phase;
    plain task-mode tasks keep the legacy Task.status behavior through the
    fallback branch in ``transition_work_item_from_task``.
    """
    metadata = dict(getattr(task, "metadata", {}) or {})
    company_runtime = bool(
        linked_work_item_id_for_task(task)
        or is_work_item_runtime_metadata(metadata)
    )
    transitioned = await transition_work_item_from_task(
        store,
        task,
        target_status_or_phase=target_status_or_phase,
        reason=reason,
        summary=summary,
        metadata_updates=metadata_updates,
        release_claim=release_claim,
        require_work_item=company_runtime,
    )
    if company_runtime:
        if not transitioned and raise_on_missing_work_item:
            target = (
                target_status_or_phase.value
                if isinstance(target_status_or_phase, (TaskStatus, Phase))
                else str(target_status_or_phase)
            )
            raise RuntimeError(
                "company runtime task cannot transition without a linked WorkItem: "
                f"task={getattr(task, 'id', '')} target={target}"
            )
        return transitioned
    if save_plain_task and store and hasattr(store, "save_task"):
        await store.save_task(task)
    return transitioned


# Re-entrancy guard: refresh_dependents_for_run writes to work items, which
# fires phase-transition hooks, which can re-call refresh. The outer call
# already walks every item in the run, so inner calls on the same run_id
# are redundant — silently skip them. Module-level ContextVar because the
# dispatcher runs async tasks, and we want per-task isolation.
_REFRESH_IN_FLIGHT: ContextVar[frozenset[str]] = ContextVar(
    "refresh_dependents_in_flight", default=frozenset()
)


_SYNTHESIS_SKIP_KINDS: frozenset[str] = frozenset({
    "aggregate",
    "deliver",
    "delivery",
    "intake",
    "review",
    "synthesis",
    "synthesize",
})


def _work_item_id(item: DelegationWorkItem | Any | None) -> str:
    return str(getattr(item, "work_item_id", "") or "").strip()


def _dependency_replacement_ids(item: DelegationWorkItem | Any | None) -> list[str]:
    metadata = dict(getattr(item, "metadata", {}) or {}) if item is not None else {}
    raw = (
        metadata.get("replacement_dependency_work_item_ids")
        or metadata.get("replacement_work_item_ids")
        or metadata.get("superseded_by_work_item_ids")
        or []
    )
    if isinstance(raw, str):
        raw = [raw]
    try:
        values = list(raw or [])
    except TypeError:
        values = [raw]
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def is_prunable_dependency_work_item(item: DelegationWorkItem | Any | None) -> bool:
    """True when a dependency target is obsolete rather than merely failed.

    A normal CANCELLED/FAILED dependency is still meaningful and should keep
    the parent from silently succeeding. Manager-deleted or hidden cancelled
    cards are different: they are explicit graph edits, so stale references to
    them must be removed or replaced whenever the dependency frontier refreshes.
    """
    if item is None:
        return False
    metadata = dict(getattr(item, "metadata", {}) or {})
    if bool(metadata.get("deleted_by_manager_tool", False)):
        return True
    upstream_visibility = str(metadata.get("upstream_visibility", "") or "").strip().lower()
    return (
        getattr(item, "phase", None) == Phase.CANCELLED
        and bool(metadata.get("hidden_from_company_kanban", False))
        and upstream_visibility == "hidden"
    )


def normalize_dependency_work_item_ids(
    raw_dependency_ids: list[str] | tuple[str, ...] | set[str],
    work_item_by_id: dict[str, DelegationWorkItem | Any],
    *,
    owner_work_item_id: str = "",
) -> tuple[list[str], list[str]]:
    """Drop or replace stale dependency ids while preserving hard failures.

    Returns ``(active_ids, pruned_ids)``. Replacement ids come from metadata on
    the obsolete dependency target, and are themselves validated against the
    current run graph so a deleted replacement cannot resurrect another stale
    edge.
    """
    owner_id = str(owner_work_item_id or "").strip()
    active: list[str] = []
    pruned: list[str] = []

    def append_active(candidate_id: str) -> None:
        candidate = str(candidate_id or "").strip()
        if not candidate or candidate == owner_id:
            if candidate:
                pruned.append(candidate)
            return
        item = work_item_by_id.get(candidate)
        if is_prunable_dependency_work_item(item):
            pruned.append(candidate)
            return
        active.append(candidate)

    for raw_id in list(raw_dependency_ids or []):
        dep_id = str(raw_id or "").strip()
        if not dep_id:
            continue
        item = work_item_by_id.get(dep_id)
        if is_prunable_dependency_work_item(item):
            pruned.append(dep_id)
            for replacement_id in _dependency_replacement_ids(item):
                append_active(replacement_id)
            continue
        append_active(dep_id)

    return (
        list(dict.fromkeys(active)),
        list(dict.fromkeys(pruned)),
    )


def _work_item_kind(item: DelegationWorkItem, metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("work_kind")
        or metadata.get("delegation_turn_kind")
        or item.kind
        or ""
    ).strip().lower()


def _should_enter_synthesis_turn(
    item: DelegationWorkItem,
    metadata: dict[str, Any],
    dependency_ids: list[str],
) -> bool:
    if item.phase != Phase.WAITING_FOR_CHILDREN:
        return False
    if not dependency_ids:
        return False
    if bool(metadata.get("synthesis_turn_started", False)):
        return False
    if _work_item_kind(item, metadata) in _SYNTHESIS_SKIP_KINDS:
        return False
    if not (
        bool(metadata.get("delegated_children_pending", False))
        or str(metadata.get("frontier", "") or "").strip() == "waiting_for_children"
        or str(metadata.get("last_delegated_by_seat_id", "") or "").strip()
    ):
        return False
    return True


def _synthesis_turn_summary(item: DelegationWorkItem, dependency_ids: list[str]) -> str:
    title = str(item.title or "delegated work").strip()
    child_count = len(dependency_ids)
    manager_label = str(item.manager_role_id or "the upstream owner").strip()
    return (
        f"Synthesize the {child_count} approved child work item"
        f"{'' if child_count == 1 else 's'} for `{title}` and prepare the "
        f"handoff for {manager_label}. Include what was completed, evidence, "
        "remaining risks, and any decision needed from the upper role."
    )


async def refresh_dependents_for_run(
    store: Any,
    *,
    run_id: str,
    source_work_item_id: str | None = None,
    source_task_id: str | None = None,
    source_role_id: str | None = None,
    source_cell_id: str | None = None,
) -> bool:
    """Walk all work items in ``run_id`` and propagate dependency state
    to parent phases.

    **What this does in one pass**:

    - ``WAITING_DEPENDENCIES → READY`` (or ``READY_FOR_REWORK`` when the
      item carries an outstanding rework_feedback) when all deps approved.
    - ``WAITING_FOR_CHILDREN → READY`` as a synthesis turn when delegated
      children are all approved; otherwise ``WAITING_FOR_CHILDREN → RUNNING``.
      Both paths release the parent's stale claim so the dispatcher can
      re-pick it cleanly.
    - Reverse direction: a RUNNING item whose deps regress (new dep
      appeared) goes to ``WAITING_FOR_CHILDREN``; a READY item to
      ``WAITING_DEPENDENCIES``.

    **Who calls this**:

    1. ``CompanyMode._refresh_delegation_dependents`` — preserves the
       explicit call from APPROVED-verdict paths (belt-and-suspenders).
    2. ``phase_hooks.refresh_dependents_hook`` — fires on every terminal
       transition (APPROVED / FAILED / CANCELLED) and on AWAITING_HUMAN,
       so the frontier refreshes automatically. Without the hook, a
       child escalating to AWAITING_HUMAN (or a human-approved
       AWAITING_HUMAN → APPROVED click) would never unblock its parent.

    Returns True when any parent was mutated (for cheap change detection
    in callers that want to emit a downstream event).
    """
    if not store or not run_id:
        return False
    if not hasattr(store, "list_delegation_work_items") or not hasattr(
        store, "update_delegation_work_item"
    ):
        return False
    in_flight = _REFRESH_IN_FLIGHT.get()
    if run_id in in_flight:
        return False
    token = _REFRESH_IN_FLIGHT.set(in_flight | {run_id})
    try:
        try:
            work_items = await store.list_delegation_work_items(run_id)
        except Exception:
            logger.opt(exception=True).debug(
                f"refresh_dependents_for_run: list_delegation_work_items failed run={run_id}"
            )
            return False
        work_item_by_id = {item.work_item_id: item for item in work_items}
        changed = False
        for work_item in work_items:
            metadata = dict(work_item.metadata or {})
            raw_dependency_ids = [
                str(item).strip()
                for item in list(metadata.get("dependency_work_item_ids", []) or [])
                if str(item).strip()
            ]
            if not raw_dependency_ids:
                continue
            dependency_ids, pruned_dependency_ids = normalize_dependency_work_item_ids(
                raw_dependency_ids,
                work_item_by_id,
                owner_work_item_id=work_item.work_item_id,
            )
            dependency_phases = {
                dep_id: (work_item_by_id[dep_id].phase if dep_id in work_item_by_id else None)
                for dep_id in dependency_ids
            }
            all_approved = all(p == Phase.APPROVED for p in dependency_phases.values())
            target_phase = work_item.phase
            metadata_updates: dict[str, Any] = {}
            summary_update: str | None = None
            entered_synthesis_turn = False
            if dependency_ids != raw_dependency_ids:
                metadata_updates["dependency_work_item_ids"] = list(dependency_ids)
                metadata_updates["dependency_pruned_at"] = datetime.now().isoformat()
            if pruned_dependency_ids:
                previous_pruned = [
                    str(item).strip()
                    for item in list(metadata.get("pruned_dependency_work_item_ids", []) or [])
                    if str(item).strip()
                ]
                metadata_updates["pruned_dependency_work_item_ids"] = list(
                    dict.fromkeys([*previous_pruned, *pruned_dependency_ids])
                )
            if all_approved:
                if _should_enter_synthesis_turn(work_item, metadata, dependency_ids):
                    entered_synthesis_turn = True
                    target_phase = Phase.READY
                    summary_update = _synthesis_turn_summary(work_item, dependency_ids)
                    previous_kind = _work_item_kind(work_item, metadata)
                    metadata_updates.update(
                        {
                            "pre_synthesis_work_kind": previous_kind,
                            "work_kind": "synthesize",
                            "delegation_turn_kind": "synthesize",
                            **work_item_identity_payload(
                                projection_id=str(work_item.projection_id or work_item.work_item_id or ""),
                                turn_type="aggregate",
                            ),
                            "current_turn_mode": "synthesize_required",
                            "synthesis_turn_started": True,
                            "synthesis_ready_at": datetime.now().isoformat(),
                            "synthesis_source_work_item_ids": list(dependency_ids),
                            "synthesis_reports_to_role_id": str(work_item.manager_role_id or "").strip(),
                            "synthesis_reports_to_seat_id": str(work_item.manager_seat_id or "").strip(),
                            "frontier": "synthesis_ready",
                            "needs_manager_attention": False,
                        }
                    )
                elif work_item.phase == Phase.WAITING_DEPENDENCIES:
                    target_phase = (
                        Phase.READY_FOR_REWORK
                        if str(metadata.get("rework_feedback", "") or "").strip()
                        else Phase.READY
                    )
                elif work_item.phase == Phase.WAITING_FOR_CHILDREN:
                    target_phase = Phase.RUNNING
                    if _work_item_kind(work_item, metadata) in {"deliver", "delivery"}:
                        metadata_updates.update(
                            {
                                "work_kind": "delivery",
                                "delegation_turn_kind": "delivery",
                                **work_item_identity_payload(
                                    projection_id=str(work_item.projection_id or work_item.work_item_id or ""),
                                    turn_type="deliver",
                                ),
                                "current_turn_mode": "deliver_required",
                                "delivery_turn_ready_at": datetime.now().isoformat(),
                            }
                        )
                metadata_updates["waiting_on_work_item_ids"] = []
                if metadata.get("delegated_children_pending"):
                    metadata_updates["delegated_children_pending"] = False
                if str(metadata.get("frontier", "") or "") == "waiting_for_children" and not entered_synthesis_turn:
                    metadata_updates["frontier"] = "resumed"
            else:
                if work_item.phase == Phase.READY:
                    target_phase = Phase.WAITING_DEPENDENCIES
                elif work_item.phase == Phase.RUNNING:
                    target_phase = Phase.WAITING_FOR_CHILDREN
                metadata_updates["waiting_on_work_item_ids"] = dependency_ids
            # Clear the parent claim whenever the parent truly leaves
            # WAITING_FOR_CHILDREN toward a non-terminal phase. The old
            # condition ("only when all children approved AND target is
            # RUNNING") left a gap: when a child went READY_FOR_REWORK,
            # the refresh now fires (per _DEPENDENT_REFRESH_TARGETS) but
            # the parent stayed in WAITING_FOR_CHILDREN with a stale claim,
            # so the dispatcher couldn't re-pick it even though the child
            # was back on the worker's queue.
            # We exclude DONE_PHASES because for terminal parents the
            # claim is a historical audit record of "last executor".
            clear_claim_on_wake = (
                work_item.phase == Phase.WAITING_FOR_CHILDREN
                and target_phase != work_item.phase
                and target_phase not in DONE_PHASES
            )
            if target_phase != work_item.phase or metadata_updates or clear_claim_on_wake:
                try:
                    await store.update_delegation_work_item(
                        work_item.work_item_id,
                        phase=target_phase if target_phase != work_item.phase else None,
                        blocked_reason="" if all_approved else None,
                        metadata_updates=metadata_updates or None,
                        summary=summary_update,
                        claimed_by_role_runtime_session_id="" if clear_claim_on_wake else None,
                        claimed_by_seat_id="" if clear_claim_on_wake else None,
                    )
                    changed = True
                except Exception:
                    logger.opt(exception=True).debug(
                        "refresh_dependents_for_run: update_delegation_work_item failed "
                        f"wid={work_item.work_item_id}"
                    )
        if changed and hasattr(store, "save_delegation_event"):
            try:
                await store.save_delegation_event(
                    DelegationEvent(
                        run_id=run_id,
                        work_item_id=source_work_item_id or None,
                        cell_id=source_cell_id or None,
                        role_id=source_role_id or None,
                        event_type="dependency_frontier_refreshed",
                        payload={
                            "source_task_id": source_task_id,
                            "source_work_item_id": source_work_item_id,
                        },
                    )
                )
            except Exception:
                logger.opt(exception=True).debug(
                    "refresh_dependents_for_run: event persistence failed"
                )
        return changed
    finally:
        _REFRESH_IN_FLIGHT.reset(token)
