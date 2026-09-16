"""Voice input (speech-to-text) for Peeko.

Stage 4: NOT IMPLEMENTED YET.

Planned behaviour: capture microphone audio off the UI thread and
transcribe it (local engine or optional cloud service configured via
``PEEKO_VOICE_INPUT_ENGINE``). The interface below is the Stage 4
contract; methods raise ``NotImplementedError`` until implemented.
"""

from __future__ import annotations


class SpeechRecognizer:
    """Planned microphone -> text recognizer (Stage 4)."""

    def __init__(self, engine: str = "") -> None:
        self.engine = engine

    def transcribe(self, audio_path_or_data) -> str:
        """Transcribe audio to text.

        .. note:: Stage 4 — not implemented yet.
        """
        raise NotImplementedError(
            "SpeechRecognizer.transcribe is not implemented until Stage 4 "
            "(voice input)."
        )