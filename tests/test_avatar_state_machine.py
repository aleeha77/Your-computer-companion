"""Avatar animation state machine tests — pure Python, no Qt, no waiting.

The engine is deliberately Qt-free, so its behaviour (blink/glance
scheduling, click vs. drag, one-shots returning to idle, delta clamping)
is tested by feeding it *synthetic* time: no event loop, no real timers,
no ``sleep`` anywhere. The manifest used here has 100 ms frames (see
``conftest.minimal_manifest``), so a few hundred ticks cover the whole
spontaneous schedule.
"""

from __future__ import annotations

import random
import subprocess
import sys
from pathlib import Path

import pytest

from peeko.avatar import state_machine
from peeko.avatar.manifest import load_manifest
from peeko.avatar.state_machine import (
    BLINK,
    BLINK_MAX_MS,
    CLICK,
    DEFAULT_STATE_ANIMATIONS,
    DRAGGING,
    IDLE,
    LOOK_DOWN,
    LOOK_LEFT,
    LOOK_MAX_MS,
    LOOK_RIGHT,
    LOOK_UP,
    AvatarStateMachine,
)
from peeko.errors import StartupError

#: Every one-shot glance state.
GLANCE_STATES = (LOOK_LEFT, LOOK_RIGHT, LOOK_UP, LOOK_DOWN)

#: Simulated milliseconds per tick. Kept <= MAX_TICK_MS so the whole delta
#: reaches the machine (it clamps anything larger).
STEP_MS = 10.0


def advance(machine: AvatarStateMachine, ms: float, step: float = STEP_MS) -> float:
    """Feed ``ms`` of simulated time to the machine, in small steps."""
    spent = 0.0
    while spent < ms:
        machine.tick(step)
        spent += step
    return spent


def advance_until(machine: AvatarStateMachine, predicate, budget_ms: float,
                  step: float = STEP_MS) -> float:
    """Tick until ``predicate()`` is true; fail loudly if it never is."""
    spent = 0.0
    while spent < budget_ms:
        if predicate():
            return spent
        machine.tick(step)
        spent += step
    raise AssertionError(
        f"condition not reached within {budget_ms} ms "
        f"(state={machine.state!r}, frame={machine.frame_index})"
    )


def wait_for_idle(machine: AvatarStateMachine, budget_ms: float = 5000.0) -> float:
    """Advance until the machine is back in ``idle``."""
    return advance_until(machine, lambda: machine.state == IDLE, budget_ms)


@pytest.fixture()
def manifest(avatar_assets):
    """The throw-away test manifest (eight states, 100 ms frames)."""
    return load_manifest(avatar_assets)


@pytest.fixture()
def machine(manifest):
    """A state machine on the test manifest with a *seeded* RNG.

    The random blink/glance schedule is what makes behaviour feel alive;
    seeding it keeps these tests deterministic.
    """
    return AvatarStateMachine(manifest, rng=random.Random(20260917))


# --------------------------------------------------------------------------- #
# Startup / introspection
# --------------------------------------------------------------------------- #
def test_starts_idle_on_the_first_idle_frame(machine):
    assert machine.state == IDLE
    assert machine.current_animation == "idle"
    assert machine.frame_index == 0
    assert machine.frame is machine.manifest.animations["idle"].frames[0]


def test_state_animation_map_falls_back_to_builtin_defaults(machine):
    """A manifest without ``state_animation_map`` uses the built-in map."""
    assert machine.state_animation_map == DEFAULT_STATE_ANIMATIONS


def test_state_animation_map_can_be_overridden_by_the_manifest(
    avatar_assets, manifest_data
):
    manifest_data["animations"]["blink_slow"] = {
        "loop": False,
        "frames": [{"duration_ms": 100}],
    }
    manifest_data["state_animation_map"] = {"blink": "blink_slow"}
    # The extra animation is declared after the files are written, so
    # re-write the manifest (the SVG layers are unchanged).
    from conftest import write_avatar_assets

    manifest_path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)

    machine = AvatarStateMachine(
        load_manifest(manifest_path), rng=random.Random(1)
    )
    assert machine.state_animation_map["blink"] == "blink_slow"
    advance_until(machine, lambda: machine.state == BLINK, BLINK_MAX_MS + 100)
    assert machine.current_animation == "blink_slow"


def test_missing_animation_fails_loudly(avatar_assets, manifest_data):
    del manifest_data["animations"]["click"]
    from conftest import write_avatar_assets

    manifest_path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)

    with pytest.raises(StartupError, match="click"):
        AvatarStateMachine(load_manifest(manifest_path))


def test_engine_source_is_qt_free():
    """The engine must stay usable (and testable) without a GUI toolkit.

    The Qt widget drives it, but never the other way round — that is what
    keeps the animation logic unit-testable and portable.
    """
    source = Path(state_machine.__file__).read_text(encoding="utf-8")
    assert "PySide6" not in source
    assert "import PySide" not in source


def test_module_is_usable_without_a_display(avatar_assets):
    """The engine runs headless — no Qt platform or display needed."""
    script = (
        "from peeko.avatar.manifest import load_manifest\n"
        "from peeko.avatar.state_machine import AvatarStateMachine\n"
        "machine = AvatarStateMachine(load_manifest(%r))\n"
        "for _ in range(10):\n"
        "    machine.tick(33)\n"
        "print(machine.state)\n"
    ) % str(avatar_assets)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == IDLE


