"""Needs-driven behaviour (Stage 7): yawning, auto-sleep and gentle nudges.

The needs values in :mod:`peeko.needs.system` only become a *character* when
something turns them into behaviour. That is this module — deliberately plain
Python, Qt-free and clock-free so it is fully testable with a fake clock:

* **yawning** — when Peeko's ``sleep`` need falls to
  :data:`YAWN_SLEEP_NEED_THRESHOLD` he gives one sleepy signal (that is
  ``sleepiness`` — ``100 - sleep`` — reaching ``60``); never more than
  :data:`YAWN_MIN_INTERVAL_HOURS` apart and at most :data:`MAX_YAWNS_PER_HOUR`
  per hour;
* **auto-sleep** — when the ``sleep`` need crosses the floor
  :data:`AUTO_SLEEP_SLEEP_NEED_THRESHOLD` Peeko really does go to sleep: the
  engine stops decaying the sleep/energy needs and starts restoring them
  (see :func:`peeko.needs.system.PetNeeds.tick`). He wakes on his own once he
  is rested (:data:`AUTO_WAKE_SLEEP_NEED_THRESHOLD`) or too hungry
  (:data:`AUTO_WAKE_HUNGER_THRESHOLD`), and auto-sleep cannot fire again for
  :data:`AUTO_SLEEP_MIN_INTERVAL_HOURS` after that — so it can never loop
  annoyingly;
* **attention-seeking** — when the ``boredom`` need falls to
  :data:`BOREDOM_ATTENTION_THRESHOLD` and Peeko is idling, he may ask for
  attention, but at most :data:`MAX_ATTENTION_NUDGES_PER_HOUR` times an hour
  and never closer together than :data:`ATTENTION_MIN_INTERVAL_HOURS`.

Every threshold and limit is a documented constant here (not an ``.env``
setting) so the behaviour is inspectable and testable. There is no quiet mode
yet — autonomous behaviour is Stage 10 — so the limits are deliberately
conservative; :class:`NeedsBehaviour` is where Stage 10 will hook a quiet
mode in.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from peeko.needs.system import Need, PetNeeds

# --------------------------------------------------------------------------- #
# Yawning
# --------------------------------------------------------------------------- #
#: Yawn when the ``sleep`` need is at or below this (0..100). Sleepiness is
#: ``100 - sleep``, so this is "sleepiness has reached 60/100 or more". The
#: brief said "sleepiness low"; on this scale a *low* sleep value is the sleepy
#: state, and the threshold is documented here so there is no ambiguity.
YAWN_SLEEP_NEED_THRESHOLD = 40.0
#: At least this many simulated hours between two yawns.
YAWN_MIN_INTERVAL_HOURS = 0.5
#: Never more than this many yawns in any simulated hour.
MAX_YAWNS_PER_HOUR = 2

# --------------------------------------------------------------------------- #
# Auto-sleep / auto-wake
# --------------------------------------------------------------------------- #
#: Go to sleep on its own when the ``sleep`` need falls to/below this floor.
AUTO_SLEEP_SLEEP_NEED_THRESHOLD = 15.0
#: Wake up on its own once the ``sleep`` need is back at/above this.
AUTO_WAKE_SLEEP_NEED_THRESHOLD = 85.0
#: …or when hunger gets this low: too hungry to keep sleeping.
AUTO_WAKE_HUNGER_THRESHOLD = 10.0
#: After a wake-up, no automatic sleep for this many simulated hours. This is
#: the "never loops annoyingly" limit: a nap cannot be followed immediately by
#: another nap, and a deliberate :meth:`NeedsBehaviour.begin_sleep` is not
#: limited at all (the user asked for it).
AUTO_SLEEP_MIN_INTERVAL_HOURS = 1.0

# --------------------------------------------------------------------------- #
# Boredom / attention-seeking
# --------------------------------------------------------------------------- #
#: Ask for attention when the ``boredom`` need falls to/below this.
BOREDOM_ATTENTION_THRESHOLD = 30.0
#: At most this many nudges per simulated hour.
MAX_ATTENTION_NUDGES_PER_HOUR = 2
#: …and never closer together than this many simulated hours.
ATTENTION_MIN_INTERVAL_HOURS = 0.25


@dataclass
class RateLimit:
    """A tiny "at most N times per window, and not too close together" limiter.

    Works purely on the caller's simulated clock (the engine's
    ``hours_elapsed``), so it needs no real time and is trivially testable.
    """

    max_count: int
    per_hours: float = 1.0
    min_interval_hours: float = 0.0
    #: Every event this limiter has ever allowed (diagnostics; pruning the
    #: window never loses this count).
    total: int = 0
    _times: list[float] = field(default_factory=list)

    def allow(self, now_hours: float) -> bool:
        """Would an event at ``now_hours`` be within the limit?

        Consumes a slot when it returns ``True``; a ``False`` verdict leaves
        the limiter untouched, so a refused event is simply retried later.
        """
        window_start = now_hours - self.per_hours
        self._times = [t for t in self._times if t > window_start]
        if len(self._times) >= self.max_count:
            return False
        if self._times and (
            now_hours - max(self._times) < self.min_interval_hours
        ):
            return False
        self._times.append(now_hours)
        self.total += 1
        return True

    @property
    def count(self) -> int:
        """How many events were allowed inside the current window."""
        return len(self._times)


@dataclass(frozen=True)
class NeedsEvent:
    """Something Peeko's needs made him do, at one moment in simulated time.

    ``kind`` is one of :data:`EVENT_KINDS`; ``summary`` is the one-line,
    user-facing description (it goes into the interaction log and Check
    Status, so it must stay truthful).
    """

    kind: str
    summary: str
    need: str = ""


#: Every event kind the needs subsystem can raise.
EVENT_YAW = "yawn"
EVENT_AUTO_SLEEP = "auto_sleep"
EVENT_AUTO_WAKE = "auto_wake"
EVENT_ATTENTION = "attention"
EVENT_KINDS: tuple[str, ...] = (
    EVENT_YAW, EVENT_AUTO_SLEEP, EVENT_AUTO_WAKE, EVENT_ATTENTION,
)


@dataclass
class NeedsBehaviour:
    """Turns need values + elapsed simulated time into a few honest events.

    The engine owns one of these, feeds it the live :class:`PetNeeds` and the
    simulated hour count, and receives :class:`NeedsEvent` values back. It
    holds only what it needs to know: whether Peeko is asleep, when he woke
    up last (the auto-sleep cooldown) and the two rate limiters.
    """

    asleep: bool = False
    #: Simulated hour before which auto-sleep may not fire again.
    next_auto_sleep_hour: float = 0.0
    yawns: RateLimit = field(
        default_factory=lambda: RateLimit(
            MAX_YAWNS_PER_HOUR, 1.0, YAWN_MIN_INTERVAL_HOURS
        )
    )
    nudges: RateLimit = field(
        default_factory=lambda: RateLimit(
            MAX_ATTENTION_NUDGES_PER_HOUR, 1.0, ATTENTION_MIN_INTERVAL_HOURS
        )
    )
    #: Counters for Check Status.
    sleeps: int = 0        # every sleep period this run (deliberate or not)
    auto_sleeps: int = 0   # the ones Peeko started himself
    auto_wakes: int = 0

    # ------------------------------------------------------------------ #
    # Deliberate sleep/wake (the Sleep / Wake Up menu entries)
    # ------------------------------------------------------------------ #
    def begin_sleep(self, now_hours: float) -> bool:
        """Put Peeko to sleep deliberately. ``False`` when already asleep."""
        if self.asleep:
            return False
        self.asleep = True
        self.sleeps += 1
        self.next_auto_sleep_hour = now_hours + AUTO_SLEEP_MIN_INTERVAL_HOURS
        return True

    def end_sleep(self, now_hours: float) -> bool:
        """Wake Peeko up. ``False`` when he was not asleep."""
        if not self.asleep:
            return False
        self.asleep = False
        self.next_auto_sleep_hour = now_hours + AUTO_SLEEP_MIN_INTERVAL_HOURS
        return True

    # ------------------------------------------------------------------ #
    # Time-driven events
    # ------------------------------------------------------------------ #
    def events(
        self, needs: PetNeeds, now_hours: float, *, idle: bool = True
    ) -> list[NeedsEvent]:
        """Every event the needs justify at ``now_hours`` (usually none).

        :param idle: whether Peeko is idling (not being used). Attention
            nudges are only raised while he is idle — a robot that nags while
            you are chatting would be exactly the spam this stage forbids.
        """
        out: list[NeedsEvent] = []
        if self.asleep:
            rested = needs.get(Need.SLEEP) >= AUTO_WAKE_SLEEP_NEED_THRESHOLD
            hungry = needs.get(Need.HUNGER) <= AUTO_WAKE_HUNGER_THRESHOLD
            if rested or hungry:
                reason = (
                    "he is rested" if rested
                    else "he is too hungry to keep sleeping"
                )
                self.asleep = False
                self.auto_wakes += 1
                self.next_auto_sleep_hour = (
                    now_hours + AUTO_SLEEP_MIN_INTERVAL_HOURS
                )
                out.append(
                    NeedsEvent(
                        EVENT_AUTO_WAKE,
                        f"Peeko woke up on his own — {reason}.",
                        need=Need.SLEEP.value,
                    )
                )
            # Nothing else happens while he is asleep: no yawning, no nudges.
            return out

        if (
            needs.get(Need.SLEEP) <= AUTO_SLEEP_SLEEP_NEED_THRESHOLD
            and now_hours >= self.next_auto_sleep_hour
        ):
            self.asleep = True
            self.sleeps += 1
            self.auto_sleeps += 1
            out.append(
                NeedsEvent(
                    EVENT_AUTO_SLEEP,
                    "Peeko was too sleepy to stay awake and fell asleep.",
                    need=Need.SLEEP.value,
                )
            )
            return out

        if (
            needs.get(Need.SLEEP) <= YAWN_SLEEP_NEED_THRESHOLD
            and self.yawns.allow(now_hours)
        ):
            out.append(
                NeedsEvent(
                    EVENT_YAW,
                    "Peeko yawned — he is getting sleepy "
                    f"(sleep {needs.get(Need.SLEEP):.0f}/100, so sleepiness "
                    f"{100 - needs.get(Need.SLEEP):.0f}/100).",
                    need=Need.SLEEP.value,
                )
            )

        if (
            idle
            and needs.get(Need.BOREDOM) <= BOREDOM_ATTENTION_THRESHOLD
            and self.nudges.allow(now_hours)
        ):
            out.append(
                NeedsEvent(
                    EVENT_ATTENTION,
                    "Peeko is bored and asked for attention — a single gentle "
                    "nudge (at most "
                    f"{MAX_ATTENTION_NUDGES_PER_HOUR} an hour).",
                    need=Need.BOREDOM.value,
                )
            )
        return out


__all__ = [
    "ATTENTION_MIN_INTERVAL_HOURS",
    "AUTO_SLEEP_MIN_INTERVAL_HOURS",
    "AUTO_SLEEP_SLEEP_NEED_THRESHOLD",
    "AUTO_WAKE_HUNGER_THRESHOLD",
    "AUTO_WAKE_SLEEP_NEED_THRESHOLD",
    "BOREDOM_ATTENTION_THRESHOLD",
    "EVENT_ATTENTION",
    "EVENT_AUTO_SLEEP",
    "EVENT_AUTO_WAKE",
    "EVENT_KINDS",
    "EVENT_YAW",
    "MAX_ATTENTION_NUDGES_PER_HOUR",
    "MAX_YAWNS_PER_HOUR",
    "NeedsBehaviour",
    "NeedsEvent",
    "RateLimit",
    "YAWN_MIN_INTERVAL_HOURS",
    "YAWN_SLEEP_NEED_THRESHOLD",
]
