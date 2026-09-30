"""Placing pattern clips in FL Studio's playlist, without taking the focus.

FL's API can neither place nor read playlist clips, but the playlist's window
takes *posted* mouse clicks while FL stays in the background (seen live, FL
2026): with the draw tool, a click in the clip area puts the active pattern
there. What the API does give is enough to aim and to check:

- `ui.scrollWindow` makes a bar the first one showing, so the click always goes
  to the left edge of the clip area, whatever the zoom;
- a click on a track's header selects the track, and `playlist.isTrackSelected`
  tells which one it was: that is how a track's row is found;
- the song's length tells where the last clip ends, and a click on the time
  ruler moves the song position to the bar under it, which can be read: that
  is how every click is aimed.

The playlist's own menu (the arrow in its caption) does the rest: Select > Select
all and Edit > Delete to empty the playlist, Snap > Bar so that a click lands on
the bar line. Windows only.
"""

from __future__ import annotations

import platform
import time

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_windows import PlaylistPanel

MENU_TIMEOUT = 1.5  # seconds for a popup menu to open
POLL_INTERVAL = 0.02
ATTEMPTS = 3
RETRY_DELAY = 0.4
# Seen live: a letter posted the moment a submenu's window appears can go unheard.
SUBMENU_SETTLE = 0.05
# FL handles a posted click before the next controller command, but only just.
CLICK_SETTLE = 0.03
CHECK_TIMEOUT = 2.0  # seconds for the song's length to show what was placed
SHOW_DELAY = 0.3

# The playlist menu's entries, counted down from the first one (Edit).
SNAP_ROW = 3
SELECT_ROW = 4
EDIT_KEY = ord("E")
DELETE_KEY = ord("D")  # in Edit
SELECT_ALL_KEY = ord("A")  # in Select
DESELECT_KEY = ord("D")  # in Select
# In the Snap submenu "Bar" is the third entry from the end, "Main" the first.
SNAP_BAR_KEYS = (fl_windows.VK_END, fl_windows.VK_UP, fl_windows.VK_UP, fl_windows.VK_RETURN)
SNAP_MAIN_KEYS = (fl_windows.VK_HOME, fl_windows.VK_RETURN)
DRAW_TOOL_KEY = ord("P")
ZOOM_KEY = ord("Z")
# In the Zoom submenu, counted down from "Zoom in". Seen live in a 730 px wide
# clip area: quick zoom 1 shows about 90 bars, 2 about 20, 3 four; "Zoom out
# far" shows the whole song.
QUICK_ZOOM_2_ROW = 3
ZOOM_OUT_FAR_ROW = 5

# A click this far right of the clip area's edge is inside the first bar showing.
CLICK_INSET = 4
# The time ruler lies between the scroll bar and the clip area.
RULER_ABOVE_GRID = 10
SCROLL_TIMEOUT = 2.0  # seconds for the playlist to scroll to a bar
SCROLL_SETTLE = 0.5  # seconds a scroll is given when it can't be checked
# A header click that selects nothing is tried again this many pixels higher.
HEADER_NUDGE = 8
HEADER_NUDGES = 4
# Halving the clip area's height this often finds a row a few pixels high.
TRACK_SEARCH_STEPS = 12
# Tracks looked at, beyond the wanted one, for the one a click selected.
TRACKS_BELOW = 60
LENGTH_TOLERANCE_BARS = 0.01


class PlaylistError(Exception):
    """The playlist could not be driven; the message says why."""


def _fl(action: str, params: dict | None = None) -> dict:
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise PlaylistError(str(e)) from e
    if "error" in result:
        raise PlaylistError(result["error"])
    return result


def state() -> dict:
    """{"visible", "length_bars", "bar_ticks", "track_count", ...} of the playlist."""
    return _fl("playlist.getState")


def _wait_for_menus(count: int) -> bool:
    deadline = time.monotonic() + MENU_TIMEOUT
    while True:
        if fl_windows.popup_menu_count() == count:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def _submenu_keys(row: int) -> tuple[int, ...]:
    """Keys that open the submenu of the playlist menu's entry `row` rows below the first."""
    return (fl_windows.VK_HOME, *[fl_windows.VK_DOWN] * row, fl_windows.VK_RIGHT)


def _walk(to_submenu: tuple[int, ...], in_submenu: tuple[int, ...]) -> bool | None:
    """One menu walk: True if the entry was chosen, None if the pointer got in the way."""
    if fl_windows.popup_menu_count() != 0 or not fl_windows.click_playlist_menu_arrow():
        return False
    try:
        if not _wait_for_menus(1):
            return False
        for key in to_submenu:
            if not fl_windows.press_in_popup_menu(key, menus=1):
                return False
        if not _wait_for_menus(2):
            return False
        time.sleep(SUBMENU_SETTLE)
        for key in in_submenu[:-1]:
            if not fl_windows.press_in_popup_menu(key, menus=2):
                return False
        # The pointer moves a menu's highlight: no choice is made under it.
        if fl_windows.pointer_over_menu():
            return None
        return fl_windows.press_in_popup_menu(in_submenu[-1], menus=2, chooses=True)
    finally:
        fl_windows.close_popup_menus()  # no menu may be left open, whatever happened


