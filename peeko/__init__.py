"""Peeko — a cute, animated desktop robot companion.

Stage 2: basic interaction. Building on Stage 1's animated desktop avatar
(hover, click, double-click and drag reactions on a timer-driven, Qt-free
state machine), the robot now has its full interaction vocabulary and the
real right-click **interaction menu**: Check Status (an honest readout of
live state) and Settings (the configuration Peeko is really running with)
open working windows, while the planned pet actions — Talk, Feed, Pet,
Play, Sleep, Wake Up — are labelled as not implemented and explain which
roadmap stage brings them.

Everything is still rendered from the asset manifest in
``peeko/avatar/assets/`` (Stage 1), so the owner can replace the artwork —
including the new reaction animations — by editing data, never code.

Project foundation (Stage 0) provides config/settings, logging, error
handling, per-OS data directories and the frame of every future subsystem
(ai, voice, emotions, needs, memory, awareness, db, ui). Modules whose
features are not implemented yet say so explicitly and never fake
functionality.
"""

__app_name__ = "Peeko"
__version__ = "0.1.0"
__stage__ = 2
__total_stages__ = 13
