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

Stage 2 adds three **reaction** states (one-shot, and on top of the core
set above):

* ``hover``       — a brief "oh!" (half-closed eyes + a tiny lift) when the
  pointer enters the avatar window; it never interrupts a click or a drag,
  and the reaction ends the moment the pointer leaves.
* ``double_click`` — a bigger, more excited bounce than a single click.
* ``confused``    — a quick "huh?" head-shake, played when the user picks a
  part of the menu that is not implemented yet.

Reaction states are *optional*: a hand-made artwork manifest that omits
their animations still runs, it simply has fewer reactions (see
:meth:`AvatarStateMachine.available_reactions`). The core states above are
mandatory — a manifest missing one of them fails loudly at startup.

Stage 4 adds one **sustained** state for voice input:

* ``listening``  — an attentive, looping "ears open" animation shown while
  the microphone is open. Unlike a reaction it does not settle back to idle
  by itself: it stays until :meth:`AvatarStateMachine.stop_listening` is
  called. Dragging still wins while it lasts — the dragging wiggle plays, and
  the listening pose resumes when the user lets go, because the microphone is
  still open. Also optional artwork, so a manifest without it simply shows no
  listening animation (the chat window still says it is listening).

Stage 5 adds the second sustained state, for voice output:

* ``talking``    — a looping "mouth is moving" animation shown while Peeko's
  reply is being played out loud. It is held by
  :meth:`AvatarStateMachine.start_talking` exactly like ``listening``, is
  equally optional artwork, and equally yields to a drag. When both are
  active — you asked Peeko to speak while the microphone is still open —
  **talking wins**: the robot shows that it is speaking, and the listening
  pose resumes by itself the moment the playback ends.

Transitions (all timers are decremented by :meth:`tick`, so nothing ever
blocks; there are no sleeps anywhere):

* ``idle`` -> ``blink`` / ``look_*``  (randomised timers)
* ``idle`` -> ``look_*``              (pointer glance, cooldown-limited)
* ``hover_enter()``                   (``idle`` -> ``hover``; ignored while
  pressed/dragging or when another animation is playing)
* ``hover_leave()``                   (``hover`` -> ``idle``, immediately)
* ``press()``                         (arms the interaction; no visual change)
* ``drag_started()``                  (any state -> ``dragging``)
* ``release(moved=True)``             (``dragging`` -> ``idle``)
* ``release(moved=False)``            (``idle``/``blink``/``look_*`` -> ``click``;
  a ``double_click`` reaction in progress is never downgraded to a click)
* ``double_click()``                  (-> ``double_click``, replays on repeat)
* ``confused()``                      (-> ``confused``)
* ``start_listening()``                (``idle`` -> ``listening``, held until
  ``stop_listening()``; declined while dragging or without the artwork)
* ``stop_listening()``                 (``listening`` -> ``idle``)
* ``start_talking()``                  (``idle`` -> ``talking``, held until
  ``stop_talking()``; declined while dragging or without the artwork)
* ``stop_talking()``                   (``talking`` -> ``idle``, or back to
  ``listening`` while the microphone is still open)
* any one-shot animation finishing    (-> ``idle``, or back to the active
  sustained state)

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

# -- Stage 2 reaction states ------------------------------------------------- #
HOVER = "hover"
DOUBLE_CLICK = "double_click"
CONFUSED = "confused"

# -- Stage 4: sustained states ---------------------------------------------- #
#: "I am listening to you" (voice input). Unlike a reaction this is not a
#: one-shot: it keeps playing until something stops it, so the robot visibly
#: stays in listening mode for as long as the microphone is open.
LISTENING = "listening"

#: "I am saying something" (voice output, Stage 5). Sustained like
#: :data:`LISTENING`: it is held for as long as Peeko's reply is being played,
#: and it wins over the listening pose when both are active.
TALKING = "talking"

LOOK_DIRECTIONS = (LOOK_LEFT, LOOK_RIGHT, LOOK_UP, LOOK_DOWN)

#: Animation-state names the engine always needs; the manifest (or the
#: built-in mapping below) must provide an animation for each of these.
REQUIRED_STATES = frozenset(
    {IDLE, BLINK, *LOOK_DIRECTIONS, CLICK, DRAGGING}
)

#: Animations a reaction state may play: by convention the state's own name
#: (``hover`` → the ``hover`` animation), overridable per state through the
#: manifest's ``state_animation_map``. A reaction whose animation the
#: manifest does not provide is simply switched off (it never raises), so
#: artwork manifests written for Stage 1 keep working unchanged — they just
#: have fewer reactions.
REACTION_STATES = (HOVER, DOUBLE_CLICK, CONFUSED)

#: Optional states that are *not* one-shot reactions: they are requested by
#: another module (:mod:`peeko.avatar.expressions`) and stay in effect until
#: they are explicitly ended. Like reactions they are switched off when the
#: artwork does not provide their animation, so an older manifest keeps
#: working.
SUSTAINED_STATES = (LISTENING, TALKING)

