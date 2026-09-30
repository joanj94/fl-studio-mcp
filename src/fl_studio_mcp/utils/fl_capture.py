"""Pictures of FL Studio's window, taken while it is in the background.

Windows draws a window into a bitmap on request (`PrintWindow`), also when
other windows cover it, so FL needn't be brought to the front. The picture is
returned as PNG, encoded here (no imaging library needed). Windows only.
"""

from __future__ import annotations

import ctypes
import platform
import struct
import zlib
from ctypes import wintypes

from fl_studio_mcp.utils import fl_windows, win_focus

# Titles of FL's panels, as their caption bars start.
PANELS = {
    "playlist": "Playlist",
    "piano roll": "Piano roll",
    "channel rack": "Channel rack",
    "mixer": "Mixer",
    "browser": "Browser",
}
_PW_RENDERFULLCONTENT = 2  # also what the window draws with the graphics card
_BI_RGB = 0
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CaptureError(Exception):
    """No picture could be taken; the message says why."""


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def encode_png(width: int, height: int, bgrx: bytes) -> bytes:
    """A PNG from 32-bit pixels as Windows stores them (blue, green, red, unused), top row first."""
    if len(bgrx) != width * height * 4 or not width or not height:
        raise ValueError(f"{len(bgrx)} bytes are not a {width}x{height} picture")
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgrx[2::4], bgrx[1::4], bgrx[0::4]
    row = width * 3
    # Every row starts with its filter type; 0 = the bytes as they are.
    rows = b"".join(b"\x00" + rgb[start:start + row] for start in range(0, len(rgb), row))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8 bits, RGB
    return (_PNG_SIGNATURE + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(rows, 6)) + _chunk(b"IEND", b""))


def crop(width: int, bgrx: bytes, box: tuple[int, int, int, int]) -> tuple[int, int, bytes]:
    """Cut (left, top, right, bottom) out of a picture; returns its width, height and pixels."""
    height = len(bgrx) // (width * 4)
    left, top = max(box[0], 0), max(box[1], 0)
    right, bottom = min(box[2], width), min(box[3], height)
    if right <= left or bottom <= top:
        raise ValueError("The area lies outside the picture")
    rows = [bgrx[(y * width + left) * 4:(y * width + right) * 4] for y in range(top, bottom)]
    return right - left, bottom - top, b"".join(rows)


def _window_pixels(user32, hwnd: int) -> tuple[int, int, bytes]:
    """Width, height and pixels of a window, drawn by the window itself."""
    gdi32 = ctypes.windll.gdi32
    for function in (user32.GetWindowDC, gdi32.CreateCompatibleDC, gdi32.CreateCompatibleBitmap,
                     gdi32.SelectObject):
        function.restype = ctypes.c_void_p
    left, top, right, bottom = fl_windows._rect(user32, hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise CaptureError("FL Studio's window has no size (is it minimized?)")
    window = wintypes.HWND(hwnd)
    window_dc = ctypes.c_void_p(user32.GetWindowDC(window))
    memory_dc = ctypes.c_void_p(gdi32.CreateCompatibleDC(window_dc))
    bitmap = ctypes.c_void_p(gdi32.CreateCompatibleBitmap(window_dc, width, height))
    try:
        previous = ctypes.c_void_p(gdi32.SelectObject(memory_dc, bitmap))
        drawn = user32.PrintWindow(window, memory_dc, _PW_RENDERFULLCONTENT)
        gdi32.SelectObject(memory_dc, previous)
        # A negative height asks for the rows top first.
        info = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, _BI_RGB)
        pixels = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(window, window_dc)
    if not drawn or copied != height:
        raise CaptureError("Windows could not draw FL Studio's window")
    return width, height, pixels.raw


def capture(panel: str = "all") -> tuple[int, int, bytes]:
    """Width, height and PNG of FL's main window, or of one panel in it; raises CaptureError."""
    if platform.system() != "Windows":
        raise CaptureError("Screenshots are only supported on Windows")
    if panel != "all" and panel not in PANELS:
        raise CaptureError(f"window must be 'all' or one of: {', '.join(PANELS)}")
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        raise CaptureError("FL Studio's window was not found")
    fl_windows.restore_behind(user32, main)
    width, height, pixels = _window_pixels(user32, main)
    if panel != "all":
        found = fl_windows.panel_caption(user32, main, PANELS[panel])
        if found is None:
            raise CaptureError(f"The {panel} isn't showing inside FL Studio's main window")
        origin = fl_windows._rect(user32, main)
        box = fl_windows._rect(user32, user32.GetParent(wintypes.HWND(found[0])))
        try:
            width, height, pixels = crop(width, pixels, (
                box[0] - origin[0], box[1] - origin[1], box[2] - origin[0], box[3] - origin[1]))
        except ValueError as e:
            raise CaptureError(f"The {panel} lies outside FL Studio's main window") from e
    return width, height, encode_png(width, height, pixels)
