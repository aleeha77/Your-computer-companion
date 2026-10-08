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
    SLEEPING,
    YAWN,
    BLINK,
    BLINK_MAX_MS,
    CLICK,
    CONFUSED,
    DEFAULT_STATE_ANIMATIONS,
    DOUBLE_CLICK,
    DRAGGING,
    HOVER,
    IDLE,
    LISTENING,
    LOOK_DOWN,
    LOOK_LEFT,
    LOOK_MAX_MS,
    LOOK_RIGHT,
    LOOK_UP,
    REACTION_STATES,
    SUSTAINED_STATES,
    TALKING,
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
    """A manifest without ``state_animation_map`` uses the built-in map.

    Stage 7 adds two states to that map: the ``sleeping`` pose (which falls
    back to an existing animation when the artwork has no sleeping frames, see
    ``SLEEPING_FALLBACK_ANIMATIONS``) and the ``yawn`` reaction, which is
    switched off for artwork that cannot draw it.
    """
    expected = {
        state: anim for state, anim in DEFAULT_STATE_ANIMATIONS.items()
        if state != YAWN
    }
    expected[SLEEPING] = machine.state_animation_map[SLEEPING]
    assert machine.state_animation_map == expected
    assert machine.state_animation_map[SLEEPING] in ("blink", "idle")
    assert YAWN not in machine.state_animation_map
    # …including both sustained states: the built-in map is the fallback the
    # machine starts from, so a state added to ``SUSTAINED_STATES`` but
    # forgotten in ``DEFAULT_STATE_ANIMATIONS`` fails right here.
    for state in SUSTAINED_STATES:
        assert DEFAULT_STATE_ANIMATIONS[state] == state
        assert machine.state_animation_map[state] is not None


def test_the_packaged_artwork_covers_every_builtin_state():
    """The shipped manifest can draw every state the built-in map names.

    Optional states are switched off when the artwork lacks them (see below),
    which is correct for older art — but the artwork Peeko actually ships must
    not be the thing that silently drops ``talking`` or ``listening``. The
    path is built from this module's own location so the test needs no Qt.
    """
    packaged = Path(state_machine.__file__).parent / "assets" / "manifest.json"
    machine = AvatarStateMachine(
        load_manifest(packaged), rng=random.Random(7)
    )
    expected = {
        state: anim for state, anim in DEFAULT_STATE_ANIMATIONS.items()
        if state != YAWN
    }
    expected[SLEEPING] = machine.state_animation_map[SLEEPING]
    assert machine.state_animation_map == expected
    assert machine.state_animation_map[SLEEPING] in ("blink", "idle")
    # The placeholder artwork has no sleeping or yawn frames yet, and says so
    # instead of pretending: sleeping shows an existing animation as its
    # documented fallback, and apply_yawn blinks instead of yawning.
    assert machine.sleeping_pose_available is False
    assert machine.reaction_available(YAWN) is False
    assert machine.talking_available is True
    assert machine.listening_available is True


def test_the_effective_map_is_the_builtin_map_minus_unavailable_states(
    avatar_assets, manifest_data
):
    """An optional animation the artwork lacks switches that state *off*.

    This is the honest rule behind the fallback above: the builtin map always
    names every state, and the machine's effective map is that map minus the
    optional states this manifest cannot draw — never a silent downgrade of a
    mandatory state, and never a pointer at an animation that is not there.
    """
    path = _manifest_without(avatar_assets, manifest_data, "talking", "listening")
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    expected = {
        state: anim for state, anim in DEFAULT_STATE_ANIMATIONS.items()
        if state not in (LISTENING, TALKING, YAWN)
    }
    expected[SLEEPING] = machine.state_animation_map[SLEEPING]
    assert machine.state_animation_map == expected
    # The *builtin* map still carries them — only this artwork does not.
    assert DEFAULT_STATE_ANIMATIONS[LISTENING] == LISTENING
    assert DEFAULT_STATE_ANIMATIONS[TALKING] == TALKING


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


