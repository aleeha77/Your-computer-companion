"""The emotion engine (Stage 6): Peeko's mood and stats, live over time.

:mod:`peeko.emotions.state` holds the *data model* (PAD space + named
emotions); :mod:`peeko.needs.system` holds the *need* model (0..100 values
that decay over time). This module owns one of each and turns them into the
thing the rest of the app can actually use:

* **drift** — :meth:`EmotionEngine.tick` advances both models by the real
  elapsed time, through an *injectable clock* so tests are deterministic;
* **interactions** — :meth:`EmotionEngine.interact` applies the documented
  deltas of one user action (talk / pet / play / status);
* **a snapshot** — :meth:`EmotionEngine.snapshot` returns the six 0..100
  stats Peeko shows and sends to the AI, on exactly the scales
  :class:`peeko.ai.context.ChatContext` documents.

Deliberately Qt-free and dependency-free: everything here is plain Python so
it can be tested without a display and called from any thread. The app's Qt
side plugs a ``QTimer`` into :meth:`tick` (see
:class:`peeko.avatar.widget.AvatarWindow`), which is what makes the stats
drift while Peeko runs without ever blocking the UI thread.

Scales and rates (all documented, all clamped — never an unclamped value
escapes this module):

* ``happiness`` is the PAD *pleasure* axis mapped onto ``0..100``
  (``(pleasure + 1) / 2 * 100``), exactly like
  :func:`peeko.ai.context._emotion_and_happiness` — 50 is neutral;
* the other five stats are the :class:`peeko.needs.system.PetNeeds` values in
  ``0..100`` (100 = fully satisfied), with ``sleepiness`` the inverse of the
  ``sleep`` need (``100 - sleep``), as ``ChatContext`` documents;
* over time the PAD axes decay toward neutral and the needs decay toward
  zero using :data:`peeko.needs.system.DECAY_PER_HOUR`;
* interactions move PAD and the needs by the fixed deltas in
  :data:`INTERACTION_EFFECTS`.

Nothing here is persisted yet: the engine is in-memory for one run. Stage 8
(persistent memory) is what will save and restore it — see the README.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from peeko.emotions.state import EmotionalState, Emotion
from peeko.needs.system import Need, PetNeeds

LOG = logging.getLogger("peeko.emotions")

#: A clock is any callable returning a monotonic-ish number of *seconds*.
Clock = Callable[[], float]

# --------------------------------------------------------------------------- #
# Drift: how fast the PAD axes fall back toward neutral
# --------------------------------------------------------------------------- #
#: PAD units recovered per hour of no interaction (linear, toward neutral).
#: Pleasure decays slowest so a nice moment lingers; arousal decays fastest so
#: Peeko calms down quickly after something exciting; dominance sits between.
PLEASURE_DECAY_PER_HOUR = 0.6
AROUSAL_DECAY_PER_HOUR = 1.2
DOMINANCE_DECAY_PER_HOUR = 0.8


# --------------------------------------------------------------------------- #
# Interaction effects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class InteractionEffect:
    """The documented deltas one interaction applies.

    ``pleasure`` / ``arousal`` / ``dominance`` are PAD deltas (unit cube);
    ``needs`` are deltas on the 0..100 need scale (positive = more
    satisfied). The amounts are small on purpose: one click should nudge
    Peeko's mood, not flip it.
    """

    id: str
    summary: str
    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    needs: tuple[tuple[Need, float], ...] = field(default_factory=tuple)


#: Every interaction Stage 6 implements. Talk is also what a chat turn counts
#: as; ``status`` is the small "you checked on me" bump.
INTERACTION_EFFECTS: dict[str, InteractionEffect] = {
    "talk": InteractionEffect(
        id="talk",
        summary="a friendly chat: Peeko feels a bit happier and closer to you",
        pleasure=0.10,
        arousal=0.05,
        dominance=0.02,
        needs=((Need.FRIENDSHIP, 2.0), (Need.BOREDOM, 3.0),
               (Need.ENERGY, -0.5)),
    ),
    "pet": InteractionEffect(
        id="pet",
        summary="being petted: Peeko is noticeably happier and more attached",
        pleasure=0.20,
        arousal=0.05,
        dominance=-0.02,
        needs=((Need.FRIENDSHIP, 3.0), (Need.BOREDOM, 2.0)),
    ),
    "play": InteractionEffect(
        id="play",
        summary="playing: fun and excitement, paid for with energy and hunger",
        pleasure=0.18,
        arousal=0.25,
        dominance=0.05,
        needs=((Need.BOREDOM, 8.0), (Need.FRIENDSHIP, 2.0),
               (Need.ENERGY, -4.0), (Need.HUNGER, -1.5)),
    ),
    "status": InteractionEffect(
        id="status",
        summary="checking on Peeko: a tiny friendly bump",
        pleasure=0.02,
        needs=((Need.FRIENDSHIP, 0.5),),
    ),
}

#: The interactions the UI may ask for (used by tests and the menu wiring).
INTERACTION_IDS: tuple[str, ...] = tuple(INTERACTION_EFFECTS)

#: What a *chat turn* counts as (see :meth:`EmotionEngine.interact`).
CHAT_INTERACTION = "talk"


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EmotionSnapshot:
    """Peeko's six 0..100 stats plus the named emotion, at one moment.

    The field names and scales match :class:`peeko.ai.context.ChatContext`
    exactly (``emotion``, ``happiness``, ``energy``, ``hunger``,
    ``sleepiness``, ``friendship``), with ``boredom`` added — it is one of the
    six stats the owner sees in Check Status, even though the AI context does
    not carry it today.
    """

    emotion: Emotion
    happiness: float
    energy: float
    hunger: float
    boredom: float
    sleepiness: float
    friendship: float
    pad: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def emotion_name(self) -> str:
        """The emotion as the lowercase string the AI context uses."""
        return str(self.emotion.value)

    def stats(self) -> dict[str, float]:
        """The six stats in display order, rounded for humans."""
        return {
            "happiness": round(self.happiness, 1),
            "energy": round(self.energy, 1),
            "hunger": round(self.hunger, 1),
            "boredom": round(self.boredom, 1),
            "sleepiness": round(self.sleepiness, 1),
            "friendship": round(self.friendship, 1),
        }

    def context_values(self) -> dict[str, object]:
        """The subset the AI chat context cares about, in its own field names."""
        return {
            "emotion": self.emotion_name,
            "happiness": round(self.happiness, 1),
            "energy": round(self.energy, 1),
            "hunger": round(self.hunger, 1),
            "sleepiness": round(self.sleepiness, 1),
            "friendship": round(self.friendship, 1),
        }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
class EmotionEngine:
    """Owns one :class:`EmotionalState` and one :class:`PetNeeds`.

    :param state: the live PAD state (defaults to neutral).
    :param needs: the live needs (defaults to the documented start values).
    :param clock: injectable zero-argument clock returning seconds; tests pass
        a fake, the app leaves the default. The engine only ever reads it to
        work out *how much time has passed*, so nothing here blocks.
    """

    def __init__(
        self,
        *,
        state: EmotionalState | None = None,
        needs: PetNeeds | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.state: EmotionalState = state or EmotionalState()
        self.needs: PetNeeds = needs or PetNeeds()
        self._clock: Clock = clock
        self._last_tick: float = float(clock())
        #: Total simulated hours this engine has advanced (diagnostics/tests).
        self.hours_elapsed: float = 0.0

    # ------------------------------------------------------------------ #
    # Time
    # ------------------------------------------------------------------ #
    def tick(self, now: float | None = None, *, hours: float | None = None) -> float:
        """Advance the drift to ``now`` (or by ``hours``) and return the hours.

        Two ways to drive the same drift, so the app and the tests can each do
        what suits them:

        * the app passes nothing — the injectable clock is read and only the
          time that really passed is simulated (:meth:`tick_now`);
        * a caller (or a test) that already knows the elapsed time passes
          ``hours`` directly.

        :raises ValueError: for negative hours.
        """
        if hours is None:
            hours = self._hours_since(now)
        if hours < 0:
            raise ValueError(f"hours must be >= 0, got {hours!r}")
        if hours == 0:
            return 0.0
        self._drift(hours)
        self.hours_elapsed += hours
        return hours

    def _hours_since(self, now: float | None) -> float:
        """Seconds between the injectable clock's last reading and now."""
        stamp = float(self._clock() if now is None else now)
        hours = max(0.0, (stamp - self._last_tick) / 3600.0)
        self._last_tick = stamp
        return hours

    def _drift(self, hours: float) -> None:
        """Decay the needs and pull the PAD axes toward neutral."""
        self.needs.tick(hours)
        # PAD decays toward (0, 0, 0) — neutral — without overshooting it.
        self.state.pleasure = _toward_zero(
            self.state.pleasure, PLEASURE_DECAY_PER_HOUR * hours
        )
        self.state.arousal = _toward_zero(
            self.state.arousal, AROUSAL_DECAY_PER_HOUR * hours
        )
        self.state.dominance = _toward_zero(
            self.state.dominance, DOMINANCE_DECAY_PER_HOUR * hours
        )

    # ------------------------------------------------------------------ #
    # Interactions
    # ------------------------------------------------------------------ #
    def interact(self, action: str, *, hours: float | None = None) -> InteractionEffect:
        """Apply one interaction's documented deltas and return the effect.

        An unknown action is a programming error, not a user-facing one, so it
        raises :class:`KeyError` instead of silently doing nothing. Pass
        ``hours`` to drift first (the UI instead lets its timer do that).

        The chat window uses :data:`CHAT_INTERACTION` for a completed turn —
        chatting with Peeko is the documented "talk" effect.
        """
        effect = INTERACTION_EFFECTS.get(action)
        if effect is None:
            raise KeyError(
                f"unknown interaction {action!r}; "
                f"known: {', '.join(INTERACTION_IDS)}"
            )
        if hours:
            self.tick(hours=hours)
        self.state.pleasure += effect.pleasure
        self.state.arousal += effect.arousal
        self.state.dominance += effect.dominance
        # ``EmotionalState`` only clamps in ``__post_init__``, so clamp here:
        # the engine never lets a value escape the unit cube.
        self.state = EmotionalState(
            pleasure=self.state.pleasure,
            arousal=self.state.arousal,
            dominance=self.state.dominance,
        )
        for need, delta in effect.needs:
            self.needs.set(need, self.needs.get(need) + delta)
        LOG.debug(
            "Interaction %r applied: PAD=%s, needs=%s",
            effect.id, self.state.tuple(), self.needs.summary(),
        )
        return effect

    #: Alias used by the chat window: one completed conversation turn.
    def chat_turn(self, **kwargs) -> InteractionEffect:
        """Apply the documented effect of one completed chat turn."""
        return self.interact(CHAT_INTERACTION, **kwargs)

    # ------------------------------------------------------------------ #
    # Read-out
    # ------------------------------------------------------------------ #
    def happiness(self) -> float:
        """The PAD pleasure axis on the 0..100 scale ``ChatContext`` uses."""
        return round((self.state.pleasure + 1.0) / 2.0 * 100.0, 1)

    def sleepiness(self) -> float:
        """How sleepy Peeko is: the inverse of the sleep need (0..100)."""
        return round(100.0 - self.needs.get(Need.SLEEP), 1)

    def dominant_emotion(self) -> Emotion:
        """The named emotion closest to the current PAD state."""
        return self.state.nearest_emotion()

    def snapshot(self) -> EmotionSnapshot:
        """The live six-stat read-out (see :class:`EmotionSnapshot`)."""
        return EmotionSnapshot(
            emotion=self.dominant_emotion(),
            happiness=self.happiness(),
            energy=self.needs.get(Need.ENERGY),
            hunger=self.needs.get(Need.HUNGER),
            boredom=self.needs.get(Need.BOREDOM),
            sleepiness=self.sleepiness(),
            friendship=self.needs.get(Need.FRIENDSHIP),
            pad=self.state.tuple(),
        )

    def describe(self) -> str:
        """One short human-readable line (used in logs and dialogs)."""
        snap = self.snapshot()
        return f"{snap.emotion_name} — " + ", ".join(
            f"{name} {value:.0f}" for name, value in snap.stats().items()
        )

    def __repr__(self) -> str:
        return f"EmotionEngine({self.describe()})"


def _toward_zero(value: float, amount: float) -> float:
    """Move ``value`` toward 0 by ``amount``, never past it."""
    if value > 0:
        return max(0.0, value - amount)
    return min(0.0, value + amount)


__all__ = [
    "AROUSAL_DECAY_PER_HOUR",
    "CHAT_INTERACTION",
    "DOMINANCE_DECAY_PER_HOUR",
    "EmotionEngine",
    "EmotionSnapshot",
    "INTERACTION_EFFECTS",
    "INTERACTION_IDS",
    "InteractionEffect",
    "PLEASURE_DECAY_PER_HOUR",
]
