"""Helpers for company work-item projection identity metadata."""

from __future__ import annotations

from typing import Any, Mapping


WORK_ITEM_PROJECTION_ID_KEY = "work_item_projection_id"
WORK_ITEM_TURN_TYPE_KEY = "work_item_turn_type"
GATE_REWORK_PROJECTION_ID_KEY = "rework_projection_id"
GATE_TARGET_PROJECTION_ID_KEY = "target_projection_id"
GATE_TARGET_PROJECTION_IDS_KEY = "target_projection_ids"

CANONICAL_WORK_ITEM_TURN_TYPES: frozenset[str] = frozenset(
    {
        "intake",
        "dispatch",
        "plan",
        "setup",
        "execute",
        "review",
        "report",
        "followup",
        "monitor",
        "aggregate",
        "deliver",
        "self_evolution",
    }
)

_TURN_TYPE_ALIASES: dict[str, str] = {
    "delegate": "dispatch",
    "delegation": "dispatch",
    "delivery": "deliver",
    "follow-up": "followup",
    "follow_up": "followup",
    "synthesis": "aggregate",
    "synthesize": "aggregate",
    "self-evolution": "self_evolution",
    "self evolution": "self_evolution",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_work_item_turn_type(value: Any, *, fallback: str = "") -> str:
    """Normalize runtime/work-item turn-kind aliases to canonical names."""
    normalized = _clean(value).lower() or _clean(fallback).lower()
    return _TURN_TYPE_ALIASES.get(normalized, normalized)


def canonical_work_item_turn_type_for_kind(value: Any, *, fallback: str = "execute") -> str:
    """Map a WorkItem/runtime business kind to the canonical runtime turn type."""
    normalized = normalize_work_item_turn_type(value, fallback="")
    if normalized in CANONICAL_WORK_ITEM_TURN_TYPES:
        return normalized
    fallback_normalized = normalize_work_item_turn_type(fallback, fallback="")
    if fallback_normalized in CANONICAL_WORK_ITEM_TURN_TYPES:
        return fallback_normalized
    return ""


def work_item_projection_id_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    fallback: str = "",
) -> str:
    """Read the canonical projected work-item task identity."""
    if not metadata:
        return _clean(fallback)
    value = _clean(metadata.get(WORK_ITEM_PROJECTION_ID_KEY))
    if value:
        return value
    return _clean(fallback)


def work_item_turn_type_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    fallback: str = "execute",
) -> str:
    """Read the canonical company work-item turn type."""
    if not metadata:
        return _clean(fallback).lower()
    for key in (
        WORK_ITEM_TURN_TYPE_KEY,
        "work_kind",
        "delegation_turn_kind",
    ):
        value = normalize_work_item_turn_type(metadata.get(key))
        if value:
            return value
    return normalize_work_item_turn_type(fallback)


def projection_id_for_task(task: Any) -> str:
    """Return the work-item projection identity for a projected Task."""
    metadata = dict(getattr(task, "metadata", {}) or {})
    return work_item_projection_id_from_metadata(
        metadata,
        fallback=_clean(getattr(task, "id", "")),
    )


def turn_type_for_task(task: Any, *, fallback: str = "execute") -> str:
    """Return the work-item turn type for a projected Task."""
    metadata = dict(getattr(task, "metadata", {}) or {})
    return work_item_turn_type_from_metadata(metadata, fallback=fallback)


def projection_id_for_work_item(item: Any) -> str:
    """Return the projection identity for a DelegationWorkItem-like object."""
    explicit_projection = _clean(getattr(item, "projection_id", ""))
    if explicit_projection:
        return explicit_projection
    metadata = dict(getattr(item, "metadata", {}) or {})
    return work_item_projection_id_from_metadata(
        metadata,
        fallback=(
            _clean(getattr(item, "projection_id", ""))
            or _clean(getattr(item, "work_item_id", ""))
        ),
    )


def turn_type_for_work_item(item: Any, *, fallback: str = "execute") -> str:
    """Return the turn type for a DelegationWorkItem-like object."""
    metadata = dict(getattr(item, "metadata", {}) or {})
    return work_item_turn_type_from_metadata(
        metadata,
        fallback=_clean(getattr(item, "kind", "")) or fallback,
    )


