# Peeko avatar assets

This directory is the **only place you need to touch to replace Peeko's
artwork**. The engine renders whatever `manifest.json` describes — no code
changes, ever.

## How it works

- `manifest.json` defines the canvas size, the compositing order of
  *layers*, the base layer files, and every *animation* as a list of
  frames.
- `layers/*.svg` are the actual pictures: one SVG per layer state (body,
  eyes-open, eyes-closed, …). All layer SVGs share the same canvas size,
  so the engine can stack them pixel-perfectly.

The engine loads assets at startup and validates them: a missing file, a
typo'd layer name, or a malformed frame fails fast with a readable error
message (Peeko shows it in a dialog and exits) instead of rendering a
broken robot.

## Quick start — swap in your own robot

1. **Draw your artwork as SVGs at the canvas size** (default `160×180`
   pixels, configurable via `canvas` in the manifest). Transparent
   background, one SVG per layer state.
2. **Open `manifest.json`.**
3. **Point the layers at your files** in `layer_defaults`, e.g.:
   ```json
   "layer_defaults": {
     "body": "layers/my_body.svg",
     "eyes": "layers/my_eyes_open.svg"
   }
   ```
   (You can also add/remove layers and reorder them in `z_order`.)
4. **Adjust the animations** to your liking (timings, bob offsets).
   Nothing else changes.
5. Run `python -m peeko` — your art shows up immediately.

## When you add a brand-new animation

Two steps, still data-only:

1. Drop the SVG(s) into `layers/` (or reuse existing ones).
2. Add an `animation` entry to `manifest.json`, and — if you want a
   behaviour state to play it — add a `state_animation_map` entry so the
   state machine knows which animation a state uses, e.g.:
   ```json
   "state_animation_map": {"sleep": "my_sleep_animation"}
   ```
   The code-side state must exist too (later stages add `sleep`, `talk`,
   …), but the *art* never requires code.

## Manifest reference

| Key                   | Meaning                                                            |
| --------------------- | ------------------------------------------------------------------ |
| `schema_version`      | Format version (must be `1` for this build).                       |
| `canvas`              | Pixel size every layer SVG is rendered at (all layers must be authored at this size). |
| `z_order`             | Compositing order, bottom → top (all `layer_defaults` names must appear). |
| `layer_defaults`      | Base layer name → SVG file (relative to this directory).           |
| `state_animation_map` | Optional: state name → animation name (state machine plays it).     |
| `animations`          | `{ name → {loop, frames[]}}` — see below.                      |

Each **frame** is an object with:

| Frame key       | Meaning                                                    |
| --------------- | ---------------------------------------------------------- |
| `duration_ms`    | **Required** — how long this frame shows (ms).          |
| `dx` / `dy`      | Optional pixel offsets shifting the whole character (bob/bounce). |
| any layer name   | Optional per-frame SVG override for that layer (e.g. swap `eyes` for a blink). |

A frame that lists no layer overrides uses `layer_defaults` as-is.

## Built-in state → animation names

The state machine plays these animations (each name is also the default
animation it uses, and any of them can be remapped via
`state_animation_map`, or re-arted by editing the animations directly):

| Kind     | States                                                             | Required? |
| -------- | ------------------------------------------------------------------ | --------- |
| Core     | `idle`, `blink`, `look_left`, `look_right`, `look_up`, `look_down`, `click`, `dragging` | **Yes** — a missing one fails loudly at startup |
| Reaction | `hover` (pointer enters), `double_click`, `confused` (a menu entry that is not implemented yet) | No — a missing one simply switches that reaction off |

Reactions are one-shot animations that return to `idle` when they finish.
They are deliberately optional so artwork written for Stage 1 keeps working
unchanged; it just has fewer reactions.

## Tips

- Keep every layer SVG exactly `canvas.width × canvas.height` so layers
  stack perfectly. The engine stretches each SVG to the canvas size, but
  artwork authored at the right size will always look best.
- The engine draws a soft ground shadow under the character automatically;
  don't bake one into your body SVG or you'll get a double shadow.
- Small changes are easy: retime the idle bob, change blink speed, add a
  `dy` dip for a "bounce" — all in `manifest.json`, all without code.