# --------------------------------------------------------------------------- #
# Stage 2: hover reaction
# --------------------------------------------------------------------------- #
def test_stage_two_reactions_are_available_from_the_manifest(machine):
    """The three Stage 2 reactions, for artwork that can draw them.

    Stage 7 added ``yawn`` to ``REACTION_STATES``; this test artwork has no
    yawn frames, so that one reaction is switched off (``apply_yawn`` then
    blinks — the documented fallback), while the Stage 2 three stay playable.
    """
    assert machine.available_reactions == frozenset(
        ({HOVER, DOUBLE_CLICK, CONFUSED})
    )
    for state in (HOVER, DOUBLE_CLICK, CONFUSED):
        assert machine.reaction_available(state)
        assert machine.state_animation_map[state] == state
    assert machine.reaction_available(YAWN) is False


def test_hover_enter_plays_the_hover_reaction(machine):
    assert machine.hover_enter() is True
    assert machine.state == HOVER
    assert machine.current_animation == "hover"
    assert machine.hovering is True


def test_hover_reaction_is_a_one_shot_that_returns_to_idle(machine):
    machine.hover_enter()
    assert machine.state == HOVER
    wait_for_idle(machine)
    assert machine.state == IDLE
    assert machine.frame_index == 0


def test_hover_enter_is_idempotent_until_the_pointer_leaves(machine):
    assert machine.hover_enter() is True
    wait_for_idle(machine)
    # Still hovering: the reaction must not fire over and over.
    assert machine.hover_enter() is False
    assert machine.state == IDLE
    machine.hover_leave()
    assert machine.hover_enter() is True
    assert machine.state == HOVER


def test_hover_enter_is_ignored_while_the_mouse_is_pressed(machine):
    """Hovering must never interfere with a click or a drag."""
    machine.press()
    assert machine.hover_enter() is False
    assert machine.state == IDLE           # a press alone stays silent
    assert machine.hovering is True        # ...but the hover is remembered
    # The click still works exactly as it did before.
    machine.release(moved=False)
    assert machine.state == CLICK


def test_hover_enter_is_ignored_while_dragging(machine):
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING
    assert machine.hover_enter() is False
    assert machine.state == DRAGGING       # the carry wiggle keeps playing
    machine.release(moved=True)
    assert machine.state == IDLE


def test_hover_enter_does_not_interrupt_another_animation(machine):
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK
    assert machine.hover_enter() is False
    assert machine.state == CLICK          # the click reaction finishes


def test_hover_leave_ends_the_reaction_immediately(machine):
    machine.hover_enter()
    assert machine.state == HOVER
    assert machine.hover_leave() is True
    assert machine.state == IDLE
    assert machine.hovering is False
    # Leaving twice (or while idle) is a harmless no-op.
    assert machine.hover_leave() is False


def test_hover_leave_leaves_other_animations_alone(machine):
    machine.double_click()
    assert machine.state == DOUBLE_CLICK
    assert machine.hover_leave() is False
    assert machine.state == DOUBLE_CLICK


def test_hover_does_not_break_the_pointer_glance_cooldown(machine):
    """A hover reaction must not eat the glance cooldown (or vice versa)."""
    machine.hover_enter()
    assert machine.state == HOVER
    # A glance request while the hover reaction plays is ignored and does
    # NOT consume the cooldown: the engine only spends it on a real glance.
    machine.pointer_direction("left")
    assert machine.state == HOVER
    wait_for_idle(machine)
    machine.pointer_direction("left")
    assert machine.state == LOOK_LEFT

    # ...and the cooldown still applies after the glance, hover or not.
    wait_for_idle(machine)
    machine.hover_leave()
    assert machine.hover_enter() is True
    wait_for_idle(machine)
    machine.pointer_direction("right")
    assert machine.state == IDLE


def test_pointer_glances_still_work_after_a_hover_reaction(machine):
    machine.hover_enter()
    wait_for_idle(machine)
    advance(machine, state_machine.POINTER_LOOK_COOLDOWN_MS + 100)
    wait_for_idle(machine)
    machine.pointer_direction("up")
    assert machine.state == LOOK_UP