def _choose(to_submenu: tuple[int, ...], in_submenu: tuple[int, ...], what: str) -> None:
    """Choose an entry of one of the playlist menu's submenus; raises PlaylistError."""
    for _ in range(ATTEMPTS):
        outcome = _walk(to_submenu, in_submenu)
        if outcome:
            return
        if outcome is False:
            raise PlaylistError(
                f"Could not {what}: the playlist's menu did not open as expected "
                "(is another menu or a dialog open in FL Studio?)"
            )
        time.sleep(RETRY_DELAY)  # the pointer was over the menu: let it move on
    raise PlaylistError(f"Could not {what}: the mouse pointer is over FL Studio's menu")


def _wait_for_length(bars: float) -> float:
    """Wait for the song to be `bars` long; returns the length it has in the end."""
    deadline = time.monotonic() + CHECK_TIMEOUT
    while True:
        length = state()["length_bars"]
        if abs(length - bars) <= LENGTH_TOLERANCE_BARS or time.monotonic() >= deadline:
            return length
        time.sleep(POLL_INTERVAL)


def open_panel() -> PlaylistPanel:
    """Show the playlist, switch it to the draw tool and find its parts on screen."""
    if platform.system() != "Windows":
        raise PlaylistError("Placing clips in the playlist is only supported on Windows")
    if fl_windows.restore_if_minimized():
        time.sleep(SHOW_DELAY)
    hidden = not state()["visible"]
    # In front of FL's other panels too: the piano roll often lies over it.
    _fl("ui.focusWindow", {"window": "playlist"})
    if hidden:
        time.sleep(SHOW_DELAY)
    panel = fl_windows.playlist_panel()
    if panel is None:
        raise PlaylistError(
            "The playlist isn't showing inside FL Studio's main window (a detached or "
            "hidden playlist can't be used)."
        )
    if panel.grid_bottom - panel.grid_top < 2 * TRACK_SEARCH_STEPS:
        raise PlaylistError("The playlist window is too small to work in.")
    fl_windows.press_key_in_window(panel.hwnd, DRAW_TOOL_KEY)
    return panel


def _zoom(row: int) -> None:
    keys = (fl_windows.VK_HOME, *[fl_windows.VK_DOWN] * row, fl_windows.VK_RETURN)
    _choose((ZOOM_KEY,), keys, "zoom the playlist")


def zoom_for_placing() -> None:
    """A zoom at which a bar is wide enough to aim at, whatever it was before."""
    _zoom(QUICK_ZOOM_2_ROW)


def show_whole_song() -> None:
    """Bring the playlist to the front and zoom out until the whole song shows."""
    if platform.system() != "Windows":
        raise PlaylistError("The playlist can only be zoomed on Windows")
    _fl("ui.focusWindow", {"window": "playlist"})
    time.sleep(SHOW_DELAY)
    _zoom(ZOOM_OUT_FAR_ROW)
    time.sleep(SHOW_DELAY)


def clear() -> None:
    """Delete every clip in the playlist."""
    if state()["length_bars"] == 0:
        return
    select = _submenu_keys(SELECT_ROW)
    _choose(select, (SELECT_ALL_KEY,), "select the playlist's clips")
    try:
        for _ in range(ATTEMPTS):
            _choose((EDIT_KEY,), (DELETE_KEY,), "delete the playlist's clips")
            left = _wait_for_length(0)
            if not left:
                break
            # The letter went unheard and the menu is still open; it only shows
            # as open once it can no longer be one that is fading after a choice.
            time.sleep(fl_windows.MENU_FADE_SECONDS)
            fl_windows.close_popup_menus()
        else:
            raise PlaylistError(
                f"The playlist could not be emptied: the song is still {left:g} bars"
            )
    except PlaylistError:
        try:
            _deselect()
        except PlaylistError:
            pass  # the failed delete is the error to report
        raise
    _deselect()


def _deselect() -> None:
    """Drop the playlist's selection.

    Select all leaves the clips and the whole song's time selected, and FL
    would loop and render a time selection instead of the song.
    """
    _choose(_submenu_keys(SELECT_ROW), (DESELECT_KEY,), "drop the playlist's selection")


def snap_to_bars(on: bool) -> None:
    """Make clicks land on bar lines (Snap > Bar), or put the snap back to Main."""
    keys = SNAP_BAR_KEYS if on else SNAP_MAIN_KEYS
    _choose(_submenu_keys(SNAP_ROW), keys, "set the playlist's snap")


