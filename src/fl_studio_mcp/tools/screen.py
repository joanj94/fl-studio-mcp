"""Looking at FL Studio: a picture of its window for what the API can't tell.

The API reads no playlist clips, no message box texts and no plugin windows;
a picture shows them. It is taken with FL in the background.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastmcp.utilities.types import Image

from fl_studio_mcp.tools.audio import get_render_dir
from fl_studio_mcp.utils import fl_capture, fl_playlist
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_capture import CaptureError
from fl_studio_mcp.utils.fl_playlist import PlaylistError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock

if TYPE_CHECKING:
    from fastmcp import FastMCP

FRONT_DELAY = 0.3  # seconds FL gets to draw a panel it has just brought to the front


def _bring_to_front(panel: str) -> None:
    """Show a panel in front of FL's other panels; raises CaptureError.

    Seen live: a panel covered by another one (the piano roll over the playlist)
    is drawn covered. The playlist is also zoomed out to show the whole song.
    """
    if panel == "playlist":
        try:
            fl_playlist.show_whole_song()
        except PlaylistError as e:
            raise CaptureError(str(e)) from e
        return
    try:
        result = call("ui.focusWindow", {"window": panel})
    except RuntimeError as e:
        raise CaptureError(str(e)) from e
    if "error" in result:
        raise CaptureError(result["error"])
    time.sleep(FRONT_DELAY)


def register_screen_tools(mcp: FastMCP) -> None:
    """Register the screenshot tool with the MCP server."""

    @mcp.tool()
    def fl_screenshot(window: str = "all", save: bool = False) -> Image | dict:
        """Take a picture of FL Studio's window, to see what the other tools can't tell.

        Use it to check the playlist after fl_arrange (the API can't list
        clips), to read a message box, or whenever a tool's result doesn't match
        what you expected. FL stays in the background. Windows only.

        Args:
            window: "all" (FL's whole window as it is) or one panel: "playlist",
                "piano roll", "channel rack", "mixer", "browser". A panel is
                shown and brought in front of FL's other panels first; the
                playlist is also zoomed out so the whole song shows.
            save: Also write the picture to the render folder and return
                {"path", "width", "height"} instead of the picture itself.

        Returns the picture (PNG), or {"error": ...}.
        """
        panel = window.strip().lower()
        try:
            with keyboard_lock:
                if panel in fl_capture.PANELS:
                    _bring_to_front(panel)
                width, height, png = fl_capture.capture(panel)
        except CaptureError as e:
            return {"error": str(e)}
        except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
            return {"error": f"The picture could not be taken: {e!r}"}
        if not save:
            return Image(data=png, format="png")
        name = panel.replace(" ", "-")
        try:
            path = get_render_dir() / f"fl-{name}-{time.strftime('%Y%m%d-%H%M%S')}.png"
            path.write_bytes(png)
        except OSError as e:
            return {"error": f"The picture could not be saved: {e}"}
        return {"path": str(path), "width": width, "height": height}