# --------------------------------------------------------------------------- #
# Stage 2: double-click reaction
# --------------------------------------------------------------------------- #
def test_double_click_plays_its_own_reaction(machine):
    assert machine.double_click() is True
    assert machine.state == DOUBLE_CLICK
    assert machine.current_animation == "double_click"
    assert machine.current_animation != machine.manifest.animations[CLICK].name


def test_double_click_reaction_is_a_one_shot(machine):
    machine.double_click()
    wait_for_idle(machine)
    assert machine.state == IDLE


def test_double_click_replays_from_the_first_frame(machine):
    machine.double_click()
    advance(machine, 150.0)                # past the first 100 ms frame
    assert machine.frame_index == 1
    machine.double_click()                 # click it again, quickly
    assert machine.state == DOUBLE_CLICK
    assert machine.frame_index == 0


def test_double_click_release_is_not_downgraded_to_a_single_click(machine):
    """Qt sends release → double-click → release; the reaction must survive."""
    machine.press()
    machine.release(moved=False)
    assert machine.state == CLICK          # the first release of the pair
    machine.press()
    machine.double_click()
    assert machine.state == DOUBLE_CLICK
    machine.release(moved=False)           # the second release
    assert machine.state == DOUBLE_CLICK
    wait_for_idle(machine)
    assert machine.state == IDLE


def test_double_click_is_ignored_while_dragging(machine):
    machine.press()
    machine.drag_started()
    assert machine.double_click() is False
    assert machine.state == DRAGGING


def test_double_click_can_still_be_dragged_afterwards(machine):
    """A double-click re-arms the press, so dragging it still works."""
    machine.press()
    machine.double_click()
    machine.drag_started()
    assert machine.state == DRAGGING
    machine.release(moved=True)
    assert machine.state == IDLE


# --------------------------------------------------------------------------- #
# Stage 2: "not implemented" reaction
# --------------------------------------------------------------------------- #
def test_confused_reaction_plays_once_then_returns_to_idle(machine):
    assert machine.confused() is True
    assert machine.state == CONFUSED
    assert machine.current_animation == "confused"
    wait_for_idle(machine)
    assert machine.state == IDLE


def test_confused_reaction_replays_on_each_menu_pick(machine):
    machine.confused()
    advance(machine, 150.0)                # past the first 100 ms frame
    assert machine.frame_index == 1
    machine.confused()
    assert machine.frame_index == 0


def test_confused_reaction_does_not_interrupt_a_drag(machine):
    machine.press()
    machine.drag_started()
    assert machine.confused() is False
    assert machine.state == DRAGGING


def test_play_reaction_rejects_an_unknown_state(machine):
    assert machine.play_reaction("nope") is False
    assert machine.state == IDLE


def test_reaction_states_are_not_scheduled_on_their_own(machine):
    """Only idle schedules spontaneous blinks/glances — never reactions."""
    seen = {machine.state}
    for _ in range(4000):                  # 40 s of animation
        machine.tick(STEP_MS)
        seen.add(machine.state)
        assert machine.state not in REACTION_STATES or machine.hovering
    assert HOVER not in seen
    assert DOUBLE_CLICK not in seen
    assert CONFUSED not in seen


def test_scheduled_timers_resume_after_a_reaction(machine):
    assert machine.blink_in_ms is not None
    machine.hover_enter()
    assert machine.blink_in_ms is None      # reactions pause the schedule
    wait_for_idle(machine)
    assert machine.blink_in_ms is not None
    advance_until(machine, lambda: machine.state == BLINK, BLINK_MAX_MS + 1000)


# --------------------------------------------------------------------------- #
# Stage 2: reactions are optional artwork
# --------------------------------------------------------------------------- #
def _manifest_without(avatar_assets, manifest_data, *animation_names):
    """Rewrite the test manifest with ``animation_names`` removed."""
    from conftest import write_avatar_assets

    for name in animation_names:
        manifest_data["animations"].pop(name, None)
    return write_avatar_assets(Path(avatar_assets).parent, manifest_data)