def _click(panel: PlaylistPanel, x: int, y: int) -> None:
    if not fl_windows.click_in_window(panel.hwnd, x, y):
        raise PlaylistError("FL Studio's playlist window did not take a click")
    time.sleep(CLICK_SETTLE)


def _track_at(panel: PlaylistPanel, y: int, upto: int) -> int | None:
    """The track whose header is at height `y`; None where a click selects nothing.

    Seen live: the lowest part of a header row doesn't select its track, so the
    selection is emptied first and an empty one afterwards means "missed".
    """
    _fl("playlist.deselectTracks")
    _click(panel, (panel.header_left + panel.grid_left) // 2, y)
    selected = _fl("playlist.getSelectedTrack", {"upto": upto})["selected"]
    return selected[0] if len(selected) == 1 else None


def find_track(panel: PlaylistPanel, track: int) -> int:
    """A height inside `track`'s row, found by clicking track headers.

    The rows' heights aren't known (each can be resized), but the track a header
    click selects grows with the height clicked, so the clip area is halved
    until the click hits the wanted track.
    """
    _fl("playlist.scrollTo", {"track": track})
    time.sleep(SCROLL_SETTLE)
    low, high = panel.grid_top + 1, panel.grid_bottom
    for _ in range(TRACK_SEARCH_STEPS):
        if low > high:
            break
        y = (low + high) // 2
        found = None
        for nudge in range(0, HEADER_NUDGES * HEADER_NUDGE, HEADER_NUDGE):
            if y - nudge <= panel.grid_top:
                break
            found = _track_at(panel, y - nudge, track + TRACKS_BELOW)
            if found is not None:
                break
        if found is None:
            raise PlaylistError(
                "Clicking the playlist's track headers selected no track; the playlist's "
                "layout isn't the expected one."
            )
        if found == track:
            return y - nudge
        if found < track:
            low = y + 1
        else:
            high = y - nudge - 1
    raise PlaylistError(f"Playlist track {track} could not be found in the playlist window")


def _aim(panel: PlaylistPanel, bar: int, bar_ticks: int) -> int:
    """Make `bar` the first bar showing; returns where to click for it.

    FL scrolls smoothly, so a click right after the scroll command would land
    on the bar that was there before. A click on the time ruler moves the song
    position to the bar under it, which can be read: the ruler is clicked until
    it answers with the wanted bar. (The song position stops at the end of the
    last clip, so this only works inside the song.)
    """
    _fl("playlist.scrollTo", {"bar": bar})
    x = panel.grid_left + CLICK_INSET
    deadline = time.monotonic() + SCROLL_TIMEOUT
    while True:
        _click(panel, x, panel.grid_top - RULER_ABOVE_GRID)
        if _fl("playlist.getSongPosition")["ticks"] == (bar - 1) * bar_ticks:
            return x
        if time.monotonic() >= deadline:
            raise PlaylistError(
                f"The playlist did not scroll to bar {bar} (is it zoomed out so far that a "
                "bar is only a few pixels wide?)"
            )
        time.sleep(POLL_INTERVAL)


def place_last_clip(panel: PlaylistPanel, y: int, bar: int, end_bar: float) -> None:
    """Put the clip that ends the song into the *empty* playlist, and check it.

    Nothing can be aimed at beyond the song's end, so this one click is made
    after a pause for the scrolling, and checked by the length the song gets:
    `end_bar` bars if the clip landed on `bar`.
    """
    _fl("playlist.scrollTo", {"bar": bar})
    time.sleep(SCROLL_SETTLE)
    _click(panel, panel.grid_left + CLICK_INSET, y)
    length = _wait_for_length(end_bar)
    if abs(length - end_bar) > LENGTH_TOLERANCE_BARS:
        raise PlaylistError(
            f"A clip placed at bar {bar} should make the song {end_bar:g} bars long, but it "
            f"is {length:g}: the click did not land where it was meant to. Is the playlist "
            "showing as usual (fl_screenshot)?"
        )


def place_clips(panel: PlaylistPanel, y: int, bars: list[int], bar_ticks: int) -> None:
    """Put a clip of the active pattern at each of `bars` (1-based, inside the song).

    The row must hold no clips to the left of those bars. They are placed from
    the last to the first: the ruler answers with the wanted bar as soon as the
    scrolling is within half a bar of it, and a click made then must not find
    the clip placed just before under it (seen live: it selects that clip
    instead of placing one).
    """
    for bar in sorted(bars, reverse=True):
        x = _aim(panel, bar, bar_ticks)
        _click(panel, x, y)


def close_menus_left_open() -> None:
    """Close a menu FL left open without acting on the choice made in it."""
    try:
        fl_windows.close_popup_menus()
    except Exception:  # noqa: BLE001 - clean-up must not hide the error being reported
        pass
