"""Canonical company-runtime identity derived from durable records.

Company-mode Tasks are execution envelopes, not the identity of a run.  A
runtime is owned by its root session and an active suspend checkpoint.  This
module deliberately has no UI dependencies so every surface can resolve the
same scope without relying on process-local task maps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from opc.layer2_organization.work_item_links import linked_work_item_id_for_task
from opc.layer2_organization.work_item_runtime import is_work_item_runtime_metadata


COMPANY_RUNTIME_CHECKPOINT_TYPES: frozenset[str] = frozenset({
    "company_runtime_suspended",
    "company_runtime_interrupted",
})
ACTIVE_COMPANY_RUNTIME_CHECKPOINT_STATUSES: frozenset[str] = frozenset({
    "pending",
    "resuming",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _metadata(task: Any) -> dict[str, Any]:
    return dict(getattr(task, "metadata", {}) or {})


def _task_id(task: Any) -> str:
    return _text(getattr(task, "id", ""))


def _task_session_id(task: Any) -> str:
    return _text(getattr(task, "session_id", ""))


def _task_parent_session_id(task: Any) -> str:
    metadata = _metadata(task)
    return _text(
        getattr(task, "parent_session_id", "")
        or metadata.get("company_runtime_root_session_id")
        or metadata.get("parent_session_id")
    )


def _has_company_runtime_marker(task: Any) -> bool:
    metadata = _metadata(task)
    exec_mode = _text(metadata.get("exec_mode")).lower()
    mode = _text(metadata.get("mode")).lower()
    execution_mode = _text(metadata.get("execution_mode")).lower()
    if exec_mode in {"company", "org", "custom"} or mode in {"company", "org", "custom"}:
        return True
    if execution_mode in {"company", "company_mode", "multi_team_org"}:
        return True
    if is_work_item_runtime_metadata(metadata):
        return True
    if linked_work_item_id_for_task(task):
        return True
    if any(
        metadata.get(key) not in (None, "", [], {})
        for key in (
            "company_work_item_plan",
            "company_runtime_root_session_id",
            "delegation_run_id",
            "work_item_projection_id",
            "work_item_projection_ref",
            "work_item_role_id",
            "shared_role_session",
        )
    ):
        return True
    # Old company records may predate exec_mode.  An explicit task-mode marker
    # wins over the legacy profile hint.
    explicitly_task_mode = (
        exec_mode in {"task", "project", "single"}
        or mode == "task"
        or execution_mode in {"task", "task_mode", "project"}
        or _text(metadata.get("task_mode_contract")) == "single_full_capability_main_agent"
    )
    return not explicitly_task_mode and bool(_text(metadata.get("company_profile")))


def is_company_runtime_task(task: Any) -> bool:
    """Return whether durable Task metadata identifies company-owned work."""

    return _has_company_runtime_marker(task)


def is_pure_company_ui_anchor(task: Any, runtime_session_id: str) -> bool:
    """Return whether *task* is the user-facing container for a runtime.

    A shared final-decider Task can have the same ``session_id`` as the UI
    anchor.  Work-item, role, or parent links therefore disqualify a Task even
    when its session id is an exact match.
    """

    session_id = _text(runtime_session_id)
    if not session_id or _task_session_id(task) != session_id:
        return False
    if _text(getattr(task, "parent_session_id", "")) or _text(getattr(task, "parent_id", "")):
        return False
    if linked_work_item_id_for_task(task):
        return False
    metadata = _metadata(task)
    return not any(
        metadata.get(key) not in (None, "", [], {}, False)
        for key in (
            "work_item_runtime",
            "work_item_projection_id",
            "work_item_projection_ref",
            "work_item_id",
            "work_item_role_id",
            "delegation_role_session_id",
            "shared_role_session",
            "shared_role_id",
            "company_runtime_root_session_id",
        )
    )


def _created_sort_key(value: Any) -> tuple[float, str]:
    created_at = getattr(value, "created_at", None)
    if isinstance(created_at, datetime):
        timestamp = created_at.timestamp()
    elif hasattr(created_at, "timestamp"):
        try:
            timestamp = float(created_at.timestamp())
        except Exception:
            timestamp = 0.0
    else:
        timestamp = 0.0
    return timestamp, _task_id(value)


def _checkpoint_sort_key(checkpoint: Any) -> tuple[float, float, str]:
    def _timestamp(value: Any) -> float:
        if isinstance(value, datetime):
            return value.timestamp()
        if hasattr(value, "timestamp"):
            try:
                return float(value.timestamp())
            except Exception:
                return 0.0
        return 0.0

    return (
        _timestamp(getattr(checkpoint, "updated_at", None)),
        _timestamp(getattr(checkpoint, "created_at", None)),
        _text(getattr(checkpoint, "checkpoint_id", "")),
    )


def _checkpoint_runtime_session_id(checkpoint: Any) -> str:
    payload = dict(getattr(checkpoint, "payload", {}) or {})
    return _text(
        getattr(checkpoint, "session_id", "")
        or payload.get("parent_session_id")
        or payload.get("session_id")
    )


@dataclass(frozen=True)
class CompanyRuntimeIdentity:
    """Resolved identity for one company runtime scope."""

    project_id: str
    runtime_session_id: str
    runtime_task_ids: tuple[str, ...]
    ui_anchor_task_id: str = ""
    config_source_task_id: str = ""
    pending_checkpoint_id: str = ""
    pending_checkpoint_type: str = ""
    pending_checkpoint_status: str = ""
    resumable: bool = False
    checkpoint: Any | None = field(default=None, repr=False, compare=False)


class CompanyRuntimeIdentityIndex:
    """Session-first index over preloaded Tasks and checkpoints."""

    def __init__(self, tasks: Iterable[Any], checkpoints: Iterable[Any] = ()) -> None:
        self.tasks = tuple(tasks or ())
        self.checkpoints = tuple(checkpoints or ())
        self.tasks_by_id = {
            _task_id(task): task
            for task in self.tasks
            if _task_id(task)
        }
        self.checkpoints_by_id = {
            _text(getattr(checkpoint, "checkpoint_id", "")): checkpoint
            for checkpoint in self.checkpoints
            if _text(getattr(checkpoint, "checkpoint_id", ""))
        }
        self._identities_by_session = self._build_identities()
        self._runtime_session_by_task_id: dict[str, str] = {}
        runtime_sessions_by_task_session_id: dict[str, set[str]] = {}
        for runtime_session_id, identity in self._identities_by_session.items():
            for task_id in identity.runtime_task_ids:
                self._runtime_session_by_task_id[task_id] = runtime_session_id
                task_session_id = _task_session_id(self.tasks_by_id.get(task_id))
                if task_session_id:
                    runtime_sessions_by_task_session_id.setdefault(
                        task_session_id,
                        set(),
                    ).add(runtime_session_id)
        self._runtime_session_by_task_session_id = {
            task_session_id: next(iter(runtime_session_ids))
            for task_session_id, runtime_session_ids in runtime_sessions_by_task_session_id.items()
            if len(runtime_session_ids) == 1
        }

    @property
    def identities(self) -> tuple[CompanyRuntimeIdentity, ...]:
        return tuple(self._identities_by_session.values())

    def task(self, task_id: str) -> Any | None:
        return self.tasks_by_id.get(_text(task_id))

    def resolve(
        self,
        *,
        task_id: str = "",
        task_session_id: str = "",
        runtime_session_id: str = "",
        checkpoint_id: str = "",
    ) -> CompanyRuntimeIdentity | None:
        requested_task_id = _text(task_id)
        requested_task_session_id = _text(task_session_id)
        requested_session_id = _text(runtime_session_id)
        requested_checkpoint_id = _text(checkpoint_id)

        task_scope_id = self._runtime_session_by_task_id.get(requested_task_id, "")
        session_scope_id = self._runtime_session_by_task_session_id.get(
            requested_task_session_id,
            "",
        )
        checkpoint = self.checkpoints_by_id.get(requested_checkpoint_id) if requested_checkpoint_id else None
        checkpoint_session_id = _checkpoint_runtime_session_id(checkpoint) if checkpoint is not None else ""

        candidates = {
            value
            for value in (
                requested_session_id,
                task_scope_id,
                session_scope_id,
                checkpoint_session_id,
            )
            if value
        }
        if requested_task_session_id and not session_scope_id:
            return None
        if len(candidates) != 1:
            return None
        resolved_session_id = next(iter(candidates))
        identity = self._identities_by_session.get(resolved_session_id)
        if identity is None:
            return None
        if requested_task_id and requested_task_id not in identity.runtime_task_ids:
            return None
        if requested_checkpoint_id and requested_checkpoint_id != identity.pending_checkpoint_id:
            return None
        return identity

    def _build_identities(self) -> dict[str, CompanyRuntimeIdentity]:
        active_checkpoints_by_session: dict[str, list[Any]] = {}
        for checkpoint in self.checkpoints:
            checkpoint_type = _text(getattr(checkpoint, "checkpoint_type", ""))
            checkpoint_status = _text(getattr(checkpoint, "status", "")).lower()
            if (
                checkpoint_type not in COMPANY_RUNTIME_CHECKPOINT_TYPES
                or checkpoint_status not in ACTIVE_COMPANY_RUNTIME_CHECKPOINT_STATUSES
            ):
                continue
            runtime_session_id = _checkpoint_runtime_session_id(checkpoint)
            if runtime_session_id:
                active_checkpoints_by_session.setdefault(runtime_session_id, []).append(checkpoint)

        known_sessions = set(active_checkpoints_by_session)
        for task in self.tasks:
            if not _has_company_runtime_marker(task):
                continue
            runtime_session_id = _task_parent_session_id(task) or _task_session_id(task)
            if runtime_session_id:
                known_sessions.add(runtime_session_id)

        tasks_by_session: dict[str, list[Any]] = {session_id: [] for session_id in known_sessions}
        for task in self.tasks:
            task_id = _task_id(task)
            if not task_id:
                continue
            parent_session_id = _task_parent_session_id(task)
            own_session_id = _task_session_id(task)
            runtime_session_id = parent_session_id or own_session_id
            if runtime_session_id not in known_sessions:
                continue
            if not (
                _has_company_runtime_marker(task)
                or runtime_session_id in active_checkpoints_by_session
                or is_pure_company_ui_anchor(task, runtime_session_id)
            ):
                continue
            tasks_by_session.setdefault(runtime_session_id, []).append(task)

        identities: dict[str, CompanyRuntimeIdentity] = {}
        for runtime_session_id in sorted(known_sessions):
            group = sorted(tasks_by_session.get(runtime_session_id, []), key=_created_sort_key)
            anchor = next(
                (task for task in group if is_pure_company_ui_anchor(task, runtime_session_id)),
                None,
            )
            def _has_runtime_config(task: Any) -> bool:
                metadata = _metadata(task)
                return any(
                    metadata.get(key) not in (None, "", [], {})
                    for key in (
                        "exec_mode",
                        "mode",
                        "company_profile",
                        "org_id",
                        "organization_id",
                        "preferred_agent",
                        "selected_execution_agent",
                    )
                )

            config_source = (
                anchor if anchor is not None and _has_runtime_config(anchor) else None
            ) or next(
                (
                    task for task in group
                    if _has_runtime_config(task)
                ),
                anchor or (group[0] if group else None),
            )
            checkpoint_candidates = active_checkpoints_by_session.get(runtime_session_id, [])
            checkpoint = max(checkpoint_candidates, key=_checkpoint_sort_key) if checkpoint_candidates else None
            checkpoint_status = _text(getattr(checkpoint, "status", "")).lower() if checkpoint is not None else ""
            project_id = _text(
                getattr(checkpoint, "project_id", "") if checkpoint is not None else ""
            ) or _text(getattr(config_source, "project_id", "") if config_source is not None else "") or "default"
            identities[runtime_session_id] = CompanyRuntimeIdentity(
                project_id=project_id,
                runtime_session_id=runtime_session_id,
                runtime_task_ids=tuple(_task_id(task) for task in group if _task_id(task)),
                ui_anchor_task_id=_task_id(anchor) if anchor is not None else "",
                config_source_task_id=_task_id(config_source) if config_source is not None else "",
                pending_checkpoint_id=_text(getattr(checkpoint, "checkpoint_id", "")) if checkpoint is not None else "",
                pending_checkpoint_type=_text(getattr(checkpoint, "checkpoint_type", "")) if checkpoint is not None else "",
                pending_checkpoint_status=checkpoint_status,
                resumable=checkpoint_status == "pending",
                checkpoint=checkpoint,
            )
        return identities


def build_company_runtime_identity_index(
    tasks: Iterable[Any],
    checkpoints: Iterable[Any] = (),
) -> CompanyRuntimeIdentityIndex:
    return CompanyRuntimeIdentityIndex(tasks, checkpoints)


async def load_company_runtime_identity_index(
    store: Any,
    project_id: str,
) -> CompanyRuntimeIdentityIndex:
    """Load durable records once and build the canonical runtime index."""

    tasks = await store.get_tasks(project_id=project_id)
    checkpoint_getter = getattr(store, "get_execution_checkpoints", None)
    if callable(checkpoint_getter):
        checkpoints = await checkpoint_getter(
            project_id=project_id,
            checkpoint_types=sorted(COMPANY_RUNTIME_CHECKPOINT_TYPES),
            statuses=sorted(ACTIVE_COMPANY_RUNTIME_CHECKPOINT_STATUSES),
        )
    else:
        checkpoint_getter = getattr(store, "get_pending_checkpoints", None)
        checkpoints = await checkpoint_getter(
            project_id=project_id,
            checkpoint_types=sorted(COMPANY_RUNTIME_CHECKPOINT_TYPES),
        ) if callable(checkpoint_getter) else []
    return build_company_runtime_identity_index(tasks, checkpoints)
