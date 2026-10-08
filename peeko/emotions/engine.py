"""The emotion engine (Stages 6-7): Peeko's mood and needs, live over time.

:mod:`peeko.emotions.state` holds the *data model* (PAD space + named
emotions); :mod:`peeko.needs.system` holds the *need* model (0..100 values
that decay over time). This module owns one of each and turns them into the
thing the rest of the app can actually use:

* **drift** — :meth:`EmotionEngine.tick` advances both models by the real
  elapsed time, through an *injectable clock* so tests are deterministic, and
  while Peeko is asleep it *restores* the sleep and energy needs instead of
  decaying them (:meth:`peeko.needs.system.PetNeeds.tick`);
* **interactions** — :meth:`EmotionEngine.interact` applies the documented
  deltas of one user action (talk / pet / play / status / feed / sleep / wake);
* **feeding and sleeping** — :meth:`EmotionEngine.feed` eats one food from the
  :data:`peeko.needs.system.FOODS` catalogue (and refuses honestly when Peeko
  is full or asleep), :meth:`EmotionEngine.sleep` / :meth:`EmotionEngine.wake`
  start and end a nap, and :meth:`EmotionEngine.take_events` hands out what
  the needs system decided on its own (yawning, falling asleep, waking up, a
  boredom nudge) — all of it rate-limited in
  :mod:`peeko.needs.behaviour`;
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

Every needs value is 0..100 and clamped; nothing unclamped escapes. Nothing
here is persisted yet: the engine is in-memory for one run. Stage 8
(persistent memory) is what will save and restore it — see the README.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from peeko.emotions.state import EmotionalState, Emotion
from peeko.needs.behaviour import NeedsBehaviour, NeedsEvent
from peeko.needs.system import (
    FOODS,
    FULL_HUNGER,
    Food,
    Need,
    PetNeeds,
    find_food,
)

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

# --------------------------------------------------------------------------- #
# Stage 7: eating, sleeping and waking
# --------------------------------------------------------------------------- #
#: Prefix of the per-food eating interactions (``feed_apple``, ``feed_pizza``,
#: ...). One :class:`InteractionEffect` is generated per entry of
#: :data:`peeko.needs.system.FOODS`, straight from the food's documented
#: deltas, so the README table, the Feed picker and the effects can never
#: drift apart.
FEED_INTERACTION_PREFIX = "feed_"


def feed_interaction_id(food_id: str) -> str:
    """The interaction id applied when Peeko eats ``food_id``."""
    return f"{FEED_INTERACTION_PREFIX}{food_id}"


for _food in FOODS.values():
    INTERACTION_EFFECTS[feed_interaction_id(_food.id)] = InteractionEffect(
        id=feed_interaction_id(_food.id),
        summary=f"eating {_food.label.lower()}: {_food.summary}",
        pleasure=_food.pleasure,
        arousal=_food.arousal,
        needs=(
            (Need.HUNGER, _food.hunger),
            (Need.ENERGY, _food.energy),
            (Need.FRIENDSHIP, _food.friendship),
            (Need.BOREDOM, _food.boredom),
        ),
    )

#: Starting a nap and ending one. Sleeping's real work is time-based
#: (:data:`peeko.needs.system.SLEEP_RECOVERY_PER_HOUR`); these small PAD deltas
#: are the mood of settling down and of waking up.
INTERACTION_EFFECTS["sleep"] = InteractionEffect(
    id="sleep",
    summary="settling down to sleep: Peeko calms down and relaxes",
    pleasure=0.02,
    arousal=-0.10,
    dominance=-0.05,
    needs=((Need.ENERGY, 0.0),),
)
INTERACTION_EFFECTS["wake"] = InteractionEffect(
    id="wake",
    summary="waking up: a small stretch, and Peeko is pleased to see you",
    pleasure=0.05,
    arousal=0.05,
    needs=((Need.FRIENDSHIP, 0.5),),
)

#: The interactions the UI may ask for (used by tests and the menu wiring).
INTERACTION_IDS: tuple[str, ...] = tuple(INTERACTION_EFFECTS)

#: The five food-eating interactions, in catalogue order.
FEED_INTERACTION_IDS: tuple[str, ...] = tuple(
    feed_interaction_id(food_id) for food_id in FOODS
)

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
    #: Stage 7: is Peeko really asleep right now (needs system, not artwork)?
    asleep: bool = False
    #: Why the *mood hint* (:meth:`EmotionEngine.mood_signal`) says what it
    #: says — ``""`` when the PAD mood is what is showing.
    mood_reason: str = ""

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
# Stage 7: needs-driven mood, feeding and sleeping results
# --------------------------------------------------------------------------- #
#: When a need is at or below its threshold, that need decides Peeko's
#: *expression* — tired when sleepy, sad when hungry or bored, tired when
#: exhausted. The placeholder emotion set has no hungry or bored emotion, so
#: they borrow ``sad`` exactly like the Stage 6 table borrows ``blink`` for
#: tired: documented approximation, never an invented animation. The lowest
#: value wins when several needs are low; ties keep this order.
NEED_MOOD_HINTS: tuple[tuple[Need, float, Emotion, str], ...] = (
    (Need.SLEEP, 45.0, Emotion.TIRED, "sleepy"),
    (Need.HUNGER, 25.0, Emotion.SAD, "hungry"),
    (Need.BOREDOM, 25.0, Emotion.SAD, "bored"),
    (Need.ENERGY, 20.0, Emotion.TIRED, "exhausted"),
)


@dataclass(frozen=True)
class MoodSignal:
    """Peeko's expression mood: the named emotion plus why it was chosen."""

    emotion: Emotion
    reason: str

    @property
    def emotion_name(self) -> str:
        return str(self.emotion.value)


