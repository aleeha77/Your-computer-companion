"""Peeko — a cute, animated desktop robot companion.

Stage 1: desktop robot avatar. The on-screen creature is now a real
animated character: a frameless, translucent, always-on-top window running
a timer-driven animation state machine (idle bob, blinking, glances at the
cursor, click reaction, dragging), rendered entirely from the asset
manifest in ``peeko/avatar/assets/`` — the owner can replace the artwork
by dropping in new SVGs and editing ``manifest.json``, with zero code
changes.

Project foundation (Stage 0) still provides config/settings, logging,
error handling, per-OS data directories and the frame of every future
subsystem (ai, voice, emotions, needs, memory, awareness, db, ui).
Modules whose features are not implemented yet say so explicitly and never
fake functionality.
"""

__app_name__ = "Peeko"
__version__ = "0.1.0"
__stage__ = 1
__total_stages__ = 13