def test_reactions_are_switched_off_when_the_manifest_lacks_them(
    avatar_assets, manifest_data
):
    """Stage 1 artwork keeps working — it just has fewer reactions."""
    path = _manifest_without(
        avatar_assets, manifest_data, "hover", "double_click", "confused"
    )
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.available_reactions == frozenset()
    assert machine.reaction_available(HOVER) is False
    loaded = machine.state_animation_map
    assert HOVER not in loaded and DOUBLE_CLICK not in loaded
    assert CONFUSED not in loaded

    # Every reaction entry point stays silent instead of raising.
    assert machine.hover_enter() is False
    assert machine.double_click() is False
    assert machine.confused() is False
    assert machine.state == IDLE
    assert machine.current_animation == "idle"


def test_a_missing_reaction_only_switches_off_that_reaction(
    avatar_assets, manifest_data
):
    """Reactions are independent: losing one leaves the others working."""
    path = _manifest_without(avatar_assets, manifest_data, "hover")
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.reaction_available(HOVER) is False
    assert HOVER not in machine.state_animation_map
    assert machine.hover_enter() is False
    assert machine.state == IDLE

    assert machine.reaction_available(DOUBLE_CLICK) is True
    assert machine.reaction_available(CONFUSED) is True
    assert machine.double_click() is True
    assert machine.current_animation == "double_click"


def test_a_reaction_state_can_be_remapped_by_the_manifest(
    avatar_assets, manifest_data
):
    from conftest import write_avatar_assets

    manifest_data["state_animation_map"] = {"double_click": "click"}
    path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.reaction_available(DOUBLE_CLICK) is True
    assert machine.state_animation_map[DOUBLE_CLICK] == "click"
    machine.double_click()
    assert machine.current_animation == "click"


def test_a_missing_core_animation_still_fails_loudly(
    avatar_assets, manifest_data
):
    """Optional reactions must not weaken the mandatory-state check."""
    path = _manifest_without(
        avatar_assets, manifest_data, "hover", "dragging"
    )
    with pytest.raises(StartupError, match="dragging"):
        AvatarStateMachine(load_manifest(path))


# --------------------------------------------------------------------------- #
# Stage 4: the sustained listening state (voice input)
# --------------------------------------------------------------------------- #
def test_the_artwork_can_show_the_listening_state(machine):
    assert machine.listening_available is True
    assert machine.listening is False
    assert machine.can_play(LISTENING) is True
    assert machine.state_animation_map[LISTENING] == LISTENING


def test_start_listening_plays_the_state_and_holds_it(machine):
    """A capture lasts as long as the user talks — so must the pose."""
    assert machine.start_listening() is True
    assert machine.state == LISTENING
    assert machine.listening is True
    assert machine.current_animation == "listening"

    # No settling back to idle, and no spontaneous blink/glance either.
    advance(machine, 1_500.0)
    assert machine.state == LISTENING
    assert machine.listening is True


def test_a_one_shot_listening_animation_keeps_playing(machine, avatar_assets,
                                                      manifest_data):
    """The state is sustained by the engine, whatever the artwork says."""
    from conftest import write_avatar_assets

    manifest_data["animations"]["listening"]["loop"] = False
    path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)
    one_shot = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert one_shot.start_listening() is True
    advance(one_shot, 500.0)                # past its two 100 ms frames
    assert one_shot.state == LISTENING
    assert one_shot.listening is True


def test_stop_listening_returns_to_idle(machine):
    machine.start_listening()
    assert machine.stop_listening() is True
    assert machine.state == IDLE
    assert machine.listening is False
    assert machine.stop_listening() is False   # nothing was playing any more
    assert machine.state == IDLE


def test_stop_listening_without_a_capture_is_ignored(machine):
    assert machine.stop_listening() is False
    assert machine.listening is False
    assert machine.state == IDLE


def test_start_listening_is_declined_while_the_robot_is_being_dragged(machine):
    """User input always wins: a drag is never interrupted by the robot."""
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING

    assert machine.start_listening() is False
    assert machine.listening is False
    assert machine.state == DRAGGING


def test_start_listening_is_declined_while_the_mouse_button_is_held(machine):
    machine.press()
    assert machine.start_listening() is False
    assert machine.listening is False
    assert machine.state == IDLE


