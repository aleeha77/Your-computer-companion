"""Peeko — a cute, animated desktop robot companion.

Stage 0: project foundation. This package provides the modular skeleton:
a frameless, translucent, always-on-top desktop window with a placeholder
avatar shape, config/settings infrastructure, logging, error handling,
and separate submodules for each future subsystem (avatar, ai, voice,
emotions, needs, memory, awareness, db, ui).

Each subsystem module documents its own stage status. Modules whose
features are not implemented yet say so explicitly and never fake
functionality.
"""

__app_name__ = "Peeko"
__version__ = "0.1.0"
__stage__ = 0
__total_stages__ = 13