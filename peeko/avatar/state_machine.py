"""Avatar animation state machine — pure Python, no Qt.

Drives the avatar's visible behaviour as a small set of states driven by a
timer tick and input events, so later stages (emotions, talking, sleeping,
needs) can plug in by *adding states and animations* — the renderer stays
ignorant of what the states mean.

States (name == the animation played while in that state, unless
``state_animation_map`` in the manifest overrides it):

* ``idle``        — looping gentle bob; the resting state.
* ``blink``       — one-shot eye blink on a natural, irregular timer.
* ``look_left/right/up/down`` — one-shot glance; chosen on an irregular
  timer, or on demand when the mouse cursor drifts near the avatar.
* ``click``       — one-shot happy squash-and-bounce when the avatar is
  clicked.
* ``dragging``    — looping "being carried" wiggle while the user drags.

Transitions (all timers are decremented by :meth:`tick`, so nothing ever
blocks; there are no sleeps anywhere):

* ``idle`` -> ``blink`` / ``look_*``  (randomised timers)
* ``idle`` -> ``look_*``              (pointer glance, cooldown-limited)
* ``press()``                         (arms the interaction; no visual change)
* ``drag_started()``                  (any state -> ``dragging``)
* ``release(moved=True)``             (``dragging`` -> ``idle``)
* ``release(moved=False)``            (``idle``/``blink``/``look_*`` -> ``click``)
* any one-shot animation finishing    (-> ``idle``)

The machine is Qt-free so it is trivially unit-testable; the Qt widget
simply calls :meth:`tick` from a ``QTimer`` and repaints ``frame``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from peeko.avatar.manifest import Animation, Frame, Manifest
from peeko.errors import StartupError

# --------------------------------------------------------------------------- #
# State names
# --------------------------------------------------------------------------- #
IDLE = "idle"
BLINK = "blink"
LOOK_LEFT = "look_left"
LOOK_RIGHT = "look_right"
LOOK_UP = "look_up"
LOOK_DOWN = "look_down"
CLICK = "click"
DRAGGING = "dragging"

LOOK_DIRECTIONS = (LOOK_LEFT, LOOK_RIGHT, LOOK_UP, LOOK_DOWN)

#: Animation-state names the engine always needs; the manifest (or the
#: built-in mapping below) must provide an animation for each of these.
REQUIRED_STATES = frozenset(
    {IDLE, BLINK, *LOOK_DIRECTIONS, CLICK, DRAGGING}
)

#: Built-in state -> animation mapping (a manifest ``state_animation_map``
#: may override any entry).
DEFAULT_STATE_ANIMATIONS: dict[str, str] = {
    IDLE: IDLE,
    BLINK: BLINK,
    LOOK_LEFT: LOOK_LEFT,
    LOOK_RIGHT: LOOK_RIGHT,
    LOOK_UP: LOOK_UP,
    LOOK_DOWN: LOOK_DOWN,
    CLICK: CLICK,
    DRAGGING: DRAGGING,
}

# --------------------------------------------------------------------------- #
# Timing constants (ms)
# --------------------------------------------------------------------------- #
BLINK_MIN_MS = 2000          # natural-ish, irregular blink schedule
BLINK_MAX_MS = 6000
LOOK_MIN_MS = 5000           # occasional glance-around schedule
LOOK_MAX_MS = 12000
POINTER_LOOK_COOLDOWN_MS = 2500   # don't look at the cursor on every frame
MAX_TICK_MS = 100.0          # clamp a single tick; avoids jumps after stalls

#: Pointer directions accepted by :meth:`pointer_direction`.
POINTER_DIRECTIONS = ("left", "right", "up", "down")


@dataclass
class AvatarStateMachine:
    """Timer-driven animation state machine for the avatar.

    :param manifest: parsed :class:`peeko.avatar.manifest.Manifest`.
    :param rng: ``random.Random`` instance (inject a seeded one in tests).
    """

    manifest: Manifest
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        self._state_map = dict(DEFAULT_STATE_ANIMATIONS)
        if self.manifest.state_animation_map:
            self._state_map.update(self.manifest.state_animation_map)

        missing_states = sorted(REQUIRED_STATES - set(self._state_map))
        bad_anims = sorted(
            name for name in self._state_map.values()
            if name not in self.manifest.animations
        )
        if missing_states or bad_anims:
            parts = []
            if missing_states:
                parts.append(
                    "missing state mapping: " + ", ".join(missing_states)
                )
            if bad_anims:
                parts.append(
                    "state maps to unknown animation(s): " + ", ".join(bad_anims)
                )
            raise StartupError(
                "Avatar state machine cannot start — " + "; ".join(parts)
                + ". Add the missing animations to the asset manifest."
            )

        self._animations: dict[str, Animation] = {
            state: self.manifest.animations[name]
            for state, name in self._state_map.items()
        }

        # -- runtime state -------------------------------------------------- #
        self._state: str = IDLE
        self._frame_index = 0
        self._frame_time_ms = 0.0
        self._elapsed_ms = 0.0
        self._blink_in_ms = self.rng.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
        self._look_in_ms = self.rng.uniform(LOOK_MIN_MS, LOOK_MAX_MS)
        self._press_active = False
        self._last_pointer_ms = -POINTER_LOOK_COOLDOWN_MS

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> str:
        """Current state name (e.g. ``idle``, ``blink``, ``click``)."""
        return self._state

    @property
    def current_animation(self) -> str:
        """Name of the animation being played for the current state."""
        return self._state_map[self._state]

    @property
    def frame(self) -> Frame:
        """The frame to render right now."""
        anim = self._animations[self._state]
        return anim.frames[self._frame_index]

    @property
    def frame_index(self) -> int:
        """Index of the current frame within the state's animation."""
        return self._frame_index

    @property
    def blink_in_ms(self) -> Optional[float]:
        """Ms until the next blink fires; ``None`` when not idle."""
        return None if self._state != IDLE else max(0.0, self._blink_in_ms)

    @property
    def look_in_ms(self) -> Optional[float]:
        """Ms until the next glance fires; ``None`` when not idle."""
        return None if self._state != IDLE else max(0.0, self._look_in_ms)

    @property
    def state_animation_map(self) -> dict[str, str]:
        """Effective state -> animation mapping (defaults + manifest)."""
        return dict(self._state_map)

    # ------------------------------------------------------------------ #
    # Timer tick
    # ------------------------------------------------------------------ #
    def tick(self, dt_ms: float) -> None:
        """Advance the animation by ``dt_ms`` milliseconds.

        Call this from the UI thread's ``QTimer`` (or the test harness).
        Never blocks; never sleeps.
        """
        dt = max(0.0, min(float(dt_ms), MAX_TICK_MS))
        self._elapsed_ms += dt

        anim = self._animations[self._state]
        self._frame_time_ms += dt
        while self._frame_time_ms >= anim.frames[self._frame_index].duration_ms:
            self._frame_time_ms -= anim.frames[self._frame_index].duration_ms
            self._frame_index += 1
            if self._frame_index >= len(anim.frames):
                if anim.loop:
                    self._frame_index = 0
                else:
                    # One-shot finished: settle back to idle.
                    self._set_state(IDLE)
                    return

        # Only the idle state schedules spontaneous actions.
        if self._state == IDLE:
            self._blink_in_ms -= dt
            self._look_in_ms -= dt
            if self._blink_in_ms <= 0:
                self._blink_in_ms = self.rng.uniform(BLINK_MIN_MS, BLINK_MAX_MS)
                self._set_state(BLINK)
            elif self._look_in_ms <= 0:
                self._look_in_ms = self.rng.uniform(LOOK_MIN_MS, LOOK_MAX_MS)
                self._set_state(self.rng.choice(LOOK_DIRECTIONS))

    # ------------------------------------------------------------------ #
    # Input events
    # ------------------------------------------------------------------ #
    def press(self) -> None:
        """Left button pressed over the avatar (arms click/drag handling)."""
        self._press_active = True

    def drag_started(self) -> None:
        """The user actually started dragging (movement threshold passed)."""
        if not self._press_active or self._state == DRAGGING:
            return
        self._set_state(DRAGGING)

    def release(self, moved: bool) -> None:
        """Left button released.

        :param moved: ``True`` if the pointer moved beyond the drag
            threshold since :meth:`press` (a drag), ``False`` for a click.
        """
        if not self._press_active:
            return
        self._press_active = False
        if self._state == DRAGGING:
            self._set_state(IDLE)
        elif not moved:
            self._set_state(CLICK)
        else:  # pragma: no cover - defensive; drag_started always precedes
            self._set_state(IDLE)

    def pointer_direction(self, direction: str) -> None:
        """The mouse cursor sits near the avatar in ``direction``.

        :param direction: one of ``"left"``, ``"right"``, ``"up"``,
            ``"down"``. Ignored unless idle and past the cooldown, so a
            hovering cursor doesn't make the avatar jitter.
        """
        if direction not in POINTER_DIRECTIONS:
            return
        if self._state != IDLE:
            return
        if self._elapsed_ms - self._last_pointer_ms < POINTER_LOOK_COOLDOWN_MS:
            return
        self._last_pointer_ms = self._elapsed_ms
        self._set_state(
            { "left": LOOK_LEFT, "right": LOOK_RIGHT,
              "up": LOOK_UP, "down": LOOK_DOWN }[direction]
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._frame_index = 0
        self._frame_time_ms = 0.0
