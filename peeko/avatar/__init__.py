"""Avatar subsystem package.

Stage 1: the on-screen robot companion — a frameless, translucent,
always-on-top window running a timer-driven animation state machine over
the asset-manifest-driven artwork in :mod:`peeko.avatar.assets`.

Key modules:

* :mod:`peeko.avatar.manifest` — asset manifest parsing/validation (data-only
  artwork pipeline: drop SVGs + edit ``manifest.json``, no code changes).
* :mod:`peeko.avatar.state_machine` — timer-driven animation states
  (idle, blink, look_*, click, dragging), Qt-free and unit-testable.
* :mod:`peeko.avatar.assets` — SVG layer rendering to pixmaps.
* :mod:`peeko.avatar.renderer` — frame compositing (shadow + layers).
* :mod:`peeko.avatar.widget` — the desktop window tying it all together.

Artwork lives in :file:`peeko/avatar/assets/` — see its ``README.md`` for
exactly how the owner swaps in custom art without touching code.
"""

from peeko.avatar.widget import AvatarWindow

__all__ = ["AvatarWindow"]