"""Roles: address a channel by a word in its name ("kick", "sub bass") instead of an index.

A role is not stored anywhere; it is matched against the channel names in the
open project, so renaming a channel in FL Studio re-tags it. Matching ignores
case and punctuation. A channel whose whole name equals the role wins; otherwise
the role must appear as whole words in exactly one name ("bass" matches
"Sub Bass" but not "Bassline").
"""

from __future__ import annotations

import re

from fl_studio_mcp.utils.connection import call

_NON_WORD = re.compile(r"[^0-9a-z]+")


def normalize(text: str) -> str:
    """Lowercase words separated by single spaces: " Sub-Bass_2 " -> "sub bass 2"."""
    return " ".join(_NON_WORD.split(text.lower())).strip()


def _candidates(role: str, channels: list[dict]) -> list[dict]:
    """Channels matching the role: exact-name matches if any, else whole-word matches."""
    wanted = normalize(role)
    if not wanted:
        raise ValueError(f"Role must contain letters or digits, got {role!r}")
    exact = [c for c in channels if normalize(c.get("name", "")) == wanted]
    if exact:
        return exact
    return [c for c in channels if f" {wanted} " in f" {normalize(c.get('name', ''))} "]


def match_role(role: str, channels: list[dict]) -> dict:
    """The one channel dict matching the role. Raises ValueError if none or several do."""
    found = _candidates(role, channels)
    if len(found) == 1:
        return found[0]
    if found:
        names = ", ".join(f"{c['index']}: {c.get('name')}" for c in found)
        raise ValueError(
            f"Role {role!r} matches several channels ({names}); "
            "use a more specific role, a channel index, or rename a channel"
        )
    names = ", ".join(repr(c.get("name")) for c in channels) or "none"
    raise ValueError(f"No channel matches role {role!r}. Channels: {names}")


def check_roles(roles: list[str], channels: list[dict]) -> dict:
    """Sort roles into found, missing and ambiguous for the given channels."""
    found: dict[str, dict] = {}
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for role in roles:
        matches = _candidates(role, channels)
        if len(matches) == 1:
            found[role] = {"channel": matches[0]["index"], "name": matches[0].get("name")}
        elif matches:
            ambiguous[role] = [c.get("name") for c in matches]
        else:
            missing.append(role)
    return {
        "ok": not missing and not ambiguous,
        "found": found,
        "missing": missing,
        "ambiguous": ambiguous,
    }


def get_channels() -> list[dict]:
    """All channels from FL Studio. Raises ValueError if FL reports an error."""
    result = call("channels.getAll")
    if "error" in result:
        raise ValueError(result["error"])
    return result.get("channels", [])


def resolve_channel(channel: int | str) -> int:
    """Channel index for an index, a numeric string, or a role name.

    Only role names need FL Studio (one channel list request).
    Raises ValueError for anything that can't be resolved.
    """
    if isinstance(channel, str) and channel.strip().isdigit():
        return int(channel.strip())
    if isinstance(channel, str):
        return match_role(channel, get_channels())["index"]
    if isinstance(channel, int) and not isinstance(channel, bool) and channel >= 0:
        return channel
    raise ValueError(f"Channel must be an index (0 or more) or a role name, got {channel!r}")
