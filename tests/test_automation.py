"""Reading automation from saved projects and creating it: files, values and the steps in FL."""

from __future__ import annotations

import math
import os
import struct
from pathlib import Path

import pytest

from fl_studio_mcp.project import fader, flp, preset, reader
from fl_studio_mcp.project.flp import Event, FlpError, FlpFile
from fl_studio_mcp.project.preset import Point
from fl_studio_mcp.tools import automation
from fl_studio_mcp.utils.fl_playlist import PlaylistError
from tests.fakes import ToolCollector

# --- building test projects ----------------------------------------------------------------


def _text(text: str) -> bytes:
    return (text + "\0").encode("utf-16-le")


def _channel(index: int, name: str, points: list[Point] | None = None) -> list[Event]:
    events = [Event(64, index.to_bytes(2, "little")), Event(203, _text(name))]
    if points is not None:
        events.append(Event(234, preset.points_data(points)))
    return events


def _link(channel: int, rec_id: int) -> Event:
    return Event(227, struct.pack("<HHI", 0, channel, 0) + struct.pack("<I", rec_id) + bytes(8))


def _clip(position: int, item: int, length: int, track: int, size: int = 88) -> bytes:
    record = struct.pack("<IHHIH", position, 20480, item, length, 500 - track)
    return record + bytes(size - len(record))


def _project() -> FlpFile:
    events = [
        Event(199, b"26.1.6.5639\0"),
        Event(172, b"\x01\x01\x00"),  # three bytes in FL 2026
        *_channel(0, "Kick"),
        *_channel(1, "Pad - Volume", [Point(0, 0.0), Point(26, 0.95, 0.26), Point(494, 0.8706)]),
        _link(1, 0x71401FC0),
        Event(241, _text("Arrangement")),
        Event(233, _clip(0, 20481, 384 * 4, 1) + _clip(7488, 1, 47424, 22)),
        Event(238, (1).to_bytes(4, "little") + bytes(66)),
        Event(239, _text("Build")),
        Event(203, _text("Fruity Limiter")),  # a mixer slot's name: not a channel's
        Event(238, (22).to_bytes(4, "little") + bytes(66)),
    ]
    return FlpFile(0, 2, 96, tuple(events))


# --- the event format ---------------------------------------------------------------------


def test_events_round_trip_including_the_three_byte_event():
    project = _project()
    parsed = flp.parse(flp.build(project))
    assert parsed == project
    assert parsed.events[1] == Event(172, b"\x01\x01\x00")


def test_long_data_events_use_a_multi_byte_length():
    event = Event(233, bytes(range(256)) * 3)
    assert flp.parse(flp.build(FlpFile(0, 0, 96, (event,)))).events == (event,)


@pytest.mark.parametrize("data", [
    b"", b"RIFF0000", b"FLhd\x06\x00\x00\x00\x00\x00\x00\x00\x60\x00XXXX",
])
def test_what_isnt_an_fl_file_is_refused(data):
    with pytest.raises(FlpError):
        flp.parse(data)


def test_a_cut_off_file_is_refused():
    data = flp.build(_project())
    with pytest.raises(FlpError):
        flp.parse(data[:-5])


def test_a_fixed_size_event_with_the_wrong_size_is_refused():
    with pytest.raises(FlpError):
        flp.build(FlpFile(0, 0, 96, (Event(64, b"\x01"),)))


# --- reading a project --------------------------------------------------------------------


def test_reads_channels_automation_links_clips_and_track_names():
    project = reader.read_project(_project())
    assert project.channels == ("Kick", "Pad - Volume")
    assert [(p.beat, p.value) for p in project.automation[1]] == [
        (0, 0.0), (26, 0.95), (494, 0.8706),
    ]
    assert project.links == {1: (0x71401FC0,)}
    (arrangement,) = project.arrangements
    assert arrangement.name == "Arrangement"
    assert [(c.track, c.position, c.pattern, c.channel) for c in arrangement.clips] == [
        (1, 0, 1, None), (22, 7488, None, 1),
    ]
    assert project.track_names == {1: "Build"}