#: Every state whose animation is optional (a manifest without it still runs).
OPTIONAL_STATES = (*REACTION_STATES, *SUSTAINED_STATES)

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
    HOVER: HOVER,
    DOUBLE_CLICK: DOUBLE_CLICK,
    CONFUSED: CONFUSED,
    LISTENING: LISTENING,
    TALKING: TALKING,
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
        overrides = dict(self.manifest.state_animation_map or {})
        state_map = dict(DEFAULT_STATE_ANIMATIONS)
        state_map.update(overrides)
        available = set(self.manifest.animations)

        # -- core states: mandatory, a missing one is a startup error ----- #
        missing_states = sorted(REQUIRED_STATES - set(state_map))
        bad_anims = sorted(
            name for state, name in state_map.items()
            if state in REQUIRED_STATES and name not in available
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

        # -- reaction/sustained states: optional, off unless the artwork has
        #    them ---------------------------------------------------------- #
        optional_map: dict[str, str] = {}
        for state in OPTIONAL_STATES:
            chosen = state_map.get(state)
            if chosen in available:
                optional_map[state] = chosen
            else:
                state_map.pop(state, None)   # optional state switched off

        self._state_map = state_map
        self._reaction_states = frozenset(
            state for state in optional_map if state in REACTION_STATES
        )
        self._sustained_states = frozenset(
            state for state in optional_map if state in SUSTAINED_STATES
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
        self._hovering = False
        self._listening = False
        self._talking = False

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
        """Effective state -> animation mapping (defaults + manifest).

        Reaction states the manifest cannot supply are absent — they are
        switched off rather than pointing at a non-existent animation.
        """
        return dict(self._state_map)

    @property
    def available_reactions(self) -> frozenset[str]:
        """Reaction states this manifest can actually play."""
        return self._reaction_states

    def reaction_available(self, state: str) -> bool:
        """Is ``state`` a reaction this machine can play right now?"""
        return state in self._reaction_states

    @property
    def listening_available(self) -> bool:
        """Can this artwork show the listening state?

        Optional like the reactions: placeholder or hand-made art without a
        ``listening`` animation simply does not show one, and everything else
        keeps working.
        """
        return LISTENING in self._sustained_states

    @property
    def listening(self) -> bool:
        """True between :meth:`start_listening` and :meth:`stop_listening`."""
        return self._listening

    @property
    def talking_available(self) -> bool:
        """Can this artwork show the talking state? (Optional, like listening.)"""
        return TALKING in self._sustained_states

    @property
    def talking(self) -> bool:
        """True between :meth:`start_talking` and :meth:`stop_talking`."""
        return self._talking

    @property
    def hovering(self) -> bool:
        """True between :meth:`hover_enter` and :meth:`hover_leave`."""
        return self._hovering

    @property
    def is_pressed(self) -> bool:
        """True between :meth:`press` and :meth:`release`."""
        return self._press_active

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
                    # One-shot finished: settle back to the resting state.
                    self._settle()
                    return

        # A sustained state (listening, talking) is the resting state while it
        # is active, so anything that ends up back at idle resumes it (this
        # also makes a one-shot sustained animation keep playing for as long
        # as the activity lasts).
        sustained = self._active_sustained_state()
        if sustained is not None and self._state == IDLE:
            self._set_state(sustained, restart=True)
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
        elif moved:
            self._set_state(IDLE)
        elif self._state == DOUBLE_CLICK:
            # The first release of a double-click already played ``click``;
            # the double-click reaction replaced it and must survive the
            # second release instead of being downgraded back to a click.
            pass
        else:
            self._set_state(CLICK)

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
    # Stage 2: reactions
    # ------------------------------------------------------------------ #
    def hover_enter(self) -> bool:
        """The pointer entered the avatar window -> a brief "oh!" reaction.

        Deliberately timid: it only fires from :data:`IDLE`, and never while
        a press/drag is in flight, so hovering can neither swallow nor
        interrupt a click. Repeats without an intervening
        :meth:`hover_leave` are ignored.

        :returns: ``True`` when the hover reaction started.
        """
        if self._hovering:
            return False
        self._hovering = True
        if self._press_active or self._state != IDLE:
            return False
        return self.play_reaction(HOVER)

    def hover_leave(self) -> bool:
        """The pointer left the avatar window.

        Ends a running ``hover`` reaction immediately (back to a natural
        ``idle``); any other animation is left alone.

        :returns: ``True`` when a hover reaction was cut short.
        """
        self._hovering = False
        if self._state == HOVER:
            self._set_state(IDLE)
            return True
        return False

    def double_click(self) -> bool:
        """The user double-clicked: a bigger, more excited reaction.

        Replays from the first frame when double-clicked again in quick
        succession. Ignored while the avatar is being dragged.

        :returns: ``True`` when the reaction started.
        """
        if self._state == DRAGGING:
            return False
        return self.play_reaction(DOUBLE_CLICK)

    def confused(self) -> bool:
        """A quick "huh?" head-shake.

        Used when the user picks a menu entry that is not implemented yet.

        :returns: ``True`` when the reaction started.
        """
        if self._state == DRAGGING:
            return False
        return self.play_reaction(CONFUSED)

    def play_reaction(self, state: str) -> bool:
        """Play the one-shot reaction ``state`` from its first frame.

        Reactions are optional artwork: an unknown state, or one this
        manifest cannot supply, is ignored instead of raising.

        :returns: ``True`` when the reaction started.
        """
        if state not in self._reaction_states:
            return False
        self._set_state(state, restart=True)
        return True

    # ------------------------------------------------------------------ #
    # Stage 3: expressions requested by something other than the user
    # ------------------------------------------------------------------ #
    def can_play(self, state: str) -> bool:
        """Can this artwork manifest play ``state``?

        Core states are always present (a manifest missing one fails at
        startup); reaction and sustained states are optional and simply
        switched off when the artwork does not provide them.
        """
        return state in self._state_map

    def play_cued(self, state: str) -> bool:
        """Play ``state`` because something *else* asked for it.

        Used from Stage 3 on: an AI reply carries an animation name, the
        avatar layer maps it to a state, and this method plays it. One-shot
        animations settle back to :data:`IDLE` when they finish, exactly
        like a reaction.

        User input always wins: the cue is declined while the user is
        holding or dragging the avatar, and while it is being carried.
        Unknown/unsupported states are declined too, so a bad name can
        never put the avatar into a state it cannot render.

        :returns: ``True`` when the state started playing.
        """
        if state not in self._state_map:
            return False
        if self._press_active or state == DRAGGING or self._state == DRAGGING:
            return False
        if self._listening or self._talking:
            # While the microphone is open or Peeko is speaking, that activity
            # is the user's own request, so a cue does not interrupt it.
            return False
        self._set_state(state, restart=True)
        return True

    # ------------------------------------------------------------------ #
    # Stage 4/5: sustained states (listening, talking)
    # ------------------------------------------------------------------ #
    def start_listening(self) -> bool:
        """Show the listening animation because a capture just started.

        Stays in effect (looping, or restarted if the artwork's animation is
        one-shot) until :meth:`stop_listening` is called — unlike a reaction
        it does not settle back to idle on its own. If Peeko is talking right
        now the talking pose stays on screen: both activities are remembered,
        and the listening pose appears the moment the playback ends.

        Declined when the artwork has no ``listening`` animation, and while
        the user is holding or dragging the robot (user input always wins).

        :returns: ``True`` when the state was recorded.
        """
        if not self.listening_available:
            return False
        if self._press_active or self._state == DRAGGING:
            return False
        self._listening = True
        active = self._active_sustained_state()
        self._set_state(active or LISTENING, restart=True)
        return True

    def stop_listening(self) -> bool:
        """End the listening state (the capture finished or was stopped).

        :returns: ``True`` when a listening animation was cut short.
        """
        was_listening = bool(self._listening and self._state == LISTENING)
        self._listening = False
        if was_listening:
            self._resume_sustained()
        return was_listening

    def start_talking(self) -> bool:
        """Show the talking animation because Peeko started speaking.

        Stage 5's sustained state, held exactly like ``listening``: it stays
        until :meth:`stop_talking` is called, and it wins over the listening
        pose while a still-open microphone is also active (the robot shows
        what it is saying; the listening pose comes back by itself).

        Declined when the artwork has no ``talking`` animation, and while the
        user is holding or dragging the robot (user input always wins).

        :returns: ``True`` when the state started playing.
        """
        if not self.talking_available:
            return False
        if self._press_active or self._state == DRAGGING:
            return False
        self._talking = True
        self._set_state(TALKING, restart=True)
        return True

    def stop_talking(self) -> bool:
        """End the talking state (the playback finished, stopped or failed).

        :returns: ``True`` when a talking animation was cut short.
        """
        was_talking = bool(self._talking and self._state == TALKING)
        self._talking = False
        if was_talking:
            self._resume_sustained()
        return was_talking

    def _active_sustained_state(self) -> str | None:
        """The sustained state that should be playing right now, if any.

        Talking beats listening: while Peeko is speaking aloud the talking
        animation is the truthful thing to show, and a still-open microphone
        resumes its own pose the moment the playback ends.
        """
        if self._talking and self.talking_available:
            return TALKING
        if self._listening and self.listening_available:
            return LISTENING
        return None

    def _resume_sustained(self) -> None:
        """Return to the active sustained state, or to :data:`IDLE`."""
        sustained = self._active_sustained_state()
        self._set_state(sustained or IDLE, restart=sustained is not None)

    def _settle(self) -> None:
        """Return a finished one-shot animation to its resting state."""
        self._resume_sustained()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _set_state(self, state: str, *, restart: bool = False) -> None:
        if state == self._state and not restart:
            return
        self._state = state
        self._frame_index = 0
        self._frame_time_ms = 0.0
