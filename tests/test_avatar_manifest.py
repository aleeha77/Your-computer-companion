"""Avatar asset manifest + asset library tests.

Two layers of the data-driven artwork pipeline are covered here:

* :func:`peeko.avatar.manifest.load_manifest` — parsing and *validation*.
  A broken manifest must fail loudly at startup with a readable message
  listing what is wrong, instead of rendering a wrong or broken robot.
* :class:`peeko.avatar.avatar.assets.AssetLibrary` — rasterising the SVG
  layers the manifest references (needs a Qt application, hence ``qapp``).

The packaged placeholder artwork that ships with the app is validated too,
so "the manifest the owner gets" is always checked by the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from peeko.avatar.assets import AssetLibrary
from peeko.avatar.manifest import load_manifest
from peeko.avatar.widget import DEFAULT_ASSETS_DIR
from peeko.errors import StartupError

from conftest import write_avatar_assets

#: The real, packaged placeholder artwork the app loads by default.
PACKAGED_MANIFEST = DEFAULT_ASSETS_DIR / "manifest.json"


def _broken_manifest(tmp_path: Path, mutate) -> Path:
    """Write the minimal manifest, apply ``mutate`` to the raw JSON, rewrite.

    The valid asset tree is written first and only ``manifest.json`` is
    replaced afterwards, so a mutation that points at a non-existent layer
    file really does point at a file that is not there.
    """
    from conftest import minimal_manifest

    root = tmp_path / "assets"
    manifest_path = write_avatar_assets(root, minimal_manifest())
    data = minimal_manifest()
    mutate(data)
    manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return manifest_path


# --------------------------------------------------------------------------- #
# The manifest that ships with the app
# --------------------------------------------------------------------------- #
def test_packaged_manifest_is_valid():
    manifest = load_manifest(PACKAGED_MANIFEST)
    assert manifest.schema_version == 1
    assert (manifest.canvas_width, manifest.canvas_height) == (160, 180)
    assert "body" in manifest.z_order and "eyes" in manifest.z_order
    assert manifest.base_dir == PACKAGED_MANIFEST.parent


def test_packaged_manifest_covers_every_state_the_engine_needs():
    from peeko.avatar.state_machine import AvatarStateMachine

    # Constructing the machine raises StartupError for any missing state
    # or unknown animation reference.
    machine = AvatarStateMachine(load_manifest(PACKAGED_MANIFEST))
    assert machine.state == "idle"


def test_every_packaged_layer_file_exists():
    """Regression guard: layer paths must resolve inside the assets dir.

    A manifest parser bug once resolved ``layers/x.svg`` relative to the
    layers directory itself, producing ``layers/layers/x.svg`` and a
    doubled ``base_dir``. Every referenced file must exist exactly once.
    """
    manifest = load_manifest(PACKAGED_MANIFEST)
    referenced = set(manifest.layer_defaults.values())
    for anim in manifest.animations.values():
        for frame in anim.frames:
            assert frame.duration_ms > 0
            referenced.update(frame.layer_files.values())
    assert referenced, "manifest references no layer files at all"
    for rel in sorted(referenced):
        assert (manifest.base_dir / rel).is_file(), f"missing packaged layer: {rel}"
        assert "layers/layers/" not in rel


def test_packaged_manifest_declares_looping_and_one_shot_animations():
    manifest = load_manifest(PACKAGED_MANIFEST)
    assert manifest.animations["idle"].loop is True
    assert manifest.animations["dragging"].loop is True
    for name in ("blink", "click", "look_left", "look_up"):
        assert manifest.animations[name].loop is False


# --------------------------------------------------------------------------- #
# Stage 2: the packaged reaction artwork
# --------------------------------------------------------------------------- #
def test_packaged_manifest_ships_every_stage_2_reaction():
    """The artwork for hover / double-click / confused is really packaged.

    Without these animations the reactions would silently switch off, so
    the packaged manifest is checked against the state machine itself
    (``available_reactions``) rather than against a hand-written list.
    """
    from peeko.avatar.state_machine import (
        CONFUSED,
        DOUBLE_CLICK,
        HOVER,
        AvatarStateMachine,
    )

    manifest = load_manifest(PACKAGED_MANIFEST)
    for name in (HOVER, DOUBLE_CLICK, CONFUSED):
        assert name in manifest.animations, f"missing animation {name!r}"
        assert manifest.animations[name].frames, f"{name!r} has no frames"
        # Every reaction is a one-shot: it settles back to idle afterwards.
        assert manifest.animations[name].loop is False

    machine = AvatarStateMachine(manifest)
    assert machine.available_reactions == frozenset(
        {HOVER, DOUBLE_CLICK, CONFUSED}
    )


def test_packaged_double_click_frames_load_including_the_wink_layer(qapp):
    """The new wink eye layer resolves, parses and renders as a pixmap."""
    from peeko.avatar.state_machine import DOUBLE_CLICK

    manifest = load_manifest(PACKAGED_MANIFEST)
    library = AssetLibrary(manifest)
    library.load()

    wink = "layers/eyes_wink.svg"
    used: set[str] = set()
    for frame in manifest.animations[DOUBLE_CLICK].frames:
        used.update(frame.layer_files.values())
        layers = library.frame_layers(frame)
        assert len(layers) == len(manifest.z_order)
        for _name, pixmap in layers:
            assert not pixmap.isNull()
    assert wink in used, "the wink layer is no longer part of double_click"
    assert (manifest.base_dir / wink).is_file()


def test_every_stage_2_reaction_animation_renders(qapp):
    """Each reaction frame paints through the real render path."""
    from peeko.avatar.renderer import draw_frame
    from peeko.avatar.state_machine import CONFUSED, DOUBLE_CLICK, HOVER

    manifest = load_manifest(PACKAGED_MANIFEST)
    library = AssetLibrary(manifest)
    library.load()

    for name in (HOVER, DOUBLE_CLICK, CONFUSED):
        for frame in manifest.animations[name].frames:
            image = QImage(
                manifest.canvas_width,
                manifest.canvas_height,
                QImage.Format_ARGB32_Premultiplied,
            )
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                draw_frame(painter, library, frame)
            finally:
                painter.end()
            assert not image.isNull()
            opaque = sum(
                1
                for y in range(0, image.height(), 8)
                for x in range(0, image.width(), 8)
                if image.pixelColor(x, y).alpha() > 0
            )
            assert opaque > 0, f"{name}: nothing was painted"


# --------------------------------------------------------------------------- #
# Manifest validation errors (each must be clear about what is wrong)
# --------------------------------------------------------------------------- #
def test_missing_manifest_file_raises(tmp_path):
    with pytest.raises(StartupError, match="not found"):
        load_manifest(tmp_path / "nope" / "manifest.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json,", encoding="utf-8")
    with pytest.raises(StartupError, match="not valid JSON"):
        load_manifest(path)


def test_non_object_json_raises(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(StartupError, match="must contain a JSON object"):
        load_manifest(path)


def test_unsupported_schema_version_raises(tmp_path):
    path = _broken_manifest(tmp_path, lambda d: d.update(schema_version=99))
    with pytest.raises(StartupError, match="Unsupported schema_version"):
        load_manifest(path)


def test_missing_layer_file_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d["layer_defaults"].update(body="layers/nope.svg"),
    )
    with pytest.raises(StartupError, match="missing file"):
        load_manifest(path)


def test_bad_layer_name_in_a_frame_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d["animations"]["click"]["frames"][0].update(
            mouth="layers/mouth.svg"
        ),
    )
    with pytest.raises(StartupError, match="unknown key 'mouth'"):
        load_manifest(path)


def test_malformed_frame_missing_duration_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d["animations"]["blink"]["frames"][0].pop("duration_ms"),
    )
    with pytest.raises(StartupError, match="duration_ms"):
        load_manifest(path)


def test_zero_duration_frame_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d["animations"]["blink"]["frames"][0].update(duration_ms=0),
    )
    with pytest.raises(StartupError, match="duration_ms"):
        load_manifest(path)


def test_non_integer_offset_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d["animations"]["idle"]["frames"][1].update(dy="down a bit"),
    )
    with pytest.raises(StartupError, match="'dy' must be an integer"):
        load_manifest(path)


def test_empty_frames_list_raises(tmp_path):
    path = _broken_manifest(
        tmp_path, lambda d: d["animations"]["blink"].update(frames=[])
    )
    with pytest.raises(StartupError, match="non-empty list"):
        load_manifest(path)


def test_unknown_layer_in_z_order_raises(tmp_path):
    path = _broken_manifest(tmp_path, lambda d: d.update(z_order=["hat"]))
    with pytest.raises(StartupError, match="z_order"):
        load_manifest(path)


def test_state_map_to_unknown_animation_raises(tmp_path):
    path = _broken_manifest(
        tmp_path,
        lambda d: d.update(state_animation_map={"idle": "nope"}),
    )
    with pytest.raises(StartupError, match="unknown"):
        load_manifest(path)


def test_all_problems_are_reported_together(tmp_path):
    """A broken manifest reports every problem, not just the first."""
    def break_everything(data: dict) -> None:
        data["schema_version"] = 7
        data["layer_defaults"]["body"] = "layers/missing.svg"
        data["animations"]["blink"]["frames"][0].pop("duration_ms")

    path = _broken_manifest(tmp_path, break_everything)
    with pytest.raises(StartupError) as excinfo:
        load_manifest(path)
    message = excinfo.value.message
    assert "schema_version" in message
    assert "missing file" in message
    assert "duration_ms" in message


def test_valid_manifest_parses_frames_and_layer_overrides(avatar_assets):
    manifest = load_manifest(avatar_assets)
    blink = manifest.animations["blink"]
    # The frame overrides only the eyes layer; body comes from the defaults.
    assert blink.frames[0].layer_files == {"eyes": "layers/eyes_closed.svg"}
    assert manifest.layer_defaults["body"] == "layers/body.svg"
    assert manifest.animations["idle"].frames[1].dy == 2
    assert manifest.state_animation_map is None


# --------------------------------------------------------------------------- #
# Asset library (needs a QApplication -> qapp fixture)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def parsed_manifest(avatar_assets):
    """The throw-away test manifest, parsed and validated."""
    return load_manifest(avatar_assets)


@pytest.fixture()
def library(qapp, parsed_manifest):
    lib = AssetLibrary(parsed_manifest)
    lib.load()
    return lib


def test_library_renders_every_referenced_layer(library, parsed_manifest):
    referenced = set(parsed_manifest.layer_defaults.values())
    for anim in parsed_manifest.animations.values():
        for frame in anim.frames:
            referenced.update(frame.layer_files.values())
    for rel in referenced:
        pixmap = library.pixmap_for(rel)
        assert not pixmap.isNull()
        assert (pixmap.width(), pixmap.height()) == (
            parsed_manifest.canvas_width,
            parsed_manifest.canvas_height,
        )


def test_frame_layers_follow_z_order_and_apply_overrides(library, parsed_manifest):
    blink_frame = parsed_manifest.animations["blink"].frames[0]
    layers = library.frame_layers(blink_frame)
    assert [name for name, _ in layers] == list(parsed_manifest.z_order)
    # The eyes layer is overridden by the frame; the body is the default.
    eyes_pixmap = dict(layers)["eyes"]
    assert eyes_pixmap.toImage() == library.pixmap_for(
        "layers/eyes_closed.svg"
    ).toImage()
    body_pixmap = dict(layers)["body"]
    assert body_pixmap.toImage() == library.pixmap_for("layers/body.svg").toImage()


def test_library_refuses_to_serve_before_loading(avatar_assets):
    manifest = load_manifest(avatar_assets)
    lib = AssetLibrary(manifest)
    with pytest.raises(RuntimeError, match="load\\(\\)"):
        lib.frame_layers(manifest.animations["idle"].frames[0])


def test_library_reports_a_file_deleted_after_validation(avatar_assets):
    manifest = load_manifest(avatar_assets)
    (manifest.base_dir / "layers" / "eyes_open.svg").unlink()
    lib = AssetLibrary(manifest)
    with pytest.raises(StartupError, match="missing"):
        lib.load()


def test_library_rejects_a_file_that_is_not_a_valid_svg(
    qapp, tmp_path, manifest_data
):
    root = tmp_path / "assets"
    manifest_data["layer_defaults"]["body"] = "layers/not_really.svg"
    manifest_path = write_avatar_assets(root, manifest_data)
    (root / "layers" / "not_really.svg").write_text(
        "this is not an SVG", encoding="utf-8"
    )
    lib = AssetLibrary(load_manifest(manifest_path))
    with pytest.raises(StartupError, match="not a valid SVG"):
        lib.load()


def test_pixmap_for_unknown_file_raises(library):
    with pytest.raises(KeyError):
        library.pixmap_for("layers/imaginary.svg")


def test_packaged_assets_load_into_a_library(qapp):
    """The artwork that ships with Peeko loads into usable pixmaps."""
    manifest = load_manifest(PACKAGED_MANIFEST)
    library = AssetLibrary(manifest)
    library.load()
    layers = library.frame_layers(manifest.animations["idle"].frames[0])
    assert len(layers) == len(manifest.z_order)
    for _name, pixmap in layers:
        assert not pixmap.isNull()
