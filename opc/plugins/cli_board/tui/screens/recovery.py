"""Recovery modal screen for the CLI board."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from opc.plugins.cli_board.services.recovery import RecoveryStatus


@dataclass
class RecoveryAction:
    action: str  # "resume" | "cancel" | "dismiss"
    parent_task_id: str = ""


class RecoveryScreen(ModalScreen[RecoveryAction | None]):
    """Show interrupted company runtimes with resume/cancel options."""

    DEFAULT_CSS = """
    RecoveryScreen {
        align: center middle;
    }

    .recovery-dialog {
        width: 88;
        max-width: 90%;
        height: auto;
        max-height: 80%;
        border: solid $primary;
        background: $surface;
        padding: 1 2;
    }

    .recovery-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .recovery-runtime {
        margin-bottom: 1;
        padding: 1;
        border: round $secondary;
    }

    .recovery-actions {
        align-horizontal: right;
        height: auto;
        margin-top: 1;
    }

    .recovery-empty {
        color: $text-muted;
        margin: 1;
    }
    """

    BINDINGS = [("escape", "dismiss_screen", "Close")]

    def __init__(self, status: RecoveryStatus) -> None:
        super().__init__()
        self.status = status

    def compose(self) -> ComposeResult:
        with Vertical(classes="recovery-dialog"):
            yield Static("Interrupted Runtimes", classes="recovery-title")

            if not self.status.interrupted:
                yield Static("No interrupted runtimes found.", classes="recovery-empty")
            else:
                with VerticalScroll():
                    for wf in self.status.interrupted:
                        with Vertical(classes="recovery-runtime"):
                            # Runtime header
                            active = wf.parent_task_id in set(self.status.active_recoveries)
                            status_label = " (recovering...)" if active else ""
                            yield Label(f"{wf.title}{status_label}")
                            yield Static(
                                f"  Profile: {wf.profile or 'unknown'}  "
                                f"Interrupted: {wf.interrupted_at[:19] if wf.interrupted_at else '?'}"
                            )

                            # Work-item summary
                            done = sum(1 for s in wf.work_items if s.status == "done")
                            total = len(wf.work_items)
                            failed = sum(1 for s in wf.work_items if s.interrupted)
                            yield Static(
                                f"  Work items: {done}/{total} done, {failed} interrupted"
                            )

                            if not active:
                                with Horizontal():
                                    yield Button(
                                        "Resume",
                                        id=f"resume-{wf.parent_task_id}",
                                        variant="primary",
                                    )
                                    yield Button(
                                        "Cancel",
                                        id=f"cancel-{wf.parent_task_id}",
                                        variant="error",
                                    )

            with Horizontal(classes="recovery-actions"):
                yield Button("Close", id="close-recovery")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "close-recovery":
            self.dismiss(None)
            return
        if btn_id.startswith("resume-"):
            task_id = btn_id[len("resume-"):]
            self.dismiss(RecoveryAction(action="resume", parent_task_id=task_id))
            return
        if btn_id.startswith("cancel-"):
            task_id = btn_id[len("cancel-"):]
            self.dismiss(RecoveryAction(action="cancel", parent_task_id=task_id))
            return

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
