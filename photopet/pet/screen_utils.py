"""Multi-monitor geometry helpers.

Everything the pet simulation uses is expressed in Qt *logical* desktop
coordinates.  Qt already maps those to the physical pixels of each monitor and
handles per-monitor DPI, so no manual scaling of mouse/window coordinates is
needed.  The available geometry (not the full geometry) excludes the taskbar and
docked app bars.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect
from PySide6.QtGui import QGuiApplication

from .physics import Bounds


@dataclass(frozen=True, slots=True)
class ScreenArea:
    name: str
    index: int
    geometry: QRect
    available: QRect
    dpr: float

    def contains(self, point: QPointF | QPoint) -> bool:
        return self.available.contains(QPoint(int(point.x()), int(point.y())))

    @property
    def floor(self) -> int:
        return self.available.bottom()

    def distance_sq_to(self, point: QPointF | QPoint) -> float:
        rect = self.available
        x = min(max(point.x(), rect.left()), rect.right())
        y = min(max(point.y(), rect.top()), rect.bottom())
        return (point.x() - x) ** 2 + (point.y() - y) ** 2


def current_screen_areas() -> list[ScreenArea]:
    app = QGuiApplication.instance()
    if app is None:
        return []
    areas: list[ScreenArea] = []
    for index, screen in enumerate(app.screens()):
        try:
            available = screen.availableGeometry()
            geometry = screen.geometry()
            dpr = float(screen.devicePixelRatio())
        except RuntimeError:
            continue
        areas.append(
            ScreenArea(
                name=screen.name() or f"screen-{index}",
                index=index,
                geometry=QRect(geometry),
                available=QRect(available),
                dpr=dpr,
            )
        )
    return areas


def choose_screen_area(
    point: QPointF | QPoint,
    areas: list[ScreenArea],
    *,
    fallback: ScreenArea | None = None,
) -> ScreenArea:
    if not areas:
        # A sensible synthetic screen keeps headless tests and unusual
        # sessions alive instead of crashing with a None screen.
        return ScreenArea(
            name="fallback",
            index=0,
            geometry=QRect(0, 0, 1920, 1080),
            available=QRect(0, 0, 1920, 1040),
            dpr=1.0,
        )
    # Prefer the work area, then a screen's full area (e.g. pet is over the
    # taskbar of that monitor), then the nearest screen.
    for area in areas:
        if area.available.contains(QPoint(int(point.x()), int(point.y()))):
            return area
    for area in areas:
        if area.geometry.contains(QPoint(int(point.x()), int(point.y()))):
            return area
    if fallback is not None and fallback in areas:
        # If the requested point is in a dead zone but we know which monitor
        # the pet was last on, keep using it.
        return fallback
    return min(areas, key=lambda area: area.distance_sq_to(point))


def choose_screen_for_rect(rect: QRect, areas: list[ScreenArea]) -> ScreenArea:
    center = QPointF(rect.center())
    return choose_screen_area(center, areas)


def virtual_desktop_rect(areas: list[ScreenArea]) -> QRect:
    if not areas:
        return QRect(0, 0, 1920, 1080)
    result = QRect(areas[0].available)
    for area in areas[1:]:
        result = result.united(area.available)
    return result


def bounds_for_area(
    area: ScreenArea, half_width_px: float, margin_px: float = 0.0
) -> Bounds:
    half = max(0.0, half_width_px) + max(0.0, margin_px)
    left = area.available.left() + half
    right = area.available.right() - half
    if left > right:
        center = area.available.center().x()
        left = right = float(center)
    return Bounds(left=left, right=right, floor=float(area.available.bottom()))


def clamp_anchor_to_area(
    x: float,
    y: float,
    area: ScreenArea,
    half_width_px: float,
    margin_px: float = 0.0,
    *,
    snap_above_floor: bool = False,
) -> tuple[float, float]:
    bounds = bounds_for_area(area, half_width_px, margin_px)
    x = min(bounds.right, max(bounds.left, x))
    if snap_above_floor and y > bounds.floor:
        y = bounds.floor
    return (x, y)


def clamp_drag_to_desktop(
    x: float,
    y: float,
    areas: list[ScreenArea],
    half_width_px: float,
    *,
    extra_top_px: float = 320.0,
    extra_bottom_px: float = 80.0,
) -> tuple[float, float]:
    """Keep a dragged pet reachable while allowing it to cross monitor gaps."""

    if not areas:
        return (x, y)
    desktop = virtual_desktop_rect(areas)
    half = max(0.0, half_width_px)
    left = desktop.left() + half
    right = desktop.right() - half
    if left > right:
        left = right = float(desktop.center().x())
    top = desktop.top() - max(0.0, extra_top_px)
    bottom = desktop.bottom() + max(0.0, extra_bottom_px)
    return (min(right, max(left, x)), min(bottom, max(top, y)))


def nearest_reachable_position(
    x: float,
    y: float,
    areas: list[ScreenArea],
    half_width_px: float,
    margin_px: float = 0.0,
) -> tuple[float, float, ScreenArea]:
    point = QPointF(x, y)
    area = choose_screen_area(point, areas)
    bounds = bounds_for_area(area, half_width_px, margin_px)
    cx = min(bounds.right, max(bounds.left, x))
    cy = min(float(area.available.bottom()), max(float(area.available.top()), y))
    return (cx, cy, area)


def point_in_any_available(point: QPointF | QPoint, areas: list[ScreenArea]) -> bool:
    return any(area.contains(point) for area in areas)


def area_signature(area: ScreenArea) -> tuple[object, ...]:
    return (
        area.name,
        area.index,
        area.geometry.x(),
        area.geometry.y(),
        area.geometry.width(),
        area.geometry.height(),
        area.available.x(),
        area.available.y(),
        area.available.width(),
        area.available.height(),
        round(area.dpr, 4),
    )


def describe_areas(areas: list[ScreenArea]) -> list[dict[str, object]]:
    return [
        {
            "name": area.name,
            "index": area.index,
            "geometry": [
                area.geometry.x(),
                area.geometry.y(),
                area.geometry.width(),
                area.geometry.height(),
            ],
            "available": [
                area.available.x(),
                area.available.y(),
                area.available.width(),
                area.available.height(),
            ],
            "dpr": area.dpr,
        }
        for area in areas
    ]
