"""Saving through FL Studio's File menu (utils/fl_save.py), against a fake of FL's windows."""

from __future__ import annotations

import os

import pytest

from fl_studio_mcp.utils import fl_save
from fl_studio_mcp.utils.fl_save import SaveError, save_project


class FakeWindows:
    """FL's File menu: Enter on the 4th entry (Save) writes the project file."""

    VK_HOME, VK_DOWN, VK_RETURN = 0x24, 0x28, 0x0D
    SYSTEM_DIALOG_CLASS = "#32770"

    def __init__(self, project, *, pointer_over=False, shop=False, save_as=False):
        self.project = project
        self.pointer_over = pointer_over
        self.shop = shop
        self.save_as = save_as
        self.menus = 0
        self.entry = None
        self.keys = []
        self.closed = []

    def open_message(self):
        return None

    def click_main_menu(self):
        self.menus, self.entry = 1, 0
        return True

    def popup_menu_count(self):
        return self.menus

    def close_popup_menus(self):
        self.menus = 0

    def pointer_over_menu(self):
        return self.pointer_over

    def press_in_popup_menu(self, key, menus):
        if self.menus != menus:
            return False
        self.keys.append(key)
        if key == self.VK_HOME:
            self.entry = 0
        elif key == self.VK_DOWN:
            self.entry += 1
        elif key == self.VK_RETURN:
            self.menus = 0
            if self.entry == 3 and not self.save_as:
                stat = self.project.stat()
                os.utime(self.project, (stat.st_atime, stat.st_mtime + 10))
        return True

    def window_count(self, class_name):
        if class_name == fl_save.TRIAL_SHOP_CLASS:
            return int(self.shop)
        return int(self.save_as and self.menus == 0 and bool(self.keys))

    def close_windows(self, class_name):
        self.closed.append(class_name)
        self.shop = False


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(fl_save.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_save, "SAVE_TIMEOUT", 0.3)
    monkeypatch.setattr(fl_save, "POLL_INTERVAL", 0.01)
    file = tmp_path / "Song.flp"
    file.write_bytes(b"FLhd")
    return file


def _use(monkeypatch, windows):
    monkeypatch.setattr(fl_save, "fl_windows", windows)
    return windows


def test_save_chooses_the_fourth_entry_and_waits_for_the_file(project, monkeypatch):
    windows = _use(monkeypatch, FakeWindows(project))
    before = project.stat().st_mtime

    assert save_project(project) is False

    assert windows.keys == [windows.VK_HOME] + [windows.VK_DOWN] * 3 + [windows.VK_RETURN]
    assert project.stat().st_mtime != before


def test_the_trial_window_is_closed(project, monkeypatch):
    windows = _use(monkeypatch, FakeWindows(project, shop=True))

    assert save_project(project) is True
    assert windows.closed == [fl_save.TRIAL_SHOP_CLASS]


def test_nothing_is_chosen_with_the_pointer_over_the_menu(project, monkeypatch):
    windows = _use(monkeypatch, FakeWindows(project, pointer_over=True))

    with pytest.raises(SaveError, match="mouse pointer"):
        save_project(project)
    assert windows.VK_RETURN not in windows.keys
    assert windows.menus == 0


def test_a_save_as_dialog_is_reported(project, monkeypatch):
    _use(monkeypatch, FakeWindows(project, save_as=True))

    with pytest.raises(SaveError, match="Save As"):
        save_project(project)


def test_a_file_that_is_not_written_is_reported(project, monkeypatch):
    windows = _use(monkeypatch, FakeWindows(project))
    windows.press_in_popup_menu = lambda key, menus: True  # Enter does nothing

    with pytest.raises(SaveError, match="was not written"):
        save_project(project)
