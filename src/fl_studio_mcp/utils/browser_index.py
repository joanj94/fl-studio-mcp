"""Finding sounds by name in the folders FL Studio's browser shows.

Walking the browser itself costs about 15 ms a file. Most of what it shows are
plain folders on disk, though: FL's own `Data/Patches` and the user's
`Presets` are merged into the browser's top level under their folder names,
and a few other user folders appear under names of their own. Searching those
on disk takes a moment for tens of thousands of files, and a match's path is
the path the browser tools take.
"""

from __future__ import annotations

import os
from pathlib import Path

from fl_studio_mcp.utils.fl_browser import LOADABLE_SUFFIXES

# Where FL keeps the folders of the browser's top level, below its install folder
# and below the user data folder (the one holding "Settings").
INSTALL_PATCHES = Path("Data") / "Patches"
USER_PRESETS = Path("Presets")
# User folders the browser shows under another name: browser name -> path below user data.
USER_FOLDERS = {
    "Project bones": Path("Projects") / "Project bones",
    "Recorded": Path("Audio") / "Recorded",
    "Rendered": Path("Audio") / "Rendered",
    "Sliced audio": Path("Audio") / "Sliced audio",
}
DEFAULT_LIMIT = 40
MAX_LIMIT = 500


def browser_roots(install_dir: Path | None, user_dir: Path) -> list[tuple[str, Path]]:
    """(browser folder name, folder on disk) for every browser folder known on disk.

    A name can come twice: FL's and the user's folder of that name are shown as one.
    """
    roots: list[tuple[str, Path]] = []
    parents = [user_dir / USER_PRESETS]
    if install_dir is not None:
        parents.append(install_dir / INSTALL_PATCHES)
    for parent in parents:
        if parent.is_dir():
            roots += [(entry.name, entry) for entry in sorted(parent.iterdir()) if entry.is_dir()]
    roots += [
        (name, user_dir / below) for name, below in USER_FOLDERS.items()
        if (user_dir / below).is_dir()
    ]
    return roots


def _files(name: str, folder: Path):
    """Browser paths of the loadable files below one root."""
    for current, _folders, files in os.walk(folder):
        below = Path(current).relative_to(folder).parts
        for file in files:
            if file.lower().endswith(LOADABLE_SUFFIXES):
                yield "/".join((name, *below, file))


def search(
    roots: list[tuple[str, Path]], query: str, folder: str = "", limit: int = DEFAULT_LIMIT
) -> tuple[list[str], int]:
    """Browser paths of loadable files matching `query`, best first, and how many there are.

    Every word of the query must be in the path (any case, in any order);
    `folder` limits the search to paths starting with it. Files whose own name
    holds all the words come first, then shorter paths. Raises ValueError for
    an empty query.
    """
    words = query.lower().split()
    if not words:
        raise ValueError("Give at least one word to search for")
    prefix = "/".join(part for part in folder.replace("\\", "/").split("/") if part).lower()
    found = set()
    for name, root in roots:
        if prefix and not (name.lower() == prefix.split("/")[0]):
            continue
        for path in _files(name, root):
            lowered = path.lower()
            if prefix and not (lowered + "/").startswith(prefix + "/"):
                continue
            if all(word in lowered for word in words):
                found.add(path)

    def rank(path: str) -> tuple[bool, int, str]:
        file_name = path.rsplit("/", 1)[-1].lower()
        return (not all(word in file_name for word in words), len(path), path.lower())

    return sorted(found, key=rank)[:limit], len(found)