def canonical_turn_type_for_work_item(item: Any, *, fallback: str = "execute") -> str:
    """Return a canonical turn type for a WorkItem-like object or metadata."""
    if isinstance(item, Mapping):
        return work_item_turn_type_from_metadata(item, fallback=fallback)
    return turn_type_for_work_item(item, fallback=fallback)


def _turn_type_for_value(value: Any, *, fallback: str = "") -> str:
    if isinstance(value, Mapping):
        return work_item_turn_type_from_metadata(value, fallback=fallback)
    if hasattr(value, "metadata"):
        return canonical_turn_type_for_work_item(value, fallback=fallback or "execute")
    return canonical_work_item_turn_type_for_kind(value, fallback=fallback)


def is_delivery_turn(value_or_metadata: Any) -> bool:
    """Return True for final delivery turns, including legacy ``delivery`` alias."""
    return _turn_type_for_value(value_or_metadata, fallback="") == "deliver"


def is_manager_reviewable_turn(value_or_metadata: Any) -> bool:
    """Return True when a finished WorkItem should enter manager review flow.

    An explicit persisted ``turn_output_kind`` wins over the turn-type
    default: delegation-kind turns (dispatch/intake/plan) are review-exempt
    only because their deliverable is normally a child card set that gets
    reviewed per child. When such a turn completed with the manager's own
    work product instead, the done-transition stamps
    ``turn_output_kind=self_produced`` on the WorkItem and that output is
    reviewable like any execute turn — every consumer of this predicate
    (DONE routing, report spawn, report completion, recovery scans) follows
    automatically.
    """
    metadata: Mapping[str, Any] | None = None
    if isinstance(value_or_metadata, Mapping):
        metadata = value_or_metadata
    elif hasattr(value_or_metadata, "metadata"):
        metadata = getattr(value_or_metadata, "metadata", None) or {}
    if metadata and str(metadata.get("turn_output_kind", "") or "").strip().lower() == "self_produced":
        return True
    turn_type = _turn_type_for_value(value_or_metadata, fallback="")
    if not turn_type:
        return False
    return turn_type not in {"intake", "plan", "dispatch", "aggregate", "deliver", "self_evolution"}


def mark_work_item_projection(
    metadata: Mapping[str, Any] | None = None,
    *,
    projection_id: str = "",
    turn_type: str = "",
) -> dict[str, Any]:
    """Return metadata with canonical work-item projection keys only."""
    result = dict(metadata or {})
    projection = _clean(projection_id) or work_item_projection_id_from_metadata(result)
    turn = normalize_work_item_turn_type(turn_type) or work_item_turn_type_from_metadata(result)
    if projection:
        result[WORK_ITEM_PROJECTION_ID_KEY] = projection
    if turn:
        result[WORK_ITEM_TURN_TYPE_KEY] = turn
    return result


def mark_projected_work_item_task(
    metadata: Mapping[str, Any] | None = None,
    *,
    projection_id: str = "",
    turn_type: str = "",
) -> dict[str, Any]:
    """Return projected task/work-item metadata with canonical identity keys."""
    return mark_work_item_projection(
        metadata,
        projection_id=projection_id,
        turn_type=turn_type,
    )


def work_item_identity_payload(
    *,
    projection_id: str = "",
    turn_type: str = "",
    source: Mapping[str, Any] | None = None,
    include_empty: bool = False,
) -> dict[str, str]:
    """Build a canonical event/checkpoint/ws payload identity fragment."""
    source_meta = dict(source or {})
    projection = _clean(projection_id) or work_item_projection_id_from_metadata(source_meta, fallback="")
    turn = normalize_work_item_turn_type(turn_type) or work_item_turn_type_from_metadata(source_meta, fallback="")
    payload: dict[str, str] = {}
    if projection or include_empty:
        payload[WORK_ITEM_PROJECTION_ID_KEY] = projection
    if turn or include_empty:
        payload[WORK_ITEM_TURN_TYPE_KEY] = turn
    return payload


