"""FL's browser: reading folders, reaching a file, its menu, and the loading tools."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import register_browser_tools, register_channel_setup_tools
from fl_studio_mcp.utils import fl_browser
from fl_studio_mcp.utils.fl_browser import BrowserError
from tests.fakes import ToolCollector


def _names(items: list[fl_browser.BrowserItem]) -> list[str]:
    return [("/" if item.is_folder else "") + item.name for item in items]


# --- reading folders -------------------------------------------------------


def test_the_top_level_is_listed_with_everything_closed(fake_fl):
    fake_fl.expanded = {("Packs",), ("Packs", "Drums")}  # left open by the user
    fake_fl.cursor = 4

    items = fl_browser.list_folder("")

    assert _names(items) == ["/Current project", "/Plugin database", "/Channel presets", "/Packs",
                             "/Templates"]
    assert fake_fl.expanded == set()
    assert fake_fl.browser_focused
    assert ("browser.selectTab", {"direction": "first"}) in fake_fl.sent


def test_a_folder_lists_only_its_own_items(fake_fl):
    assert _names(fl_browser.list_folder("Packs")) == ["/Drums", "/Loops"]
    assert _names(fl_browser.list_folder("packs/drums/kicks")) == [
        "808 Kick.wav", "909 Kick.wav", "Kick.wav",
    ]
    assert fake_fl.focused_path() == ("Packs", "Drums", "Kicks")


def test_the_last_folder_of_a_level_ends_where_its_parents_level_continues(fake_fl):
    # "Loops" is the last item of "Packs": its items end at "Templates", two levels up.
    assert _names(fl_browser.list_folder("Packs/Loops")) == ["/Kicks", "Snares.wav"]
    assert _names(fl_browser.list_folder("Packs\\Loops\\Kicks")) == ["Loop.wav"]


def test_the_very_last_folder_ends_at_the_end_of_the_browser(fake_fl):
    fake_fl.tree = {**fake_fl.tree, "User data": {"Mine": {"a.wav": None}}}

    assert _names(fl_browser.list_folder("User data/Mine")) == ["a.wav"]


def test_an_item_named_like_the_next_folder_does_not_end_the_listing(fake_fl):
    # "Drums" holds a "Loops" folder; the item after "Drums" is a "Loops" folder too.
    fake_fl.tree["Packs"]["Drums"] = {"Loops": {"x.wav": None}, **fake_fl.tree["Packs"]["Drums"]}

    assert _names(fl_browser.list_folder("Packs/Drums")) == ["/Loops", "/Hats", "/Kicks"]
    assert fake_fl.focused_path() == ("Packs", "Drums")
    assert _names(fl_browser.list_folder("Packs/Drums/Loops")) == ["x.wav"]


def test_a_folder_holding_a_folder_of_its_own_name_is_read_in_full(fake_fl):
    fake_fl.tree["Packs"]["Drums"]["Kicks"] = {"Kicks": {"deep.wav": None}, "a.wav": None}

    assert _names(fl_browser.list_folder("Packs/Drums/Kicks")) == ["/Kicks", "a.wav"]
    assert _names(fl_browser.list_folder("Packs/Drums/Kicks/Kicks")) == ["deep.wav"]
    assert fl_browser.go_to_file("Packs/Drums/Kicks/a").name == "a.wav"
    assert fake_fl.focused_path() == ("Packs", "Drums", "Kicks", "a.wav")


def test_an_open_first_item_is_closed_too(fake_fl):
    fake_fl.tree = {"First": {"inside.wav": None}, **fake_fl.tree}
    fake_fl.expanded = {("First",)}

    assert _names(fl_browser.list_folder(""))[:2] == ["/First", "/Current project"]


def test_an_empty_folder_lists_nothing(fake_fl):
    assert fl_browser.list_folder("Templates") == []


@pytest.mark.parametrize(("path", "message"), [
    ("Nope", "'Nope' is not in the browser's top level"),
    ("Packs/Drums/Snares", "'Snares' is not in 'Drums'"),
    ("Packs/Loops/Snares.wav/x", "is a file, not a folder"),
])
def test_a_folder_that_is_not_there_is_reported(fake_fl, path, message):
    with pytest.raises(BrowserError, match=message):
        fl_browser.list_folder(path)


def test_a_long_folder_is_read_in_several_commands(fake_fl, monkeypatch):
    monkeypatch.setattr(fl_browser, "STEPS_PER_COMMAND", 2)

    assert len(fl_browser.list_folder("Packs/Drums/Kicks")) == 3


def test_an_endless_browser_is_given_up(fake_fl, monkeypatch):
    monkeypatch.setattr(fl_browser, "MAX_ITEMS", 2)
    monkeypatch.setattr(fl_browser, "STEPS_PER_COMMAND", 1)

    with pytest.raises(BrowserError, match="more than 2 items"):
        fl_browser.list_folder("")


def test_a_lost_connection_is_a_browser_error(fake_fl, monkeypatch):
    def dropped(action, params=None, timeout=2.0):
        raise RuntimeError("MIDI port closed")

    monkeypatch.setattr(fake_fl, "send_command", dropped)

    with pytest.raises(BrowserError, match="MIDI port closed"):
        fl_browser.list_folder("")


# --- reaching a file -------------------------------------------------------


def test_go_to_file_puts_the_cursor_on_the_file(fake_fl):
    item = fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick.wav")

    assert item == fl_browser.BrowserItem("909 Kick.wav", 7)
    assert fake_fl.focused_path() == ("Packs", "Drums", "Kicks", "909 Kick.wav")


def test_the_extension_may_be_left_out(fake_fl):
    assert fl_browser.go_to_file("Packs/Drums/Kicks/kick").name == "Kick.wav"
    assert fl_browser.go_to_file("Channel presets/3x Osc/Hoover").name == "Hoover.fst"


@pytest.mark.parametrize(("path", "message"), [
    ("Kick", "with its folders"),
    ("Packs/Drums", "is a folder, not a file"),
    ("Packs/Drums/Kicks/song.flp", "not a sample or a preset"),
    ("Packs/Drums/Kicks/Nope", "'Nope' is not in 'Kicks'"),
])
def test_a_file_that_cannot_be_reached_is_reported(fake_fl, path, message):
    fake_fl.tree["Packs"]["Drums"]["Kicks"]["song.flp"] = None
    with pytest.raises(BrowserError, match=message):
        fl_browser.go_to_file(path)


def test_two_files_with_the_same_stem_need_the_extension(fake_fl):
    fake_fl.tree["Packs"]["Drums"]["Hats"]["Hat 1.fst"] = None

    with pytest.raises(BrowserError, match="more than once"):
        fl_browser.go_to_file("Packs/Drums/Hats/Hat 1")
    assert fl_browser.go_to_file("Packs/Drums/Hats/Hat 1.fst").name == "Hat 1.fst"


def test_a_plugins_own_preset_format_can_be_reached(fake_fl):
    # Seen live: Morphine's presets are .mrp, and their menu is the same as an .fst's.
    fake_fl.tree["Channel presets"]["Morphine"] = {"LED Scream MC.mrp": None}

    assert fl_browser.go_to_file("Channel presets/Morphine/LED Scream MC").name == (
        "LED Scream MC.mrp")


# --- the item's menu -------------------------------------------------------


def test_choose_from_menu_walks_down_to_the_entry_and_confirms(fake_fl):
    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")

    fl_browser.choose_from_menu(fl_browser.OPEN_IN_NEW_CHANNEL)

    assert fake_fl.rack[-1]["name"] == "909 Kick"
    assert fake_fl.menus == 0


def test_a_minimized_fl_is_brought_back_first_because_its_menu_would_not_open(fake_fl):
    fake_fl.minimized = True

    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")
    assert fake_fl.actions() and not fake_fl.minimized
    fl_browser.choose_from_menu(fl_browser.OPEN_IN_NEW_CHANNEL)

    assert fake_fl.rack[-1]["name"] == "909 Kick"
    assert not fake_fl.minimized


def test_the_walk_starts_from_the_first_entry_whatever_the_pointer_highlighted(fake_fl):
    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")
    fake_fl.hovered_entry = 7  # the menu opened under the pointer, on a lower entry

    fl_browser.choose_from_menu(fl_browser.OPEN_IN_NEW_CHANNEL)

    assert fake_fl.rack[-1]["name"] == "909 Kick"


def test_nothing_is_chosen_while_the_pointer_is_over_the_menu(fake_fl):
    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")
    fake_fl.pointer_on_menu = True

    with pytest.raises(BrowserError, match="pointer is over"):
        fl_browser.choose_from_menu(fl_browser.OPEN_IN_NEW_CHANNEL)
    assert len(fake_fl.rack) == 2 and fake_fl.menus == 0


def test_choose_from_menu_is_windows_only(fake_fl, monkeypatch):
    monkeypatch.setattr(fl_browser.platform, "system", lambda: "Darwin")

    with pytest.raises(BrowserError, match="only supported on Windows"):
        fl_browser.choose_from_menu(1)
    assert "browser.openFocused" not in fake_fl.actions()


def test_choose_from_menu_does_nothing_while_another_menu_is_open(fake_fl):
    fake_fl.menus = 1

    with pytest.raises(BrowserError, match="has a menu open"):
        fl_browser.choose_from_menu(1)
    assert "browser.openFocused" not in fake_fl.actions()


def test_choose_from_menu_reports_a_menu_that_did_not_open(fake_fl):
    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")
    fake_fl.browser_focused = False  # the menu only opens for a focused browser

    with pytest.raises(BrowserError, match="did not open"):
        fl_browser.choose_from_menu(1)


def test_choose_from_menu_closes_a_menu_that_did_not_take_the_choice(fake_fl, monkeypatch):
    from fl_studio_mcp.utils import fl_windows

    fl_browser.go_to_file("Packs/Drums/Kicks/909 Kick")
    monkeypatch.setattr(fl_windows, "press_enter_in_popup_menu", lambda: False)

    with pytest.raises(BrowserError, match="did not take the choice"):
        fl_browser.choose_from_menu(1)
    assert fake_fl.menus == 0


# --- tools -----------------------------------------------------------------


@pytest.fixture
def tools(fake_fl) -> dict[str, Any]:
    collector = ToolCollector()
    register_browser_tools(collector)
    return collector.tools


def test_browser_list_tool(tools):
    assert tools["fl_browser_list"]("Packs/Loops") == {
        "path": "Packs/Loops",
        "items": [{"name": "Kicks", "kind": "folder"}, {"name": "Snares.wav", "kind": "file"}],
    }
    assert "is not in" in tools["fl_browser_list"]("Nope")["error"]


def test_a_sample_is_loaded_in_a_new_channel_and_named(tools, fake_fl):
    fake_fl.plugin_windows = 1  # one was open before

    result = tools["fl_load_in_new_channel"]("Packs/Drums/Kicks/909 Kick", name="Kick")

    assert result == {"index": 2, "name": "Kick", "plugin": "Sampler", "loaded": "909 Kick.wav"}
    assert [c["name"] for c in fake_fl.rack] == ["808 Kick", "Lead", "Kick"]
    assert fake_fl.plugin_windows == 0  # closed before the load and after it


def test_a_preset_is_loaded_with_its_plugin(tools, fake_fl):
    result = tools["fl_load_in_new_channel"]("Channel presets/3x Osc/Hoover")

    assert result == {"index": 2, "name": "Hoover", "plugin": "3x Osc", "loaded": "Hoover.fst"}
    assert "channels.setName" not in fake_fl.actions()


def test_an_effect_is_not_opened_in_a_new_channel(tools, fake_fl):
    result = tools["fl_load_in_new_channel"]("Plugin database/Effects/Dynamics/Maximus")

    assert "fl_add_effect" in result["error"]
    assert fake_fl.sent == []


def test_an_effect_anywhere_in_the_plugin_folders_is_not_for_a_channel(tools, fake_fl):
    result = tools["fl_load_in_new_channel"]("Plugin database/Installed/Effects/VST/Thing")

    assert "fl_add_effect" in result["error"]
    assert fake_fl.sent == []


def test_a_message_box_during_a_load_is_closed_and_told_about(tools, fake_fl):
    fake_fl.unavailable = {"3x Osc"}  # FL loads it as a demo and says so

    result = tools["fl_load_in_new_channel"]("Channel presets/3x Osc/Hoover", name="Lead")

    assert (result["index"], result["name"], result["plugin"]) == (2, "Lead", "3x Osc")
    assert "message box ('FL Studio trial')" in result["warning"]
    assert fake_fl.message is None
    assert fake_fl.rack[2]["name"] == "Lead"  # the rename went through once the box was gone


def test_a_failed_load_mentions_the_message_box_too(tools, fake_fl):
    fake_fl.loads = False
    fake_fl.message = "Confirm"

    error = tools["fl_load_in_new_channel"]("Packs/Drums/Kicks/909 Kick")["error"]

    assert "did not add a channel" in error and "message box ('Confirm')" in error


def test_close_message_tool(tools, fake_fl):
    assert tools["fl_close_message"]() == {"closed": 0, "title": None}

    fake_fl.message = "Confirm"
    assert tools["fl_close_message"]() == {"closed": 1, "title": "Confirm"}
    assert fake_fl.message is None


def test_a_load_that_adds_no_channel_is_reported(tools, fake_fl):
    fake_fl.loads = False

    result = tools["fl_load_in_new_channel"]("Packs/Drums/Kicks/909 Kick", name="Kick")

    assert "did not add a channel" in result["error"]
    assert "channels.setName" not in fake_fl.actions()


def test_an_oddly_changed_rack_is_not_renamed(tools, fake_fl, monkeypatch):
    def two_channels() -> bool:
        fake_fl.menus = 0
        fake_fl.rack.extend([{"name": "a", "plugin": None, "track": 0}] * 2)
        return True

    from fl_studio_mcp.utils import fl_windows
    monkeypatch.setattr(fl_windows, "press_enter_in_popup_menu", two_channels)

    result = tools["fl_load_in_new_channel"]("Packs/Drums/Kicks/909 Kick", name="Kick")

    assert "unexpected way" in result["error"]
    assert "channels.setName" not in fake_fl.actions()


def test_a_sample_replaces_a_channels_sound_and_the_name_is_kept(tools, fake_fl):
    result = tools["fl_load_in_channel"]("Packs/Drums/Kicks/909 Kick", "808 kick")

    assert result == {"index": 0, "name": "808 Kick", "plugin": "Sampler",
                      "loaded": "909 Kick.wav"}
    assert fake_fl.selected == 0
    # FL renamed it to "909 Kick"; the tool put the old name back
    assert fake_fl.sent[-1] == ("channels.setName", {"index": 0, "name": "808 Kick"})


def test_the_new_name_can_be_kept(tools, fake_fl):
    result = tools["fl_load_in_channel"]("Packs/Drums/Kicks/909 Kick", 1, keep_name=False)

    assert result["name"] == "909 Kick" and result["plugin"] == "3x Osc"
    assert fake_fl.selected == 1


def test_loading_the_sound_a_channel_is_named_after_is_still_confirmed(tools, fake_fl):
    fake_fl.rack[0]["name"] = "909 Kick"

    result = tools["fl_load_in_channel"]("Packs/Drums/Kicks/909 Kick", 0)

    assert result["name"] == "909 Kick" and "warning" not in result


def test_a_replacement_that_did_not_happen_is_an_error_and_the_name_is_put_back(tools, fake_fl):
    fake_fl.loads = False

    result = tools["fl_load_in_channel"]("Packs/Drums/Kicks/909 Kick", 0)

    assert "did not load '909 Kick.wav' into channel 0" in result["error"]
    assert fake_fl.rack[0]["name"] == "808 Kick"


def test_the_name_is_put_back_when_the_menu_fails(tools, fake_fl, monkeypatch):
    from fl_studio_mcp.utils import fl_windows
    monkeypatch.setattr(fl_windows, "press_enter_in_popup_menu", lambda: False)

    assert "did not take the choice" in tools["fl_load_in_channel"](
        "Packs/Drums/Kicks/909 Kick", 0)["error"]
    assert fake_fl.rack[0]["name"] == "808 Kick"


@pytest.mark.parametrize(("channel", "message"), [("tuba", "tuba"), (9, "no channel 9")])
def test_a_channel_that_is_not_there_is_reported(tools, fake_fl, channel, message):
    result = tools["fl_load_in_channel"]("Packs/Drums/Kicks/909 Kick", channel)

    assert message in result["error"]
    assert "browser.openFocused" not in fake_fl.actions()


def test_an_effect_goes_to_the_track_of_a_channel_routed_there(tools, fake_fl):
    fake_fl.selected = 1

    result = tools["fl_add_effect"]("Plugin database/Effects/Dynamics/Maximus", 1)

    assert result == {"track": 1, "slot": 0, "plugin": "Maximus",
                      "effects": [{"slot": 0, "plugin": "Maximus"}]}
    assert "channels.routeToMixer" not in fake_fl.actions()
    assert fake_fl.selected == 1  # the selection is put back
    assert fake_fl.plugin_windows == 0


def test_an_effect_for_a_track_without_channels_borrows_one(tools, fake_fl):
    result = tools["fl_add_effect"]("Plugin database/Effects/Dynamics/Maximus", 0)

    assert result["slot"] == 1 and result["plugin"] == "Maximus"
    assert fake_fl.effects[0] == ["Fruity Limiter", "Maximus"]
    assert fake_fl.rack[0]["track"] == 1  # routed back


def test_a_borrowed_channel_is_routed_back_when_the_load_fails(tools, fake_fl):
    fake_fl.loads = False

    result = tools["fl_add_effect"]("Plugin database/Effects/Dynamics/Maximus", 5)

    assert "did not add" in result["error"]
    assert fake_fl.rack[0]["track"] == 1


def test_a_channel_that_cannot_be_routed_back_is_reported_not_raised(tools, fake_fl, monkeypatch):
    send = fake_fl.send_command
    routes: list[int] = []

    def flaky(action, params=None, timeout=2.0):
        if action == "channels.routeToMixer":
            routes.append(params["mixer_track"])
            if len(routes) == 2:  # the way back
                raise RuntimeError("MIDI port closed")
        return send(action, params, timeout)

    monkeypatch.setattr(fake_fl, "send_command", flaky)

    result = tools["fl_add_effect"]("Plugin database/Effects/Dynamics/Maximus", 5)

    assert result["plugin"] == "Maximus"
    assert "could not be routed back to 1: MIDI port closed" in result["warning"]


def test_a_lost_connection_is_an_error_not_an_exception(tools, fake_fl, monkeypatch):
    def dropped(action, params=None, timeout=2.0):
        raise RuntimeError("MIDI port closed")

    monkeypatch.setattr(fake_fl, "send_command", dropped)

    for call in (lambda: tools["fl_get_track_effects"](0), lambda: tools["fl_undo"](),
                 lambda: tools["fl_add_effect"]("Plugin database/Effects/Dynamics/Maximus", 1),
                 lambda: tools["fl_load_in_new_channel"]("Packs/Drums/Kicks/909 Kick")):
        assert call() == {"error": "MIDI port closed"}


def test_only_effects_are_added_as_effects(tools, fake_fl):
    result = tools["fl_add_effect"]("Packs/Drums/Kicks/909 Kick", 1)

    assert "An effect must come from" in result["error"]
    assert fake_fl.sent == []


def test_an_effect_needs_a_channel_to_aim_with(tools, fake_fl):
    fake_fl.rack.clear()

    assert "rack is empty" in tools["fl_add_effect"](
        "Plugin database/Effects/Dynamics/Maximus", 1)["error"]


def test_track_effects_and_undo_pass_fls_answer_on(tools, fake_fl):
    assert tools["fl_get_track_effects"](0)["effects"] == [
        {"slot": 0, "plugin": "Fruity Limiter"}]
    assert tools["fl_undo"](2)["undone"] == 2
    fake_fl.errors["project.undo"] = "nope"
    assert tools["fl_undo"]() == {"error": "nope"}


# --- fl_add_channel goes through the plugin database folder ----------------


@pytest.fixture
def channel_tools(fake_fl, settings_dir) -> dict[str, Any]:
    root = settings_dir.parent / "Presets" / "Plugin database" / "Generators"
    for relative in ("Synth classic/3x Osc.fst", "Synth classic/Sytrus.fst"):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(b"")
    collector = ToolCollector()
    register_channel_setup_tools(collector)
    return collector.tools


def test_add_channel_loads_the_generator_from_the_plugin_database(channel_tools, fake_fl):
    result = channel_tools["fl_add_channel"]("sytrus", name="Screech")

    assert result == {"index": 2, "name": "Screech", "plugin": "Sytrus"}
    assert fake_fl.rack[2] == {"name": "Screech", "plugin": "Sytrus", "track": 0}


def test_add_channel_keeps_the_plugin_name_by_default(channel_tools, fake_fl):
    assert channel_tools["fl_add_channel"]("3x Osc") == {
        "index": 2, "name": "3x Osc", "plugin": "3x Osc"}


def test_add_channel_rejects_unknown_plugins_before_touching_fl(channel_tools, fake_fl):
    assert "No generator named" in channel_tools["fl_add_channel"]("zzqq")["error"]
    assert fake_fl.sent == []


def test_add_channel_reports_a_generator_the_browser_does_not_show(channel_tools, fake_fl):
    del fake_fl.tree["Plugin database"]["Generators"]["Synth classic"]["Sytrus.fst"]

    assert "'Sytrus' is not in" in channel_tools["fl_add_channel"]("Sytrus")["error"]