def test_a_drag_wins_over_the_pose_and_it_resumes_afterwards(machine):
    """The microphone is still open, so the pose comes back on release."""
    machine.start_listening()
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING
    assert machine.listening is True

    machine.release(moved=True)
    advance(machine, STEP_MS * 2)
    assert machine.state == LISTENING
    assert machine.listening is True


def test_a_state_machine_cue_does_not_interrupt_listening(machine):
    """The listening pose is the user's own request — a cue waits its turn."""
    machine.start_listening()
    assert machine.play_cued(DOUBLE_CLICK) is False
    assert machine.state == LISTENING
    assert machine.current_animation == "listening"


def test_a_reaction_still_plays_after_listening_stops(machine):
    machine.start_listening()
    machine.stop_listening()
    assert machine.play_cued(HOVER) is True
    assert machine.state == HOVER


def test_listening_is_switched_off_when_the_manifest_lacks_it(
    avatar_assets, manifest_data
):
    """Stage 1-3 artwork keeps working — it just shows no listening pose."""
    path = _manifest_without(avatar_assets, manifest_data, "listening")
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.listening_available is False
    assert LISTENING not in machine.state_animation_map
    assert machine.can_play(LISTENING) is False

    # Every entry point stays silent instead of raising.
    assert machine.start_listening() is False
    assert machine.stop_listening() is False
    assert machine.listening is False
    assert machine.state == IDLE
    assert machine.current_animation == "idle"


def test_a_sustained_state_is_not_a_reaction(machine):
    """Reactions are one-shot and cued; listening and talking are neither."""
    assert SUSTAINED_STATES == (LISTENING, TALKING, SLEEPING)
    for state in SUSTAINED_STATES:
        assert state not in REACTION_STATES
        assert machine.reaction_available(state) is False
    assert machine.available_reactions == frozenset(
        {HOVER, DOUBLE_CLICK, CONFUSED}
    )


# --------------------------------------------------------------------------- #
# Stage 5: the sustained talking state (voice output)
# --------------------------------------------------------------------------- #
def test_the_artwork_can_show_the_talking_state(machine):
    assert machine.talking_available is True
    assert machine.talking is False
    assert machine.can_play(TALKING) is True
    assert machine.state_animation_map[TALKING] == TALKING


def test_start_talking_plays_the_state_and_holds_it(machine):
    """A sentence lasts as long as it lasts — so must the pose."""
    assert machine.start_talking() is True
    assert machine.state == TALKING
    assert machine.talking is True
    assert machine.current_animation == "talking"

    # No settling back to idle, and no spontaneous blink/glance either.
    advance(machine, 3_000.0)
    assert machine.state == TALKING
    assert machine.talking is True


def test_a_one_shot_talking_animation_keeps_playing(machine, avatar_assets,
                                                    manifest_data):
    """The state is sustained by the engine, whatever the artwork says."""
    from conftest import write_avatar_assets

    manifest_data["animations"]["talking"]["loop"] = False
    path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)
    one_shot = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert one_shot.start_talking() is True
    advance(one_shot, 500.0)                # past its two 100 ms frames
    assert one_shot.state == TALKING
    assert one_shot.talking is True


def test_stop_talking_returns_to_idle(machine):
    machine.start_talking()
    assert machine.stop_talking() is True
    assert machine.state == IDLE
    assert machine.talking is False
    assert machine.stop_talking() is False      # nothing was playing any more
    assert machine.state == IDLE


def test_stop_talking_without_a_playback_is_ignored(machine):
    assert machine.stop_talking() is False
    assert machine.talking is False
    assert machine.state == IDLE


def test_start_talking_is_declined_while_the_robot_is_being_dragged(machine):
    """User input always wins: a drag is never interrupted by the robot."""
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING

    assert machine.start_talking() is False
    assert machine.talking is False
    assert machine.state == DRAGGING


def test_start_talking_is_declined_while_the_mouse_button_is_held(machine):
    machine.press()
    assert machine.start_talking() is False
    assert machine.talking is False
    assert machine.state == IDLE