def work_item_identity_payload_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    projection_id_fallback: str = "",
    turn_type_fallback: str = "",
    include_empty: bool = False,
) -> dict[str, str]:
    """Build a canonical payload identity fragment from metadata."""
    source_meta = dict(metadata or {})
    return work_item_identity_payload(
        projection_id=work_item_projection_id_from_metadata(
            source_meta,
            fallback=projection_id_fallback,
        ),
        turn_type=work_item_turn_type_from_metadata(
            source_meta,
            fallback=turn_type_fallback,
        ),
        include_empty=include_empty,
    )


def work_item_identity_payload_for_task(
    task: Any,
    *,
    fallback_turn_type: str = "",
    include_empty: bool = False,
) -> dict[str, str]:
    """Build a canonical payload identity fragment for a Task-like object."""
    if task is None:
        return work_item_identity_payload(
            turn_type=fallback_turn_type,
            include_empty=include_empty,
        )
    return work_item_identity_payload(
        projection_id=projection_id_for_task(task),
        turn_type=turn_type_for_task(task, fallback=fallback_turn_type),
        include_empty=include_empty,
    )


def migrate_work_item_projection_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    projection_id_fallback: str = "",
    turn_type_fallback: str = "",
) -> tuple[dict[str, Any], bool]:
    """Normalize canonical projection metadata from canonical inputs only."""
    before = dict(metadata or {})
    result = dict(before)
    projection = work_item_projection_id_from_metadata(
        result,
        fallback=projection_id_fallback,
    )
    turn = work_item_turn_type_from_metadata(
        result,
        fallback=turn_type_fallback or "execute",
    )
    if projection and not _clean(result.get(WORK_ITEM_PROJECTION_ID_KEY)):
        result[WORK_ITEM_PROJECTION_ID_KEY] = projection
    if turn and not _clean(result.get(WORK_ITEM_TURN_TYPE_KEY)):
        result[WORK_ITEM_TURN_TYPE_KEY] = turn
    return result, result != before


def rework_projection_id_for_gate(gate: Any, *, fallback: str = "") -> str:
    """Return the gate rework target as a work-item projection identity."""
    metadata = dict(getattr(gate, "metadata", {}) or {})
    return _clean(
        metadata.get(GATE_REWORK_PROJECTION_ID_KEY)
        or getattr(gate, "rework_projection_id", "")
        or fallback
    )


def mark_gate_rework_projection(gate: Any, projection_id: str) -> Any:
    """Attach projection-only gate rework identity."""
    projection = _clean(projection_id)
    metadata = dict(getattr(gate, "metadata", {}) or {})
    if projection:
        metadata[GATE_REWORK_PROJECTION_ID_KEY] = projection
    setattr(gate, "metadata", metadata)
    if hasattr(gate, "rework_projection_id"):
        setattr(gate, "rework_projection_id", projection or None)
    return gate


def target_projection_id_for_decision(decision: Any, *, fallback: str = "") -> str:
    """Return a gate-harness target as a work-item projection identity."""
    return _clean(
        getattr(decision, GATE_TARGET_PROJECTION_ID_KEY, "")
        or fallback
    )


def target_projection_ids_for_decision(decision: Any) -> list[str]:
    """Return all gate-harness targets as work-item projection identities."""
    raw_ids = list(getattr(decision, GATE_TARGET_PROJECTION_IDS_KEY, []) or [])
    if not raw_ids:
        single = target_projection_id_for_decision(decision)
        raw_ids = [single] if single else []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        value = _clean(item)
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def gate_rework_payload(
    *,
    rework_projection_id: str = "",
    target_projection_id: str = "",
    review_projection_id: str = "",
) -> dict[str, Any]:
    """Build projection-only gate/rework payload metadata."""
    rework = _clean(rework_projection_id)
    target = _clean(target_projection_id)
    review = _clean(review_projection_id)
    payload: dict[str, Any] = {}
    if rework:
        payload[GATE_REWORK_PROJECTION_ID_KEY] = rework
    if target:
        payload[GATE_TARGET_PROJECTION_ID_KEY] = target
    if review:
        payload["review_projection_id"] = review
    return payload
