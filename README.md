# Peeko — Desktop Robot Companion

A cute, animated robot companion that lives on your desktop: a transparent,
draggable, always-on-top creature with personality. Built as a real,
maintainable, portable **Python desktop application** (PySide6/Qt) — modular,
privacy-first, no cloud infrastructure. You configure optional AI/voice API
keys yourself via a local `.env` file and run it on your own machine.

> **Current development stage: Stage 2 of 13 (basic interaction —
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
- ✅ **Right-click interaction menu** (Stage 2): *Talk*, *Feed*, *Pet*,
  *Play*, *Sleep*, *Wake Up*, *Check Status…*, *Settings…* and *Quit* — all
  in their final places. Entries whose feature arrives in a later stage are
  labelled `— Stage N (not implemented)` right in the menu and answer with a
  plain explanation instead of a fake window. **Check Status** and
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
- 🔜 **Coming in later stages**: AI chat, voice input, text-to-speech,
  emotions, virtual-pet needs, persistent memory, app awareness, autonomous
  life, polish, packaging, final testing.

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
| `PEEKO_AI_PROVIDER`     | 3     | AI provider (config only until Stage 3)                   | `openai`        |
| `PEEKO_AI_MODEL`        | 3     | AI model name (config only until Stage 3)                 | *(empty)*       |
| `PEEKO_AI_API_KEY`      | 3     | **Secret** — AI API key (never logged; Stage 3)           | *(empty)*       |
| `PEEKO_VOICE_INPUT_ENGINE` | 4  | Voice input engine (Stage 4)                              | *(empty)*       |
| `PEEKO_TTS_ENGINE`      | 5     | Text-to-speech engine (Stage 5)                           | *(empty)*       |
| `PEEKO_TTS_VOICE`       | 5     | TTS voice identifier (Stage 5)                            | *(empty)*       |

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
Peeko v0.1.0 — Stage 2 of 13          (info header, not clickable)
────────────────────────────────
Talk — Stage 3 (not implemented)
Feed — Stage 7 (not implemented)
Pet — Stage 6 (not implemented)
Play — Stage 6 (not implemented)
Sleep — Stage 7 (not implemented)
Wake Up — Stage 7 (not implemented)
────────────────────────────────
Check Status…
Settings…
────────────────────────────────
Quit                                  Ctrl+Q
```

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
   ├── widget.py      the frameless/translucent/always-on-top window + input
   └── assets/        the artwork: manifest.json + layers/*.svg
├── ai/                conversational AI — Stage 3, interface only
├── voice/             voice input (Stage 4) + TTS (Stage 5), interface only
├── emotions/          PAD emotional-state model (real data model at Stage 0)
├── needs/             virtual-pet needs model + decay logic (real at Stage 0)
├── memory/            persistent memory — Stage 4, interface only
├── awareness/         active-app awareness — Stage 8, interface only
├── db/                SQLite connection + schema versioning (real at Stage 0)
└── ui/                Stage 2 interaction layer
   ├── context_menu.py  the menu described as data (MENU_SPEC) + Qt builder
   └── dialogs.py       Check Status readout, read-only Settings, "not yet"
```

**How they relate at Stage 2:**

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
- `emotions` and `needs` ship as real, unit-tested data models that later
  stages animate and simulate.
- `ai`, `voice`, `memory`, `awareness` ship as **honest interfaces**: their
  methods raise `NotImplementedError` naming the target stage — no fake
  buttons, no pretend features.

## Current development stage

**Stage 2 of 13 — basic interaction (completed).** Stage 0 delivered the
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
- The character only knows its Stage 1–2 behaviours (idle, blink, glance,
  click, double-click, hover, drag, "huh?"). It cannot chat, feel or need
  anything yet — those are Stages 3–7.
- **The six pet actions in the menu (Talk, Feed, Pet, Play, Sleep,
  Wake Up) do not do anything yet.** They are deliberately visible and
  labelled `— Stage N (not implemented)`, and picking one opens an
  explanation rather than pretending. The settings for AI/voice/TTS exist
  but have no effect until Stages 3–5.
- **Settings is read-only.** You can see the configuration Peeko runs with,
  but editing it from the UI is not implemented yet — use `.env` or
  environment variables.
- **AI chat, voice input, TTS, persistent memory and app awareness are not
  implemented yet** — their config variables exist but have no effect, and
  their interfaces raise `NotImplementedError` on purpose.
- No tray icon and no installer yet (packaging is Stage 12).
- On headless machines you must set `QT_QPA_PLATFORM=offscreen`; on Linux
  desktops, standard Qt system libraries are required.
- Peeko has no cloud backend: AI/voice features (later stages) will work
  only with your own API keys, and the app never sends data anywhere at
  Stage 2.

## License

MIT — see `pyproject.toml`. (Add a `LICENSE` file before any public
distribution.)