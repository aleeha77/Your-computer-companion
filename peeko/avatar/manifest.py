"""Avatar asset manifest: parse, validate and model ``manifest.json``.

The avatar is **asset-driven**: every animation the engine can play is
described in a machine-readable manifest (``peeko/avatar/assets/
manifest.json``) that maps named animations → frames → layer files. The
rendering code has no knowledge of the individual SVGs — it plays whatever
the manifest describes. This is the owner's hard requirement: replacing the
placeholder robot with custom artwork is a data-only change (drop new SVG
files + edit ``manifest.json``), never a code change.

Manifest schema (version 1)::

    {
      "schema_version": 1,
      "canvas": {"width": 160, "height": 180},
      "z_order": ["body", "eyes"],
      "layer_defaults": {"body": "layers/body.svg",
                         "eyes": "layers/eyes_open.svg"},
      "state_animation_map": {"idle": "idle", "blink": "blink", ...},
      "animations": {
        "idle": {
          "loop": true,
          "frames": [
            {"dy": 0, "duration_ms": 1300},
            {"dy": 2, "duration_ms": 350}
          ]
        },
        "blink": {
          "loop": false,
          "frames": [
            {"eyes": "layers/eyes_half.svg",   "duration_ms": 60},
            {"eyes": "layers/eyes_closed.svg", "duration_ms": 120}
          ]
        }
      }
    }

Rules
-----

* ``canvas`` — integer pixel size every layer SVG is rendered at. All
  layer files should be authored at this exact size so they stack
  pixel-perfectly (layers are composited by the engine, in ``z_order``).
* ``layer_defaults`` — the base set of layers; a frame may *override* any
  layer by naming it as a key with a different file value (e.g. swap
  ``eyes`` to a closed-eyes SVG for a blink frame).
* ``animations`` — a named animation is an ordered list of frames. A frame
  has a positive ``duration_ms`` and optional whole-character offsets
  ``dx``/``dy`` (pixels, used for bobbing/bouncing without new art).
  ``loop: true`` animations repeat forever; one-shot animations return to
  ``idle`` when finished.
* ``state_animation_map`` (optional) — remaps animation-state names to
  animation names. If omitted, the state machine's built-in mapping is
  used (state name == animation name for the default set: ``idle``,
  ``blink``, ``look_left``, ``look_right``, ``look_up``, ``look_down``,
  ``click``, ``dragging``).

Any problem (bad JSON, wrong types, missing file, unknown layer name,
missing animation) raises :class:`peeko.errors.StartupError` with a
human-readable list of every problem found, so a broken manifest fails
loudly at startup instead of rendering a wrong/broken avatar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from peeko.errors import StartupError

#: Manifest format version this engine understands.
SUPPORTED_SCHEMA_VERSION = 1

#: Frame keys that are engine-recognised (anything else must be a layer
#: name defined in ``layer_defaults``).
_FRAME_CONTROL_KEYS = {"duration_ms", "dx", "dy"}


@dataclass(frozen=True)
class Frame:
    """One still of an animation.

    ``layer_files`` maps layer name → SVG file (relative to the assets
    directory). The manifest parser pre-resolves every frame so that
    default layers and per-frame overrides are already merged; the
    renderer never touches the raw JSON.
    """

    duration_ms: int
    dx: int = 0
    dy: int = 0
    layer_files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Animation:
    """A named animation: an ordered sequence of frames."""

    name: str
    frames: tuple[Frame, ...]
    loop: bool


@dataclass(frozen=True)
class Manifest:
    """Parsed, validated avatar manifest."""

    schema_version: int
    canvas_width: int
    canvas_height: int
    z_order: tuple[str, ...]
    layer_defaults: dict[str, str]
    animations: dict[str, Animation]
    state_animation_map: dict[str, str] | None
    base_dir: Path


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _add_problem(problems: list[str], message: str, *details: str) -> None:
    problems.append(message)
    problems.extend(f"  - {d}" for d in details)


def _require_str(value: Any, what: str, problems: list[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{what} must be a non-empty string, got {value!r}.")


def _require_positive_int(value: Any, what: str, problems: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        problems.append(f"{what} must be a positive integer, got {value!r}.")


def _parse_frames(raw_frames: Any, anim_name: str, layer_names: set[str],
                  base_dir: Path, problems: list[str]) -> tuple[Frame, ...]:
    if not isinstance(raw_frames, list) or not raw_frames:
        problems.append(
            f"animation {anim_name!r}: 'frames' must be a non-empty list."
        )
        return ()
    frames: list[Frame] = []
    for i, raw in enumerate(raw_frames):
        tag = f"animation {anim_name!r}, frame #{i}"
        if not isinstance(raw, dict):
            problems.append(f"{tag} must be an object.")
            continue
        duration = raw.get("duration_ms")
        _require_positive_int(duration, f"{tag}: 'duration_ms'", problems)
        dx = raw.get("dx", 0)
        dy = raw.get("dy", 0)
        for key, value in (("dx", dx), ("dy", dy)):
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{tag}: '{key}' must be an integer.")
                dx, dy = 0, 0
        layer_files: dict[str, str] = {}
        for key, file_name in raw.items():
            if key in _FRAME_CONTROL_KEYS:
                continue
            if key not in layer_names:
                problems.append(
                    f"{tag}: unknown key {key!r} (not a control key and "
                    f"not a layer defined in 'layer_defaults')."
                )
                continue
            _require_str(file_name, f"{tag}: layer {key!r}", problems)
            layer_path = base_dir / file_name
            if not layer_path.is_file():
                problems.append(
                    f"{tag}: layer {key!r} references missing file "
                    f"{file_name!r} (looked at {layer_path})."
                )
            layer_files[key] = file_name
        frames.append(
            Frame(
                duration_ms=duration if isinstance(duration, int) else 1,
                dx=dx if isinstance(dx, int) else 0,
                dy=dy if isinstance(dy, int) else 0,
                layer_files=layer_files,
            )
        )
    return tuple(frames)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def load_manifest(manifest_path: str | Path) -> Manifest:
    """Parse and validate the manifest at ``manifest_path``.

    Raises :class:`peeko.errors.StartupError` listing every problem found
    (bad JSON, schema errors, missing files, unknown layers, ...).
    """
    path = Path(manifest_path)
    if not path.is_file():
        raise StartupError(
            f"Avatar manifest not found: {path}. The avatar cannot render "
            f"without its asset manifest."
        )

    problems: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StartupError(
            f"Avatar manifest {path} is not valid JSON: {exc}."
        ) from exc
    if not isinstance(data, dict):
        raise StartupError(
            f"Avatar manifest {path} must contain a JSON object, "
            f"got {type(data).__name__}."
        )

    schema = data.get("schema_version")
    if schema != SUPPORTED_SCHEMA_VERSION:
        problems.append(
            f"Unsupported schema_version {schema!r}; this build of Peeko "
            f"understands schema version {SUPPORTED_SCHEMA_VERSION}."
        )

    # -- canvas ----------------------------------------------------------- #
    raw_canvas = data.get("canvas", {})
    if not isinstance(raw_canvas, dict):
        problems.append("'canvas' must be an object with width and height.")
        raw_canvas = {}
    _require_positive_int(raw_canvas.get("width"), "'canvas.width'", problems)
    _require_positive_int(raw_canvas.get("height"), "'canvas.height'", problems)
    canvas_w = raw_canvas.get("width") if isinstance(raw_canvas.get("width"), int) else 160
    canvas_h = raw_canvas.get("height") if isinstance(raw_canvas.get("height"), int) else 180

    # -- layers ----------------------------------------------------------- #
    raw_layers = data.get("layer_defaults", {})
    if not isinstance(raw_layers, dict) or not raw_layers:
        problems.append("'layer_defaults' must be a non-empty object.")
        raw_layers = {}
    layer_defaults: dict[str, str] = {}
    for name, file_name in raw_layers.items():
        _require_str(name, "layer_defaults: layer name", problems)
        _require_str(file_name, f"layer_defaults[{name!r}]", problems)
        if isinstance(file_name, str) and file_name:
            # NB: never rebind ``path`` here — it is the manifest path used
            # below to build every asset's base directory.
            layer_path = path.parent / file_name
            if not layer_path.is_file():
                problems.append(
                    f"layer_defaults[{name!r}]: missing file {file_name!r} "
                    f"(looked at {layer_path})."
                )
            layer_defaults[name] = file_name

    z_order = tuple(data.get("z_order", list(layer_defaults.keys())))
    if not isinstance(data.get("z_order", []), list):
        problems.append("'z_order' must be a list of layer names.")
        z_order = tuple(layer_defaults.keys())
    missing_layers = [n for n in z_order if n not in layer_defaults]
    if missing_layers:
        problems.append(
            "'z_order' references unknown layers: "
            + ", ".join(repr(n) for n in missing_layers) + "."
        )
    if not z_order:
        z_order = tuple(layer_defaults.keys())
    z_order = z_order + tuple(
        n for n in layer_defaults if n not in z_order
    )

    # -- animations ------------------------------------------------------- #
    raw_anims = data.get("animations", {})
    if not isinstance(raw_anims, dict) or not raw_anims:
        problems.append("'animations' must be a non-empty object.")
        raw_anims = {}
    animations: dict[str, Animation] = {}
    for anim_name, raw_anim in raw_anims.items():
        if not isinstance(raw_anim, dict):
            problems.append(f"animation {anim_name!r} must be an object.")
            continue
        frames = _parse_frames(
            raw_anim.get("frames"), anim_name, set(layer_defaults), path.parent,
            problems,
        )
        loop = raw_anim.get("loop", False)
        if not isinstance(loop, bool):
            problems.append(
                f"animation {anim_name!r}: 'loop' must be a boolean."
            )
            loop = False
        animations[anim_name] = Animation(
            name=anim_name, frames=frames, loop=loop
        )

    # -- optional state mapping ------------------------------------------ #
    raw_map = data.get("state_animation_map")
    state_map: dict[str, str] | None = None
    if raw_map is not None:
        if not isinstance(raw_map, dict) or not raw_map:
            problems.append(
                "'state_animation_map' must be a non-empty object "
                "mapping state names to animation names."
            )
        else:
            state_map = {}
            for state, anim_name in raw_map.items():
                _require_str(state, "state_animation_map: state name", problems)
                _require_str(anim_name, f"state_animation_map[{state!r}]", problems)
                if isinstance(anim_name, str) and anim_name not in animations:
                    problems.append(
                        f"state_animation_map[{state!r}] maps to unknown "
                        f"animation {anim_name!r}."
                    )
                state_map[state] = anim_name

    if problems:
        raise StartupError(
            "Invalid avatar manifest:\n" + "\n".join(problems)
        )

    return Manifest(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        canvas_width=canvas_w,
        canvas_height=canvas_h,
        z_order=z_order,
        layer_defaults=layer_defaults,
        animations=animations,
        state_animation_map=state_map,
        base_dir=path.parent,
    )
