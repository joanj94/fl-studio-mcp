"""Reading the generator list from FL Studio's plugin database.

The database (`Presets/Plugin database/Generators/<category>/<name>.fst`) is what
FL's plugin picker shows, so a name found here is one the picker can load.
"""

from __future__ import annotations

from difflib import get_close_matches
from pathlib import Path

from fl_studio_mcp.utils.paths import get_fl_settings_dir

PRESET_SUFFIX = ".fst"
MAX_SUGGESTIONS = 3


def get_generators_dir() -> Path:
    """Folder with one sub-folder per generator category (next to FL's Settings folder)."""
    return get_fl_settings_dir().parent / "Presets" / "Plugin database" / "Generators"


def get_effects_dir() -> Path:
    """Folder with one sub-folder per effect category."""
    return get_generators_dir().parent / "Effects"


def effect_browser_path(name: str) -> str:
    """The browser path of an effect in the plugin database ("Plugin database/Effects/
    Filter/Fruity Parametric EQ 2"), name matched ignoring case. Raises ValueError."""
    root = get_effects_dir()
    wanted = name.strip().lower()
    for path in sorted(root.glob("*/*")):
        if path.suffix.lower() == PRESET_SUFFIX and path.stem.lower() == wanted:
            return f"Plugin database/Effects/{path.parent.name}/{path.stem}"
    raise ValueError(f"No effect named {name!r} in FL Studio's plugin database ({root})")


def list_generators() -> dict[str, str]:
    """Generator name -> category, sorted by name. Raises ValueError if none are found."""
    root = get_generators_dir()
    if not root.is_dir():
        raise ValueError(f"FL Studio's plugin database was not found at {root}")
    found = {
        path.stem: path.parent.name
        for path in root.glob("*/*")
        if path.is_file() and path.suffix.lower() == PRESET_SUFFIX
    }
    if not found:
        raise ValueError(f"No generators are listed in {root}")
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


def find_generator(name: str) -> str:
    """The database's spelling of a generator name (case ignored), or ValueError.

    Only a whole-name match counts: FL's picker search is fuzzy and loads its
    best guess, so a name must be known before it is typed there.
    """
    wanted = name.strip().lower()
    known = {generator.lower(): generator for generator in list_generators()}
    if wanted in known:
        return known[wanted]
    close = get_close_matches(wanted, known, n=MAX_SUGGESTIONS)
    if close:
        hint = "Did you mean: %s?" % ", ".join(known[match] for match in close)
    else:
        hint = "fl_list_generators shows the names."
    raise ValueError(f"No generator named {name!r} in FL Studio's plugin database. {hint}")
