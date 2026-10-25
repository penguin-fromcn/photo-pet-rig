"""Mouse interaction helpers: click-vs-drag classification and throw velocity.

This module is intentionally free of Qt imports.  The window converts Qt mouse
events to plain coordinates and timestamps, which keeps the tricky parts
(thresholds, recent-path velocity estimation) unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PointerSample:
    t: float
    x: float
    y: float


@dataclass(slots=True)
class PointerVelocityTracker:
    """Least-squares velocity over the most recent path samples.

    A straight ``last - first`` estimate is very noisy for mouse input, so the
    tracker uses a recency-weighted linear regression.  Samples older than the
    configured window are ignored, which means a user who stops before
    releasing gets no artificial throw.
    """

    window_s: float = 0.12
    samples: list[PointerSample] = field(default_factory=list)
    _last_velocity: tuple[float, float] = (0.0, 0.0)

    def reset(self) -> None:
        self.samples.clear()
        self._last_velocity = (0.0, 0.0)

    def add(self, t: float, x: float, y: float) -> None:
        if not (math.isfinite(t) and math.isfinite(x) and math.isfinite(y)):
            return
        if self.samples and t < self.samples[-1].t:
            # A non-monotonic timestamp (rare with synthetic events) resets
            # the history instead of corrupting the regression.
            self.samples.clear()
        self.samples.append(PointerSample(t, x, y))
        cutoff = t - max(0.03, self.window_s) * 2.5
        while len(self.samples) > 2 and self.samples[0].t < cutoff:
            self.samples.pop(0)
        # Hard cap to avoid unbounded growth if move events are extremely dense.
        if len(self.samples) > 240:
            del self.samples[: len(self.samples) - 240]

    def velocity(
        self, now: float | None = None, gain: float = 1.0, max_speed: float = 2700.0
    ) -> tuple[float, float]:
        if not self.samples:
            return (0.0, 0.0)
        now = self.samples[-1].t if now is None else now
        cutoff = now - max(0.03, self.window_s)
        recent = [s for s in self.samples if s.t >= cutoff]
        if len(recent) < 2:
            # If the only sample is very recent and the previous one was just
            # outside the window, there is not enough evidence of motion.
            self._last_velocity = (0.0, 0.0)
            return self._last_velocity

        t0 = recent[-1].t
        # Recency weights: newest samples contribute more.
        weights: list[float] = []
        n = len(recent)
        for idx, _ in enumerate(recent):
            weights.append(0.35 + 0.65 * ((idx + 1) / n))

        total_w = sum(weights)
        mean_t = sum(w * (s.t - t0) for w, s in zip(weights, recent)) / total_w
        mean_x = sum(w * s.x for w, s in zip(weights, recent)) / total_w
        mean_y = sum(w * s.y for w, s in zip(weights, recent)) / total_w
        var_t = sum(
            w * ((s.t - t0) - mean_t) ** 2 for w, s in zip(weights, recent)
        )
        if var_t <= 1e-9:
            self._last_velocity = (0.0, 0.0)
            return self._last_velocity
        cov_x = sum(
            w * ((s.t - t0) - mean_t) * (s.x - mean_x)
            for w, s in zip(weights, recent)
        )
        cov_y = sum(
            w * ((s.t - t0) - mean_t) * (s.y - mean_y)
            for w, s in zip(weights, recent)
        )
        vx = cov_x / var_t
        vy = cov_y / var_t
        if not math.isfinite(vx) or not math.isfinite(vy):
            vx = vy = 0.0
        vx *= gain
        vy *= gain
        speed = math.hypot(vx, vy)
        if speed > max_speed > 0.0:
            factor = max_speed / speed
            vx *= factor
            vy *= factor
        self._last_velocity = (vx, vy)
        return self._last_velocity


@dataclass(frozen=True, slots=True)
class ReleaseOutcome:
    is_click: bool
    moved: bool
    distance: float
    duration: float
    velocity: tuple[float, float]
    position: tuple[float, float]
    start_position: tuple[float, float]

    @property
    def drag_offset(self) -> tuple[float, float]:
        return (
            self.position[0] - self.start_position[0],
            self.position[1] - self.start_position[1],
        )


@dataclass
class InteractionController:
    """Turns a press/move/release sequence into a click or a drag release.

    A press puts the pet into DRAG immediately (so autonomous walking stops),
    but a release below the movement threshold is reclassified as a click.
    This is why a click never causes a click animation during an actual drag.
    """

    drag_threshold_px: float = 5.0
    window_s: float = 0.12
    velocity_gain: float = 1.0
    max_release_speed_px_s: float = 2700.0
    tracker: PointerVelocityTracker = field(init=False)

    def __init__(
        self,
        drag_threshold_px: float = 5.0,
        window_s: float = 0.12,
        velocity_gain: float = 1.0,
        max_release_speed_px_s: float = 2700.0,
    ) -> None:
        self.drag_threshold_px = max(0.5, float(drag_threshold_px))
        self.window_s = max(0.03, float(window_s))
        self.velocity_gain = float(velocity_gain)
        self.max_release_speed_px_s = float(max_release_speed_px_s)
        self.tracker = PointerVelocityTracker(window_s=self.window_s)
        self.active = False
        self.moved = False
        self._start: tuple[float, float] = (0.0, 0.0)
        self._last: tuple[float, float] = (0.0, 0.0)
        self._press_time = 0.0
        self._max_distance = 0.0

    @property
    def dragging(self) -> bool:
        return self.active and self.moved

    @property
    def press_time(self) -> float:
        return self._press_time

    def press(self, x: float, y: float, t: float) -> bool:
        if self.active:
            return False
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(t)):
            return False
        self.active = True
        self.moved = False
        self._start = (float(x), float(y))
        self._last = (float(x), float(y))
        self._press_time = float(t)
        self._max_distance = 0.0
        self.tracker.reset()
        self.tracker.add(t, x, y)
        return True

    def move(self, x: float, y: float, t: float) -> bool:
        if not self.active:
            return False
        self._last = (float(x), float(y))
        self.tracker.add(t, x, y)
        distance = math.hypot(x - self._start[0], y - self._start[1])
        self._max_distance = max(self._max_distance, distance)
        if distance >= self.drag_threshold_px:
            self.moved = True
        return self.moved

    def release(
        self,
        x: float,
        y: float,
        t: float,
        *,
        use_velocity: bool = True,
    ) -> ReleaseOutcome | None:
        if not self.active:
            return None
        self.move(x, y, t)
        distance = math.hypot(x - self._start[0], y - self._start[1])
        is_click = not self.moved and distance < self.drag_threshold_px
        duration = max(0.0, t - self._press_time)
        if is_click or not use_velocity:
            velocity = (0.0, 0.0)
        else:
            velocity = self.tracker.velocity(
                t, gain=self.velocity_gain, max_speed=self.max_release_speed_px_s
            )
        outcome = ReleaseOutcome(
            is_click=is_click,
            moved=self.moved or distance >= self.drag_threshold_px,
            distance=max(distance, self._max_distance),
            duration=duration,
            velocity=velocity,
            position=(float(x), float(y)),
            start_position=self._start,
        )
        self.active = False
        self.moved = False
        self.tracker.reset()
        return outcome

    def cancel(self) -> None:
        self.active = False
        self.moved = False
        self.tracker.reset()

    @property
    def current_velocity(self) -> tuple[float, float]:
        return self.tracker.velocity(
            gain=self.velocity_gain, max_speed=self.max_release_speed_px_s
        )

    def velocity_at(self, now: float) -> tuple[float, float]:
        return self.tracker.velocity(
            now, gain=self.velocity_gain, max_speed=self.max_release_speed_px_s
        )
