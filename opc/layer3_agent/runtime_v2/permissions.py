"""Permission helpers for Native Runtime V2."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from opc.core.config import PermissionsV2Config, get_opc_home
from opc.core.models import PermissionResolution, PermissionScope, RiskLevel, RuntimePermissionDecision
from opc.llm.retry import LLMRetryError, call_llm_json_with_retry
from opc.layer2_organization.data_acquisition_policy import (
    ACQUISITION_SHELL_PREFIXES,
    is_projection_scoped_acquisition_shell_command,
)
from opc.layer4_tools.registry import ToolDefinition


_DEFAULT_PATH_KEYS = (
    "path",
    "file_path",
    "directory",
    "working_directory",
    "target_output_dir",
    "workspace_path",
)
_DEFAULT_COMMAND_KEYS = ("command", "cmd")
_DEFAULT_URL_KEYS = ("url",)
_READ_ONLY_PREFIXES = {
    "cat",
    "echo",
    "find",
    "git diff",
    "git log",
    "git show",
    "git status",
    "head",
    "ls",
    "node -v",
    "npm -v",
    "pwd",
    "python -V",
    "python3 -V",
    "rg",
    "tail",
    "wc",
}
_RISKY_SHELL_KEYWORDS = (
    "curl ",
    "wget ",
    "invoke-webrequest",
    "invoke-restmethod",
    "mv ",
    "cp ",
    "rm ",
    "del ",
    "remove-item",
    "git commit",
    "git push",
    "npm install",
    "pip install",
    "pnpm install",
    "cargo test",
    "pytest",
    "tee ",
    "sed -i",
    ">",
    ">>",
)
_ACQUISITION_SHELL_PREFIXES = {str(item) for item in ACQUISITION_SHELL_PREFIXES}
_ANY_GRANT_VALUE = "*"


class ToolPermissionResolver:
    """Runtime permission gate with persisted session/project/global grants."""

    def __init__(
        self,
        config: PermissionsV2Config | None = None,
        *,
        store: Any = None,
        runtime_session_id: str = "",
        project_id: str = "default",
        llm: Any | None = None,
    ) -> None:
        self.config = config or PermissionsV2Config()
        self.store = store
        self.runtime_session_id = runtime_session_id
        self.project_id = project_id or "default"
        self.llm = llm
        self._loaded = False
        self._session_grants: set[tuple[str, str, str, str, str]] = set()
        self._project_grants: set[tuple[str, str, str, str, str]] = set()
        self._global_grants: set[tuple[str, str, str, str, str]] = set()
        self._denial_counts: dict[str, int] = {}

    async def warmup(self) -> None:
        if self._loaded or not self.store or not hasattr(self.store, "list_runtime_permission_grants"):
            self._loaded = True
            return
        session_rows = await self.store.list_runtime_permission_grants(
            runtime_session_id=self.runtime_session_id or None,
            scopes=["session"],
        )
        project_rows = await self.store.list_runtime_permission_grants(
            project_id=self.project_id,
            scopes=["project"],
        )
        global_rows = await self.store.list_runtime_permission_grants(scopes=["global"])
        self._session_grants = {self._grant_key_from_row(row) for row in session_rows}
        self._project_grants = {self._grant_key_from_row(row) for row in project_rows}
        self._global_grants = {self._grant_key_from_row(row) for row in global_rows}
        self._loaded = True

    def _candidate_extractors(self) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        keys = [str(item or "").strip() for item in self.config.candidate_extractors if str(item or "").strip()]
        if not keys:
            keys = [*_DEFAULT_PATH_KEYS, *_DEFAULT_COMMAND_KEYS, *_DEFAULT_URL_KEYS]
        path_keys = tuple(item for item in keys if item in _DEFAULT_PATH_KEYS)
        command_keys = tuple(item for item in keys if item in _DEFAULT_COMMAND_KEYS)
        url_keys = tuple(item for item in keys if item in _DEFAULT_URL_KEYS)
        return (
            path_keys or _DEFAULT_PATH_KEYS,
            command_keys or _DEFAULT_COMMAND_KEYS,
            url_keys or _DEFAULT_URL_KEYS,
        )

    def _grant_key_from_row(self, row: dict[str, Any]) -> tuple[str, str, str, str, str]:
        tool_name = str(row.get("tool_name", "") or "").strip()
        candidate = str(row.get("candidate", "") or "").strip()
        metadata = dict(row.get("metadata", {}) or {})
        sandbox_mode = str(metadata.get("sandbox_mode", "") or "").strip() or _ANY_GRANT_VALUE
        allow_network = str(metadata.get("allow_network", "") or "").strip().lower() or _ANY_GRANT_VALUE
        workspace_class = str(metadata.get("workspace_class", "") or "").strip() or _ANY_GRANT_VALUE
        return self._grant_key(
            tool_name,
            candidate,
            sandbox_mode=sandbox_mode,
            allow_network=allow_network,
            workspace_class=workspace_class,
        )

    def _grant_key(
        self,
        tool_name: str,
        candidate: str,
        *,
        sandbox_mode: str,
        allow_network: str,
        workspace_class: str,
    ) -> tuple[str, str, str, str, str]:
        normalized = candidate.strip() or _ANY_GRANT_VALUE
        return (
            tool_name,
            normalized,
            sandbox_mode.strip() or _ANY_GRANT_VALUE,
            allow_network.strip().lower() or _ANY_GRANT_VALUE,
            workspace_class.strip() or _ANY_GRANT_VALUE,
        )

    def _candidate(self, arguments: dict[str, Any] | None = None) -> str:
        if not arguments:
            return _ANY_GRANT_VALUE
        path_keys, command_keys, url_keys = self._candidate_extractors()
        for key in (*path_keys, *command_keys, *url_keys):
            value = str(arguments.get(key, "") or "").strip()
            if value:
                return value
        return _ANY_GRANT_VALUE

    def _grant_context(self, task: Any = None) -> tuple[str, str, str]:
        sandbox_mode = _ANY_GRANT_VALUE
        allow_network = _ANY_GRANT_VALUE
        workspace_class = _ANY_GRANT_VALUE
        if task is None:
            return sandbox_mode, allow_network, workspace_class
        metadata = getattr(task, "metadata", {}) or {}
        execution_context = dict(metadata.get("_execution_context", {}) or {})
        sandbox = dict(execution_context.get("sandbox", {}) or {})
        sandbox_mode = str(sandbox.get("mode", "") or "").strip() or _ANY_GRANT_VALUE
        allow_network = str(bool(sandbox.get("allow_network", True))).lower()
        workspace_root = (
            str(execution_context.get("workspace_root", "") or "").strip()
            or str(metadata.get("workspace_root", "") or "").strip()
            or str(metadata.get("comms_workspace_root", "") or "").strip()
            or str(metadata.get("target_output_dir", "") or "").strip()
        )
        workspace_class = "workspace" if workspace_root else "default"
        return sandbox_mode, allow_network, workspace_class

    @staticmethod
    def _risk(value: Any, default: RiskLevel) -> RiskLevel:
        try:
            return RiskLevel(str(value or default.value))
        except Exception:
            return default

    @staticmethod
    def _looks_like_shell_tool(tool_name: str) -> bool:
        return tool_name in {"shell_exec", "python_exec", "git_commit"}

    def _normalized_tool_set(self, values: list[str]) -> set[str]:
        return {str(item or "").strip() for item in values if str(item or "").strip()}

    def _matches_path_rule(self, candidate: str, rules: list[str]) -> bool:
        if not candidate or candidate == "*":
            return False
        raw = str(candidate).strip()
        for rule in rules:
            token = str(rule or "").strip()
            if not token:
                continue
            if token == "*" or raw == token:
                return True
            try:
                rule_path = Path(token).resolve()
                candidate_path = Path(raw).resolve()
            except Exception:
                if raw.startswith(token.rstrip("\\/")):
                    return True
                continue
            if candidate_path == rule_path or rule_path in candidate_path.parents:
                return True
        return False

    def _workspace_paths(self, task: Any = None) -> list[Path]:
        roots: list[Path] = []
        metadata = getattr(task, "metadata", {}) or {} if task else {}
        for raw in (
            str(metadata.get("workspace_root", "") or "").strip(),
            str(metadata.get("comms_workspace_root", "") or "").strip(),
            str(metadata.get("output_root", "") or "").strip(),
            str(metadata.get("target_output_dir", "") or "").strip(),
        ):
            if not raw:
                continue
            try:
                path = Path(raw).resolve()
            except Exception:
                continue
            if path not in roots:
                roots.append(path)
        try:
            memory_root = (Path(get_opc_home()) / "memory").resolve()
            if memory_root not in roots:
                roots.append(memory_root)
        except Exception:
            pass
        if not roots:
            try:
                roots.append(Path.cwd().resolve())
            except Exception:
                pass
        return roots

    def _path_decision(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any] | None,
        task: Any = None,
    ) -> RuntimePermissionDecision | None:
        if not arguments:
            return None
        path_keys, _, _ = self._candidate_extractors()
        candidate = ""
        for key in path_keys:
            value = str(arguments.get(key, "") or "").strip()
            if value:
                candidate = value
                break
        if not candidate:
            return None
        if self._matches_path_rule(candidate, self.config.denied_paths):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.DENY,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.HIGH,
                rationale="Target path matches a denied runtime permission rule.",
                source="permission_rules",
            )
        if self._matches_path_rule(candidate, self.config.allowed_paths):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.PROJECT,
                risk_level=RiskLevel.LOW,
                rationale="Target path matches an explicit runtime allow rule.",
                source="permission_rules",
            )
        try:
            resolved = Path(candidate).resolve()
        except Exception:
            return None
        if tool.read_only:
            return None
        for root in self._workspace_paths(task):
            if resolved == root or root in resolved.parents:
                return None
        risk = RiskLevel.HIGH if self.config.sandbox_policy.treat_external_paths_as_high_risk else RiskLevel.MEDIUM
        return RuntimePermissionDecision(
            resolution=PermissionResolution.ASK if self.config.fail_closed else PermissionResolution.DENY,
            scope=PermissionScope.ONCE,
            risk_level=risk,
            rationale="Target path is outside the current runtime workspace roots.",
            source="path_guard",
            metadata={"candidate": candidate},
        )

    def _split_command_prefix(self, command: str) -> str:
        text = str(command or "").strip()
        if not text:
            return ""
        try:
            parts = shlex.split(text, posix=os.name != "nt")
        except Exception:
            parts = text.split()
        if not parts:
            return ""
        if len(parts) >= 2:
            return f"{parts[0]} {parts[1]}".strip()
        return parts[0]

    def _matches_command_prefix(self, command: str, prefixes: list[str]) -> bool:
        raw = str(command or "").strip()
        prefix = self._split_command_prefix(raw)
        candidates = {raw, prefix}
        for item in prefixes:
            token = str(item or "").strip()
            if not token:
                continue
            if raw == token or prefix == token:
                return True
            if raw.startswith(f"{token} ") or prefix.startswith(f"{token} "):
                return True
        return False

    def _split_shell_command_segments(self, command: str) -> list[list[str]]:
        text = str(command or "").replace("\r\n", "\n").replace("\n", " ; ").strip()
        if not text:
            return []
        try:
            lexer = shlex.shlex(text, posix=os.name != "nt", punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except Exception:
            try:
                tokens = shlex.split(text, posix=os.name != "nt")
            except Exception:
                tokens = text.split()

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in {"&&", "||", ";", "|", "&"}:
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(current)
        return segments

    def _command_has_redirection(self, command: str) -> bool:
        text = str(command or "").replace("\r\n", "\n").replace("\n", " ; ").strip()
        if not text:
            return False
        try:
            lexer = shlex.shlex(text, posix=os.name != "nt", punctuation_chars=";&|<>")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except Exception:
            return any(marker in text for marker in (">", "<"))
        return any(token in {">", ">>", "<", "<<"} for token in tokens)

    def _matches_safe_shell_prefix(self, command: str, prefixes: list[str]) -> bool:
        cleaned = " ".join(str(command or "").split()).strip()
        if not cleaned or self._command_has_redirection(cleaned):
            return False
        segments = self._split_shell_command_segments(cleaned)
        if len(segments) != 1:
            return False
        return self._matches_command_prefix(" ".join(segments[0]).strip(), prefixes)

    def _shell_ast_reason(self, command: str) -> tuple[RiskLevel, str] | None:
        lowered = str(command or "").strip().lower()
        if not lowered:
            return None
        if lowered in _READ_ONLY_PREFIXES or self._matches_command_prefix(lowered, list(_READ_ONLY_PREFIXES)):
            return RiskLevel.LOW, "Command matches a read-only shell prefix."
        for keyword in _RISKY_SHELL_KEYWORDS:
            if keyword in lowered:
                risk = RiskLevel.HIGH
                if keyword in {"curl ", "wget ", "invoke-webrequest", "invoke-restmethod"} and self.config.sandbox_policy.treat_network_as_risky:
                    risk = RiskLevel.CRITICAL
                return risk, f"Command contains risky shell operation `{keyword.strip()}`."
        return RiskLevel.MEDIUM, "Shell AST classifier could not prove the command is read-only."

    def _shell_decision(
        self,
        tool: ToolDefinition,
        arguments: dict[str, Any] | None,
        *,
        task: Any = None,
    ) -> RuntimePermissionDecision | None:
        if not self._looks_like_shell_tool(tool.name) or not arguments:
            return None
        _, command_keys, _ = self._candidate_extractors()
        command = ""
        for key in command_keys:
            value = str(arguments.get(key, "") or "").strip()
            if value:
                command = value
                break
        if not command:
            return None
        projection_scoped_low_risk = is_projection_scoped_acquisition_shell_command(
            command=command,
            task=task,
            working_directory=str(arguments.get("working_directory", "") or arguments.get("workdir", "") or "").strip(),
            target_output_dir=str(getattr(task, "metadata", {}).get("target_output_dir", "") or "").strip() if task else "",
        )
        if projection_scoped_low_risk:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.LOW,
                rationale="Command matches a work-item-scoped acquisition prefix inside the assigned workspace.",
                source="shell_prefix",
                metadata={"candidate": command},
            )
        for pattern in self.config.dangerous_shell_patterns:
            if pattern and re.search(pattern, command, flags=re.IGNORECASE):
                return RuntimePermissionDecision(
                    resolution=PermissionResolution.ASK,
                    scope=PermissionScope.ONCE,
                    risk_level=RiskLevel.CRITICAL,
                    rationale=f"Command matched dangerous shell pattern `{pattern}`.",
                    source="shell_pattern",
                    metadata={"candidate": command},
                )
        filtered_safe_prefixes = [
            item for item in self.config.safe_shell_prefixes
            if str(item or "").strip() not in _ACQUISITION_SHELL_PREFIXES
        ]
        if self._matches_safe_shell_prefix(command, filtered_safe_prefixes):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.LOW,
                rationale="Command matches a safe shell prefix.",
                source="shell_prefix",
                metadata={"candidate": command},
            )
        if self._matches_command_prefix(command, self.config.ask_shell_prefixes):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ASK,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.MEDIUM,
                rationale="Command matches an ask-first shell prefix.",
                source="shell_prefix",
                metadata={"candidate": command},
            )
        if self.config.shell_ast_validation:
            risk, rationale = self._shell_ast_reason(command) or (RiskLevel.MEDIUM, "Shell command requires manual review.")
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW if risk == RiskLevel.LOW else PermissionResolution.ASK,
                scope=PermissionScope.ONCE,
                risk_level=risk,
                rationale=rationale,
                source="shell_ast",
                metadata={"candidate": command},
            )
        if tool.requires_confirmation or self.config.fail_closed:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ASK,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.HIGH if not tool.read_only else RiskLevel.MEDIUM,
                rationale="Shell command requires explicit approval under runtime_v2.",
                source="shell_guard",
                metadata={"candidate": command},
            )
        return None

    def _candidate_matches(self, candidate: str, granted_candidate: str) -> bool:
        if granted_candidate == _ANY_GRANT_VALUE:
            return True
        if candidate == granted_candidate:
            return True
        return candidate.startswith(granted_candidate.rstrip("\\/"))

    @staticmethod
    def _sandbox_rank(mode: str) -> int:
        return {
            "workspace-write": 1,
            "elevated": 2,
            "off": 3,
        }.get(str(mode or "").strip().lower(), 0)

    def _match_grant(self, grants: set[tuple[str, str, str, str, str]], tool_name: str, candidate: str, *, task: Any = None) -> bool:
        if not grants:
            return False
        sandbox_mode, allow_network, workspace_class = self._grant_context(task)
        for grant_tool, grant_candidate, grant_sandbox_mode, grant_allow_network, grant_workspace_class in grants:
            if grant_tool != tool_name:
                continue
            if not self._candidate_matches(candidate, grant_candidate):
                continue
            if grant_sandbox_mode not in {_ANY_GRANT_VALUE, sandbox_mode}:
                if not (
                    self.config.guardian.cache_upgrade_context
                    and self._sandbox_rank(sandbox_mode) >= self._sandbox_rank(grant_sandbox_mode)
                ):
                    continue
            if grant_allow_network not in {_ANY_GRANT_VALUE, allow_network}:
                continue
            if grant_workspace_class not in {_ANY_GRANT_VALUE, workspace_class}:
                continue
            return True
        return False

    def _denial_memory_key(self, tool_name: str, arguments: dict[str, Any] | None) -> str:
        return f"{tool_name}:{self._candidate(arguments)}"

    def record_denial(self, tool_name: str, arguments: dict[str, Any] | None) -> None:
        if not self.config.denial_memory.enabled:
            return
        key = self._denial_memory_key(tool_name, arguments)
        self._denial_counts[key] = self._denial_counts.get(key, 0) + 1

    def _repeat_denial_decision(self, tool_name: str, arguments: dict[str, Any] | None) -> RuntimePermissionDecision | None:
        if not self.config.denial_memory.enabled:
            return None
        key = self._denial_memory_key(tool_name, arguments)
        repeats = self._denial_counts.get(key, 0)
        if repeats < max(1, self.config.denial_memory.repeat_threshold):
            return None
        return RuntimePermissionDecision(
            resolution=PermissionResolution.DENY,
            scope=PermissionScope.ONCE,
            risk_level=RiskLevel.HIGH,
            rationale="Repeated denial memory indicates this action should stop and ask for a new plan.",
            source="denial_memory",
            metadata={"repeated_denials": repeats},
        )

    def build_blocked_result(
        self,
        decision: RuntimePermissionDecision,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = "reject" if decision.resolution == PermissionResolution.DENY else "require_input"
        candidate = self._candidate(arguments)
        return {
            "error": decision.rationale or f"Runtime permission blocked `{tool_name}`.",
            "success": False,
            "approval": {
                "action": action,
                "risk_level": decision.risk_level.value,
                "policy_source": decision.source,
                "scope": decision.scope.value,
                "candidate": candidate,
                "explanation": decision.rationale,
                "metadata": dict(decision.metadata or {}),
            },
            "permission_context": {
                "tool_name": tool_name,
                "candidate": candidate,
                "resolution": decision.resolution.value,
                "risk_level": decision.risk_level.value,
                "source": decision.source,
            },
        }

    def predicted_decision(
        self,
        tool: ToolDefinition | None,
        arguments: dict[str, Any] | None = None,
        *,
        task: Any = None,
    ) -> RuntimePermissionDecision:
        if tool is None:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ASK if self.config.fail_closed else PermissionResolution.DENY,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.HIGH,
                rationale="Unknown tool requires manual review.",
                source="runtime_prediction",
            )
        tool_name = tool.name
        candidate = self._candidate(arguments)
        repeated_denial = self._repeat_denial_decision(tool_name, arguments)
        if repeated_denial is not None:
            return repeated_denial
        if tool_name in self._normalized_tool_set(self.config.deny_tools):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.DENY,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.HIGH,
                rationale="Tool is explicitly denied by runtime permission rules.",
                source="permission_rules",
            )
        if self._match_grant(self._session_grants, tool_name, candidate, task=task):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.SESSION,
                risk_level=RiskLevel.LOW,
                rationale="Allowed by runtime session grant.",
                source="runtime_session_grant",
            )
        if self._match_grant(self._project_grants, tool_name, candidate, task=task):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.PROJECT,
                risk_level=RiskLevel.LOW,
                rationale="Allowed by persisted project grant.",
                source="runtime_project_grant",
            )
        if self._match_grant(self._global_grants, tool_name, candidate, task=task):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.GLOBAL,
                risk_level=RiskLevel.LOW,
                rationale="Allowed by persisted global grant.",
                source="runtime_global_grant",
            )
        if tool_name in self._normalized_tool_set(self.config.allow_tools):
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.PROJECT,
                risk_level=RiskLevel.LOW,
                rationale="Tool is explicitly allowed by runtime permission rules.",
                source="permission_rules",
            )
        path_decision = self._path_decision(tool, arguments, task=task)
        if path_decision is not None:
            return path_decision
        shell_decision = self._shell_decision(tool, arguments, task=task)
        if shell_decision is not None:
            return shell_decision
        if tool.requires_confirmation:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ASK,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.MEDIUM,
                rationale="Tool is marked as requiring confirmation.",
                source="runtime_prediction",
                metadata={"candidate": candidate},
            )
        if self.config.guardian.enabled and self.config.guardian.auto_allow_read_only and tool.read_only:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.ONCE,
                risk_level=RiskLevel.LOW,
                rationale="Guardian pre-check marked the tool as deterministic read-only.",
                source="guardian",
                metadata={"candidate": candidate},
            )
        return RuntimePermissionDecision(
            resolution=PermissionResolution.ALLOW,
            scope=PermissionScope.ONCE,
            risk_level=RiskLevel.LOW,
            rationale="No runtime permission warning triggered.",
            source="runtime_prediction",
            metadata={"candidate": candidate},
        )

    async def refine_decision(
        self,
        decision: RuntimePermissionDecision,
        *,
        tool: ToolDefinition | None,
        arguments: dict[str, Any] | None,
        task: Any = None,
    ) -> RuntimePermissionDecision:
        if decision.resolution != PermissionResolution.ASK:
            return decision
        if not self.config.classifier_enabled or not self.config.llm_classifier_model or self.llm is None or tool is None:
            return decision
        payload = {
            "tool_name": tool.name,
            "arguments": arguments or {},
            "candidate": self._candidate(arguments),
            "project_id": getattr(task, "project_id", self.project_id),
            "heuristic_rationale": decision.rationale,
        }
        def _validate_classifier(parsed: Any) -> str | None:
            if not isinstance(parsed, dict):
                return "Top-level response must be a JSON object."
            try:
                score_val = float(parsed.get("score", 0.5) or 0.5)
            except (TypeError, ValueError):
                return "`score` must be a number between 0 and 1."
            if score_val < 0 or score_val > 1:
                return "`score` must be a number between 0 and 1."
            return None

        try:
            parsed = await call_llm_json_with_retry(
                self.llm,
                system=(
                    "You are a runtime permission classifier.\n"
                    "Return strict JSON with keys `score` and `reason`.\n"
                    "`score` is a float between 0 and 1 where 0 is clearly safe and 1 is clearly unsafe.\n"
                    "Classify file mutation, shell execution, path escape risk, and network side effects conservatively."
                ),
                payload=payload,
                task_type="quick_tasks",
                validator=_validate_classifier,
                label="runtime_permission_classifier",
            )
        except LLMRetryError:
            return decision
        score = float(parsed.get("score", 0.5) or 0.5)
        reason = str(parsed.get("reason", "") or decision.rationale)
        thresholds = self.config.classifier_thresholds
        if score <= thresholds.allow:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=decision.scope,
                risk_level=RiskLevel.LOW,
                rationale=reason or "Classifier marked the action safe.",
                source="llm_classifier",
                metadata={**dict(decision.metadata or {}), "classifier_score": score},
            )
        if score >= thresholds.deny:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.DENY,
                scope=decision.scope,
                risk_level=RiskLevel.HIGH,
                rationale=reason or "Classifier marked the action unsafe.",
                source="llm_classifier",
                metadata={**dict(decision.metadata or {}), "classifier_score": score},
            )
        return RuntimePermissionDecision(
            resolution=PermissionResolution.ASK,
            scope=decision.scope,
            risk_level=RiskLevel.MEDIUM if score < thresholds.ask else RiskLevel.HIGH,
            rationale=reason or decision.rationale,
            source="llm_classifier",
            metadata={**dict(decision.metadata or {}), "classifier_score": score},
        )

    def decision_from_result(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        result: dict[str, Any],
    ) -> RuntimePermissionDecision:
        approval = dict(result.get("approval", {}) or {})
        action = str(approval.get("action", "") or "").strip().lower()
        if action in {"require_input", "escalate"}:
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ASK,
                scope=PermissionScope.ONCE,
                risk_level=self._risk(approval.get("risk_level"), RiskLevel.MEDIUM),
                rationale=str(result.get("error", "") or "Awaiting explicit permission."),
                source="approval_engine",
                metadata=approval,
            )
        if action == "reject":
            self.record_denial(tool_name, arguments)
            return RuntimePermissionDecision(
                resolution=PermissionResolution.DENY,
                scope=PermissionScope.ONCE,
                risk_level=self._risk(approval.get("risk_level"), RiskLevel.HIGH),
                rationale=str(result.get("error", "") or "Permission denied."),
                source="approval_engine",
                metadata=approval,
            )
        human_reply = str(approval.get("human_reply") or result.get("human_reply") or "").strip().lower()
        candidate = self._candidate(arguments)
        grant = self._grant_key(
            tool_name,
            candidate,
            sandbox_mode=_ANY_GRANT_VALUE,
            allow_network=_ANY_GRANT_VALUE,
            workspace_class=_ANY_GRANT_VALUE,
        )
        if human_reply == "approve_session":
            self._session_grants.add(grant)
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.SESSION,
                risk_level=RiskLevel.LOW,
                rationale="Approved for this runtime session.",
                source="human_escalation",
                metadata=approval,
            )
        if human_reply == "always_project":
            self._project_grants.add(grant)
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.PROJECT,
                risk_level=RiskLevel.LOW,
                rationale="Approved for this project.",
                source="human_escalation",
                metadata=approval,
            )
        if human_reply == "always_global":
            self._global_grants.add(grant)
            return RuntimePermissionDecision(
                resolution=PermissionResolution.ALLOW,
                scope=PermissionScope.GLOBAL,
                risk_level=RiskLevel.LOW,
                rationale="Approved globally.",
                source="human_escalation",
                metadata=approval,
            )
        return RuntimePermissionDecision(
            resolution=PermissionResolution.ALLOW,
            scope=PermissionScope.ONCE,
            risk_level=RiskLevel.LOW,
            rationale="Tool execution allowed.",
            source="approval_engine",
            metadata=approval,
        )
