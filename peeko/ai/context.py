"""The structured context Peeko hands to the AI (Stage 3).

The conversation system receives one JSON-ready block describing Peeko's
state and situation::

    {"emotion": "neutral", "happiness": 50.0, "energy": 80.0,
     "hunger": 80.0, "sleepiness": 10.0, "friendship": 50.0,
     "current_app": null, "recent_interactions": [...], "memory": [...]}

:func:`build_context` is the **seam** later stages plug into. Everything it
needs is an optional argument:

* ``emotional_state`` — a real :class:`peeko.emotions.state.EmotionalState`
  (Stage 6 wires the live mood in; today the caller may pass one, or the
  builder reports the neutral default);
* ``needs`` — a real :class:`peeko.needs.system.PetNeeds` (Stage 7 tick it
  over time; today the documented starting values are used);
* ``active_app_provider`` — a callable returning the active window title
  (Stage 9 app awareness). ``None`` means "unknown", which is what the
  model is told — never a guess;
* ``memory_provider`` — a callable returning remembered facts (Stage 8
  persistent memory). Empty until then;
* ``interactions`` — an :class:`InteractionLog` of what the user and Peeko
  have done recently (real today: clicks, drags, chat turns).

Nothing here talks to the network, the OS or Qt — it is pure data
assembly, so it is trivially testable and safe to call anywhere.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

LOG = logging.getLogger("peeko.ai")

#: How many recent interactions are remembered/sent by default.
DEFAULT_INTERACTION_HISTORY = 8
#: How many remembered facts (Stage 8) are sent by default.
DEFAULT_MEMORY_LIMIT = 8

#: Documented placeholder for "the app does not know what you are doing yet".
UNKNOWN_APP = None

#: Fields :func:`build_context` owns; ``extra`` may never override them.
RESERVED_FIELDS = frozenset({
    "emotion", "happiness", "energy", "hunger", "sleepiness", "friendship",
    "current_app", "recent_interactions", "memory", "placeholders",
})


class InteractionLog:
    """A short, bounded log of what Peeko and the user just did.

    Real data (Stage 3): the avatar records clicks/drags and the chat window
    records turns, so ``recent_interactions`` is never a lie. Bounded on
    purpose — only the tail is interesting to the model.
    """

    def __init__(self, max_entries: int = DEFAULT_INTERACTION_HISTORY) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self.max_entries = int(max_entries)
        self._entries: deque[str] = deque(maxlen=self.max_entries)

    def record(self, text: str) -> None:
        """Add one short, human-readable line (empty text is ignored)."""
        text = (text or "").strip()
        if text:
            self._entries.append(text)

    def entries(self) -> tuple[str, ...]:
        """The remembered lines, oldest first."""
        return tuple(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"InteractionLog(max_entries={self.max_entries}, entries={len(self)})"


@dataclass(frozen=True)
class ChatContext:
    """Peeko's situation at the moment a message is answered.

    All pet-state values are on documented scales: the needs copied from
    :class:`peeko.needs.system.PetNeeds` are ``0..100`` (100 = fully
    satisfied), ``happiness`` derives from the PAD pleasure axis mapped onto
    ``0..100``, and ``sleepiness`` is the inverse of the sleep need.
    """

    emotion: str = "neutral"
    happiness: float = 50.0
    energy: float = 80.0
    hunger: float = 80.0
    sleepiness: float = 10.0
    friendship: float = 50.0
    current_app: str | None = UNKNOWN_APP
    recent_interactions: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = field(default_factory=tuple)
    #: Extra named fields merged into the block as-is (a later stage that has
    #: something the model should know, e.g. the user's chosen nickname).
    #: Kept as sorted key/value pairs so the context stays immutable and
    #: JSON-friendly.
    extra: tuple[tuple[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The JSON-ready context block handed to the model."""
        block: dict[str, Any] = {
            "emotion": self.emotion,
            "happiness": self.happiness,
            "energy": self.energy,
            "hunger": self.hunger,
            "sleepiness": self.sleepiness,
            "friendship": self.friendship,
            "current_app": self.current_app,
            "recent_interactions": list(self.recent_interactions),
            "memory": list(self.memory),
            "placeholders": list(self.placeholders),
        }
        for key, value in self.extra:
            block[str(key)] = value
        return block

    def to_json(self) -> str:
        """Deterministic JSON (sorted keys) — handy in tests and logs."""
        return json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def _emotion_and_happiness(emotional_state: object | None) -> tuple[str, float]:
    """Named emotion + 0..100 happiness from an ``EmotionalState``."""
    if emotional_state is None:
        return "neutral", 50.0  # documented neutral midpoint
    try:
        emotion = emotional_state.nearest_emotion()
        name = getattr(emotion, "value", str(emotion))
        pleasure = max(-1.0, min(1.0, float(emotional_state.pleasure)))
    except (AttributeError, TypeError, ValueError):  # duck-typed seam
        LOG.debug("Unusable emotional_state — using neutral placeholder")
        return "neutral", 50.0
    return str(name), round((pleasure + 1.0) / 2.0 * 100.0, 1)


