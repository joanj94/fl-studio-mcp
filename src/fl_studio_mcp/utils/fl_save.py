"""Saving the open project through FL Studio's File menu.

Seen live (FL 2026): `transport.globalTransport(midi.FPT_Save)` (Ctrl+S) saved the
project twice and then, after FL had reloaded it, did nothing at all (no window,
no file), while File > Save did save. So this posts the menu walk: a click on
FILE in the menu bar, Home, three Downs (New, New from template, Open, Save; the
separators are skipped) and Enter, like the export (`fl_render`). The save is
confirmed by the project's file on disk (seen live: written about 7 s after
Enter). Seen live: Save doesn't rewrite a project that hasn't changed since its
last save. Windows only.

A project without a file: the same Save opens FL's own "Save as" window
(`TNewProjForm`, not a Windows file dialog; seen live in FL 2026.1.6), which puts
the project in "Projects/<name>/<name>.flp" (its "Create new project folder" is on
by default) or "Projects/<name>.flp". `save_new_project` types the name, clicks its
Save and waits for either file. Only one such window may ever be open: stacked
ones, closed out of order, crashed FL (seen live).
"""

from __future__ import annotations

import platform
import time
from pathlib import Path

from fl_studio_mcp.utils import fl_windows

MENU_CLICKS = 3
MENU_CLICK_TIMEOUT = 0.5  # seconds the File menu gets to open after a click
SAVE_DOWNS = 3  # File menu: New, New from template, Open, Save
SAVE_TIMEOUT = 20.0  # seconds for the file to be written
POLL_INTERVAL = 0.1
# FL 2026's trial shows a "Buy FL Studio" window first and saves once it is closed.
TRIAL_SHOP_CLASS = "TInAppShopForm"
FORM_TIMEOUT = 5.0  # seconds for FL's Save as window to open
FORBIDDEN_NAME_CHARACTERS = set('\\/:*?"<>|')
MAX_NAME_LENGTH = 100


class SaveError(Exception):
    """The project could not be saved; the message says why."""


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(POLL_INTERVAL)
    return done()


def _choose_save() -> None:
    """Open FILE in the menu bar and choose Save."""
    for _ in range(MENU_CLICKS):
        if not fl_windows.click_main_menu():
            raise SaveError("FL Studio's File menu did not open")
        if _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_CLICK_TIMEOUT):
            break
    else:
        raise SaveError("FL Studio's File menu did not open")
    for key in [fl_windows.VK_HOME] + [fl_windows.VK_DOWN] * SAVE_DOWNS:
        if not fl_windows.press_in_popup_menu(key, menus=1):
            raise SaveError("FL Studio's File menu closed unexpectedly")
    if fl_windows.pointer_over_menu():
        fl_windows.close_popup_menus()
        raise SaveError(
            "The mouse pointer is over FL Studio's menu, where it moves the highlight, so "
            "nothing was chosen. Try again with the pointer elsewhere."
        )
    if not fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=1):
        raise SaveError("FL Studio's File menu closed unexpectedly")


def _check_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH or FORBIDDEN_NAME_CHARACTERS & set(name):
        raise SaveError(f"A project name is 1-{MAX_NAME_LENGTH} characters without "
                        f"{''.join(sorted(FORBIDDEN_NAME_CHARACTERS))}; got {name!r}")
    if not name.isprintable() or name.endswith("."):
        raise SaveError(f"{name!r} can't be a file name")
    return name


def _ready() -> None:
    """Raise SaveError while anything in FL would take or block the menu walk."""
    if platform.system() != "Windows":
        raise SaveError("Saving through FL Studio's menu works on Windows only")
    message = fl_windows.open_message()
    if message is not None:
        raise SaveError(f"FL Studio is showing a message box ({message!r})")
    if (fl_windows.popup_menu_count() or fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS)
            or fl_windows.window_count(fl_windows.NEW_PROJECT_FORM_CLASS)):
        raise SaveError("a menu or dialog is open in FL Studio")


def save_new_project(projects_dir: Path, name: str) -> Path:
    """Save a project that has no file yet as `name` in FL's Projects folder.

    Returns the file FL wrote. Raises SaveError (FL's window is cancelled then).
    """
    name = _check_name(name)
    targets = (projects_dir / name / f"{name}.flp", projects_dir / f"{name}.flp")
    taken = [t for t in targets if t.exists()]
    if taken:
        raise SaveError(f"{taken[0]} already exists; pick another name")
    _ready()
    _choose_save()
    if not _wait_until(lambda: fl_windows.window_count(fl_windows.NEW_PROJECT_FORM_CLASS) == 1,
                       FORM_TIMEOUT):
        if fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS):
            fl_windows.cancel_dialogs()
        raise SaveError("FL Studio didn't open its Save as window (does the project "
                        "already have a file? Then save it without a name)")
    try:
        if not fl_windows.type_new_project_name(name):
            raise SaveError("The name could not be typed into FL Studio's Save as window")
        if not fl_windows.click_new_project_button("save"):
            raise SaveError("FL Studio's Save as window closed unexpectedly")
        if not _wait_until(lambda: any(t.exists() for t in targets), SAVE_TIMEOUT):
            raise SaveError(f"FL Studio didn't write {targets[0]} within {SAVE_TIMEOUT:g} s")
    except SaveError:
        if fl_windows.window_count(fl_windows.NEW_PROJECT_FORM_CLASS) == 1:
            fl_windows.click_new_project_button("cancel")
        raise
    return next(t for t in targets if t.exists())


def save_project(project_file: Path | None) -> bool:
    """Choose File > Save; returns once `project_file` has been written.

    A project with no file yet makes FL open its Save As dialog, which is left
    for the user and reported as an error. Returns whether the trial's "Buy FL
    Studio" window came (it is closed). Raises SaveError.
    """
    _ready()
    before = project_file.stat().st_mtime if project_file and project_file.exists() else None
    _choose_save()
    shop = False

    def written() -> bool:
        nonlocal shop
        if fl_windows.window_count(TRIAL_SHOP_CLASS):
            fl_windows.close_windows(TRIAL_SHOP_CLASS)
            shop = True
        if fl_windows.window_count(fl_windows.NEW_PROJECT_FORM_CLASS):
            fl_windows.click_new_project_button("cancel")
            raise SaveError("The project has no file yet: give fl_save_project a name")
        return (project_file is not None and project_file.exists()
                and project_file.stat().st_mtime != before)

    if not _wait_until(written, SAVE_TIMEOUT):
        raise SaveError(
            f"File > Save was chosen, but {project_file} was not written within "
            f"{SAVE_TIMEOUT:g} s. FL Studio doesn't rewrite a project that hasn't changed "
            "since its last save (seen live), so it may already be saved"
        )
    return shop
