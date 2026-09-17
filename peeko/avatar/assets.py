"""Avatar asset loading: render manifest layer SVGs to pixmaps.

:class:`AssetLibrary` turns the (validated) manifest plus the SVG files in
the assets directory into ready-to-draw ``QPixmap`` layers. It has no
knowledge of individual animation names — it renders every file the
manifest references, and :meth:`frame_layers` hands back the ordered layer
pixmaps for a given frame (defaults + per-frame overrides, in
``z_order``).

Requires a running ``QGuiApplication`` (pixmaps are QGui resources). All
work happens at load time; painting is just pixmap blitting, so the UI
thread is never blocked by SVG rasterisation.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from peeko.avatar.manifest import Frame, Manifest
from peeko.errors import StartupError

LOG = logging.getLogger("peeko.avatar.assets")


class AssetLibrary:
    """Loads and caches every layer pixmap described by a manifest."""

    def __init__(self, manifest: Manifest) -> None:
        self._manifest = manifest
        self._pixmaps: dict[str, QPixmap] = {}
        self._loaded = False

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """Rasterise every SVG file referenced by the manifest.

        Raises :class:`peeko.errors.StartupError` (clear, human-readable)
        if a referenced file is missing or not a renderable SVG.
        """
        files: set[str] = set(self._manifest.layer_defaults.values())
        for anim in self._manifest.animations.values():
            for frame in anim.frames:
                files.update(frame.layer_files.values())

        for rel in sorted(files):
            path = self._manifest.base_dir / rel
            if not path.is_file():
                raise StartupError(
                    f"Avatar asset file missing: {path}. Check the asset "
                    f"manifest and the peeko/avatar/assets/ directory."
                )
            self._pixmaps[rel] = self._render(path)
            LOG.debug("Rendered avatar asset: %s (%dx%d)", rel,
                      self._pixmaps[rel].width(), self._pixmaps[rel].height())
        self._loaded = True
        LOG.info("Loaded %d avatar asset layer(s).", len(self._pixmaps))

    # ------------------------------------------------------------------ #
    # Query
    # ------------------------------------------------------------------ #
    def frame_layers(self, frame: Frame) -> list[tuple[str, QPixmap]]:
        """Ordered [(layer_name, pixmap)] for ``frame`` (z-order applied).

        The frame's per-layer file overrides replace the defaults.
        """
        if not self._loaded:
            raise RuntimeError("AssetLibrary.load() must be called first.")
        layers: list[tuple[str, QPixmap]] = []
        for name in self._manifest.z_order:
            file_name = frame.layer_files.get(
                name, self._manifest.layer_defaults[name]
            )
            layers.append((name, self._pixmaps[file_name]))
        return layers

    def pixmap_for(self, rel_file: str) -> QPixmap:
        """Pixmap for a single manifest-referenced file."""
        if rel_file not in self._pixmaps:
            raise KeyError(f"asset not loaded: {rel_file!r}")
        return self._pixmaps[rel_file]

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #
    def _render(self, path: Path) -> QPixmap:
        w, h = self._manifest.canvas_width, self._manifest.canvas_height
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            raise StartupError(
                f"Avatar asset is not a valid SVG: {path}"
            )
        image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            renderer.render(painter, QRectF(0, 0, w, h))
        finally:
            painter.end()
        return QPixmap.fromImage(image)