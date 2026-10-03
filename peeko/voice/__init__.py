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

**Stage 5 — voice output is implemented.** Peeko now *speaks* its chat replies
out loud (a **Speak** button in the chat window replays one) while the robot
plays a sustained talking animation. How it is put together:

* :mod:`peeko.voice.output_providers` — the speech seam: one real
  OpenAI-compatible ``POST /audio/speech`` provider (stdlib HTTP; model,
  voice, speed, format and base URL from ``.env``) plus a mock used by the
  tests. The key is the same ``PEEKO_AI_API_KEY`` the chat uses.
* :mod:`peeko.voice.player` — playback behind an injectable
  :class:`~peeko.voice.player.AudioPlayer`. ``sounddevice`` is imported
  **lazily** here too, and audio is decoded from WAV with the standard
  library alone, so speaking adds **no new dependency**. No audio device (or
  no library) is an honest message, not a crash — Peeko simply stays silent.
* :mod:`peeko.voice.output` — :class:`~peeko.voice.output.SpeechSynthesizer`,
  the "text in, sound out" engine the UI uses.

Shared by both directions:

* :mod:`peeko.voice.worker` — the Qt bridge that keeps capture, synthesis,
  HTTP and playback off the UI thread, with cancellation; a reply cannot
  freeze the robot while it waits to be voiced.
* :mod:`peeko.voice.errors` — honest, key-free failure messages.

Turning it on and off

``PEEKO_VOICE_ENABLED=1`` (the default) shows a working Mic button in the chat
window; ``PEEKO_VOICE_ENABLED=0`` disables the microphone completely — Peeko
then never opens an audio device, and says why in the chat window.
``PEEKO_TTS_ENABLED`` works the same way for speaking and defaults to **0
(off)**: Peeko only talks once you ask it to, so a fresh install is quiet and
the test suite never reaches for a sound card.

Privacy

* microphone audio is captured in memory, sent **only** to the speech-to-text
  endpoint you configure, and never written to disk;
* a reply is spoken only through the text-to-speech endpoint you configure,
  and the audio that comes back goes straight to your speakers — nothing is
  saved, and the clip keeps only a character count, never the text;
* transcripts and replies are not stored anywhere (persistent memory is
  Stage 8).
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
    TTSPlaybackError,
    TTSProviderError,
    TTSResponseError,
    TTSTimeoutError,
    VoiceCaptureError,
    VoiceConfigError,
    VoiceError,
    VoicePermissionError,
    VoiceUnavailableError,
)
from peeko.voice.input import SpeechRecognizer, build_recognizer
from peeko.voice.output import SpeechSynthesizer, build_synthesizer
from peeko.voice.output_providers import (
    DEFAULT_RESPONSE_FORMAT,
    DEFAULT_VOICE,
    MockSpeechProvider,
    OpenAISpeechProvider,
    SpeechClip,
    SpeechProvider,
    build_speaker,
)
from peeko.voice.player import (
    AudioPlayer,
    SoundDevicePlayer,
    UnavailablePlayer,
    default_player,
)
from peeko.voice.providers import (
    DEFAULT_MODEL,
    MockTranscriptionProvider,
    OpenAITranscriptionProvider,
    SpeechToTextProvider,
    build_transcriber,
)

__all__ = [
    "AudioClip",
    "AudioPlayer",
    "AudioSource",
    "DEFAULT_MODEL",
    "DEFAULT_RESPONSE_FORMAT",
    "DEFAULT_VOICE",
    "MockSpeechProvider",
    "MockTranscriptionProvider",
    "OpenAISpeechProvider",
    "OpenAITranscriptionProvider",
    "STTProviderError",
    "STTResponseError",
    "STTTimeoutError",
    "SpeechClip",
    "SpeechProvider",
    "SpeechRecognizer",
    "SpeechSynthesizer",
    "SpeechToTextProvider",
    "SoundDeviceAudioSource",
    "SoundDevicePlayer",
    "TTSPlaybackError",
    "TTSProviderError",
    "TTSResponseError",
    "TTSTimeoutError",
    "UnavailableAudioSource",
    "UnavailablePlayer",
    "VoiceCaptureError",
    "VoiceConfigError",
    "VoiceError",
    "VoicePermissionError",
    "VoiceUnavailableError",
    "build_recognizer",
    "build_speaker",
    "build_synthesizer",
    "build_transcriber",
    "capture_clip",
    "default_audio_source",
    "default_player",
]
