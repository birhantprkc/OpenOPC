"""Stateless turn-mode classifier for role-instance dispatch.

A role's session can be called into action for several different kinds
of turn on the SAME work item. ``infer_turn_mode`` looks at the work
item's state (phase + metadata) and the queue entry type to return
one of five canonical modes. The prompt / context assembly branches
on this value so each mode gets the right context block injected.

    EXECUTE    — do the work yourself (leaf role, no children)
    DELEGATE   — break into subtasks (manager role, no children yet)
    REVIEW     — evaluate a subordinate's deliverable and emit a verdict
    INTEGRATE  — parent resumes after all children APPROVED; produce the
                 rolled-up deliverable for upstream review
    REWORK     — reviewer rejected your prior turn; address the feedback
    REPORT     — worker DONE; resume the same session under a dedicated
                 prompt to produce a structured handoff for the reviewer

The classifier is pure: given the same work item + queue entry kind,
it always returns the same mode. It does **not** load the store; all
state must be present on the work item (phase + metadata). This is a
deliberate tradeoff so the mode can be recomputed cheaply at any
point in the dispatcher or context-assembly path.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from opc.core.models import Phase


class TurnMode(str, Enum):
    EXECUTE = "execute"
    DELEGATE = "delegate"
    REVIEW = "review"
    INTEGRATE = "integrate"
    REWORK = "rework"
    REPORT = "report"


def _as_phase(value: Any) -> Phase | None:
    if isinstance(value, Phase):
        return value
    if isinstance(value, str):
        try:
            return Phase(value.strip().lower())
        except Exception:
            return None
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def infer_turn_mode(
    work_item: Any,
    *,
    is_review_entry: bool = False,
) -> TurnMode:
    """Classify the turn the agent is about to run.

    ``work_item`` is the DelegationWorkItem (or any object exposing
    the same ``phase`` / ``kind`` / ``metadata`` attributes).
    ``is_review_entry`` should be True when the dispatcher popped a
    ``review-work-item::`` queue entry — those are always reviews,
    even if the underlying work_item metadata is ambiguous.
    """
    metadata = _as_mapping(getattr(work_item, "metadata", None))
    kind = str(getattr(work_item, "kind", "") or "").strip().lower()
    phase = _as_phase(getattr(work_item, "phase", None))

    # Priority 0: report turn. The hidden auxiliary card spawned after
    # a worker DONE so the same session can produce a structured
    # handoff before the reviewer is invoked. Detected purely from the
    # work item's metadata flag or kind.
    if (
        bool(metadata.get("report_execution_work_item", False))
        or kind == "report"
    ):
        return TurnMode.REPORT

    # Priority 1: review turn. Either the queue entry tag says so,
    # the work item is explicitly marked as the hidden review card,
    # or kind == "review".
    if (
        is_review_entry
        or bool(metadata.get("review_execution_work_item", False))
        or kind == "review"
    ):
        return TurnMode.REVIEW

    # Priority 2: rework. Phase READY_FOR_REWORK is the canonical
    # signal, but the dispatcher flips the work item to RUNNING
    # before the prompt is built — by the time the agent runs we
    # may only see RUNNING. Fall back to the metadata trail the
    # reviewer leaves: ``rework_feedback`` is set on rejection and
    # cleared on approval, and ``review_rework_count`` increments
    # on each rejection. Either signal means "the previous turn
    # was rejected", so render this as REWORK.
    if phase == Phase.READY_FOR_REWORK:
        return TurnMode.REWORK
    rework_feedback = str(metadata.get("rework_feedback", "") or "").strip()
    rework_count = int(metadata.get("review_rework_count", 0) or 0)
    if rework_feedback or rework_count > 0:
        return TurnMode.REWORK

    # Priority 3: integrate. The parent has dependency_work_item_ids
    # (it delegated previously) AND is currently runnable (RUNNING /
    # READY). That can only mean children have completed and the
    # parent is being dispatched for its integration turn. The
    # metadata.frontier == "resumed" flag is also set by the wake
    # edge when present.
    dependency_ids = [
        str(x).strip()
        for x in list(metadata.get("dependency_work_item_ids", []) or [])
        if str(x).strip()
    ]
    frontier = str(metadata.get("frontier", "") or "").strip().lower()
    if dependency_ids and (
        phase in {Phase.RUNNING, Phase.READY} or frontier == "resumed"
    ):
        return TurnMode.INTEGRATE

    # Priority 4: delegate. Manager role with nothing spawned yet.
    allowed_delegate_role_ids = [
        str(x).strip()
        for x in list(metadata.get("allowed_delegate_role_ids", []) or [])
        if str(x).strip()
    ]
    if allowed_delegate_role_ids and not dependency_ids:
        return TurnMode.DELEGATE

    # Default: worker executing their own work item.
    return TurnMode.EXECUTE
