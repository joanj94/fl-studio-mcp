"""Mixing intents and chains, read from intents.json.

An intent is a musical move ("remove_mud", "large_hall") on one stock plugin:
parameter names with the values they should show, some fixed and some going
from a neutral value (amount 0) to the full move (amount 1). Values are display
texts in the plugin's own units, set by utils/param_values.py. An EQ intent
uses one band of Parametric EQ 2: "{band}" in its parameter names is replaced
by a free band, tried in the order of "bands".

A chain is a list of steps, each a group of intents applied to one plugin.
"""

from __future__ import annotations

import json
from pathlib import Path

from fl_studio_mcp.utils.param_values import parse_display

DATA_FILE = Path(__file__).resolve().parent / "intents.json"
BAND = "{band}"


def load() -> dict:
    """The intents and chains; raises ValueError if the data is inconsistent."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict) -> None:
    """Check every intent's values and every chain's steps. Raises ValueError."""
    intents = data.get("intents", {})
    for name, intent in intents.items():
        if not intent.get("plugin") or not intent.get("set"):
            raise ValueError(f"Intent {name!r} needs a plugin and values to set")
        banded = any(BAND in param for param in intent["set"])
        if banded != bool(intent.get("bands")):
            raise ValueError(f"Intent {name!r}: '{BAND}' in names and 'bands' go together")
        for param, value in intent["set"].items():
            if isinstance(value, dict):
                ends = [parse_display(value.get("from", "")), parse_display(value.get("to", ""))]
                if None in ends or ends[0][0] != ends[1][0]:
                    raise ValueError(f"Intent {name!r}, {param!r}: from/to need the same unit")
            elif not isinstance(value, str):
                raise ValueError(f"Intent {name!r}, {param!r}: a value is a text or from/to")
    for name, chain in data.get("chains", {}).items():
        for step in chain.get("steps", []):
            unknown = [i for i in step if i not in intents]
            if unknown:
                raise ValueError(f"Chain {name!r} uses unknown intents {unknown}")
            if len({intents[i]["plugin"] for i in step}) != 1:
                raise ValueError(f"Chain {name!r}: a step's intents must share one plugin")


def _format(kind: str, number: float) -> str:
    if kind == "frequency":
        return f"{number:.0f}Hz"
    if kind == "time":
        return f"{number * 1000:.1f}ms"
    if kind == "level":
        return f"{number:.1f}dB"
    if kind == "percent":
        return f"{number:.1f}%"
    if kind == "ratio":
        return f"{number:.2f}:1"
    return f"{number:g}"


def settings(intent: dict, amount: float, band: int | None = None) -> dict[str, str]:
    """{parameter name: value to show} for an intent at `amount` (0-1). Raises ValueError."""
    if not 0 <= amount <= 1:
        raise ValueError(f"amount must be 0 to 1, got {amount}")
    if BAND in "".join(intent["set"]) and band is None:
        raise ValueError("This intent needs an EQ band")
    result = {}
    for param, value in intent["set"].items():
        name = param.replace(BAND, str(band))
        if isinstance(value, dict):
            kind, low = parse_display(value["from"])
            _, high = parse_display(value["to"])
            value = _format(kind, low + (high - low) * amount)
        result[name] = value
    return result


def describe(data: dict) -> dict:
    """What fl_list_intents shows: each intent's plugin and description, and the chains."""
    return {
        "intents": {
            name: {"plugin": i["plugin"], "description": i["description"]}
            for name, i in data["intents"].items()
        },
        "chains": {
            name: {"description": c["description"], "steps": c["steps"]}
            for name, c in data.get("chains", {}).items()
        },
    }