# --------------------------------------------------------------------------- #
# Idle behaviour and spontaneous timers
# --------------------------------------------------------------------------- #
def test_idle_bob_cycles_frames_and_loops(machine):
    """The idle animation advances through its frames and loops back."""
    seen = set()
    spent = 0.0
    while spent < 400:  # two loop lengths; a blink cannot fire this early
        assert machine.state == IDLE
        seen.add(machine.frame_index)
        machine.tick(STEP_MS)
        spent += STEP_MS
    assert seen == {0, 1}
    assert machine.state == IDLE


def test_blink_fires_within_its_scheduled_window(machine):
    elapsed = advance_until(
        machine, lambda: machine.state == BLINK, BLINK_MAX_MS + 1000
    )
    assert BLINK_MAX_MS >= elapsed > 0


def test_blink_returns_to_idle_when_finished(machine):
    advance_until(machine, lambda: machine.state == BLINK, BLINK_MAX_MS + 1000)
    wait_for_idle(machine)
    assert machine.state == IDLE
    assert machine.current_animation == "idle"
    assert machine.frame_index == 0  # one-shots restart from the top


def test_glance_fires_on_its_own_timer(machine):
    """Occasionally the avatar looks somewhere on its own (not at a click)."""
    advance_until(
        machine,
        lambda: machine.state in GLANCE_STATES,
        budget_ms=max(LOOK_MAX_MS * 3, 30_000),
    )
    assert machine.current_animation == machine.state


def test_one_shot_glance_returns_to_idle(machine):
    advance_until(machine, lambda: machine.state in GLANCE_STATES, 60_000)
    wait_for_idle(machine)
    assert machine.state == IDLE


def test_timers_are_reported_only_while_idle(machine):
    assert machine.blink_in_ms is not None
    assert machine.look_in_ms is not None
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK
    assert machine.blink_in_ms is None
    assert machine.look_in_ms is None


# --------------------------------------------------------------------------- #
# Click vs. drag semantics
# --------------------------------------------------------------------------- #
def test_press_alone_does_not_change_the_animation(machine):
    machine.press()
    assert machine.state == IDLE  # armed, but visually unchanged


def test_press_then_release_without_movement_is_a_click(machine):
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK


def test_click_plays_once_then_returns_to_idle(machine):
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK
    wait_for_idle(machine)
    assert machine.state == IDLE
    assert machine.frame_index == 0


def test_drag_starts_only_after_a_press(machine):
    machine.drag_started()
    assert machine.state == IDLE


def test_drag_then_release_returns_to_idle(machine):
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING
    machine.drag_started()  # repeated move events are idempotent
    assert machine.state == DRAGGING
    machine.release(moved=True)
    assert machine.state == IDLE


def test_dragging_is_looping_not_a_click(machine):
    """Releasing after a drag must never look like a click."""
    machine.press()
    machine.drag_started()
    dragging = machine.manifest.animations["dragging"]
    machine.tick(STEP_MS)
    assert machine.state == DRAGGING
    assert machine.frame is dragging.frames[machine.frame_index]
    # It loops: after the last frame it wraps around instead of ending.
    advance(machine, 200.0)
    assert machine.state == DRAGGING
    machine.release(moved=False)  # moved=False, but a drag started
    assert machine.state == IDLE


def test_release_without_press_is_ignored(machine):
    machine.release(moved=False)
    assert machine.state == IDLE


def test_release_after_movement_without_drag_start_is_not_a_click(machine):
    machine.press()
    machine.release(moved=True)
    assert machine.state == IDLE


# --------------------------------------------------------------------------- #
# Pointer glances (cursor awareness "taste")
# --------------------------------------------------------------------------- #
def test_pointer_direction_glances_toward_the_cursor(machine):
    machine.pointer_direction("left")
    assert machine.state == LOOK_LEFT


def test_pointer_direction_rejects_unknown_values(machine):
    machine.pointer_direction("sideways")
    assert machine.state == IDLE


def test_pointer_direction_is_ignored_while_busy(machine):
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK
    machine.pointer_direction("left")
    assert machine.state == CLICK  # a click reaction outranks a glance


def test_pointer_glance_honours_its_cooldown(machine):
    machine.pointer_direction("left")
    assert machine.state == LOOK_LEFT
    wait_for_idle(machine)

    # Immediately afterwards the cooldown blocks further glances: without
    # it a cursor hovering next to the avatar would make it jitter.
    machine.pointer_direction("right")
    assert machine.state == IDLE

    # After the cooldown window has passed, glancing works again.
    spent = 0.0
    while spent < state_machine.POINTER_LOOK_COOLDOWN_MS + 30_000:
        machine.tick(100.0)
        spent += 100.0
        if machine.state == IDLE and spent >= state_machine.POINTER_LOOK_COOLDOWN_MS:
            break
    assert machine.state == IDLE
    machine.pointer_direction("down")
    assert machine.state == LOOK_DOWN


# --------------------------------------------------------------------------- #
# Delta handling (never blocks, never jumps)
# --------------------------------------------------------------------------- #
def test_huge_delta_is_clamped_to_one_max_tick(machine):
    """A stalled/suspended app must not fast-forward the animation."""
    machine.tick(30_000.0)
    assert machine.frame_index == 1  # exactly one 100 ms frame elapsed
    assert machine.state == IDLE


def test_negative_delta_is_treated_as_zero(machine):
    machine.tick(-500.0)
    assert machine.state == IDLE
    assert machine.frame_index == 0


def test_ticks_never_block(machine):
    """The engine consumes time; it never waits for it."""
    import time

    start = time.monotonic()
    advance(machine, 60_000.0, step=100.0)  # a minute of animation
    assert time.monotonic() - start < 2.0
