"""Stand-ins for FL Studio's embedded API modules.

The FL-side scripts (fl_controller/, scripts/*.pyscript) import modules that only
exist inside FL Studio. These fakes let the scripts be imported and exercised by
pytest.
"""

from __future__ import annotations

import types
from typing import Any, Callable
from unittest.mock import MagicMock

FL_CONTROLLER_MODULES = (
    "channels", "general", "midi", "mixer", "patterns", "plugins", "transport",
)


class ToolCollector:
    """Minimal stand-in for FastMCP: records functions registered via @mcp.tool()."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Callable:
        def register(fn: Callable) -> Callable:
            self.tools[fn.__name__] = fn
            return fn

        return register


class ScriptedConnection:
    """Stand-in FL connection: records commands, answers with canned results.

    Results are successful unless the canned result has an "error" key.
    """

    def __init__(self, results: dict[str, dict] | None = None) -> None:
        self.results = results or {}
        self.sent: list[tuple[str, dict]] = []

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        self.sent.append((action, params or {}))
        result = self.results.get(action, {})
        return {"success": "error" not in result, "id": "x", **result}


class FakeNote:
    """Mirror of flpianoroll.Note with FL's default field values."""

    def __init__(self) -> None:
        self.number = 60
        self.time = 0
        self.length = 0
        self.velocity = 0.8
        self.release = 0.5
        self.pan = 0.5
        self.color = 0
        self.fcut = 0.5
        self.fres = 0.5
        self.slide = False
        self.porta = False
        self.pitchofs = 0
        self.selected = False
        self.muted = False


class FakeScore:
    """Mirror of flpianoroll.score backed by a plain list."""

    def __init__(self, ppq: int = 96) -> None:
        self.PPQ = ppq
        self.notes: list[FakeNote] = []

    @property
    def noteCount(self) -> int:  # noqa: N802 - FL API name
        return len(self.notes)

    def addNote(self, note: FakeNote) -> None:  # noqa: N802 - FL API name
        self.notes.append(note)

    def getNote(self, index: int) -> FakeNote:  # noqa: N802 - FL API name
        return self.notes[index]

    def deleteNote(self, index: int) -> None:  # noqa: N802 - FL API name
        del self.notes[index]


def make_controller_modules() -> dict[str, MagicMock]:
    """One MagicMock per FL API module the controller script imports."""
    return {name: MagicMock(name=name) for name in FL_CONTROLLER_MODULES}


def make_flpianoroll(ppq: int = 96) -> types.ModuleType:
    module = types.ModuleType("flpianoroll")
    module.Note = FakeNote
    module.score = FakeScore(ppq)
    return module