def test_playlists_of_older_versions_have_shorter_records():
    clips = reader.parse_playlist(_clip(384, 20482, 384, 3, size=60))
    assert [(c.track, c.pattern) for c in clips] == [(3, 2)]


def test_a_playlist_whose_records_cant_be_told_apart_reads_empty():
    assert reader.parse_playlist(b"\x00" * 88) == ()


@pytest.mark.parametrize(("rec_id", "track", "parameter"), [
    (0x71401FC0, 5, "volume"), (0x73C01FC0, 15, "volume"), (0x70801FC0, 2, "volume"),
    (0x70801FC1, 2, "pan"),
])
def test_mixer_targets_are_decoded(rec_id, track, parameter):
    assert reader.describe_target(rec_id) == {
        "rec_id": f"0x{rec_id:08X}", "kind": "mixer track", "track": track,
        "parameter": parameter,
    }


def test_an_unknown_target_keeps_only_its_id():
    assert reader.describe_target(0x00050000) == {"rec_id": "0x00050000"}


def test_summary_gives_bars_and_db():
    summary = automation.summarize(reader.read_project(_project()), include_clips=True)
    (pad,) = summary["automation"]
    assert pad["name"] == "Pad - Volume"
    assert pad["targets"][0]["track"] == 5
    assert pad["clips"] == [{"arrangement": "Arrangement", "track": 22, "bar": 20.5,
                             "length_bars": 123.5}]
    assert pad["points"][0]["db"] == -math.inf
    assert pad["points"][2]["db"] == pytest.approx(2.0, abs=0.05)
    clips = summary["arrangements"][0]["clips"]
    assert clips[0] == {"track": 1, "track_name": "Build", "bar": 1.0, "length_bars": 4.0,
                        "pattern": 1}
    assert clips[1]["channel_name"] == "Pad - Volume"


# --- presets ---------------------------------------------------------------------------------


def test_a_preset_holds_the_points_given():
    points = [Point(0, 0.0), Point(4, 1.0), Point(8, 0.2, -0.5), Point(16, 0.8)]
    parsed = flp.parse(preset.automation_preset(points, "Test shape"))
    (data,) = [e.data for e in parsed.events if e.id == 234]
    assert [(p.beat, p.value, p.tension) for p in reader.parse_points(data)] == [
        (0, 0.0, 0), (4, 1.0, 0), (8, 0.2, -0.5), (16, 0.8, 0),
    ]
    assert [e.data for e in parsed.events if e.id == 192] == [b"Test shape\0"]
    assert [e.value for e in parsed.events if e.id == 21] == [5]  # an automation clip


def test_a_preset_spans_the_parameters_whole_range():
    # Seen live: with the stock preset's range (647-2200 of 12800) a point at 0.8 put a
    # fader at 0.148 (-25 dB). A clip FL makes itself has 0-12800.
    parsed = flp.parse(preset.automation_preset([Point(0, 0.5), Point(4, 0.5)], "Range"))
    (data,) = [e.data for e in parsed.events if e.id == 219]
    assert struct.unpack_from("<II", data) == (0, 12800)


def test_a_preset_with_the_stock_points_is_fls_own_preset_but_for_the_range():
    stock = (Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
             / "Image-Line/FL Studio 2026/Data/Patches/Channel presets/Automation clips"
             / "Special/60-180 Tempo.fst")
    if not stock.is_file():
        pytest.skip("FL Studio 2026's presets aren't installed here")
    built = flp.parse(preset.automation_preset([Point(0, 0.5), Point(4, 0.5)], "Tempo (coarse)"))
    original = flp.parse(stock.read_bytes())
    assert [e.id for e in built.events] == [e.id for e in original.events]
    differing = [b.id for b, o in zip(built.events, original.events) if b.data != o.data]
    assert differing == [219]


