"""Channels and effects through FL's own assistant tools, each checked by reading back."""

from __future__ import annotations

import pytest

from fl_studio_mcp.utils import fl_assistant, gopher


class FakeFL:
    """A rack and a mixer that FL's tools change and the controller reads."""

    def __init__(self) -> None:
        self.rack = ["Kick", "Clap"]
        self.slots: dict[int, dict[int, str]] = {1: {0: "Fruity Limiter"}}
        self.tool_calls: list[tuple[str, dict]] = []
        self.ignore = False  # FL answers but changes nothing

    def call_tool(self, name: str, arguments: dict, timeout: float = 0) -> str:
        self.tool_calls.append((name, arguments))
        if self.ignore:
            return "ok"
        if name == "add_channel":
            self.rack.append(arguments["plugin"])
        elif name == "remove_channels":
            numbers = sorted((int(n) for n in arguments["target_channels"].split(",")),
                             reverse=True)
            for number in numbers:
                del self.rack[number - 1]
        elif name == "add_effect":
            track = int(arguments["target_tracks"])
            self.slots.setdefault(track, {})[arguments["slot_number"] - 1] = arguments["plugin"]
        elif name == "remove_effect":
            track = int(arguments["target_tracks"])
            self.slots.get(track, {}).pop(int(arguments["slot_numbers"]) - 1, None)
        else:
            raise gopher.GopherError(f"unknown tool {name}")
        return "done"

    def call(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        if action == "channels.getAll":
            return {"channels": [{"index": i, "name": n} for i, n in enumerate(self.rack)]}
        if action == "channels.setName":
            self.rack[params["index"]] = params["name"]
            return {"index": params["index"], "name": params["name"]}
        if action == "mixer.getTrackEffects":
            effects = self.slots.get(params["track"], {})
            return {"track": params["track"], "effects": [
                {"slot": s, "plugin": p} for s, p in sorted(effects.items())]}
        raise AssertionError(f"unexpected command {action}")


@pytest.fixture
def fl(monkeypatch: pytest.MonkeyPatch) -> FakeFL:
    fake = FakeFL()
    monkeypatch.setattr(gopher, "call_tool", fake.call_tool)
    monkeypatch.setattr(gopher, "page_url", lambda *a: "ws://fake")
    monkeypatch.setattr(fl_assistant, "call", fake.call)
    monkeypatch.setattr(fl_assistant, "SETTLE_TIMEOUT", 0.05)
    return fake


def test_available_follows_the_page(monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)
    assert not fl_assistant.available()
    monkeypatch.setattr(gopher, "page_url", lambda *a: "ws://x")
    assert fl_assistant.available()


def test_add_channel_reports_the_new_channel(fl):
    result = fl_assistant.add_channel("3x Osc")

    assert result == {"index": 2, "name": "3x Osc", "plugin": "3x Osc"}
    assert fl.rack == ["Kick", "Clap", "3x Osc"]
    assert fl.tool_calls == [("add_channel", {"plugin": "3x Osc"})]


def test_add_channel_that_changes_nothing_is_an_error(fl):
    fl.ignore = True

    with pytest.raises(fl_assistant.AssistantError, match="no channel"):
        fl_assistant.add_channel("Nothing")


def test_add_effect_goes_to_the_first_free_slot(fl):
    result = fl_assistant.add_effect("Fruity Reeverb 2", track=1)

    assert result["slot"] == 1
    assert result["plugin"] == "Fruity Reeverb 2"
    assert fl.tool_calls == [("add_effect", {"plugin": "Fruity Reeverb 2", "target_tracks": "1",
                                             "slot_number": 2})]


def test_add_effect_to_a_taken_slot_is_refused(fl):
    with pytest.raises(fl_assistant.AssistantError, match="slot 0"):
        fl_assistant.add_effect("Fruity Delay 3", track=1, slot=0)
    assert fl.tool_calls == []


def test_add_effect_to_a_full_track_is_refused(fl):
    fl.slots[2] = {s: "EQ" for s in range(10)}

    with pytest.raises(fl_assistant.AssistantError, match="free"):
        fl_assistant.add_effect("Fruity Delay 3", track=2)


def test_add_effect_that_does_not_appear_is_an_error(fl):
    fl.ignore = True

    with pytest.raises(fl_assistant.AssistantError, match="didn't appear"):
        fl_assistant.add_effect("Nonexistent", track=1)


def test_delete_effect_empties_the_slot(fl):
    assert fl_assistant.delete_effect(track=1, slot=0) == "Fruity Limiter"
    assert fl.slots[1] == {}
    assert fl.tool_calls == [("remove_effect", {"target_tracks": "1", "slot_numbers": "1"})]


def test_delete_effect_of_an_empty_slot_is_an_error(fl):
    with pytest.raises(fl_assistant.AssistantError, match="no effect"):
        fl_assistant.delete_effect(track=1, slot=4)


def test_delete_channels_by_global_index(fl):
    fl.rack = ["Kick", "Clap", "Hat", "Lead"]

    remaining = fl_assistant.delete_channels([1, 3])

    assert remaining == 2
    assert fl.rack == ["Kick", "Hat"]
    assert fl.tool_calls == [("remove_channels", {"target_channels": "2,4"})]


def test_delete_channels_that_removed_the_wrong_ones_is_an_error(fl, monkeypatch):
    fl.rack = ["Kick", "Clap", "Hat"]

    def wrong(name, arguments, timeout=0):
        del fl.rack[0]
        return "done"

    monkeypatch.setattr(gopher, "call_tool", wrong)

    with pytest.raises(fl_assistant.UnexpectedChange, match="Kick"):
        fl_assistant.delete_channels([1])


def test_delete_channels_that_removed_nothing_may_be_retried(fl):
    fl.ignore = True

    with pytest.raises(fl_assistant.AssistantError) as raised:
        fl_assistant.delete_channels([1])
    assert not isinstance(raised.value, fl_assistant.UnexpectedChange)


def test_fl_errors_become_assistant_errors(fl, monkeypatch):
    def refuse(name, arguments, timeout=0):
        raise gopher.GopherError("Error: Failed to add channel")

    monkeypatch.setattr(gopher, "call_tool", refuse)

    with pytest.raises(fl_assistant.AssistantError, match="Failed to add"):
        fl_assistant.add_channel("3x Osc")


def _lost_answer(fl: FakeFL, monkeypatch):
    """FL makes the change but its answer never arrives."""
    def lost(name, arguments, timeout=0):
        fl.call_tool(name, arguments)
        raise gopher.GopherError("FL did not answer within 20 s")

    monkeypatch.setattr(gopher, "call_tool", lost)


def test_a_lost_answer_after_the_change_counts_as_done(fl, monkeypatch):
    _lost_answer(fl, monkeypatch)
    fl.rack = ["Kick", "Clap", "Hat"]

    assert fl_assistant.add_channel("3x Osc")["index"] == 3
    assert fl_assistant.delete_channels([0]) == 3
    assert fl_assistant.add_effect("Fruity Delay 3", track=1)["slot"] == 1
    assert fl_assistant.delete_effect(track=1, slot=1) == "Fruity Delay 3"


def test_a_lost_answer_without_a_change_may_be_retried(fl, monkeypatch):
    def lost(name, arguments, timeout=0):
        raise gopher.GopherError("FL did not answer within 20 s")

    monkeypatch.setattr(gopher, "call_tool", lost)

    with pytest.raises(fl_assistant.AssistantError, match="did not answer") as raised:
        fl_assistant.add_channel("3x Osc")
    assert not isinstance(raised.value, fl_assistant.UnexpectedChange)


def test_an_effect_that_landed_elsewhere_is_an_unexpected_change(fl, monkeypatch):
    def elsewhere(name, arguments, timeout=0):
        fl.slots[1][5] = arguments["plugin"]
        return "done"

    monkeypatch.setattr(gopher, "call_tool", elsewhere)

    with pytest.raises(fl_assistant.UnexpectedChange):
        fl_assistant.add_effect("Fruity Delay 3", track=1)