def test_a_drag_wins_over_the_talking_pose_and_it_resumes_afterwards(machine):
    """Peeko keeps talking while carried, so the pose comes back on release."""
    machine.start_talking()
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING
    assert machine.talking is True

    machine.release(moved=True)
    advance(machine, STEP_MS * 2)
    assert machine.state == TALKING
    assert machine.talking is True


def test_a_state_machine_cue_does_not_interrupt_talking(machine):
    """Peeko's own voice has the floor — a cued expression waits its turn."""
    machine.start_talking()
    assert machine.play_cued(DOUBLE_CLICK) is False
    assert machine.state == TALKING
    assert machine.current_animation == "talking"


def test_a_reaction_still_plays_after_talking_stops(machine):
    machine.start_talking()
    machine.stop_talking()
    assert machine.play_cued(HOVER) is True
    assert machine.state == HOVER


# --------------------------------------------------------------------------- #
# Stage 5: two sustained states, one pose — precedence and exclusivity
# --------------------------------------------------------------------------- #
def test_exactly_one_sustained_state_is_on_screen_at_a_time(machine):
    """Listening and talking can *both* be active; only one pose is drawn."""
    assert machine.start_listening() is True
    assert machine.state == LISTENING

    assert machine.start_talking() is True
    # Talking outranks listening while Peeko is audibly speaking.
    assert machine.state == TALKING
    assert machine.state in SUSTAINED_STATES
    assert machine.talking is True
    assert machine.listening is True        # the capture is still remembered


def test_starting_a_capture_does_not_steal_the_talking_pose(machine):
    machine.start_talking()
    assert machine.start_listening() is True
    assert machine.listening is True
    assert machine.state == TALKING          # …and the pose does not move


def test_the_listening_pose_resumes_when_the_playback_ends(machine):
    machine.start_listening()
    machine.start_talking()
    assert machine.state == TALKING

    assert machine.stop_talking() is True
    assert machine.state == LISTENING        # the microphone is still open
    assert machine.talking is False
    assert machine.listening is True


def test_stopping_the_capture_first_leaves_the_talking_pose_alone(machine):
    """Ending the microphone never cuts Peeko off mid-sentence."""
    machine.start_talking()
    machine.start_listening()
    assert machine.stop_listening() is False   # no listening pose was playing
    assert machine.listening is False
    assert machine.state == TALKING
    assert machine.stop_talking() is True
    assert machine.state == IDLE


def test_a_drag_wins_over_both_sustained_states(machine):
    """Either activity, or both: the user carrying Peeko always wins."""
    machine.start_listening()
    machine.start_talking()
    machine.press()
    machine.drag_started()
    assert machine.state == DRAGGING

    machine.release(moved=True)
    advance(machine, STEP_MS * 2)
    assert machine.state == TALKING            # talking still outranks listening


def test_talking_is_switched_off_when_the_manifest_lacks_it(
    avatar_assets, manifest_data
):
    """Stage 1-4 artwork keeps working — it just shows no talking pose."""
    path = _manifest_without(avatar_assets, manifest_data, "talking")
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.talking_available is False
    assert TALKING not in machine.state_animation_map
    assert machine.can_play(TALKING) is False

    # Every entry point stays silent instead of raising.
    assert machine.start_talking() is False
    assert machine.stop_talking() is False
    assert machine.talking is False
    assert machine.state == IDLE
    assert machine.current_animation == "idle"

    # …and the other sustained state is unaffected: the two are independent.
    assert machine.listening_available is True
    assert machine.start_listening() is True
    assert machine.state == LISTENING


def test_the_talking_state_can_be_remapped_by_the_manifest(
    avatar_assets, manifest_data
):
    from conftest import write_avatar_assets

    manifest_data["state_animation_map"] = {"talking": "hover"}
    path = write_avatar_assets(Path(avatar_assets).parent, manifest_data)
    machine = AvatarStateMachine(load_manifest(path), rng=random.Random(7))

    assert machine.talking_available is True
    assert machine.state_animation_map[TALKING] == "hover"
    assert machine.start_talking() is True
    assert machine.current_animation == "hover"
