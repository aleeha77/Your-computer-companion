"""Voice output (text-to-speech) for Peeko.

Stage 5: NOT IMPLEMENTED YET.

Planned behaviour: speak Peeko's replies using the engine selected via
``PEEKO_TTS_ENGINE`` / ``PEEKO_TTS_VOICE``, playing audio without
blocking the UI thread. The interface below is the Stage 5 contract;
methods raise ``NotImplementedError`` until implemented.
"""

from __future__ import annotations


class SpeechSynthesizer:
    """Planned text -> speech synthesizer (Stage 5)."""

    def __init__(self, engine: str = "", voice: str = "") -> None:
        self.engine = engine
        self.voice = voice

    def speak(self, text: str) -> None:
        """Play ``text`` aloud.

        .. note:: Stage 5 — not implemented yet.
        """
        raise NotImplementedError(
            "SpeechSynthesizer.speak is not implemented until Stage 5 (TTS)."
        )