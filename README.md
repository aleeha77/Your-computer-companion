# Peeko — Desktop Robot Companion

A cute, animated robot companion that lives on your desktop: a transparent,
draggable, always-on-top creature with personality. Built as a real,
maintainable, portable **Python desktop application** (PySide6/Qt) — modular,
privacy-first, no cloud infrastructure. You configure optional AI/voice API
keys yourself via a local `.env` file and run it on your own machine.

> **Current development stage: Stage 6 of 13 (emotions —
> completed).**
> See [Current development stage](#current-development-stage) below.

---

## Features

- ✅ **Animated desktop avatar**: a small robot that lives directly on your
  desktop — frameless, translucent, always-on-top, draggable anywhere.
- ✅ **Alive-looking behaviour** (Stage 1): a gentle idle bob, natural-ish
  irregular blinking, occasional glances left/right/up/down, glances toward
  the mouse cursor when it hovers nearby, a happy squash-and-bounce when
  clicked, and a "being carried" wiggle while dragged.
- ✅ **Reacting to you** (Stage 2): a subtle "oh!" when the pointer hovers
  over it, a bigger winkier bounce on double-click, and a quick "huh?"
  head-shake when you pick something from the menu that is not built yet.
  Hovering never steals a click and never interrupts a drag.
- ✅ **AI chat** (Stage 3): right-click → **Talk** opens a real chat window
  (its own window — the robot keeps floating). Type a message, press Enter,
  watch a "Peeko is thinking…" state while the answer is on its way, and see
  Peeko's reply in the transcript. Peeko answers with a defined, cute/warm/
  playful personality, and the emotion + animation in each reply drive the
  robot's *existing* animations (unknown names fall back to idle).
  Conversation requests run off the UI thread, so the robot never freezes.
- ✅ **Honest about AI configuration** (Stage 3): with no API key the chat
  window shows a banner — *"AI not configured — set `PEEKO_AI_API_KEY` in
  `.env`"* — and sending a message says the same thing instead of inventing
  an answer. Network errors, timeouts and unreadable replies are reported in
  plain words; nothing is ever faked.
- ✅ **Safe AI output** (Stage 3): the AI returns one JSON object
  (`response`, `emotion`, `animation`, `action`). Emotion, animation and
  action are validated against **hard-coded allowed lists**, and the only
  legal action is `null`, so nothing an AI reply says can run a command or
  touch your computer. The only things a reply can change are the text in the
  chat window and which existing animation the robot plays.
- ✅ **Voice input — talk instead of typing** (Stage 4): click **Mic** in the
  chat window and speak. Peeko records through your microphone, asks an
  OpenAI-compatible speech-to-text endpoint (OpenAI Whisper, a gateway, or a
  local server) for the words, and drops the transcript into the message box —
  **nothing is sent to Peeko until you press Send**, so you can fix a misheard
  word first. Click Mic again (or close the window) to stop; a recording stops
  itself after `PEEKO_VOICE_MAX_SECONDS`. While Peeko listens the robot plays a
  sustained "ears open" listening pose and the window shows
  *"Listening… speak now…"*.
- ✅ **Voice input is honest and private** (Stage 4): every failure —
  no API key, no microphone, permission refused, the OS refusing access, a
  capture error, an HTTP error, a timeout, an unreadable reply — is reported in
  plain words in the transcript, and Peeko **never invents a transcript** (a
  cancelled or silent capture says nothing at all). Your audio is held in memory
  only (`capture_clip` returns a clip; nothing in the voice package writes a
  file) and is uploaded to exactly one place: the endpoint you configured.
  `PEEKO_VOICE_ENABLED=0` switches the microphone off completely — Peeko then
  never opens an audio device and the Mic button explains why instead of
  pretending.
- ✅ **Peeko talks back — text-to-speech** (Stage 5): press **Speak** in the
  chat window to hear Peeko's last reply again, or just send a message — when
  the voice is on, every reply is spoken as it arrives. The whole turn is:
  reply text → one OpenAI-compatible `/audio/speech` request (OpenAI, a
  gateway, or a local speech server) → real audio decoded in memory → your
  speakers, while the robot plays a sustained "talking" pose and the window
  shows *"Peeko is speaking…"* and turns the same button into **Stop**.
  Speech is **off by default** (`PEEKO_TTS_ENABLED=1` to hear it), so a fresh
  install stays quiet — and while it is off the Speak button is **hidden
  rather than dead**, with a hint naming the variable.
- ✅ **Speaking is honest about its limits too** (Stage 5): engine, voice,
  model, speed, volume, base URL and timeout all come from `.env`; a missing
  key, no audio output device, an HTTP error, a timeout, an unreadable audio
  reply, a cancelled playback and a switched-off voice each produce their own
  plain-language note in the transcript — **never fake speech**. Nothing is
  played unless audio really came back, the audio is never written to disk,
  and synthesis plus playback run on a worker thread so the robot keeps
  animating while Peeko talks.
- ✅ **Peeko has feelings — a live emotion engine** (Stage 6): one PAD
  (pleasure–arousal–dominance) mood plus six `0..100` stats — **happiness,
  energy, hunger, boredom, sleepiness, friendship**. Left alone, Peeko drifts
  back toward neutral (pleasure decays slowest, arousal fastest) and the needs
  decay on their own documented clock; every value is clamped, so nothing can
  run away. The drift runs on a `QTimer` callback, never on the UI thread, so
  the robot keeps animating.
- ✅ **Pet and Play are real interactions now** (Stage 6): pick *Pet* or *Play*
  from the right-click menu and Peeko's *live* mood and needs change — petting
  lifts happiness and friendship, playing is fun and exciting but costs energy
  and makes him hungrier. Those two entries have lost their "not implemented"
  label because they no longer are; *Feed*, *Sleep* and *Wake Up* keep theirs
  until the Stage 7 needs simulation.
- ✅ **The AI sees Peeko's real mood** (Stage 6): the structured context sent
  with every chat message now carries the live emotion, happiness, energy,
  hunger, sleepiness and friendship instead of documented placeholders, and one
  completed reply counts as the documented *talk* interaction that nudges the
  mood. Only memory and app awareness remain honest placeholders (Stages 8–9).
- ✅ **Feelings drive the *existing* artwork** (Stage 6): the dominant emotion
  maps onto animations the loaded manifest can really play (happy → the
  squash-and-bounce, excited → the bigger double-click bounce, tired → a blink,
  sad → a downward glance, …). A mood the current artwork cannot show changes
  nothing at all rather than inventing an animation — replace the artwork and
  only one table in the code changes.
- ✅ **Check Status shows the mood** (Stage 6): the live readout now lists the
  dominant emotion and all six stats, and says plainly that they are kept in
  memory for this run until Stage 8 adds persistence.
- ✅ **Voice input never freezes the robot** (Stage 4): recording and the HTTP
  round trip both run on a worker thread, so the animation keeps ticking, you
  can keep typing and you can close the window while it listens.
- ✅ **No audio hard dependency** (Stage 4): the audio library (`sounddevice`)
  is optional and imported lazily. Without it — or on a machine with no
  microphone — Peeko still starts, the robot still animates, and pressing Mic
  says exactly what is missing.
- ✅ **Right-click interaction menu** (Stage 2): *Talk*, *Feed*, *Pet*,
  *Play*, *Sleep*, *Wake Up*, *Check Status…*, *Settings…* and *Quit* — all
  in their final places. Entries whose feature arrives in a later stage are
  labelled `— Stage N (not implemented)` right in the menu and answer with a
  plain explanation instead of a fake window. **Talk**, **Check Status** and
  **Settings** work today (see below).
- ✅ **Check Status** (Stage 2): a live readout — version and stage, the
  current animation/state and available reactions, how many animations the
  loaded artwork manifest contains, the canvas size and layer order, where
  the artwork is loaded from, plus an honest list of what works today and
  what does not.
- ✅ **Settings viewer** (Stage 2): a **read-only** window showing the
  configuration Peeko is actually running with (log level, data/config/log
  directories, database file, artwork folder). Editing settings from the UI
  is not implemented yet; the window says so and points at `.env` instead.
- ✅ **Swap-in-your-own artwork**: the whole character is described by a
  data manifest (`peeko/avatar/assets/manifest.json`) plus SVG layers — no
  code changes to replace the placeholder robot with your own art. See
  [Avatar artwork](#avatar-artwork--swap-in-your-own-robot).
- ✅ **Obvious quit affordances**: right-click menu → *Quit*,
  `Ctrl+Q`, window close, or `Ctrl+C` in the terminal — all quit cleanly.
- ✅ **Configuration**: environment variables + optional `.env` file
  (python-dotenv), with sane defaults and per-OS data directories
  (platformdirs). No machine-specific hard-coded paths.
- ✅ **Logging**: console + rotating file log in your user data directory;
  log level set via `PEEKO_LOG_LEVEL`; secrets are never logged.
- ✅ **Error handling**: global exception hook logs the traceback and shows a
  friendly dialog before exiting; a broken artwork manifest fails loudly at
  startup with a readable list of every problem instead of drawing a broken
  robot.
- ✅ **Never blocks the UI**: animation runs on `QTimer` callbacks (plus a
  Qt-free, unit-tested state machine); menus and dialogs open
  **asynchronously** (`show()`), never with a nested event loop, so the robot
  keeps animating while a window is open. No sleeps anywhere.
- ✅ **Headless smoke test**: `PEEKO_SMOKE_TEST=1` auto-quits ~2 s after
  launch with exit code 0, so CI/headless boxes can verify the whole app.
- 🔜 **Coming in later stages**: virtual-pet needs that drive behaviour,
  persistent memory, app awareness, autonomous life, polish, packaging, final
  testing.

## Tech stack

| Piece        | Choice                                             |
| ------------ | -------------------------------------------------- |
| Language     | Python ≥ 3.10                                      |
| GUI          | PySide6 (Qt 6)                                     |
| Config       | python-dotenv + stdlib `os.environ`                |
| Paths        | platformdirs (OS-correct data/config dirs)         |
| Persistence  | SQLite (stdlib `sqlite3`, wired up in `peeko.db`)  |
| Tests        | pytest                                             |
| Packaging    | setuptools (PEP 621 `pyproject.toml`)              |

## Installation

Requirements: **Python 3.10+** and `pip`.

```bash
# 1. Get the code
git clone https://github.com/aleeha77/Your-computer-companion.git
cd Your-computer-companion

# 2. (Recommended) create a virtual environment — Windows:
python -m venv .venv
.venv\Scripts\activate
# — macOS/Linux:
# python3 -m venv .venv
# source .venv/bin/activate

# 3. Install Peeko (gets PySide6, python-dotenv, platformdirs)
pip install .

# 4. (Optional) dev tools for running the tests
pip install .[dev]
```

> **Linux note**: Qt needs a few system libraries. On headless servers use
> `QT_QPA_PLATFORM=offscreen` (see below); on a desktop, install your
> distro's Qt/GL packages if the window fails to open (e.g. Debian/Ubuntu:
> `sudo apt install libegl1 libfontconfig1 libdbus-1-3 libxkbcommon0`).

## Environment variables

Copy the template and edit:

```bash
cp .env.example .env   # optional — Peeko works with defaults alone
```

| Variable                | Stage | Purpose                                                   | Default         |
| ----------------------- | ----- | --------------------------------------------------------- | --------------- |
| `PEEKO_LOG_LEVEL`       | 0     | Log verbosity (`CRITICAL`/`ERROR`/`WARNING`/`INFO`/`DEBUG`) | `INFO`        |
| `PEEKO_SMOKE_TEST`      | 0     | Auto-quit ~2 s after startup (headless verification)      | `0` (off)       |
| `PEEKO_AVATAR_ASSETS_DIR` | 1   | Folder with your own `manifest.json` + `layers/`          | packaged art    |
| `PEEKO_DATA_DIR`        | 0     | Override user data directory (defaults: OS convention)    | platformdirs    |
| `PEEKO_CONFIG_DIR`      | 0     | Override user config directory                            | platformdirs    |
| `PEEKO_LOG_DIR`         | 0     | Override log directory (default `<data_dir>/logs`)        | platformdirs    |
| `PEEKO_AI_PROVIDER`     | 3     | AI provider: `openai` or `openai-compatible`               | `openai`        |
| `PEEKO_AI_MODEL`        | 3     | Chat model name, e.g. `gpt-4o-mini` (needed to chat)       | *(empty)*       |
| `PEEKO_AI_API_KEY`      | 3     | **Secret** — your AI API key (needed to chat; never logged) | *(empty)*       |
| `PEEKO_AI_BASE_URL`     | 3     | API root; any OpenAI-compatible endpoint (or a local model) | `https://api.openai.com/v1` |
| `PEEKO_AI_TIMEOUT_S`    | 3     | How long to wait for an answer, in seconds                 | `30`            |
| `PEEKO_VOICE_ENABLED`   | 4     | Master switch for the microphone (`0` = never open a device) | `1` (on)      |
| `PEEKO_VOICE_INPUT_ENGINE` | 4  | Voice input engine: `openai` / `openai-compatible` (`whisper` is an alias) | `openai-compatible` |
| `PEEKO_STT_MODEL`       | 4     | Transcription model, e.g. `whisper-1`                      | engine default  |
| `PEEKO_STT_BASE_URL`    | 4     | Speech-to-text API root (falls back to `PEEKO_AI_BASE_URL`) | `PEEKO_AI_BASE_URL`, then engine default |
| `PEEKO_VOICE_TIMEOUT_S` | 4     | How long to wait for a transcription, in seconds           | `60`            |
| `PEEKO_VOICE_MAX_SECONDS` | 4   | Hard cap on one recording, in seconds                      | `30`            |
| `PEEKO_TTS_ENABLED`     | 5     | Master switch for Peeko's voice (`1` = speak replies)      | `0` (off)       |
| `PEEKO_TTS_ENGINE`      | 5     | Speech engine: `openai` / `openai-compatible` (`tts` is an alias) | empty → `openai-compatible` |
| `PEEKO_TTS_VOICE`       | 5     | Voice for speech, e.g. `alloy` (`verse`, `shimmer`, …)     | empty → engine default |
| `PEEKO_TTS_MODEL`       | 5     | Speech model, e.g. `tts-1` / `tts-1-hd`                    | empty → engine default |
| `PEEKO_TTS_SPEED`       | 5     | Speaking rate sent in the request (1.0 = normal)           | `1.0`           |
| `PEEKO_TTS_VOLUME`      | 5     | Playback volume applied locally, `0.0`–`1.0`               | `1.0`           |
| `PEEKO_TTS_BASE_URL`    | 5     | Speech API root (falls back to `PEEKO_AI_BASE_URL`)        | `PEEKO_AI_BASE_URL`, then engine default |
| `PEEKO_TTS_TIMEOUT_S`   | 5     | How long to wait for the audio before giving up, in seconds | `60`           |

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`.

## How to run locally

From the repo root (installed or not — `python -m peeko` works from the
source tree):

```bash
python -m peeko          # after `pip install .` also just: peeko
```

A small robot appears in the top-right of your screen, floating on a soft
shadow **without any window frame or background** — you can keep working in
other apps and it stays visible on top. It bobs gently, blinks now and then,
occasionally glances around (and toward your mouse cursor when it comes
close), does a happy squash-and-bounce when you click it, and wiggles while
you drag it anywhere on the desktop.

**Talking to it (Stage 2 interactions):**

| You do this                | Peeko does this                                          |
| -------------------------- | -------------------------------------------------------- |
| Move the pointer onto it   | a subtle "oh!" (half-closed eyes, tiny lift), then back to idle. Leaving ends it immediately; it stays quiet mid-click/mid-drag. |
| Left-click once            | the happy squash-and-bounce                              |
| Left-click twice           | the bigger, winkier double-click bounce                  |
| Hold the left button + move| drag it anywhere; it plays the "being carried" wiggle    |
| Right-click                | opens the interaction menu (below)                       |
| `Ctrl+Q`                   | quits                                                    |

**The interaction menu** (right-click → menu):

```
Peeko v0.1.0 — Stage 6 of 13          (info header, not clickable)
────────────────────────────────
Talk
Feed — Stage 7 (not implemented)
Pet                                   (real since Stage 6)
Play                                  (real since Stage 6)
Sleep — Stage 7 (not implemented)
Wake Up — Stage 7 (not implemented)
────────────────────────────────
Check Status…
Settings…
────────────────────────────────
Quit                                  Ctrl+Q
```

- **Pet** and **Play** change Peeko's live mood and needs (see
  [Stage 6](#current-development-stage)) and the robot reacts with the right
  animation. **Feed**, **Sleep** and **Wake Up** are still labelled
  `— Stage 7 (not implemented)`; picking one opens a plain explanation of when
  it lands instead of a fake window.
- **Check Status…** opens a live readout: version/stage, the animation and
  state Peeko is in *right now*, the reactions the loaded artwork can play,
  animation/canvas/layer facts from the manifest, where the artwork came
  from, and a plain list of what works today versus what does not.
- **Settings…** opens a **read-only** window with the configuration Peeko
  is really running with (log level, data/config/log directories, database
  file, artwork folder). Editing from the UI is not implemented yet — the
  window says so and points at `.env`.
- The six planned pet actions are **not implemented yet**: choosing one makes
  Peeko do a "huh?" head-shake and opens an explanation naming the roadmap
  stage that will build it. Nothing is faked, and no menu entry is a dead
  button.

All windows are non-blocking: the robot keeps blinking and bobbing behind
them.

Headless verification (CI servers, containers, SSH boxes):

```bash
PEEKO_SMOKE_TEST=1 QT_QPA_PLATFORM=offscreen timeout 30 python -m peeko
echo $?    # -> 0, and "Smoke-test mode" appears in the log
```

Run the tests:

```bash
pytest          # or: python -m pytest
```

## How to build / deploy

The owner deploys and distributes the app themselves; there is no hosted
backend. Two supported ways:

1. **Run from source** (simplest, cross-platform): install Python 3.10+,
   `pip install .`, run `peeko` (or `python -m peeko`). Works on
   Windows/macOS/Linux.
2. **Standalone binary**: planned properly in **Stage 12 (packaging)**.
   Until then, if you need a single-file binary, PyInstaller is the expected
   route (`pyinstaller peeko/__main__.py --name peeko --windowed`), but be
   aware it is not yet part of the build pipeline or CI.

Use `pip install .[dev]` plus `pytest` before shipping anything. Never
commit a real `.env` — the repo ignores it.

## Avatar artwork — swap in your own robot

Peeko's character is **data, not code**. Everything you see is described by
`peeko/avatar/assets/manifest.json` (canvas size, layer stacking order,
animations, frame timings) plus the SVG files in
`peeko/avatar/assets/layers/`.

To use your own art:

1. Draw your robot as SVGs at the manifest's canvas size (default
   `160×180`), one file per layer state (body, eyes open/closed/looking…).
2. Edit `layer_defaults` in `manifest.json` to point at your files, and
   adjust the animations (timings, bob offsets) to taste.
3. Run `peeko` — your artwork appears. No Python changes, ever.

Keep your art **outside** the installed package if you prefer: point
`PEEKO_AVATAR_ASSETS_DIR` at a folder containing your own `manifest.json`
and `layers/`, and Peeko loads that instead.

**Reaction animations are optional.** `hover`, `double_click` and `confused`
are looked up by name (or remapped with a `state_animation_map` entry in the
manifest). If your manifest does not define one of them, that reaction is
simply switched off — your artwork still runs, it just has fewer reactions.
The core animations (`idle`, `blink`, `look_*`, `click`, `dragging`) are
mandatory, and a manifest missing one fails loudly at startup with a message
naming it.

The full walk-through (manifest reference, frame keys, adding new
animations, gotchas) lives in
[`peeko/avatar/assets/README.md`](peeko/avatar/assets/README.md).

## Architecture

```
peeko/
├── __init__.py        app name / version / stage numbers
├── __main__.py        entry point for `python -m peeko`
├── app.py             wiring: settings → logging → Qt app → avatar window
├── settings.py        Settings dataclass (env + .env + defaults)
├── paths.py           per-OS data/config/log dirs (platformdirs + overrides)
├── logging_setup.py   console + rotating file logging; secret redaction
├── errors.py          PeekoError, global exception hook, friendly dialog
├── avatar/            the animated on-screen creature (Stage 1 + 2 reactions)
   ├── manifest.py    artwork manifest parsing + validation
   ├── state_machine.py  timer-driven animation + reaction states (Qt-free)
   ├── assets.py      SVG layer files -> ready-to-draw pixmaps
   ├── renderer.py    compositing one frame (ground shadow + layers)
   ├── expressions.py AI animation name -> an existing avatar animation
   ├── widget.py      the frameless/translucent/always-on-top window + input
   └── assets/        the artwork: manifest.json + layers/*.svg
├── ai/                conversational AI (Stage 3)
   ├── client.py     AIClient: persona + context in, validated reply out
   ├── personality.py the persona (data) + the JSON contract for the model
   ├── context.py    the structured context block (fed live by Stage 6;
                      the seam for Stages 7/8)
   ├── providers.py  engine-agnostic seam + OpenAI-compatible provider
   ├── schema.py     validates model output against the allowed lists
   ├── vocabulary.py the allowed emotions / animations / actions
   ├── errors.py     honest, key-free AI error messages
   └── worker.py     Qt bridge: runs requests off the UI thread
├── voice/             voice input (Stage 4) + text-to-speech (Stage 5)
   ├── __init__.py     the package's public surface (one import point)
   ├── audio.py       microphone capture + the AudioSource seam (lazy audio)
   ├── providers.py   speech-to-text seam + the OpenAI-compatible provider
   ├── input.py       SpeechRecognizer: microphone in -> words out
   ├── output.py      SpeechSynthesizer: reply text in -> audio out
   ├── output_providers.py the /audio/speech seam (real + mock provider)
   ├── player.py      playback through sounddevice (imported lazily) + WAV
   ├── worker.py      Qt bridge: runs a capture or an utterance off-thread
   └── errors.py      honest, key-free voice error messages
├── emotions/          live emotion engine (Stage 6)
   ├── state.py       PAD emotional-state model + the named emotions
   └── engine.py      EmotionEngine: drift over an injectable clock,
                      documented interaction effects, the six-stat snapshot
├── needs/             virtual-pet needs model + per-hour decay (Stage 6
                      already drifts one live inside the emotion engine;
                      Stage 7 adds feeding/sleeping and their UI)
├── memory/            persistent memory — Stage 8, interface only
├── awareness/         active-app awareness — Stage 8, interface only
├── db/                SQLite connection + schema versioning (real at Stage 0)
└── ui/                Stage 2 interaction layer
   ├── context_menu.py  the menu described as data (MENU_SPEC) + Qt builder
   ├── chat_window.py   the Stage 3 chat window (Talk)
   └── dialogs.py       Check Status readout, read-only Settings, "not yet"
```

**How they relate at Stage 6:**

- `__main__` → `app` → `settings` + `paths` + `logging_setup` + `errors`
  → `avatar` (window) + `ui` (menu and dialogs).
- Inside `avatar`: the window loads `manifest.json` → rasterises its SVG
  layers into pixmaps → runs the animation state machine on a `QTimer`
  (33 fps), repainting only when the frame actually changes. The engine
  (`state_machine.py`) never imports Qt, which is why it is unit-testable
  without a display. Mouse events map to machine calls in `widget.py`:
  `enterEvent`/`leaveEvent` → hover reaction, `release` → click,
  `mouseDoubleClickEvent` → double-click reaction, movement past a 6 px
  threshold → drag.
- Inside `ui`: `MENU_SPEC` is the single description of the interaction
  menu. `build_avatar_context_menu()` renders it for Qt, and
  `dialogs.py` builds the three windows (Check Status, read-only Settings,
  "not implemented yet"). The text builders are plain functions, so the
  honesty of every readout is unit-tested without a display.
- `db` is wired and tested now so Stages 7/8 can persist needs and memory
  without rework.
- Inside `emotions` (Stage 6): `EmotionEngine` owns one `EmotionalState` and
  one `PetNeeds`, reads time through an *injectable clock* (so tests are
  deterministic and nothing blocks), drifts both toward neutral/zero with the
  documented per-hour rates, applies the `INTERACTION_EFFECTS` deltas for
  talk/pet/play/status (clamped to the unit cube and `0..100`) and reports the
  six-stat snapshot the chat context and Check Status use.
  `avatar/widget.py` owns one live engine, advances it from a `QTimer`
  callback, passes the snapshot into `build_context()` and points
  `avatar/expressions.py` at the animation for the dominant emotion. The
  engine imports no Qt, so it is unit-testable without a display.
- Inside `ai` (Stage 3): `client.py` builds the prompt from `personality.py`
  plus the structured context from `context.py`, sends it through the
  `providers.py` seam (one real OpenAI-compatible provider, stdlib HTTP), and
  validates the model's JSON with `schema.py` + `vocabulary.py`. `worker.py`
  runs that blocking call on a `QThreadPool` worker so the UI never waits, and
  `ui/chat_window.py` shows the transcript. `avatar/expressions.py` is the
  only place that knows both worlds: it maps the reply's animation name onto
  an animation the current artwork manifest can really play.
- The AI package deliberately never imports `peeko.avatar` or `peeko.ui`
  (a test enforces this), and no AI module can run a command — model output
  is data, never instructions.
- Inside `voice` (Stage 4): `audio.py` owns the microphone behind the
  `AudioSource` seam — one real `sounddevice` implementation (imported lazily,
  so the library stays optional) and an honest "no microphone" source.
  `input.py`'s `SpeechRecognizer` glues a source to a provider: capture the
  audio, hand it to speech-to-text, return the words (or `None`). `providers.py`
  holds the provider seam — one real OpenAI-compatible `/audio/transcriptions`
  provider (stdlib HTTP) plus a mock used by tests — and `errors.py` keeps every
  message honest and free of the API key. `worker.py` runs a whole capture on a
  `QThreadPool` worker, exactly like the AI worker, so `ui/chat_window.py` (Mic
  button, "Listening…" line) never blocks; it tells the avatar through
  `listeningChanged`, and `avatar/expressions.py` maps that onto the optional
  sustained `listening` pose.
- The voice package deliberately never imports `peeko.ui` or `peeko.avatar`, and
  `capture_clip` never writes audio to disk. `peeko.ui.chat_window` stays free
  of `sounddevice` too: everything testable is injectable, which is why the
  whole feature is unit-tested on a machine with no microphone.
- `memory` and `awareness` still ship as **honest interfaces**: their methods
  raise `NotImplementedError` naming the target stage — no fake buttons, no
  pretend features. `voice/output.py` (text-to-speech, Stage 5) is real now:
  with the voice switched off it refuses in plain words instead of pretending
  to speak, and it never plays anything it did not actually receive.

## Current development stage

**Stage 6 of 13 — emotions (completed).** Peeko is no longer a happy face with
no feelings: it has a live inner state, and two of its menu actions change it:

1. **one mood, six stats** — the mood is a PAD
   (pleasure–arousal–dominance) state in `[-1, 1]`; `happiness` is the
   pleasure axis mapped onto `0..100` (`(pleasure + 1) / 2 * 100`, so 50 is
   neutral) and the other five stats come from the virtual-pet needs —
   **energy, hunger, boredom, sleepiness, friendship**. `Check Status…` lists
   the dominant mood and all six numbers and says plainly that they live in
   memory for this run until Stage 8 adds persistence;
2. **it drifts by itself** — with nobody interacting, the PAD axes decay
   toward neutral at documented rates (pleasure `0.6`/hour, arousal `1.2`,
   dominance `0.8`) and the needs decay on their own per-hour clock. The
   drift never overshoots neutral, everything is clamped, and it advances
   from a `QTimer` callback in `avatar/widget.py`, so the robot never stops
   animating and the UI thread is never blocked;
3. **two real interactions** — *Pet* and *Play* in the right-click menu apply
   the `INTERACTION_EFFECTS` table: petting is `+0.20` pleasure and friendship,
   playing is fun and exciting but costs energy and raises hunger. They are
   real menu entries now (no "not implemented" label, no placeholder dialog);
   a completed chat reply also counts as the documented *talk* interaction;
4. **the AI and the robot both see the mood** — the structured context sent
   with every chat message carries the live emotion/happiness/energy/hunger/
   sleepiness/friendship (memory and app awareness stay honest placeholders),
   and the dominant emotion maps onto animations the loaded manifest can
   really play (`avatar/expressions.py`); a mood the artwork cannot show
   changes nothing rather than inventing an animation;
5. **still honest** — nothing is persisted yet (Stage 8), the needs are *read*
   live but feeding/sleeping are not implemented until Stage 7 (those three
   menu entries keep their label), and no new `.env` variable was added: the
   engine's rates and effects are documented constants in
   `peeko/emotions/engine.py`.

**Stage 5 of 13 — text-to-speech (completed).** Peeko can now *speak*:

1. **Speak in the chat window** — right-click the robot → *Talk*, then press
   **Speak** to hear Peeko's last reply again, or simply send a message: with
   the voice on, every reply is spoken as it arrives. While Peeko talks the
   window shows *"Peeko is speaking…"*, the button turns into **Stop**, and
   the robot plays a sustained "talking" pose (optional artwork — older
   manifests simply show no talking pose and keep working);
2. **engine-agnostic speech** — one real OpenAI-compatible `/audio/speech`
   request (stdlib HTTP) with the voice, model, speed, volume, base URL and
   timeout from `.env`, so OpenAI, a gateway or a local speech server all work
   by configuration (the base URL falls back to `PEEKO_AI_BASE_URL`, so one
   gateway can configure chat, dictation *and* speech);
3. **real audio, decoded in memory** — the response is decoded to samples with
   the standard library and played through `sounddevice`, which is imported
   lazily and stays optional. Nothing is written to disk and the reply text is
   sent only to the endpoint you configured;
4. **off by default and honest about every limit** — `PEEKO_TTS_ENABLED=0` is
   the default; while it is off the Speak button is *hidden* with a hint
   naming the variable rather than shown as a dead control, and a missing key,
   no audio output device, an HTTP error, a timeout, an unreadable reply, a
   cancelled playback and a broken worker each produce their own
   plain-language note. No note means no problem — and never a fake sound;
5. **nothing blocks** — synthesis and playback run on a worker thread, so the
   animation keeps ticking, typing keeps working, and closing the window (or
   pressing **Stop**) stops the playback cleanly.

**Stage 4 of 13 — voice input (completed).** Peeko can *hear* you instead of
only reading you:

1. **Mic in the chat window** — right-click the robot → *Talk* (or the
   microphone button already in the window) and press **Mic**. While Peeko
   listens it says *"Listening… speak now…"*, the button turns into **Stop**,
   and the robot plays a sustained, "ears open" listening pose;
2. **microphone in, words out** — `capture_clip()` records through
   `sounddevice` in 16-bit mono chunks and stops on its own after
   `PEEKO_VOICE_MAX_SECONDS`; `SpeechRecognizer` hands the clip to the
   speech-to-text provider and returns the transcript;
3. **engine-agnostic speech-to-text** — one real OpenAI-compatible
   `/audio/transcriptions` provider (stdlib HTTP, multipart upload, model, base
   URL and timeout from `.env`), so OpenAI Whisper, a gateway or a local server
   all work by configuration (the base URL falls back to `PEEKO_AI_BASE_URL`, so
   one gateway configures chat *and* dictation);
4. **the transcript is never sent for you** — the words land in the message box
   with a *"Heard: … — fix it if I misheard, then press Send"* note, so a
   misheard word is a correction, not a wrong message;
5. **nothing blocks** — recording and the HTTP round trip run on a worker
   thread; the animation keeps ticking, typing keeps working and closing the
   window stops the capture;
6. **never faking, never leaking** — a missing key, no microphone, refused
   permission, a busy device, a capture failure, an HTTP error, a timeout and an
   unreadable reply each produce their own plain-language note and **no
   transcript at all**; audio stays in memory, is uploaded only to the endpoint
   you configured, and the API key never appears in a log line or a message.

Stage 3 — AI chat (completed). The robot can hold a typed conversation in its
own window while it keeps floating on your desktop: a transcript with Enter to
send and Esc to close, a "Peeko is thinking…" line, a versioned personality, a
structured context about Peeko's situation sent with every message, a
validated JSON reply (response / emotion / animation / action) whose emotion and
animation must come from hard-coded allowed lists and whose only legal action is
`null` — so nothing an AI reply says can run a command or touch your computer —
and requests on a worker thread, with honest messages for a missing key, a
timeout, a network failure or an unreadable reply.

Stage 2 — basic interaction (completed). Stage 0 delivered the
project foundation (modular package, settings, logging, errors, SQLite,
tests, smoke test). Stage 1 turned the placeholder window into a real little
character: a frameless translucent always-on-top robot, drawn from a
data-driven artwork manifest, with a timer-driven animation state machine
(idle bob, blinking, self-initiated glances and cursor glances, click
squash-and-bounce, drag wiggle) and click-vs-drag mouse handling.

Stage 2 gives that character its interaction vocabulary and a real menu:

1. three new **reaction states** (`hover`, `double_click`, `confused`) added
   to the same Qt-free state machine, plus three matching animations and the
   `eyes_wink` artwork layer — all still pure manifest data;
2. hover reactions that are deliberately timid (they never swallow a click,
   never interrupt a drag, and end the moment the pointer leaves);
3. the full **interaction menu** with *Check Status…* (live readout) and
   *Settings…* (read-only configuration viewer) genuinely working, and the
   six planned pet actions labelled with their roadmap stage and answering
   with an honest explanation instead of a fake window;
4. every window opened **asynchronously**, so the animation never freezes.

Reaction animations are optional artwork: a manifest that omits them simply
has fewer reactions, so Stage 1 artwork keeps working unchanged.

The full owner roadmap: 1 avatar → 2 interaction → 3 AI chat → 4 voice
input → 5 TTS → 6 emotions → 7 needs → 8 memory → 9 app awareness →
10 autonomous life → 11 polish → 12 packaging → 13 final testing. Each
stage is built, tested, documented, committed and pushed before the next
begins.

## Known limitations

- The artwork is a **cute placeholder robot** drawn as SVG layers. It is
  meant to be replaced — see
  [Avatar artwork](#avatar-artwork--swap-in-your-own-robot). Nothing about
  the placeholder is hard-coded in the engine.
- The character knows its Stage 1–6 behaviours (idle, blink, glance, click,
  double-click, hover, drag, "huh?", listening, talking, and the mood-driven
  expressions). It can listen, speak and *feel* now, but feeding and sleeping
  are still to come — those are the Stage 7 needs simulation.
- **The remaining pet actions (Feed, Sleep, Wake Up) do not do anything yet.**
  They are deliberately visible and labelled `— Stage 7 (not implemented)`,
  and picking one opens an explanation rather than pretending. *Pet* and
  *Play* are real since Stage 6 and are no longer labelled.
- **A real conversation needs your own API key.** Peeko has no cloud
  backend: set `PEEKO_AI_API_KEY` (and `PEEKO_AI_MODEL`) in `.env` to chat.
  Without a key the chat window says so and no request is ever attempted.
  The automated tests use a mock provider and a fake HTTP transport — they
  never need a key and never touch the network, so a green test run proves
  the plumbing, not a live conversation.
- **Voice input needs a microphone, the optional `sounddevice` package and the
  same API key.** Install it with `pip install "peeko[voice]"` (or
  `pip install sounddevice`). Without it, Peeko starts normally and pressing Mic
  explains what is missing — the robot never crashes over audio.
- **A real microphone and a live speech-to-text call have not been tested by
  us.** Peeko is built and tested on a headless machine with no microphone and
  no API key, so the microphone capture, the live `/audio/transcriptions` call
  and the listening pose were verified with injected fakes, a mock provider and
  a recording HTTP transport instead of real hardware. The plumbing, the
  cancellation path, the off-thread behaviour and every error path are covered
  by tests, but **you will be the first person to speak into it** — if a real
  device misbehaves you will see a plain-language note rather than a fake
  transcript, and the `PEEKO_VOICE_*` variables in `.env` are where to adjust
  how long it listens and waits.
- **A real speaker and a live text-to-speech call have not been tested by
  us either.** Playback and the `/audio/speech` round trip were verified with
  injected fakes, a mock speech provider and a recording HTTP transport — the
  plumbing, the cancellation path, the off-thread behaviour and every error
  path are covered by tests, but you will be the first person to hear Peeko
  speak. Speech is off by default, so nothing plays until you set
  `PEEKO_TTS_ENABLED=1`, and the `PEEKO_TTS_*` variables are where to change
  voice, speed, volume or endpoint.
- **Settings is read-only.** You can see the configuration Peeko runs with,
  but editing it from the UI is not implemented yet — use `.env` or
  environment variables.
- **Persistent memory (Stage 8) and app awareness (Stage 9) are not
  implemented yet** — their config variables exist but have no effect, and
  their interfaces raise `NotImplementedError` naming the stage that will
  build them. Peeko's chat does not remember anything between runs until the
  memory stage lands. Its mood and needs are live since Stage 6, but they are
  held **in memory only** — every restart starts Peeko back at neutral, and
  Stage 8 is what will save and restore them.
- **Peeko cannot control your computer.** It can chat and play an expression;
  that is all. The chat window says so, and the `action` field of every
  AI reply is forced to `null`.
- No tray icon and no installer yet (packaging is Stage 12).
- On headless machines you must set `QT_QPA_PLATFORM=offscreen`; on Linux
  desktops, standard Qt system libraries are required.
- Peeko has no cloud backend: AI/voice features work only with your own API
  keys. At Stage 3 the only network request the app can make is your chat
  message to the provider you configured.

## License

MIT — see `pyproject.toml`. (Add a `LICENSE` file before any public
distribution.)