@dataclass(frozen=True)
class FeedResult:
    """What happened when the user offered Peeko one food.

    ``applied`` is the honest part: ``False`` means nothing changed and
    ``message`` says exactly why (Peeko is full, or asleep). No food is ever
    consumed by a refusal.
    """

    food: Food
    applied: bool
    message: str


@dataclass(frozen=True)
class SleepResult:
    """What happened when somebody asked Peeko to sleep (or to wake up)."""

    asleep: bool
    changed: bool
    message: str


@dataclass(frozen=True)
class NeedsReport:
    """The feeding/sleeping facts Check Status reports (all real)."""

    asleep: bool
    last_meal: str | None
    last_meal_ago_hours: float | None
    sleeps: int
    auto_sleeps: int
    auto_wakes: int
    yawns: int
    attention_nudges: int


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
        #: Stage 7: yawning / auto-sleep / boredom-nudge rules and limits.
        self.behaviour: NeedsBehaviour = NeedsBehaviour()
        #: Events the needs system raised but the UI has not read yet.
        self._events: list[NeedsEvent] = []
        #: The last thing Peeko ate (``None`` = nothing yet this run).
        self.last_meal: str | None = None
        #: Simulated hour at which :attr:`last_meal` was eaten.
        self.last_meal_hour: float | None = None

    # ------------------------------------------------------------------ #
    # Time
    # ------------------------------------------------------------------ #
    def tick(
        self,
        now: float | None = None,
        *,
        hours: float | None = None,
        idle: bool = True,
    ) -> float:
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
        self._collect_events(idle=idle)
        return hours

    def _hours_since(self, now: float | None) -> float:
        """Seconds between the injectable clock's last reading and now."""
        stamp = float(self._clock() if now is None else now)
        hours = max(0.0, (stamp - self._last_tick) / 3600.0)
        self._last_tick = stamp
        return hours

    def _drift(self, hours: float) -> None:
        """Drift the needs and pull the PAD axes toward neutral.

        While Peeko is asleep the ``sleep`` and ``energy`` needs *recover*
        instead of decaying (a nap has to be worth taking, and it is the only
        way the sleep need comes back); the PAD axes still fall toward neutral,
        which is what "calm, undisturbed sleep" looks like on this model.
        """
        self.needs.tick(hours, sleeping=self.behaviour.asleep)
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
    # Stage 7: feeding
    # ------------------------------------------------------------------ #
    @property
    def is_asleep(self) -> bool:
        """Is Peeko really asleep (needs system), regardless of artwork?"""
        return bool(self.behaviour.asleep)

    def feed(self, food_id: str) -> FeedResult:
        """Feed Peeko one food from the catalogue.

        The deltas are the food's documented ones, applied through
        :meth:`interact` so the mood and every need move exactly as the README
        table says. Two honest refusals, both leaving the food untouched:

        * Peeko is **asleep** — wake him up first;
        * Peeko is **full** (hunger at/above
          :data:`peeko.needs.system.FULL_HUNGER`) — he would not eat it.

        :raises KeyError: for a food id that is not on the menu (a programming
            error, like an unknown interaction).
        """
        food = find_food(food_id)
        if food is None:
            raise KeyError(
                f"unknown food {food_id!r}; known: {', '.join(FOODS)}"
            )
        if self.behaviour.asleep:
            return FeedResult(
                food, False,
                f"Peeko is asleep, so he did not eat the {food.label.lower()}. "
                "Wake him up (Wake Up) first — nothing was wasted.",
            )
        if self.needs.is_full():
            return FeedResult(
                food, False,
                f"Peeko is full (hunger "
                f"{self.needs.get(Need.HUNGER):.0f}/100) and refused the "
                f"{food.label.lower()}. Nothing was wasted — offer it again "
                "once he is hungry.",
            )
        effect = self.interact(feed_interaction_id(food.id))
        self.last_meal = food.label
        self.last_meal_hour = self.hours_elapsed
        LOG.info("Fed %s: %s", food.label, effect.summary)
        return FeedResult(
            food, True,
            f"{food.label} eaten — {food.summary}. Peeko now: {self.describe()}",
        )

    # ------------------------------------------------------------------ #
    # Stage 7: sleeping and waking
    # ------------------------------------------------------------------ #
    def sleep(self) -> SleepResult:
        """Start a nap deliberately (the Sleep menu entry).

        While asleep the needs drift inverts (see :meth:`_drift`) and the
        avatar shows the sleeping pose; :meth:`wake` (or an automatic wake-up
        once he is rested) ends it. Asking again while already asleep changes
        nothing and says so.
        """
        if self.behaviour.asleep:
            return SleepResult(
                True, False,
                "Peeko is already asleep — nothing changed.",
            )
        self.tick()
        self.behaviour.begin_sleep(self.hours_elapsed)
        effect = self.interact("sleep")
        LOG.info("Sleep started: %s", effect.summary)
        return SleepResult(
            True, True,
            "Peeko curled up and went to sleep. While he sleeps his sleepiness "
            "and energy recover; hunger does not. Use Wake Up to end the nap.",
        )

    def wake(self) -> SleepResult:
        """Wake Peeko up (the Wake Up menu entry, or an automatic wake-up)."""
        if not self.behaviour.asleep:
            return SleepResult(
                False, False,
                "Peeko is already awake — nothing changed.",
            )
        self.tick()
        self.behaviour.end_sleep(self.hours_elapsed)
        self.interact("wake")
        LOG.info("Woke up: sleepiness %.0f/100", self.sleepiness())
        return SleepResult(
            False, True,
            f"Peeko woke up. Sleepiness is now {self.sleepiness():.0f}/100 "
            "and energy "
            f"{self.needs.get(Need.ENERGY):.0f}/100.",
        )

    # ------------------------------------------------------------------ #
    # Stage 7: what the needs decided on their own
    # ------------------------------------------------------------------ #
    def _collect_events(self, *, idle: bool = True) -> tuple[NeedsEvent, ...]:
        """Ask the behaviour rules what the needs justify and queue it."""
        events = self.behaviour.events(
            self.needs, self.hours_elapsed, idle=idle
        )
        for event in events:
            LOG.info("Needs event %s: %s", event.kind, event.summary)
        self._events.extend(events)
        return tuple(events)

    def take_events(self) -> tuple[NeedsEvent, ...]:
        """Every event raised since the last call, in order (then cleared).

        The UI consumes these to react truthfully: a yawn, falling asleep,
        waking up, or one gentle boredom nudge.
        """
        events = tuple(self._events)
        self._events = []
        return events

    def needs_status(self) -> NeedsReport:
        """The feeding/sleeping facts the status readout shows (all real)."""
        ago = (
            None if self.last_meal_hour is None
            else round(self.hours_elapsed - self.last_meal_hour, 2)
        )
        return NeedsReport(
            asleep=self.is_asleep,
            last_meal=self.last_meal,
            last_meal_ago_hours=ago,
            sleeps=self.behaviour.sleeps,
            auto_sleeps=self.behaviour.auto_sleeps,
            auto_wakes=self.behaviour.auto_wakes,
            yawns=self.behaviour.yawns.total,
            attention_nudges=self.behaviour.nudges.total,
        )

    def mood_signal(self) -> MoodSignal:
        """The expression mood: a low need outranks the PAD mood.

        :data:`NEED_MOOD_HINTS` decides (deepest need first); with every need
        comfortable this is simply the PAD-dominant emotion, reason ``""``.
        Sleeping always wins: a sleeping robot looks sleepy.
        """
        if self.is_asleep:
            return MoodSignal(Emotion.TIRED, "asleep")
        candidates = [
            (self.needs.get(need), need, emotion, reason)
            for need, threshold, emotion, reason in NEED_MOOD_HINTS
            if self.needs.get(need) <= threshold
        ]
        if candidates:
            _, _, emotion, reason = min(candidates, key=lambda c: c[0])
            return MoodSignal(emotion, reason)
        return MoodSignal(self.dominant_emotion(), "")

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
            asleep=self.is_asleep,
            mood_reason=self.mood_signal().reason,
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
    "FEED_INTERACTION_IDS",
    "FEED_INTERACTION_PREFIX",
    "FeedResult",
    "FULL_HUNGER",
    "INTERACTION_EFFECTS",
    "INTERACTION_IDS",
    "InteractionEffect",
    "MoodSignal",
    "NEED_MOOD_HINTS",
    "NeedsEvent",
    "NeedsReport",
    "PLEASURE_DECAY_PER_HOUR",
    "SleepResult",
    "feed_interaction_id",
]
