"""Style pack tools: optional genre data for the AI to follow (see style_packs.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fl_studio_mcp import style_packs
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.roles import check_roles, get_channels

if TYPE_CHECKING:
    from fastmcp import FastMCP

SUMMARY_FIELDS = ("name", "title", "description", "tempo")


def register_style_tools(mcp: FastMCP) -> None:
    """Register style pack tools with the MCP server."""

    @mcp.tool()
    def style_list() -> dict:
        """List the available style packs.

        A style pack describes a musical style in this server's vocabulary: tempo
        range, scales, the roles (channels) it expects, song structure in bars,
        example rhythm grids and chord progressions, and guidance. Packs are
        optional starting points, not rules. Use style_get for the full pack.
        """
        packs = style_packs.list_packs()
        extra = style_packs.user_dir()
        return {
            "styles": [{k: pack[k] for k in SUMMARY_FIELDS} for pack in packs["styles"]],
            "invalid": packs["invalid"],
            "builtin_dir": str(style_packs.BUILTIN_DIR),
            "user_dir": str(extra) if extra else None,
        }

    @mcp.tool()
    def style_get(name: str) -> dict:
        """Get a full style pack by name (see style_list).

        Rhythm grids go straight into music_rhythm, progressions into music_chords
        (with a key of your choice), and roles into fl_check_roles and the
        `channel` parameter of the note and step tools.
        """
        try:
            return style_packs.get_pack(name)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def style_check_project(name: str) -> dict:
        """Check whether the open FL Studio project suits a style pack.

        Compares the project tempo with the pack's range and looks for a channel
        for each role. ok is true when the tempo is in range and every required
        role has exactly one channel; optional roles are reported but don't count.
        """
        try:
            pack = style_packs.get_pack(name)
        except ValueError as e:
            return {"error": str(e)}
        tempo = call("project.getTempo")
        if "error" in tempo:
            return tempo
        try:
            channels = get_channels()
        except ValueError as e:
            return {"error": str(e)}
        return _compare(pack, tempo["bpm"], channels)


def _compare(pack: dict[str, Any], bpm: float, channels: list[dict]) -> dict:
    low, high = pack["tempo"]["min"], pack["tempo"]["max"]
    tempo = {"bpm": bpm, "min": low, "max": high, "ok": low <= bpm <= high}
    required = check_roles(pack["roles"]["required"], channels)
    optional = check_roles(pack["roles"]["optional"], channels)
    return {
        "style": pack["name"],
        "ok": tempo["ok"] and required["ok"],
        "tempo": tempo,
        "required_roles": required,
        "optional_roles": optional,
    }
