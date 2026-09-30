"""A fake FL Studio for the browser tools: a browser tree, its item menu, a rack and a mixer.

Browser and UI commands run through the real controller script, whose `ui`
module is wired to this fake, so the walk is tested against the controller's
own code. Rack and mixer commands are answered directly.
"""

from __future__ import annotations

import copy
from types import ModuleType
from typing import Any

FOLDER, PRESET, WAVE = -100, 4, 7
BROWSER_WINDOW = 4

TREE: dict = {
    "Current project": {},
    "Plugin database": {
        "Effects": {"Dynamics": {"Fruity Limiter.fst": None, "Maximus.fst": None}},
        "Generators": {"Synth classic": {"3x Osc.fst": None, "Sytrus.fst": None}},
    },
    "Channel presets": {"3x Osc": {"Hoover.fst": None}},
    "Packs": {
        "Drums": {
            "Hats": {"Hat 1.wav": None},
            "Kicks": {"808 Kick.wav": None, "909 Kick.wav": None, "Kick.wav": None},
        },
        "Loops": {"Kicks": {"Loop.wav": None}, "Snares.wav": None},
    },
    "Templates": {},
}


class FakeFL:
    """What FL does with the controller's browser calls, plus a rack and mixer model."""

    def __init__(self, controller: ModuleType, modules: dict, tree: dict | None = None) -> None:
        self.controller = controller
        self.tree = copy.deepcopy(tree if tree is not None else TREE)
        self.expanded: set[tuple[str, ...]] = set()
        self.cursor = 0
        self.menus = 0
        self.entry = 0
        self.browser_focused = False
        self.minimized = False
        self.hovered_entry = 0
        self.pointer_on_menu = False
        self.message: str | None = None  # title of the message box FL is showing
        self.unavailable: set[str] = set()  # plugins FL answers with a message box
        self.rack = [
            {"name": "808 Kick", "plugin": None, "track": 1},
            {"name": "Lead", "plugin": "3x Osc", "track": 2},
        ]
        self.selected = 0
        self.effects: dict[int, list[str]] = {0: ["Fruity Limiter"]}
        self.plugin_windows = 0
        self.loads = True  # False: choosing a menu entry does nothing
        self.sent: list[tuple[str, dict]] = []
        self.errors: dict[str, str] = {}

        ui = modules["ui"]
        # The controller read these constants from the (fake) midi module when it loaded.
        self.up = controller.BROWSER_DIRECTIONS["previous"]
        self.browser_window = controller.UI_WINDOWS["browser"]
        ui.navigateBrowser.side_effect = self._navigate
        ui.getFocusedNodeCaption.side_effect = lambda: self.focused_path()[-1]
        ui.getFocusedNodeFileType.side_effect = lambda: self._file_type(self.focused_path())
        ui.toggleBrowserNode.side_effect = self._toggle
        ui.selectBrowserMenuItem.side_effect = self._open_menu
        ui.down.side_effect = self._menu_down
        ui.isInPopupMenu.side_effect = lambda: self.menus > 0
        ui.setFocused.side_effect = self._focus
        ui.getFocusedFormCaption.return_value = "Browser"
        ui.getFocusedFormID.return_value = BROWSER_WINDOW

    # --- browser ---------------------------------------------------------

    def _node(self, path: tuple[str, ...]) -> Any:
        node: Any = self.tree
        for name in path:
            node = node[name]
        return node

    def _file_type(self, path: tuple[str, ...]) -> int:
        if self._node(path) is not None:
            return FOLDER
        return WAVE if path[-1].endswith(".wav") else PRESET

    def visible(self) -> list[tuple[str, ...]]:
        rows: list[tuple[str, ...]] = []

        def add(folder: dict, prefix: tuple[str, ...]) -> None:
            for name, child in folder.items():
                path = (*prefix, name)
                rows.append(path)
                if child is not None and path in self.expanded:
                    add(child, path)

        add(self.tree, ())
        return rows

    def focused_path(self) -> tuple[str, ...]:
        return self.visible()[self.cursor]

    def _navigate(self, direction: int, shift: bool) -> str:
        step = -1 if direction == self.up else 1
        self.cursor = max(0, min(len(self.visible()) - 1, self.cursor + step))
        return self.focused_path()[-1]

    def _toggle(self, value: int) -> None:
        path = self.focused_path()
        if self._node(path) is None:
            return
        if value:
            self.expanded.add(path)
        else:
            self.expanded = {p for p in self.expanded if p[:len(path)] != path}

    def _focus(self, window: int) -> None:
        self.browser_focused = window == self.browser_window and not self.minimized

    def _open_menu(self) -> None:
        if self.browser_focused and not self.minimized:
            self.menus = 1
            self.entry = self.hovered_entry  # the pointer may already be on an entry

    def _menu_down(self) -> None:
        if self.menus:
            self.entry += 1

    # --- the item menu (what fl_windows does on the real thing) ------------

    def press(self, key: int, menus: int = 1) -> bool:
        """Home and Down in the item's menu (fl_windows.press_in_popup_menu)."""
        if self.menus != menus:
            return False
        self.entry = 1 if key == 0x24 else self.entry + (key == 0x28)
        return True

    def press_enter(self) -> bool:
        if self.menus != 1:
            return False
        self.menus = 0
        if not self.loads:
            return True
        path = self.focused_path()
        stem = path[-1].rsplit(".", 1)[0]
        if path[:2] == ("Plugin database", "Effects"):
            if self.entry == 1:
                track = self.rack[self.selected]["track"]
                self.effects.setdefault(track, []).append(stem)
                self.plugin_windows += 1
        elif self.entry == 1:
            self.rack[self.selected]["name"] = stem
            self.plugin_windows += 1
        elif self.entry == 2:
            plugin = None if path[-1].endswith(".wav") else (
                stem if path[0] == "Plugin database" else path[-2])
            self.rack.append({"name": stem, "plugin": plugin, "track": 0})
            self.plugin_windows += 1
            if plugin in self.unavailable:
                self.message = "FL Studio trial"
        return True

    def restore(self) -> bool:
        was_minimized, self.minimized = self.minimized, False
        return was_minimized

    def open_message(self) -> str | None:
        return self.message

    def close_messages(self) -> int:
        closed, self.message = int(self.message is not None), None
        return closed

    def close_menus(self) -> None:
        self.menus = 0

    def close_plugin_windows(self) -> int:
        closed, self.plugin_windows = self.plugin_windows, 0
        return closed

    # --- connection --------------------------------------------------------

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0) -> dict:
        params = params or {}
        self.sent.append((action, params))
        if action in self.errors:
            return {"success": False, "id": "x", "error": self.errors[action]}
        if action.startswith(("browser.", "ui.")):
            result = self.controller.dispatch_command(action, params)
        else:
            result = self._rack_command(action, params)
        return {"success": "error" not in result, "id": "x", **result}

    def _rack_command(self, action: str, params: dict) -> dict:
        if action == "channels.getAll":
            return {"channels": [
                {"index": i, "name": c["name"], "plugin": c["plugin"],
                 "target_fx_track": c["track"], "is_selected": i == self.selected}
                for i, c in enumerate(self.rack)
            ]}
        if action == "channels.selectOne":
            self.selected = params["index"]
        elif action == "channels.setName":
            self.rack[params["index"]]["name"] = params["name"]
        elif action == "channels.routeToMixer":
            self.rack[params["channel_index"]]["track"] = params["mixer_track"]
        elif action == "mixer.getTrackEffects":
            names = self.effects.get(params["track"], [])
            return {"track": params["track"], "name": "Insert",
                    "effects": [{"slot": i, "plugin": n} for i, n in enumerate(names)]}
        elif action == "project.undo":
            return {"undone": params["steps"], "history_position": 1, "history_count": 2}
        return {}

    def actions(self) -> list[str]:
        return [action for action, _ in self.sent]
