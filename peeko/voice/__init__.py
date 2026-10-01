"""Voice subsystem: speech-to-text in (Stage 4), text-to-speech out (Stage 5).

**Stage 4 — voice input is implemented.** Press **Mic** in the chat window and
Peeko listens through your microphone, shows a listening animation while it
does, and puts the transcribed words in the message box (nothing is sent
until you press Send). How it is put together:

* :mod:`peeko.voice.audio` — microphone capture behind an injectable
  :class:`~peeko.voice.audio.AudioSource`. The audio library (``sounddevice``)
  is imported **lazily** and is not a hard dependency: on a machine without
  it (or without a microphone) Peeko starts normally and the mic reports an
  honest reason instead of crashing.
* :mod:`peeko.voice.providers` — the speech-to-text seam: one real
  OpenAI-compatible ``/audio/transcriptions`` provider (stdlib HTTP, model and
  base URL from ``.env``) plus a mock used by the tests.
* :mod:`peeko.voice.input` — :class:`~peeko.voice.input.SpeechRecognizer`, the
  "microphone in, words out" engine the UI uses.
* :mod:`peeko.voice.worker` — the Qt bridge that keeps capture and HTTP off
  the UI thread, with cancellation.
* :mod:`peeko.voice.errors` — honest, key-free failure messages.

Turning it on and off

``PEEKO_VOICE_ENABLED=1`` (the default) shows a working Mic button in the chat
window; ``PEEKO_VOICE_ENABLED=0`` disables the microphone completely — Peeko
then never opens an audio device, and says why in the chat window.

Privacy

Audio is captured in memory, sent **only** to the speech-to-text endpoint you
configure, and never written to disk. Transcripts are not stored anywhere
(persistent memory is Stage 8).

**Stage 5 — text-to-speech is not implemented yet**: see
:mod:`peeko.voice.output`, whose ``SpeechSynthesizer`` raises
``NotImplementedError`` naming its stage. No fake buttons.
"""

from __future__ import annotations

from peeko.voice.audio import (
    AudioClip,
    AudioSource,
    SoundDeviceAudioSource,
    UnavailableAudioSource,
    capture_clip,
    default_audio_source,
)
from peeko.voice.errors import (
    STTProviderError,
    STTResponseError,
    STTTimeoutError,
    VoiceCaptureError,
    VoiceConfigError,
    VoiceError,
    VoicePermissionError,
    VoiceUnavailableError,
)
from peeko.voice.input import SpeechRecognizer, build_recognizer
from peeko.voice.providers import (
    DEFAULT_MODEL,
    MockTranscriptionProvider,
    OpenAITranscriptionProvider,
    SpeechToTextProvider,
    build_transcriber,
)

__all__ = [
    "AudioClip",
    "AudioSource",
    "DEFAULT_MODEL",
    "MockTranscriptionProvider",
    "OpenAITranscriptionProvider",
    "STTProviderError",
    "STTResponseError",
    "STTTimeoutError",
    "SoundDeviceAudioSource",
    "SpeechRecognizer",
    "SpeechToTextProvider",
    "UnavailableAudioSource",
    "VoiceCaptureError",
    "VoiceConfigError",
    "VoiceError",
    "VoicePermissionError",
    "VoiceUnavailableError",
    "build_recognizer",
    "build_transcriber",
    "capture_clip",
    "default_audio_source",
]
