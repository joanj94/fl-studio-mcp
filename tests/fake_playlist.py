"""A stand-in for FL Studio's playlist as the server drives it: by clicks, menus and commands.

It behaves the way the real one was seen to (FL 2026): a click in the clip area
places the active pattern, unless a clip is already there; the view scrolls
smoothly, so it lags behind a scroll command; a click on the time ruler moves
the song position, but never beyond the song's end; the lowest part of a track
header doesn't select the track; Select all leaves a time selection behind.
"""

from __future__ import annotations

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.fl_windows import PlaylistPanel

BAR_TICKS = 384
BAR_PIXELS = 50
HEADER_DEAD_ZONE = 12  # pixels at the bottom of a header row that select nothing
MENU = ("Edit", "Tools", "View", "Snap", "Select", "Group", "Zoom")
ZOOMS = ("Zoom in", "Zoom out", "Quick zoom 1", "Quick zoom 2", "Quick zoom 3",
         "Zoom out far", "Zoom on selection")
SNAPS = ("Main", "Line", "Cell", "(none)", "1/6 step", "1/4 step", "1/3 step", "1/2 step",
         "Step", "1/6 beat", "1/4 beat", "1/3 beat", "1/2 beat", "Beat", "Bar", "Events",
         "Markers")


class FakePlaylist:
    """The playlist, its menu and the controller commands about it, in one model."""

    def __init__(self, pattern_bars: dict[int, int]) -> None:
        self.panel: PlaylistPanel | None = PlaylistPanel(
            hwnd=7, header_left=100, grid_left=200, grid_top=50, grid_bottom=650
        )
        self.row_height = 40
        self.pattern_bars = pattern_bars
        self.pattern_names = {index: f"Pattern {index}" for index in pattern_bars}
        self.clips: set[tuple[int, int, int]] = set()  # (track, first bar, pattern)
        self.pattern = 1
        self.visible = True
        self.in_front = False
        self.zoom = "as the user left it"
        self.tool = "slice"
        self.snap = "Main"
        self.mode = "pattern"
        self.track_names: dict[int, str] = {}
        self.selected_tracks: list[int] = []
        self.time_selection = False
        self.clips_selected = False
        self.position = 0
        self.view_bar = 1
        self.scroll_target = 1
        self.scroll_lag = 0  # clicks a scroll command takes to show
        self._lag_left = 0
        self.menus = 0
        self._row = 0
        self._submenu: str | None = None
        self._entry = 0
        self.pointer_on_menu = False
        self.deaf_deletes = 0  # how often Delete's letter goes unheard
        self.sent: list[str] = []
        self.clicks_on_clips = 0

    # --- what the song is ---------------------------------------------------

    @property
    def length_bars(self) -> int:
        return max((bar - 1 + self.pattern_bars[p] for _, bar, p in self.clips), default=0)

    def _clip_at(self, track: int, bar: float) -> bool:
        return any(
            t == track and first <= bar < first + self.pattern_bars[p]
            for t, first, p in self.clips
        )

    # --- the controller -----------------------------------------------------

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        params = params or {}
        self.sent.append(action)
        result = self._answer(action, params)
        return {"success": "error" not in result, "id": "x", **result}

    def _answer(self, action: str, params: dict) -> dict:
        if action == "project.getInfo":
            return {"beats_per_bar": 4, "patterns": [
                {"index": index, "name": self.pattern_names[index], "length_beats": bars * 4.0}
                for index, bars in self.pattern_bars.items()
            ]}
        if action == "playlist.getState":
            return {"visible": self.visible, "length_bars": float(self.length_bars),
                    "length_ticks": self.length_bars * BAR_TICKS, "bar_ticks": BAR_TICKS,
                    "track_count": 500}
        if action == "ui.focusWindow":
            assert params == {"window": "playlist"}
            self.visible = self.in_front = True
        elif action == "playlist.scrollTo":
            if params.get("bar") is not None:
                self.scroll_target = params["bar"]
                self._lag_left = self.scroll_lag
                if not self._lag_left:
                    self.view_bar = self.scroll_target
        elif action == "playlist.deselectTracks":
            self.selected_tracks = []
        elif action == "playlist.getSelectedTrack":
            return {"selected": [t for t in self.selected_tracks if t <= params["upto"]]}
        elif action == "playlist.getSongPosition":
            return {"ticks": self.position}
        elif action == "playlist.getTracks":
            return {"tracks": [
                {"track": t, "name": self.track_names.get(t, f"Track {t}"), "is_muted": False}
                for t in range(1, params["count"] + 1)
            ]}
        elif action == "playlist.setTrackName":
            if params["name"]:
                self.track_names[params["track"]] = params["name"]
            else:
                self.track_names.pop(params["track"], None)  # FL: back to "Track N"
        elif action == "patterns.select":
            self.pattern = params["index"]
        elif action == "transport.setLoopMode":
            self.mode = params["mode"]
        elif action == "transport.setPosition":
            self.position = params["position"]
        return {}

    # --- the window ---------------------------------------------------------

    def settle(self) -> None:
        """Time passes: a scroll finishes."""
        self._lag_left = 0
        self.view_bar = self.scroll_target

    def _scroll_a_little(self) -> None:
        if self._lag_left:
            self._lag_left -= 1
            if not self._lag_left:
                self.view_bar = self.scroll_target

    def click(self, hwnd: int, x: int, y: int) -> bool:
        panel = self.panel
        assert panel is not None and hwnd == panel.hwnd
        bar = self.view_bar + round((x - panel.grid_left) / BAR_PIXELS)
        track = (y - panel.grid_top) // self.row_height + 1
        if x >= panel.grid_left and y < panel.grid_top:  # the time ruler
            last_tick = max(self.length_bars * BAR_TICKS - 1, 0)
            self.position = min((bar - 1) * BAR_TICKS, last_tick)
        elif x < panel.grid_left and y >= panel.grid_top:  # a track header
            in_row = (y - panel.grid_top) % self.row_height
            if in_row < self.row_height - HEADER_DEAD_ZONE:
                self.selected_tracks = [track]
        elif y >= panel.grid_top and self.tool == "draw":
            assert self.snap == "Bar", "a click without bar snap lands off the bar line"
            if self._clip_at(track, bar):
                self.clicks_on_clips += 1  # FL selects that clip; nothing is placed
            else:
                self.clips.add((track, bar, self.pattern))
        self._scroll_a_little()
        return True

    def press_key(self, hwnd: int, key: int) -> bool:
        if key == ord("P"):
            self.tool = "draw"
        return True

    # --- the playlist's menu --------------------------------------------------

    def click_menu_arrow(self) -> bool:
        if self.panel is None:
            return False
        self.menus, self._row, self._submenu = 1, 0, None
        return True

    def press(self, key: int, menus: int = 1, chooses: bool = False) -> bool:
        if self.menus != menus:
            return False
        if menus == 1:
            if key == fl_windows.VK_HOME:
                self._row = 0
            elif key == fl_windows.VK_DOWN:
                self._row += 1
            elif key in (fl_windows.VK_RIGHT, ord("E"), ord("Z")):
                letters = {ord("E"): "Edit", ord("Z"): "Zoom"}
                self._submenu = letters.get(key, MENU[self._row])
                self.menus, self._entry = 2, 0
            return True
        if self._submenu == "Zoom":
            if key == fl_windows.VK_HOME:
                self._entry = 0
            elif key == fl_windows.VK_DOWN:
                self._entry += 1
            elif key == fl_windows.VK_RETURN:
                self.zoom, self.menus = ZOOMS[self._entry], 0
            return True
        if self._submenu == "Snap":
            if key == fl_windows.VK_HOME:
                self._entry = 0
            elif key == fl_windows.VK_END:
                self._entry = len(SNAPS) - 1
            elif key == fl_windows.VK_UP:
                self._entry -= 1
            elif key == fl_windows.VK_RETURN:
                self.snap, self.menus = SNAPS[self._entry], 0
        elif self._submenu == "Select" and key == ord("A"):
            self.clips_selected = self.time_selection = bool(self.clips)
            self.menus = 0
        elif self._submenu == "Select" and key == ord("D"):
            self.clips_selected = self.time_selection = False
            self.menus = 0
        elif self._submenu == "Edit" and key == ord("D") and self.clips_selected:
            if self.deaf_deletes:
                self.deaf_deletes -= 1  # the letter goes unheard: the menu stays open
            else:
                self.clips, self.clips_selected, self.menus = set(), False, 0
        return True

    def close_menus(self) -> None:
        self.menus = 0