@pytest.mark.parametrize("points", [
    [Point(0, 0.5)],
    [Point(1, 0.5), Point(4, 0.5)],
    [Point(0, 0.5), Point(4, 1.5)],
    [Point(0, 0.5), Point(4, 0.5, 2.0)],
    [Point(0, 0.5), Point(4, 0.5), Point(2, 0.5)],
])
def test_points_fl_cant_show_are_refused(points):
    with pytest.raises(ValueError):
        preset.automation_preset(points, "x")


# --- fader --------------------------------------------------------------------------------


@pytest.mark.parametrize(("position", "db"), [
    (0.8, 0.0), (1.0, 5.59), (0.5, -9.179), (0.025, -42.179),
])
def test_fader_positions_give_the_measured_levels(position, db):
    assert fader.position_to_db(position) == pytest.approx(db)
    assert fader.db_to_position(db) == pytest.approx(position)


def test_fader_levels_between_measurements_and_at_the_ends():
    assert fader.position_to_db(0.0) == -math.inf
    assert fader.db_to_position(-math.inf) == 0.0
    for db in (-60.0, -20.0, -4.0, 2.0, 4.0):
        assert fader.position_to_db(fader.db_to_position(db)) == pytest.approx(db, abs=1e-6)
    with pytest.raises(ValueError):
        fader.db_to_position(6.0)


# --- points for a new clip -----------------------------------------------------------------


def test_points_at_song_bars_become_beats_from_the_first():
    start, points = automation.to_points(
        [{"bar": 20.5, "db": -80}, {"bar": 27, "db": 4.2, "tension": 0.3},
         {"bar": 144, "value": 0.87}],
        "mixer track", "volume", 4,
    )
    assert start == 20
    assert [p.beat for p in points] == [0, 2, 28, 496]  # held from the bar's start
    assert points[0].value == points[1].value
    assert points[2].value == pytest.approx(fader.db_to_position(4.2))
    assert points[2].tension == 0.3


def test_points_starting_on_a_bar_line_get_no_extra_point():
    start, points = automation.to_points(
        [{"bar": 11, "value": 0.0}, {"bar": 22, "value": 0.9}], "mixer track", "volume", 4)
    assert start == 11
    assert [p.beat for p in points] == [0, 44]


def test_pan_points_go_from_left_to_right():
    _start, points = automation.to_points(
        [{"bar": 1, "pan": -1}, {"bar": 2, "pan": 0}, {"bar": 3, "pan": 1}], "channel", "pan", 4)
    assert [p.value for p in points] == [0.0, 0.5, 1.0]


@pytest.mark.parametrize(("points", "kind", "parameter"), [
    ([], "mixer track", "volume"),
    ([{"db": 0}, {"bar": 2, "db": 0}], "mixer track", "volume"),
    ([{"bar": 1, "db": 0}, {"bar": 2, "db": 0}], "channel", "volume"),
    ([{"bar": 1, "pan": 0}, {"bar": 2, "pan": 0}], "mixer track", "volume"),
    ([{"bar": 1, "value": 0.5, "db": 0}, {"bar": 2, "value": 0}], "mixer track", "volume"),
    ([{"bar": 0, "value": 0}, {"bar": 2, "value": 0}], "mixer track", "volume"),
])
def test_wrong_points_are_refused(points, kind, parameter):
    with pytest.raises(ValueError):
        automation.to_points(points, kind, parameter, 4)


# --- finding the saved project ---------------------------------------------------------


def test_the_newest_save_of_the_open_project_is_read(tmp_path):
    folder = tmp_path / "Song" / "Song"
    (folder / "Backup").mkdir(parents=True)
    files = {
        "saved": folder / "Song.flp",
        "autosave": folder / "Backup" / "Song (autosaved on 4-10-2026 at 12h52).flp",
        "overwritten": folder / "Backup" / "Song (overwritten on 4-10-2026 at 13h00).flp",
        "other": tmp_path / "Other.flp",
    }
    for age, path in enumerate(files.values()):
        path.write_bytes(b"")
        os.utime(path, (1000 + age, 1000 + age))
    assert automation.find_project_file(tmp_path, "Song") == files["autosave"]
    assert automation.find_project_file(tmp_path, None) == files["other"]
    assert automation.find_project_file(tmp_path / "missing", "Song") is None


