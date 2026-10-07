"""The interaction menu and its dialogs (offscreen Qt).

Stage 3 turned the menu's *Talk* entry on: it now opens the real chat window
(see :mod:`tests.test_ui_chat_window`). Stage 6 did the same for *Pet* and
*Play* — they apply the emotion engine's documented interaction effects — so
this file covers both the entries that really work and the ones that are
still planned, and that the two are clearly distinguished.

Two things are checked here:

* the menu is **complete and honest** — every entry the owner asked for is
  present in the right place, planned entries say so in their own label,
  and the working entries really work;
* the dialogs behind those entries show **live, truthful content** —
  Check Status reads the running version/stage/avatar state, Settings shows
  the configuration Peeko is actually using (and never a secret), and the
  "not implemented yet" box names the roadmap stage that will implement it.

The text builders are plain functions, so most of this runs without Qt at
all; the dialog tests use the shared offscreen ``qapp`` fixture.
"""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

from peeko import __stage__, __total_stages__, __version__
from peeko.avatar.manifest import load_manifest
from peeko.avatar.state_machine import AvatarStateMachine
from peeko.avatar.widget import DEFAULT_ASSETS_DIR
from peeko.settings import Settings
from peeko.ui.context_menu import (
    MENU_SPEC,
    PET_ID,
    PLAY_ID,
    QUIT_ID,
    SETTINGS_ID,
    STATUS_ID,
    TALK_ID,
    MenuEntry,
    build_avatar_context_menu,
    entries,
    find_entry,
    future_entries,
    header_text,
)
from peeko.ui.dialogs import (
    SETTINGS_NOTE,
    SettingsDialog,
    build_not_implemented_dialog,
    build_not_implemented_text,
    build_settings_dialog,
    build_status_dialog,
    build_status_text,
    settings_rows,
    show_not_implemented_dialog,
    show_settings_dialog,
    show_status_dialog,
)

PACKAGED_MANIFEST = DEFAULT_ASSETS_DIR / "manifest.json"

#: The pet actions that are *still* only planned, with the stage that will
#: implement each. Order matters: it is the documented menu order. Pet and
#: Play used to be listed here; Stage 6 turned them into real interactions
#: (they drive the live emotion engine), so only the Stage 7 needs actions
#: remain.
EXPECTED_FUTURE_ENTRIES = (
    ("feed", "Feed", 7),
    ("sleep", "Sleep", 7),
    ("wake", "Wake Up", 7),
)

#: The pet actions that became real in Stage 6, in menu order.
EXPECTED_EMOTION_ACTIONS = (
    ("pet", "Pet"),
    ("play", "Play"),
)


