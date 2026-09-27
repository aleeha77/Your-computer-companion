"""Peeko — a cute, animated desktop robot companion.

Stage 3: AI chat. The robot can now hold a typed conversation in its own
window (right-click → **Talk**), while it keeps floating on the desktop.
The conversation system lives in :mod:`peeko.ai` and is deliberately
separate from the desktop/avatar system: it receives a structured context
about Peeko (mood, needs, what the user just did) and answers with a
validated object — text, an emotion from a controlled list, an animation
from a controlled list, and an action that must be ``null``. Model output is
data, never instructions: nothing an AI reply says can run a command or
touch the operating system. The only thing a reply can change is what the
chat window shows and which existing animation the robot plays. Requests run
off the UI thread, a missing API key is reported honestly instead of faked,
and the owner supplies their own key through ``PEEKO_AI_API_KEY``.

Stage 2: basic interaction. Building on Stage 1's animated desktop avatar
(hover, click, double-click and drag reactions on a timer-driven, Qt-free
state machine), the robot has its full interaction vocabulary and the real
right-click **interaction menu**: Check Status (an honest readout of live
state) and Settings (the configuration Peeko is really running with) open
working windows, while the remaining planned pet actions — Feed, Pet, Play,
Sleep, Wake Up — are labelled as not implemented and explain which roadmap
stage brings them.

Everything is still rendered from the asset manifest in
``peeko/avatar/assets/`` (Stage 1), so the owner can replace the artwork —
including the reaction animations — by editing data, never code.

Project foundation (Stage 0) provides config/settings, logging, error
handling, per-OS data directories and the frame of every future subsystem
(ai, voice, emotions, needs, memory, awareness, db, ui). Modules whose
features are not implemented yet say so explicitly and never fake
functionality.
"""

__app_name__ = "Peeko"
__version__ = "0.1.0"
__stage__ = 3
__total_stages__ = 13