def test_fl_read_project_reads_a_given_file(tmp_path):
    path = tmp_path / "Song.flp"
    path.write_bytes(flp.build(_project()))
    tools = ToolCollector()
    automation.register_automation_tools(tools)
    result = tools.tools["fl_read_project"](path=str(path))
    assert result["file"] == str(path)
    assert result["automation"][0]["name"] == "Pad - Volume"
    assert "arrangements" not in result


def test_fl_read_project_reports_a_file_it_cant_read(tmp_path):
    path = tmp_path / "Broken.flp"
    path.write_bytes(b"nope")
    tools = ToolCollector()
    automation.register_automation_tools(tools)
    assert "error" in tools.tools["fl_read_project"](path=str(path))


# --- creating in FL ------------------------------------------------------------------------


class FakeFL:
    """The controller commands creating automation uses, and what FL does in its menu."""

    def __init__(self) -> None:
        self.rack = [{"name": "Pad"}]
        self.made_name = "Pad - Volume"
        self.position = 0
        self.song_bars = 150
        self.log: list[str] = []
        self.loads: list[tuple[str, int]] = []
        self.deleted: list[list[int]] = []

    def call(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        self.log.append(action)
        if action == "playlist.getState":
            return {"bar_ticks": 384}
        if action == "transport.setPosition":
            self.position = params["position"]
            return {}
        if action == "playlist.getSongPosition":
            return {"ticks": self.position}
        if action == "channels.getAll":
            return {"channels": [dict(c) for c in self.rack]}
        if action == "channels.setName":
            self.rack[params["index"]]["name"] = params["name"]
            return {}
        if action.startswith(("mixer.set", "channels.set")):
            return {}
        raise AssertionError(f"unexpected command {action}")

    def menu(self) -> None:
        self.log.append("menu")
        self.rack.append({"name": self.made_name})

    def click_ruler_at(self, bar: int, bar_ticks: int) -> None:
        if bar > self.song_bars:
            raise PlaylistError(f"The playlist did not scroll to bar {bar}")
        self.log.append("ruler")
        self.position = (bar - 1) * bar_ticks


@pytest.fixture
def fake_fl(monkeypatch, settings_dir):
    fl = FakeFL()
    monkeypatch.setattr(automation, "call", fl.call)
    monkeypatch.setattr(automation, "choose_automation_for_last_tweaked", fl.menu)
    monkeypatch.setattr(automation.fl_playlist, "click_ruler_at", fl.click_ruler_at)
    monkeypatch.setattr(automation, "_make_browser_see", lambda name: fl.log.append("browser"))
    monkeypatch.setattr(automation, "send_to_channel",
                        lambda load, path, index, keep_name: fl.loads.append((path, index)))
    monkeypatch.setattr(automation.fl_channel_rack, "delete_channels", fl.deleted.append)
    monkeypatch.setattr(automation, "NAME_TIMEOUT", 0.0)
    return fl


TARGET = {"kind": "mixer track", "index": 5, "owner": "Pad", "value": 0.9, "parameter": "volume"}
POINTS = [Point(0, 0.0), Point(26, 0.95), Point(494, 0.87)]


class _Load:
    def wait_until(self, read, done):
        value = read()
        return value if done(value) else None


def test_creating_clicks_the_bar_tweaks_the_target_and_loads_the_points(fake_fl, settings_dir):
    result = automation.create_automation(_Load(), TARGET, 20, POINTS, None, 4)
    assert fake_fl.position == 19 * 384
    assert fake_fl.log.index("ruler") < fake_fl.log.index("mixer.setTrackVolume")
    assert fake_fl.log.index("mixer.setTrackVolume") < fake_fl.log.index("menu")
    assert fake_fl.loads == [("Project bones/FL-MCP automation/Pad - Volume.fst", 1)]
    written = settings_dir.parent / "Projects/Project bones/FL-MCP automation/Pad - Volume.fst"
    (data,) = [e.data for e in flp.parse(written.read_bytes()).events if e.id == 234]
    assert [p.beat for p in reader.parse_points(data)] == [0, 26, 494]
    assert result["channel"] == 1
    assert result["length_bars"] == 123.5


class _RemovingFakeFL(FakeFL):
    """Deleting a channel takes it out of the rack, and FL's menu makes `names` in turn."""

    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.names = list(names)

    def menu(self) -> None:
        self.made_name = self.names.pop(0)
        super().menu()

    def delete(self, indexes: list[int]) -> None:
        self.deleted.append(indexes)
        for index in sorted(indexes, reverse=True):
            del self.rack[index]


@pytest.fixture
def removing_fl(fake_fl, monkeypatch):
    def install(names):
        fl = _RemovingFakeFL(names)
        monkeypatch.setattr(automation, "call", fl.call)
        monkeypatch.setattr(automation, "choose_automation_for_last_tweaked", fl.menu)
        monkeypatch.setattr(automation, "send_to_channel",
                            lambda load, path, index, keep_name: fl.loads.append((path, index)))
        monkeypatch.setattr(automation.fl_channel_rack, "delete_channels", fl.delete)
        return fl
    return install


def test_an_unlinked_clip_is_removed_and_fl_asked_again(removing_fl):
    fl = removing_fl(["Auto clip", "Pad - Volume"])
    result = automation.create_automation(_Load(), TARGET, 1, POINTS, None, 4)
    assert fl.deleted == [[1]]
    assert result["channel"] == 1
    assert [c["name"] for c in fl.rack] == ["Pad", "Pad - Volume"]
    assert fl.loads == [("Project bones/FL-MCP automation/Pad - Volume.fst", 1)]


def test_a_clip_made_for_something_else_every_time_is_refused(removing_fl):
    fl = removing_fl(["Lead - Volume"] * automation.CREATE_ATTEMPTS)
    with pytest.raises(automation.AutomationError):
        automation.create_automation(_Load(), TARGET, 1, POINTS, None, 4)
    assert fl.deleted == [[1]] * automation.CREATE_ATTEMPTS
    assert [c["name"] for c in fl.rack] == ["Pad"]
    assert fl.loads == []


def test_a_bar_past_the_song_end_is_refused_before_anything_is_made(fake_fl):
    with pytest.raises(automation.AutomationError):
        automation.create_automation(_Load(), TARGET, 200, POINTS, None, 4)
    assert "menu" not in fake_fl.log


def test_the_clip_can_be_renamed(fake_fl):
    result = automation.create_automation(_Load(), TARGET, 1, POINTS, "Pad fade", 4)
    assert result["name"] == "Pad fade"
    assert fake_fl.rack[1]["name"] == "Pad fade"


# --- FL's menus ------------------------------------------------------------------------------


class FakeMenus:
    """FL's menu bar: FILE opens a menu, Right moves on to the next one, Enter chooses."""

    BAR = [(10, 30, 200, 600), (60, 30, 260, 500), (110, 30, 650, 1000)]  # FILE, EDIT, ADD

    def __init__(self, right_opens_submenu: bool = False) -> None:
        self.open: int | None = None
        self.keys: list[int] = []
        self.chosen = False
        self.right_opens_submenu = right_opens_submenu

    def click_main_menu(self) -> bool:
        self.open = 0
        return True

    def popup_menu_rects(self) -> list:
        return [] if self.open is None else [self.BAR[self.open]]

    def popup_menu_count(self) -> int:
        return len(self.popup_menu_rects())

    def press_in_popup_menu(self, key, menus=1) -> bool:
        self.keys.append(key)
        if key == automation.fl_windows.VK_RIGHT and not self.right_opens_submenu:
            self.open = min(self.open + 1, len(self.BAR) - 1)
        return True

    def press_enter_in_popup_menu(self) -> bool:
        self.chosen = self.open == 2
        self.open = None
        return True

    def close_popup_menus(self) -> None:
        self.open = None


@pytest.fixture
def menus(monkeypatch):
    from fl_studio_mcp.utils import fl_automation

    def install(**kwargs):
        fake = FakeMenus(**kwargs)
        for name in ("click_main_menu", "popup_menu_rects", "popup_menu_count",
                     "press_in_popup_menu", "press_enter_in_popup_menu", "close_popup_menus"):
            monkeypatch.setattr(fl_automation.fl_windows, name, getattr(fake, name))
        monkeypatch.setattr(fl_automation.fl_windows, "pointer_over_menu", lambda: False)
        monkeypatch.setattr(fl_automation.platform, "system", lambda: "Windows")
        monkeypatch.setattr(fl_automation, "MENU_TIMEOUT", 0.05)
        return fake

    return install


def test_the_menu_walk_reaches_add_and_chooses_second_from_the_bottom(menus):
    from fl_studio_mcp.utils import fl_automation, fl_windows

    fake = menus()
    fl_automation.choose_automation_for_last_tweaked()
    assert fake.keys == [fl_windows.VK_RIGHT, fl_windows.VK_RIGHT, fl_windows.VK_END,
                         fl_windows.VK_UP]
    assert fake.chosen


def test_the_menu_walk_stops_when_the_bar_doesnt_move_on(menus):
    from fl_studio_mcp.utils import fl_automation

    fake = menus(right_opens_submenu=True)
    with pytest.raises(fl_automation.AutomationError):
        fl_automation.choose_automation_for_last_tweaked()
    assert not fake.chosen
    assert fake.open is None


def test_the_target_is_moved_and_put_back_so_fl_counts_it_as_tweaked(fake_fl, monkeypatch):
    settings = []
    original = fake_fl.call

    def recording(action, params=None):
        if action == "mixer.setTrackVolume":
            settings.append(params["volume"])
        return original(action, params)

    monkeypatch.setattr(automation, "call", recording)
    automation.create_automation(_Load(), TARGET, 1, POINTS, None, 4)
    assert settings == [pytest.approx(0.91), 0.9]


def test_a_parameter_at_its_top_is_moved_down_and_back():
    calls = []
    original = automation.call
    try:
        automation.call = lambda action, params=None: calls.append(params) or {}
        automation._touch({**TARGET, "kind": "channel", "parameter": "pan", "value": 1.0})
    finally:
        automation.call = original
    assert calls == [{"index": 5, "pan": pytest.approx(0.99)}, {"index": 5, "pan": 1.0}]


def test_a_clip_fl_renames_after_its_target_a_moment_later_is_kept(fake_fl, monkeypatch):
    original = fake_fl.call
    reads = []

    def renaming_later(action, params=None):
        result = original(action, params)
        if action == "channels.getAll" and len(result["channels"]) == 2:
            reads.append(1)
            if len(reads) <= 2:  # the reads right after the channel appeared
                result["channels"][1]["name"] = "Auto clip"
        return result

    monkeypatch.setattr(automation, "call", renaming_later)
    monkeypatch.setattr(automation, "NAME_TIMEOUT", 1.0)
    monkeypatch.setattr(automation, "NAME_POLL_INTERVAL", 0.0)
    result = automation.create_automation(_Load(), TARGET, 1, POINTS, None, 4)
    assert result["name"] == "Pad - Volume"
    assert fake_fl.deleted == []


def test_find_saved_project_skips_backups(tmp_path):
    from fl_studio_mcp.tools.automation import find_saved_project

    own = tmp_path / "Song" / "Song.flp"
    backup = tmp_path / "Song" / "Backup" / "Song.flp"
    for f in (own, backup):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"FLhd")
    (tmp_path / "Song" / "Backup" / "Song (autosaved on 1).flp").write_bytes(b"FLhd")

    assert find_saved_project(tmp_path, "Song") == own
    assert find_saved_project(tmp_path, "Other") is None
