"""Layered character renderer using Qt's 2D painting.

* Layer PNGs are decoded once by :mod:`rig_loader` (Pillow) and converted to
  premultiplied QImages.
* Cropped layer images are resampled once per display scale / DPR, then drawn
  under per-bone affine transforms.  Nothing is re-read from disk per frame.
* The output is a transparent QImage; the window only blits that image.
* The same image is used for per-pixel mouse hit testing.

The rig's angle convention is counter-clockwise-positive.  QPainter's positive
rotation is clockwise, hence the ``-angle`` in the transforms.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QRadialGradient,
    QTransform,
    qAlpha,
)

from .animation_controller import PoseFrame
from .rig_loader import CharacterAssets, LoadedLayer
from .settings import RenderSettings, WindowSettings


@dataclass(frozen=True, slots=True)
class DisplayLayout:
    display_height_px: int
    display_scale: float
    padding_px: int
    raw_dpr: float
    render_dpr: float
    window_width: int
    window_height: int
    anchor_local_x: float
    anchor_local_y: float
    frame_width: int
    frame_height: int

    @property
    def window_size(self) -> QSize:
        return QSize(self.window_width, self.window_height)

    @property
    def anchor_local(self) -> QPointF:
        return QPointF(self.anchor_local_x, self.anchor_local_y)


@dataclass(slots=True)
class _RenderLayer:
    definition: object
    image: QImage
    crop_offset: tuple[int, int]
    crop_size: tuple[int, int]
    bone: str


class CharacterRenderer:
    """Transparent offscreen compositor for one character state."""

    def __init__(
        self,
        assets: CharacterAssets,
        render_settings: RenderSettings | None = None,
        window_settings: WindowSettings | None = None,
    ) -> None:
        self.assets = assets
        self.render_settings = render_settings or RenderSettings()
        self.window_settings = window_settings or WindowSettings()
        self._base_layers: list[_RenderLayer] = []
        for loaded in assets.layers:
            self._base_layers.append(
                _RenderLayer(
                    definition=loaded.definition,
                    image=self._pil_to_qimage(loaded.image),
                    crop_offset=loaded.crop_offset,
                    crop_size=loaded.crop_size,
                    bone=loaded.definition.bone,
                )
            )
        self._layer_by_id: dict[str, _RenderLayer] = {
            str(layer.definition.id): layer for layer in self._base_layers
        }
        self._layout: DisplayLayout | None = None
        self._last_frame: QImage | None = None
        self._shadow_enabled = bool(self.window_settings.shadow)
        self.render_count = 0
        self.last_render_ms = 0.0
        self._total_render_ms = 0.0
        self._bone_pivots: dict[str, tuple[float, float]] = {
            name: bone.pivot for name, bone in assets.bones.items()
        }
        self._bone_parents: dict[str, str | None] = {
            name: bone.parent for name, bone in assets.bones.items()
        }
        root_pivot = assets.bones.get("root")
        self._root_pivot = root_pivot.pivot if root_pivot else assets.anchor

    # ------------------------------------------------------------------
    # Layout and pre-scaling
    # ------------------------------------------------------------------
    def configure(
        self,
        display_height_px: int,
        padding_px: int,
        raw_dpr: float,
        *,
        shadow_enabled: bool | None = None,
    ) -> DisplayLayout:
        display_height_px = max(100, int(display_height_px))
        padding_px = max(0, int(padding_px))
        dpr = float(raw_dpr) if math.isfinite(raw_dpr) else 1.0
        dpr = max(1.0, min(4.0, dpr))
        render_dpr = min(dpr, max(1.0, float(self.render_settings.max_dpr)))
        display_scale = display_height_px / max(1, self.assets.canvas_height)
        s = display_scale * render_dpr
        window_width = int(math.ceil(self.assets.canvas_width * display_scale + 2 * padding_px))
        window_height = int(math.ceil(display_height_px + 2 * padding_px))
        anchor_local_x = padding_px + self.assets.anchor[0] * display_scale
        anchor_local_y = padding_px + self.assets.anchor[1] * display_scale
        frame_width = max(1, int(round(window_width * render_dpr)))
        frame_height = max(1, int(round(window_height * render_dpr)))
        if shadow_enabled is not None:
            self._shadow_enabled = bool(shadow_enabled)

        layout = DisplayLayout(
            display_height_px=display_height_px,
            display_scale=display_scale,
            padding_px=padding_px,
            raw_dpr=dpr,
            render_dpr=render_dpr,
            window_width=window_width,
            window_height=window_height,
            anchor_local_x=anchor_local_x,
            anchor_local_y=anchor_local_y,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        self._layout = layout
        return layout

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, pose: PoseFrame) -> QImage:
        layout = self._layout
        if layout is None:
            layout = self.configure(
                self.window_settings.display_height_px,
                self.window_settings.padding_px,
                1.0,
            )
        started = time.perf_counter()
        frame = QImage(
            layout.frame_width,
            layout.frame_height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        frame.fill(Qt.GlobalColor.transparent)

        painter = QPainter(frame)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            s = layout.display_scale * layout.render_dpr
            anchor_px = QPointF(
                layout.anchor_local_x * layout.render_dpr,
                layout.anchor_local_y * layout.render_dpr,
            )
            if self._shadow_enabled and pose.shadow_alpha > 0.001:
                self._draw_shadow(
                    painter, anchor_px, s, pose.shadow_alpha, pose.shadow_scale
                )
            transforms = self._compute_bone_transforms(pose, anchor_px, s)
            # draw_order is back-to-front and authoritative (patches first).
            for layer_id in self.assets.draw_order:
                layer = self._layer_by_id.get(layer_id)
                if layer is None:
                    continue
                transform = transforms.get(layer.bone, transforms["root"])
                # Draw from the full-resolution crop and let QPainter do one
                # smooth scale + rotation.  Pre-scaling every layer separately
                # would open 1px alpha seams at the cut lines (knee/hip), so
                # quality is intentionally preferred over the small saved
                # resampling cost.  Typical measured cost is a few ms/frame.
                offset_x = (layer.crop_offset[0] - self.assets.anchor[0]) * s
                offset_y = (layer.crop_offset[1] - self.assets.anchor[1]) * s
                target = QRectF(
                    offset_x,
                    offset_y,
                    layer.image.width() * s,
                    layer.image.height() * s,
                )
                painter.save()
                painter.setTransform(transform)
                painter.drawImage(target, layer.image)
                painter.restore()
        finally:
            painter.end()

        # Set the DPR only after painting.  QPainter uses a QImage's device
        # pixel ratio as a logical-coordinate scale, so setting it before the
        # paint would multiply every coordinate by DPR twice on high-DPI
        # displays.  The image is always painted in physical pixels.
        frame.setDevicePixelRatio(layout.render_dpr)

        self._last_frame = frame
        self.render_count += 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.last_render_ms = elapsed_ms
        self._total_render_ms += elapsed_ms
        return frame

    def _compute_bone_transforms(
        self, pose: PoseFrame, anchor_px: QPointF, s: float
    ) -> dict[str, QTransform]:
        root_angle = float(pose.bones.get("root", 0.0))
        root_pivot_local = QPointF(
            (self._root_pivot[0] - self.assets.anchor[0]) * s,
            (self._root_pivot[1] - self.assets.anchor[1]) * s,
        )
        ground_local = QPointF(
            0.0,
            (self.assets.ground_y - self.assets.anchor[1]) * s,
        )
        root_transform = QTransform()
        root_transform.translate(anchor_px.x(), anchor_px.y())
        root_transform.translate(
            pose.root_offset_x * s, pose.root_offset_y * s
        )
        root_transform.translate(root_pivot_local.x(), root_pivot_local.y())
        root_transform.rotate(-root_angle)
        root_transform.translate(-root_pivot_local.x(), -root_pivot_local.y())
        root_transform.translate(ground_local.x(), ground_local.y())
        root_transform.scale(max(0.01, pose.scale_x), max(0.01, pose.scale_y))
        root_transform.translate(-ground_local.x(), -ground_local.y())

        transforms: dict[str, QTransform] = {"root": root_transform}
        # Compute in parent-before-child order.  The rig is validated acyclic.
        pending = [
            name
            for name in self.assets.bones
            if name != "root" and name not in transforms
        ]
        guard = 0
        while pending and guard < 1000:
            guard += 1
            remaining: list[str] = []
            for name in pending:
                parent = self._bone_parents.get(name)
                if parent is None:
                    parent_transform = root_transform
                else:
                    parent_transform = transforms.get(parent)
                if parent_transform is None:
                    remaining.append(name)
                    continue
                pivot = self._bone_pivots.get(name, self.assets.anchor)
                pivot_local = QPointF(
                    (pivot[0] - self.assets.anchor[0]) * s,
                    (pivot[1] - self.assets.anchor[1]) * s,
                )
                world = QTransform(parent_transform)
                world.translate(pivot_local.x(), pivot_local.y())
                world.rotate(-float(pose.bones.get(name, 0.0)))
                world.translate(-pivot_local.x(), -pivot_local.y())
                transforms[name] = world
            if len(remaining) == len(pending):
                break
            pending = remaining
        return transforms

    def _draw_shadow(
        self,
        painter: QPainter,
        anchor_px: QPointF,
        s: float,
        alpha_factor: float,
        scale_factor: float,
    ) -> None:
        width = max(8.0, self.assets.visible_width * s * 0.55 * scale_factor)
        height = max(3.0, width * 0.17)
        alpha = int(min(110.0, 80.0 * (alpha_factor / 0.22)))
        if alpha <= 1:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.translate(anchor_px.x(), anchor_px.y())
        painter.scale(1.0, height / width)
        gradient = QRadialGradient(0.0, 0.0, width * 0.5)
        gradient.setColorAt(0.0, QColor(0, 0, 0, alpha))
        gradient.setColorAt(0.55, QColor(0, 0, 0, int(alpha * 0.55)))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(0.0, 0.0), width * 0.5, width * 0.5)
        painter.restore()

    # ------------------------------------------------------------------
    # Hit testing / diagnostics
    # ------------------------------------------------------------------
    def hit_test(self, window_pos: QPointF) -> bool:
        frame = self._last_frame
        layout = self._layout
        if frame is None or layout is None:
            return True
        dpr = layout.render_dpr
        x = int(round(window_pos.x() * dpr))
        y = int(round(window_pos.y() * dpr))
        if x < 0 or y < 0 or x >= frame.width() or y >= frame.height():
            return False
        radius = max(1, int(round(2.5 * dpr)))
        for yy in range(max(0, y - radius), min(frame.height(), y + radius + 1)):
            for xx in range(max(0, x - radius), min(frame.width(), x + radius + 1)):
                if qAlpha(frame.pixel(xx, yy)) >= 8:
                    return True
        return False

    @property
    def layout(self) -> DisplayLayout | None:
        return self._layout

    @property
    def last_frame(self) -> QImage | None:
        return self._last_frame

    @property
    def average_render_ms(self) -> float:
        if self.render_count <= 0:
            return 0.0
        return self._total_render_ms / self.render_count

    def alpha_coverage(self) -> float:
        """Fraction of non-transparent pixels in the last frame (diagnostics)."""

        frame = self._last_frame
        if frame is None or frame.width() <= 0 or frame.height() <= 0:
            return 0.0
        # Sample on a regular grid; cheap and enough for a smoke test.
        step_x = max(1, frame.width() // 32)
        step_y = max(1, frame.height() // 32)
        visible = 0
        total = 0
        for y in range(0, frame.height(), step_y):
            for x in range(0, frame.width(), step_x):
                total += 1
                if qAlpha(frame.pixel(x, y)) > 8:
                    visible += 1
        return (visible / total) if total else 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _pil_to_qimage(self, image: Image.Image) -> QImage:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        width, height = image.size
        data = image.tobytes("raw", "RGBA")
        qimage = QImage(
            data,
            width,
            height,
            width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        # Premultiplied storage avoids dark fringes when Qt rotates/scales with
        # bilinear filtering.
        return qimage.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
