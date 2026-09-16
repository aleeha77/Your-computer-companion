# Peeko — Desktop Robot Companion

A cute, animated robot companion that lives on your desktop: a transparent,
draggable, always-on-top creature with personality. Built as a real,
maintainable, portable **Python desktop application** (PySide6/Qt) — modular,
privacy-first, no cloud infrastructure. You configure optional AI/voice API
keys yourself via a local `.env` file and run it on your own machine.

> **Current development stage: Stage 0 of 13 (project foundation).**
> See [Current development stage](#current-development-stage) below.

---

## Features

At Stage 0 the foundation is in place; later stages add the creature's life:

- ✅ **Desktop window**: frameless, translucent, always-on-top placeholder
  avatar (a rounded blob with eyes) that you can drag anywhere.
- ✅ **Obvious quit affordances**: right-click context menu → *Quit*,
  `Ctrl+Q`, window close, or `Ctrl+C` in the terminal — all quit cleanly.
- ✅ **Configuration**: environment variables + optional `.env` file
  (python-dotenv), with sane defaults and per-OS data directories
  (platformdirs). No machine-specific hard-coded paths.
- ✅ **Logging**: console + rotating file log in your user data directory;
  log level set via `PEEKO_LOG_LEVEL`; secrets are never logged.
- ✅ **Error handling**: global exception hook logs the traceback and shows a
  friendly dialog before exiting; clean startup-failure paths.
- ✅ **Headless smoke test**: `PEEKO_SMOKE_TEST=1` auto-quits ~2 s after
  launch with exit code 0, so CI/headless boxes can verify the whole app.
- 🔜 **Coming in later stages**: AI chat, voice input, text-to-speech,
  emotions, virtual-pet needs, persistent memory, app awareness,
  autonomous life, polish, packaging, final testing.

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

You should see a small floating teal blob with eyes in the top-right of your
screen. Drag it anywhere. Right-click → **Quit** (or press `Ctrl+Q`, or hit
`Ctrl+C` in the terminal) to exit.

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
├── avatar/            the on-screen creature (Stage 0: placeholder window)
├── ai/                conversational AI — Stage 3, interface only
├── voice/             voice input (Stage 4) + TTS (Stage 5), interface only
├── emotions/          PAD emotional-state model (real data model at Stage 0)
├── needs/             virtual-pet needs model + decay logic (real at Stage 0)
├── memory/            persistent memory — Stage 4, interface only
├── awareness/         active-app awareness — Stage 8, interface only
├── db/                SQLite connection + schema versioning (real at Stage 0)
└── ui/                menus/dialogs (context menu with Quit at Stage 0)
```

**How they relate at Stage 0:**

- `__main__` → `app` → `settings` + `paths` + `logging_setup` + `errors`
  → `avatar` (window) + `ui` (menu).
- `db` is wired and tested now so Stages 4/7 can persist memory and needs
  without rework.
- `emotions` and `needs` ship as real, unit-tested data models that later
  stages animate and simulate.
- `ai`, `voice`, `memory`, `awareness` ship as **honest interfaces**: their
  methods raise `NotImplementedError` naming the target stage — no fake
  buttons, no pretend features.

## Current development stage

**Stage 0 of 13 — project foundation (this milestone).** The full owner
roadmap: 1 avatar → 2 interaction → 3 AI chat → 4 voice input → 5 TTS →
6 emotions → 7 needs → 8 memory → 9 app awareness → 10 autonomous life →
11 polish → 12 packaging → 13 final testing. Each stage is built, tested,
documented, committed and pushed before the next begins.

## Known limitations

- The avatar is a **static placeholder shape** — animation arrives at
  Stage 1/2; emotions at Stage 6.
- **AI chat, voice input, TTS, persistent memory and app awareness are not
  implemented yet** — their config variables exist but have no effect, and
  their interfaces raise `NotImplementedError` on purpose.
- No tray icon, no settings UI, no installer yet (packaging is Stage 12).
- On headless machines you must set `QT_QPA_PLATFORM=offscreen`; on Linux
  desktops, standard Qt system libraries are required.
- Peeko has no cloud backend: AI/voice features (later stages) will work
  only with your own API keys, and the app never sends data anywhere at
  Stage 0.

## License

MIT — see `pyproject.toml`. (Add a `LICENSE` file before any public
distribution.)