def _need_entry(needs: object | None, name: str,
                default: float) -> tuple[float, bool]:
    """One need value plus whether it is a placeholder.

    Accepts the real :class:`peeko.needs.system.PetNeeds` (duck-typed via its
    ``Need`` enum) or any mapping with the same key names.
    """
    if needs is None:
        return default, True
    try:
        from peeko.needs.system import Need

        need = Need(name)
        if hasattr(needs, "get"):
            return round(float(needs.get(need)), 1), False
        return round(float(needs[need]), 1), False  # type: ignore[index]
    except (ImportError, KeyError, TypeError, ValueError, AttributeError):
        LOG.debug("No usable %s need — using the documented default", name)
        return default, True


def build_context(
    *,
    emotional_state: object | None = None,
    needs: object | None = None,
    active_app_provider: Callable[[], str | None] | None = None,
    memory_provider: Callable[[], Sequence[str]] | None = None,
    interactions: InteractionLog | Iterable[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> ChatContext:
    """Assemble the structured context block for one AI message.

    Every argument is optional; missing pieces become documented
    placeholders (listed in :attr:`ChatContext.placeholders`) rather than
    invented values, so the model can be told the truth about what Peeko
    does not know yet. ``extra`` adds named fields of the caller's choosing
    (used by later stages that have more to say); it never replaces a
    documented field.
    """
    placeholders: list[str] = []

    emotion, happiness = _emotion_and_happiness(emotional_state)
    if emotional_state is None:
        placeholders.append("emotion")

    from peeko.needs.system import DEFAULT_VALUES, Need

    energy, energy_is_default = _need_entry(
        needs, Need.ENERGY.value, DEFAULT_VALUES[Need.ENERGY]
    )
    hunger, hunger_is_default = _need_entry(
        needs, Need.HUNGER.value, DEFAULT_VALUES[Need.HUNGER]
    )
    sleep, sleep_is_default = _need_entry(
        needs, Need.SLEEP.value, DEFAULT_VALUES[Need.SLEEP]
    )
    friendship, friendship_is_default = _need_entry(
        needs, Need.FRIENDSHIP.value, DEFAULT_VALUES[Need.FRIENDSHIP]
    )
    for name, is_default in (
        ("energy", energy_is_default),
        ("hunger", hunger_is_default),
        ("sleepiness", sleep_is_default),
        ("friendship", friendship_is_default),
    ):
        if is_default:
            placeholders.append(name)

    current_app: str | None = UNKNOWN_APP
    if active_app_provider is not None:
        try:
            value = active_app_provider()
            if value:
                current_app = str(value)
        except Exception:  # noqa: BLE001 - awareness is optional/best-effort
            LOG.debug("Active-app provider failed — reporting unknown", exc_info=True)
    if current_app in (None, ""):
        current_app = UNKNOWN_APP
        placeholders.append("current_app")

    memories: tuple[str, ...] = ()
    if memory_provider is not None:
        try:
            memories = tuple(
                str(item) for item in memory_provider() if str(item).strip()
            )[:DEFAULT_MEMORY_LIMIT]
        except Exception:  # noqa: BLE001 - memory is optional/best-effort
            LOG.debug("Memory provider failed — sending none", exc_info=True)
    if not memories:
        placeholders.append("memory")

    recent: tuple[str, ...] = ()
    if isinstance(interactions, InteractionLog):
        recent = interactions.entries()
    elif interactions is not None:
        recent = tuple(str(item) for item in interactions if str(item).strip())
    if not recent:
        placeholders.append("recent_interactions")

    context = ChatContext(
        emotion=emotion,
        happiness=happiness,
        energy=energy,
        hunger=hunger,
        sleepiness=round(100.0 - sleep, 1),
        friendship=friendship,
        current_app=current_app,
        recent_interactions=recent,
        memory=memories,
        placeholders=tuple(placeholders),
        extra=tuple(
            sorted((str(k), v) for k, v in dict(extra or {}).items()
                   if k not in RESERVED_FIELDS)
        ),
    )
    LOG.debug("AI context: %s", context.to_json())
    return context


__all__ = [
    "ChatContext",
    "DEFAULT_INTERACTION_HISTORY",
    "DEFAULT_MEMORY_LIMIT",
    "InteractionLog",
    "UNKNOWN_APP",
    "build_context",
]
