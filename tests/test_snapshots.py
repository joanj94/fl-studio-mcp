"""Snapshots of the mix (controller's project.getSnapshot/applyState and the tools)
and the report of unused parts of a project."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import snapshots
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector

MUTE, MIX = 7, 8  # stand-ins for FL's event offsets


class Mix:
    """A small FL mixer and rack: master, inserts 1-3, FL's "current" track."""

    def __init__(self, fl: dict) -> None:
        self.names = {0: "Master", 1: "Kick", 2: "Insert 2", 3: "Bus", 4: "Current"}
        self.volume = {t: 0.8 for t in range(5)}
        self.pan = dict.fromkeys(range(5), 0.0)
        self.muted: set[int] = set()
        self.solo: set[int] = set()
        self.routes = {1: {3}, 2: {0}, 3: {0}}
        self.effects = {(3, 0): {"name": "Fruity Limiter", "on": 1, "mix": 1 << 30,
                                 "params": [0.5, 0.25, 0.75]}}
        self.rack = [{"name": "Kick", "volume": 0.78, "pan": 0.0, "muted": False, "track": 1},
                     {"name": "Pad", "volume": 0.5, "pan": 0.2, "muted": True, "track": 2}]
        self._wire(fl)

    def _wire(self, fl: dict) -> None:
        mixer, plugins, channels, midi = fl["mixer"], fl["plugins"], fl["channels"], fl["midi"]
        midi.REC_Plug_Mute, midi.REC_Plug_MixLevel = MUTE, MIX
        midi.REC_Control, midi.REC_UpdateControl, midi.PIM_None = 1, 16, 0
        mixer.trackCount.return_value = 5
        mixer.getTrackName.side_effect = lambda t: self.names[t]
        mixer.setTrackName.side_effect = lambda t, n: self.names.__setitem__(
            t, n or ("Master" if t == 0 else f"Insert {t}"))
        mixer.getTrackVolume.side_effect = lambda t, *mode: self.volume[t]
        mixer.setTrackVolume.side_effect = lambda t, v, *a: self.volume.__setitem__(t, v)
        mixer.getTrackPan.side_effect = lambda t: self.pan[t]
        mixer.setTrackPan.side_effect = lambda t, v, *a: self.pan.__setitem__(t, v)
        mixer.isTrackMuted.side_effect = lambda t: int(t in self.muted)
        mixer.isTrackSolo.side_effect = lambda t: int(t in self.solo)
        mixer.muteTrack.side_effect = (
            lambda t, on: (self.muted.add if on else self.muted.discard)(t))
        mixer.soloTrack.side_effect = (
            lambda t, on, *mode: (self.solo.add if on else self.solo.discard)(t))
        mixer.getRouteSendActive.side_effect = lambda t, d: d in self.routes.get(t, set())
        mixer.setRouteTo.side_effect = (
            lambda t, d, on: (self.routes[t].add if on else self.routes[t].discard)(d))
        mixer.getTrackPluginId.side_effect = lambda t, slot: 100 * t + 10 * slot
        mixer.getEventValue.side_effect = self._event
        fl["general"].processRECEvent.side_effect = self._set_event
        plugins.isValid.side_effect = lambda t, slot, *g: (t, slot) in self.effects
        plugins.getPluginName.side_effect = lambda t, slot, *a: self.effects[(t, slot)]["name"]
        plugins.getParamCount.side_effect = (
            lambda t, slot, *g: len(self.effects[(t, slot)]["params"]))
        plugins.getParamValue.side_effect = (
            lambda p, t, slot, *g: self.effects[(t, slot)]["params"][p])
        plugins.setParamValue.side_effect = self._set_param
        channels.channelCount.side_effect = lambda *g: len(self.rack)
        channels.getChannelName.side_effect = lambda i, *g: self.rack[i]["name"]
        channels.setChannelName.side_effect = lambda i, n, *g: self.rack[i].__setitem__("name", n)
        channels.getChannelVolume.side_effect = lambda i, *a, **k: self.rack[i]["volume"]
        channels.setChannelVolume.side_effect = (
            lambda i, v, *a, **k: self.rack[i].__setitem__("volume", v))
        channels.getChannelPan.side_effect = lambda i, *g: self.rack[i]["pan"]
        channels.setChannelPan.side_effect = (
            lambda i, v, *a, **k: self.rack[i].__setitem__("pan", v))
        channels.isChannelMuted.side_effect = lambda i, *g: int(self.rack[i]["muted"])
        channels.muteChannel.side_effect = (
            lambda i, on, *g: self.rack[i].__setitem__("muted", bool(on)))
        channels.getTargetFxTrack.side_effect = lambda i, *g: self.rack[i]["track"]
        channels.setTargetFxTrack.side_effect = (
            lambda i, t, *g: self.rack[i].__setitem__("track", t))

    def _event(self, event: int) -> int:
        effect = self.effects[(event // 100, event % 100 // 10)]
        return effect["on"] if event % 10 == MUTE else effect["mix"]

    def _set_event(self, event: int, value: int, flags: int) -> None:
        effect = self.effects[(event // 100, event % 100 // 10)]
        if event % 10 == MUTE:
            effect["on"] = value
        else:
            effect["mix"] = round(value / 12800 * (1 << 30))

    def _set_param(self, value, p, t, slot, pickup, use_global) -> None:
        assert pickup == 0  # PIM_None: pickup mode would make FL ignore the value
        self.effects[(t, slot)]["params"][p] = value


@pytest.fixture
def mix(fl_modules) -> Mix:
    return Mix(fl_modules)


def test_get_snapshot_reads_tracks_effects_and_channels(controller, mix):
    state = controller.dispatch_command("project.getSnapshot", {})

    bus = next(t for t in state["tracks"] if t["index"] == 3)
    assert bus["effects"] == [{"slot": 0, "plugin": "Fruity Limiter", "enabled": True,
                               "mix": 1.0, "params": [0.5, 0.25, 0.75],
                               "params_truncated": False}]
    kick = next(t for t in state["tracks"] if t["index"] == 1)
    assert kick["routes"] == [3] and kick["volume"] == 0.8
    assert state["channels"][1] == {"index": 1, "name": "Pad", "volume": 0.5, "pan": 0.2,
                                    "is_muted": True, "target_fx_track": 2}
    without = controller.dispatch_command("project.getSnapshot", {"params": False})
    assert "params" not in next(t for t in without["tracks"] if t["index"] == 3)["effects"][0]


def test_apply_state_puts_back_only_what_changed(controller, mix, fl_modules):
    state = controller.dispatch_command("project.getSnapshot", {})
    mix.volume[1] = 0.5
    mix.names[1] = "Renamed"
    mix.routes[1] = {0}
    mix.muted.add(2)
    mix.solo.add(1)
    mix.effects[(3, 0)]["params"][1] = 0.9
    mix.effects[(3, 0)]["on"] = 0
    mix.rack[0].update(volume=0.1, track=3, name="Kick 2")
    fl_modules["plugins"].setParamValue.reset_mock()

    result = controller.dispatch_command("project.applyState", state)

    assert result["skipped"] == []
    assert mix.volume[1] == 0.8 and mix.names[1] == "Kick" and mix.routes[1] == {3}
    assert mix.muted == set() and mix.solo == set()
    assert mix.effects[(3, 0)]["params"] == [0.5, 0.25, 0.75] and mix.effects[(3, 0)]["on"] == 1
    assert fl_modules["plugins"].setParamValue.call_count == 1  # the others were unchanged
    assert mix.rack[0] == {"name": "Kick", "volume": 0.78, "pan": 0.0, "muted": False,
                           "track": 1}
    assert controller.dispatch_command("project.applyState", state)["changed"] == 0


def test_apply_state_keeps_a_solo_and_a_default_track_name(controller, mix):
    mix.solo.add(3)
    mix.names[2] = "Lead"
    state = controller.dispatch_command("project.getSnapshot", {})
    mix.solo.clear()

    controller.dispatch_command("project.applyState", state)

    assert mix.solo == {3}
    named = dict(next(t for t in state["tracks"] if t["index"] == 2), name="Insert 2")
    controller.dispatch_command("project.applyState", {"tracks": [named]})
    assert mix.names[2] == "Insert 2"  # set as "" (FL's default name), not as a name


def test_apply_state_skips_an_effect_that_was_replaced(controller, mix):
    state = controller.dispatch_command("project.getSnapshot", {})
    mix.effects[(3, 0)]["name"] = "Fruity Reeverb 2"

    result = controller.dispatch_command("project.applyState", state)

    assert result["skipped"] == ["track 3 slot 0: Fruity Limiter isn't there any more"]


@pytest.mark.parametrize("params", [{"tracks": [{"index": 9}]}, {"tracks": "all"},
                                    {"channels": [{"index": 5}]}, {"channels": [{}]}])
def test_apply_state_refuses_bad_input(controller, mix, params):
    assert "error" in controller.dispatch_command("project.applyState", params)


# --- the tools -------------------------------------------------------------------


def test_channels_are_matched_by_name_then_by_place():
    saved = [{"index": 0, "name": "Kick"}, {"index": 1, "name": "Pad"},
             {"index": 2, "name": "Gone"}]

    matched, missing = snapshots.match_channels(
        saved, [{"index": 0, "name": "Pad"}, {"index": 1, "name": "Kick"}])

    assert [(c["name"], c["index"]) for c in matched] == [("Kick", 1), ("Pad", 0)]
    assert missing == ["Gone"]
    same_length, _ = snapshots.match_channels(
        saved[:2], [{"index": 0, "name": "Kick A"}, {"index": 1, "name": "Pad"}])
    assert [c["index"] for c in same_length] == [0, 1]  # a renamed channel, by its place


STATE = {
    "tracks": [{"index": 1, "name": "Kick", "effects": [{"slot": 0, "plugin": "EQ"}]}],
    "channels": [{"index": 0, "name": "Kick"}, {"index": 1, "name": "Pad"}],
}


class SnapshotFL(ScriptedConnection):
    def __init__(self) -> None:
        super().__init__({
            "project.getSnapshot": STATE,
            "project.applyState": {"changed": 3, "skipped": []},
            "channels.getAll": {"channels": [{"index": 0, "name": "Pad"},
                                             {"index": 1, "name": "Kick"}]},
        })
        self.timeouts: list[float] = []

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        self.timeouts.append(timeout)
        return super().send_command(action, params, timeout)


@pytest.fixture
def tools(monkeypatch, tmp_path) -> tuple[dict[str, Any], SnapshotFL]:
    conn = SnapshotFL()
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    monkeypatch.setattr(snapshots, "get_hardware_dir", lambda: tmp_path)
    collector = ToolCollector()
    snapshots.register_snapshot_tools(collector)
    return collector.tools, conn


def test_take_list_and_restore_a_snapshot(tools, tmp_path):
    tool, conn = tools

    taken = tool["fl_take_snapshot"]("before eq")

    assert taken == {"name": "before eq", "path": str(tmp_path / "snapshots" / "before eq.json"),
                     "tracks": 1, "channels": 2, "effects": 1}
    assert conn.timeouts[-1] == snapshots.SNAPSHOT_TIMEOUT
    listed = tool["fl_list_snapshots"]()["snapshots"]
    assert [(s["name"], s["tracks"], s["channels"]) for s in listed] == [("before eq", 1, 2)]

    restored = tool["fl_restore_snapshot"]("before eq")

    assert restored == {"name": "before eq", "changed": 3, "skipped": []}
    action, sent = conn.sent[-1]
    assert action == "project.applyState"
    assert sent["tracks"] == STATE["tracks"]
    assert [(c["name"], c["index"]) for c in sent["channels"]] == [("Kick", 1), ("Pad", 0)]


def test_restore_only_the_mixer(tools):
    tool, conn = tools
    tool["fl_take_snapshot"]("a")

    tool["fl_restore_snapshot"]("a", channels=False)

    assert conn.sent[-1] == ("project.applyState", {"tracks": STATE["tracks"], "channels": []})


@pytest.mark.parametrize("name", ["", "../up", "a/b", " pad", "x" * 65, "end."])
def test_snapshot_names_are_checked(tools, name):
    tool, _ = tools

    assert "error" in tool["fl_take_snapshot"](name)
    assert "error" in tool["fl_restore_snapshot"](name)


def test_restoring_a_missing_snapshot(tools):
    tool, _ = tools

    assert "fl_list_snapshots" in tool["fl_restore_snapshot"]("never")["error"]
    assert tool["fl_list_snapshots"]() == {"snapshots": []}


# --- what the project no longer uses ---------------------------------------------


@pytest.fixture
def unused(monkeypatch) -> tuple[Any, ScriptedConnection]:
    conn = ScriptedConnection({
        "project.getInfo": {
            "current_pattern": 1,
            "patterns": [{"index": 1, "name": "Drums", "length_beats": 4.0},
                         {"index": 2, "name": "Old", "length_beats": 0.0},
                         {"index": 3, "name": "Fill", "length_beats": 4.0}],
            "channels": [{"index": 0, "name": "Kick"}, {"index": 1, "name": "Pad"},
                         {"index": 2, "name": "Fade", "is_automation": True},
                         {"index": 3, "name": "Rise", "is_automation": True}],
        },
        "mixer.getAllTracks": {"tracks": [
            {"index": 0, "name": "Master", "channels": [], "routes": []},
            {"index": 1, "name": "Kick", "channels": ["Kick"], "routes": [3]},
            {"index": 2, "name": "Old verb", "channels": [], "routes": [0]},
            {"index": 3, "name": "Drum bus", "channels": [], "routes": [0]},
        ]},
    })
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    collector = ToolCollector()
    snapshots.register_snapshot_tools(collector)
    return collector.tools["fl_find_unused"], conn


def test_find_unused_from_the_mixer_and_the_patterns(unused):
    tool, _ = unused

    result = tool(from_save=False)

    assert result == {"mixer_tracks": [{"track": 2, "name": "Old verb"}],  # the bus is fed
                      "empty_patterns": [{"pattern": 2, "name": "Old"}]}


def test_find_unused_reads_the_playlist_from_the_newest_save(unused, monkeypatch, tmp_path):
    from fl_studio_mcp.project.reader import Arrangement, Clip, Project

    tool, _ = unused
    file = tmp_path / "Song.flp"
    file.write_bytes(b"")
    clips = (Clip(0, 384, 1, 1, None), Clip(0, 384, 5, None, 2))
    project = Project(96, ("Kick", "Pad", "Fade", "Rise"), {}, {}, (Arrangement("A", clips),))
    monkeypatch.setattr(snapshots, "newest_project_file", lambda: file)
    monkeypatch.setattr(snapshots, "parse", lambda data: data)
    monkeypatch.setattr(snapshots, "read_project", lambda flp: project)

    result = tool()

    assert result["save"]["file"] == str(file)
    assert result["patterns_not_in_playlist"] == [{"pattern": 2, "name": "Old"},
                                                  {"pattern": 3, "name": "Fill"}]
    assert result["automation_without_clip"] == [{"channel": 3, "name": "Rise"}]


def test_find_unused_without_a_save(unused, monkeypatch):
    tool, _ = unused
    monkeypatch.setattr(snapshots, "newest_project_file", lambda: None)

    assert "save it" in tool()["save"]["error"]


def test_find_unused_can_check_every_piano_roll(unused, monkeypatch):
    tool, conn = unused
    notes = {(0, 1): [{"midi": 36, "time": 0.0}]}
    monkeypatch.setattr(snapshots, "read_notes", lambda c, p: notes.get((c, p), []))

    result = tool(from_save=False, check_notes=True)

    assert result["silent_channels"] == [{"channel": 1, "name": "Pad"}]
    assert conn.sent[-1] == ("patterns.select", {"index": 1})


def test_restoring_only_channels_leaves_mutes_and_solos_alone(controller, mix):
    mix.solo.add(3)
    mix.muted.add(1)
    state = controller.dispatch_command("project.getSnapshot", {})

    controller.dispatch_command("project.applyState", {"tracks": [], "channels": state["channels"]})

    assert mix.solo == {3} and mix.muted == {1}