@pytest.fixture()
def settings(tmp_path):
    """Real settings pointed at a throw-away data directory."""
    return Settings(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture()
def machine():
    """A real state machine on the artwork that ships with Peeko."""
    return AvatarStateMachine(load_manifest(PACKAGED_MANIFEST),
                              rng=random.Random(4))


# --------------------------------------------------------------------------- #
# Menu structure — every entry present, planned ones flagged
# --------------------------------------------------------------------------- #
def test_menu_lists_the_remaining_planned_pet_actions_with_their_stage():
    future = future_entries()
    assert [(e.id, e.label, e.stage) for e in future] == list(
        EXPECTED_FUTURE_ENTRIES
    )
    for entry in future:
        assert entry.implemented is False


def test_pet_and_play_are_real_interaction_entries_since_stage_6():
    for action_id, label in EXPECTED_EMOTION_ACTIONS:
        entry = find_entry(action_id)
        assert entry is not None
        assert entry.label == label
        assert entry.implemented is True
        assert entry.stage is None
        assert entry.display_label == label  # no "(not implemented)" suffix
        assert "not implemented" not in entry.description.lower()


def test_menu_planned_entries_are_labelled_as_not_implemented():
    for entry in future_entries():
        label = entry.display_label
        assert entry.label in label
        assert f"Stage {entry.stage}" in label
        assert "not implemented" in label.lower()


def test_menu_has_the_working_entries_the_owner_asked_for():
    working = {e.id: e for e in entries() if e.implemented}
    # Talk joined the working entries in Stage 3 (it opens the chat window);
    # Pet and Play joined in Stage 6 (they drive the emotion engine).
    assert set(working) == {
        TALK_ID, PET_ID, PLAY_ID, STATUS_ID, SETTINGS_ID, QUIT_ID,
    }
    assert working[TALK_ID].display_label == "Talk"
    assert working[PET_ID].display_label == "Pet"
    assert working[PLAY_ID].display_label == "Play"
    assert working[STATUS_ID].display_label == "Check Status…"
    assert working[SETTINGS_ID].display_label == "Settings…"
    assert working[QUIT_ID].display_label == "Quit"


def test_menu_spec_keeps_quit_last_after_a_separator():
    assert MENU_SPEC[-1] is not None and MENU_SPEC[-1].id == QUIT_ID
    assert MENU_SPEC[-2] is None


def test_menu_ids_are_unique():
    ids = [entry.id for entry in entries()]
    assert len(ids) == len(set(ids))


def test_find_entry_resolves_ids_and_rejects_junk():
    assert find_entry(QUIT_ID).label == "Quit"
    assert find_entry("nope") is None
    assert find_entry(None) is None
    assert find_entry("header") is None


def test_planned_entry_descriptions_name_their_stage():
    feed = find_entry("feed")
    assert "not implemented" in feed.description.lower()
    assert f"Stage {feed.stage} of {__total_stages__}" in feed.description

    # Talk is implemented as of Stage 3, so it makes no such claim.
    talk = find_entry(TALK_ID)
    assert talk.implemented is True
    assert "not implemented" not in talk.description.lower()


def test_working_entry_descriptions_make_no_false_promises():
    status = find_entry(STATUS_ID)
    assert "not implemented" not in status.description.lower()


def test_built_menu_contains_every_entry_and_separator(qapp):
    menu = build_avatar_context_menu(None)
    try:
        labels = [action.text() for action in menu.actions()]
        # Header line carries the version and the current stage.
        assert header_text() in labels
        assert f"v{__version__}" in labels[0]
        assert f"Stage {__stage__} of {__total_stages__}" in labels[0]
        assert menu.actions()[0].isEnabled() is False
        assert menu.actions()[0].data() == "header"
        assert menu.actions()[1].isSeparator()

        by_id = {action.data(): action for action in menu.actions()}
        for entry in entries():
            action = by_id[entry.id]
            assert action.text() == entry.display_label
            assert action.isEnabled() is True, (
                f"{entry.id} must be clickable — it either works or explains "
                f"why it does not"
            )
        assert sum(a.isSeparator() for a in menu.actions()) == 3
        assert by_id[QUIT_ID].shortcut().toString() == "Ctrl+Q"
    finally:
        menu.deleteLater()
        qapp.processEvents()


def test_built_menu_tooltips_flag_the_planned_entries(qapp):
    menu = build_avatar_context_menu(None)
    try:
        by_id = {action.data(): action for action in menu.actions()}
        # The Stage 7 needs actions are still flagged…
        for entry in future_entries():
            assert "Not implemented yet" in by_id[entry.id].toolTip()
        # …while the Stage 6 emotion interactions are real, so they carry no
        # "coming soon" tooltip (Qt reports the action text when none is set).
        for action_id, _ in EXPECTED_EMOTION_ACTIONS:
            assert "Not implemented" not in by_id[action_id].toolTip()
        # Working entries carry no "coming soon" tooltip either.
        assert "Not implemented" not in by_id[STATUS_ID].toolTip()
    finally:
        menu.deleteLater()
        qapp.processEvents()


def test_future_entries_are_listed_by_the_status_readout(settings):
    text = build_status_text(settings)
    for entry in future_entries():
        assert f"{entry.label} (Stage {entry.stage})" in text


# --------------------------------------------------------------------------- #
# Check Status — honest, live content
# --------------------------------------------------------------------------- #
def test_status_text_reports_version_stage_and_live_avatar_state(
    settings, machine
):
    manifest = machine.manifest
    machine.hover_enter()
    text = build_status_text(settings, machine, manifest)

    assert f"Peeko v{__version__}" in text
    assert f"Stage {__stage__} of {__total_stages__}" in text
    assert f"State now: {machine.state}" in text
    assert f'"{machine.current_animation}"' in text
    assert f"Animations in the manifest: {len(manifest.animations)}" in text
    assert "160 x 180" in text
    assert "hover, double_click, confused" in text or "confused" in text


def test_status_text_lists_what_works_and_what_does_not(settings, machine):
    text = build_status_text(settings, machine, machine.manifest)
    assert "WORKS TODAY" in text
    assert "NOT IMPLEMENTED YET" in text
    assert "Quit" in text
    # The status readout is honest about both the new chat and the rest.
    assert "AI CHAT (Stage 3)" in text
    assert "Talk" in text and "not" in text.lower()


def test_status_text_degrades_honestly_without_a_machine(settings):
    text = build_status_text(settings)
    assert "unavailable" in text.lower()
    assert "State now: (unavailable" in text


def test_status_text_names_the_artwork_source(settings, machine):
    packaged = build_status_text(settings, machine, machine.manifest)
    assert "packaged artwork" in packaged

    custom = replace(settings, avatar_assets_dir=Path("/tmp/my-robot-art"))
    assert "/tmp/my-robot-art" in build_status_text(custom)


def test_status_dialog_shows_the_same_readout(qapp, settings, machine):
    box = build_status_dialog(None, settings, machine, machine.manifest)
    try:
        assert box.windowTitle() == "Peeko — Status"
        assert box.textFormat() == Qt.PlainText
        assert box.informativeText() == build_status_text(
            settings, machine, machine.manifest
        )
        assert box.icon() == QMessageBox.Information
    finally:
        box.deleteLater()
        qapp.processEvents()


def test_showing_the_status_dialog_does_not_block(qapp, settings, machine):
    box = show_status_dialog(None, settings, machine, machine.manifest)
    try:
        assert box.isModal() is False
        assert box.isVisible() is True
    finally:
        box.close()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# Settings — real configuration, read-only for now
# --------------------------------------------------------------------------- #
def test_settings_rows_show_the_running_configuration(settings):
    rows = dict(settings_rows(settings))
    assert rows["Application"] == "Peeko"
    assert rows["Version"] == __version__
    assert rows["Development stage"] == f"Stage {__stage__} of {__total_stages__}"
    assert rows["Log level"] == settings.log_level
    assert rows["Data directory"] == str(settings.data_dir)
    assert rows["Config directory"] == str(settings.config_dir)
    assert rows["Log directory"] == str(settings.log_dir)
    assert rows["Artwork folder"] == (
        "packaged artwork inside the Peeko package"
    )


def test_settings_rows_show_a_configured_artwork_folder(tmp_path):
    custom = Settings(avatar_assets_dir=tmp_path / "art")
    assert dict(settings_rows(custom))["Artwork folder"] == str(tmp_path / "art")


def test_settings_dialog_is_a_read_only_configuration_view(qapp, settings):
    dialog = build_settings_dialog(None, settings)
    try:
        assert isinstance(dialog, QDialog)
        assert "read-only" in dialog.windowTitle().lower()
        assert dialog.isModal() is False
        text = dialog.settings_text()
        assert str(settings.data_dir) in text
        assert "Artwork folder" in text
        # It says plainly that editing is not implemented yet.
        labels = [w.text() for w in dialog.findChildren(QLabel)]
        assert any(SETTINGS_NOTE in label for label in labels)
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_showing_the_settings_dialog_does_not_block(qapp, settings):
    """Settings opens as a normal window — never a nested modal loop."""
    dialog = show_settings_dialog(None, settings)
    try:
        assert dialog.isModal() is False
        assert dialog.isVisible() is True
        # The readout is live, so it matches the settings it was given.
        assert str(settings.log_dir) in dialog.settings_text()
    finally:
        dialog.close()
        qapp.processEvents()


def test_settings_window_is_owned_by_the_avatar_and_closes_with_it(
    qapp, settings
):
    """The avatar owns the window (parent + reference) — nothing leaks."""
    from peeko.avatar.widget import AvatarWindow

    window = AvatarWindow(settings)
    try:
        assert window._settings_dialog is None
        action = next(
            a for a in window._menu.actions() if a.data() == SETTINGS_ID
        )
        window._on_menu_triggered(action)
        dialog = window._settings_dialog
        assert dialog is not None
        assert dialog.parent() is window
        assert dialog.isModal() is False
        assert dialog.isVisible() is True
    finally:
        try:
            window.hide()
            window.deleteLater()
        except RuntimeError:  # pragma: no cover - already deleted by Qt
            pass
        qapp.processEvents()


def test_settings_dialog_never_reveals_a_secret(qapp, tmp_path):
    secret = "sk-do-not-print-me"
    settings = Settings(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        log_dir=tmp_path / "logs",
        ai_api_key=secret,
    )
    dialog = build_settings_dialog(None, settings)
    try:
        assert secret not in dialog.settings_text()
        rendered = "\n".join(
            w.text() for w in dialog.findChildren(QLabel)
        )
        assert secret not in rendered
        assert "ai_api_key" not in rendered
    finally:
        dialog.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------------------- #
# "Not implemented yet" explanations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", future_entries(), ids=lambda e: e.id)
def test_not_implemented_text_is_honest_about_every_planned_entry(entry):
    text = build_not_implemented_text(entry)
    assert "not implemented" in text.lower()
    assert f"Stage {entry.stage}" in text
    assert "deliberately not faked" in text
    # It always points at what *does* work, so the user is never stuck.
    assert "Check Status" in text and "Settings" in text and "Quit" in text


def test_not_implemented_dialog_matches_the_entry(qapp):
    entry = find_entry("feed")
    box = build_not_implemented_dialog(None, entry)
    try:
        assert box.windowTitle() == "Peeko — Feed"
        assert "Feed" in box.text()
        assert box.informativeText() == build_not_implemented_text(entry)
    finally:
        box.deleteLater()
        qapp.processEvents()


def test_showing_the_not_implemented_dialog_does_not_block(qapp):
    box = show_not_implemented_dialog(None, find_entry("feed"))
    try:
        assert box.isModal() is False
        assert box.isVisible() is True
    finally:
        box.close()
        qapp.processEvents()


def test_menu_entry_defaults_are_only_what_is_given():
    entry = MenuEntry("x", "X")
    assert entry.implemented is True
    assert entry.display_label == "X"
    assert entry.description == "X"
