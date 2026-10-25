"""Deterministic 2D physics for the desktop pet.

Coordinates are screen pixels in Qt's logical desktop coordinate system.
``Body.x`` and ``Body.y`` are the character's ground anchor (between the feet),
not the window's top-left corner.  The caller supplies the current usable
screen rectangle transformed into collision limits.

The module has no Qt dependency and no wall-clock dependency, which makes the
simulation easy to test and to reproduce.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .settings import PhysicsSettings


@dataclass(slots=True)
class Body:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    on_ground: bool = False
    bounce_count: int = 0

    def snapshot(self) -> "Body":
        return Body(
            x=self.x,
            y=self.y,
            vx=self.vx,
            vy=self.vy,
            on_ground=self.on_ground,
            bounce_count=self.bounce_count,
        )


@dataclass(frozen=True, slots=True)
class Bounds:
    """Horizontal limits for the anchor and the floor Y coordinate.

    ``left``/``right`` already include the character's half width; the feet
    anchor is not allowed outside them.
    """

    left: float
    right: float
    floor: float

    def clamp_x(self, x: float) -> float:
        if self.left > self.right:
            return (self.left + self.right) * 0.5
        return min(self.right, max(self.left, x))


@dataclass(slots=True)
class StepResult:
    landed: bool = False
    impact_speed: float = 0.0
    bounced: bool = False
    wall_hit_left: bool = False
    wall_hit_right: bool = False
    substeps: int = 0
    max_impact_speed: float = 0.0

    def merge(self, other: "StepResult") -> None:
        self.landed = self.landed or other.landed
        self.bounced = self.bounced or other.bounced
        self.wall_hit_left = self.wall_hit_left or other.wall_hit_left
        self.wall_hit_right = self.wall_hit_right or other.wall_hit_right
        self.substeps += other.substeps
        if other.impact_speed > self.impact_speed:
            self.impact_speed = other.impact_speed
        self.max_impact_speed = max(self.max_impact_speed, other.max_impact_speed)


@dataclass(slots=True)
class PhysicsWorld:
    """Fixed-step gravity, collision and impulse solver."""

    config: PhysicsSettings
    body: Body
    last_result: StepResult = field(default_factory=StepResult)

    def __post_init__(self) -> None:
        self._sanitize()

    def _sanitize(self) -> None:
        cfg = self.config
        if not math.isfinite(self.body.x):
            self.body.x = 0.0
        if not math.isfinite(self.body.y):
            self.body.y = 0.0
        if not math.isfinite(self.body.vx):
            self.body.vx = 0.0
        if not math.isfinite(self.body.vy):
            self.body.vy = 0.0
        self.body.vx = _clamp_abs(self.body.vx, cfg.max_speed_px_s)
        self.body.vy = _clamp_abs(self.body.vy, cfg.max_speed_px_s)

    @property
    def airborne(self) -> bool:
        return not self.body.on_ground

    def set_position(self, x: float, y: float) -> None:
        self.body.x = x
        self.body.y = y
        self.body.vx = 0.0
        self.body.vy = 0.0
        self.body.on_ground = False
        self.body.bounce_count = 0

    def apply_impulse(self, vx: float = 0.0, vy: float = 0.0) -> None:
        self.body.vx = _clamp_abs(self.body.vx + vx, self.config.max_speed_px_s)
        self.body.vy = _clamp_abs(self.body.vy + vy, self.config.max_speed_px_s)
        if self.body.vy < 0.0:
            self.body.on_ground = False

    def set_velocity(self, vx: float, vy: float) -> None:
        self.body.vx = _clamp_abs(vx, self.config.max_speed_px_s)
        self.body.vy = _clamp_abs(vy, self.config.max_speed_px_s)
        if self.body.vy < 0.0 or self.body.y < 0.0:
            self.body.on_ground = False

    def step(
        self,
        dt: float,
        bounds: Bounds,
        *,
        walking: bool = False,
        apply_ground_friction: bool = True,
    ) -> StepResult:
        """Advance the simulation by ``dt`` seconds.

        ``dt`` is split into fixed substeps, which prevents tunnelling at high
        throw speeds.  Horizontal velocity is preserved when landing; only
        contact friction reduces it over time.  A release therefore never has
        its inertia silently zeroed by the landing code.
        """

        result = StepResult()
        if not math.isfinite(dt) or dt <= 0.0:
            self.last_result = result
            return result

        cfg = self.config
        fixed = max(1.0 / 480.0, min(0.05, cfg.fixed_step_s))
        substeps = max(1, int(math.ceil(dt / fixed)))
        # Never simulate more than 16 substeps for one frame; the window clamps
        # dt already, this is a second safety net.
        substeps = min(substeps, 16)
        h = min(dt / substeps, fixed * 1.5)

        self._sanitize()
        bounds = Bounds(
            left=min(bounds.left, bounds.right) if bounds.left > bounds.right else bounds.left,
            right=max(bounds.left, bounds.right) if bounds.left > bounds.right else bounds.right,
            floor=bounds.floor,
        )

        for _ in range(substeps):
            was_grounded = self.body.on_ground
            # Gravity is always applied.  While standing it is cancelled by
            # the floor contact on the same substep, which keeps impact
            # detection consistent.
            self.body.vy += cfg.gravity_px_s2 * h
            self.body.x += self.body.vx * h
            self.body.y += self.body.vy * h

            # Horizontal collision.
            if self.body.x < bounds.left:
                self.body.x = bounds.left
                if self.body.vx < 0.0:
                    speed = -self.body.vx
                    result.wall_hit_left = True
                    if speed > cfg.min_bounce_speed_px_s * 0.5:
                        self.body.vx = speed * cfg.wall_restitution
                    else:
                        self.body.vx = 0.0
            elif self.body.x > bounds.right:
                self.body.x = bounds.right
                if self.body.vx > 0.0:
                    speed = self.body.vx
                    result.wall_hit_right = True
                    if speed > cfg.min_bounce_speed_px_s * 0.5:
                        self.body.vx = -speed * cfg.wall_restitution
                    else:
                        self.body.vx = 0.0

            # Floor collision / bounce.
            if self.body.y >= bounds.floor:
                self.body.y = bounds.floor
                impact = max(0.0, self.body.vy)
                if was_grounded:
                    # Standing contact: do not report repeated landings.
                    self.body.vy = 0.0
                    self.body.on_ground = True
                elif impact > cfg.min_bounce_speed_px_s:
                    self.body.vy = -impact * cfg.restitution
                    self.body.on_ground = False
                    self.body.bounce_count += 1
                    result.landed = True
                    result.bounced = True
                    result.impact_speed = max(result.impact_speed, impact)
                else:
                    self.body.vy = 0.0
                    self.body.on_ground = True
                    result.landed = True
                    result.impact_speed = max(result.impact_speed, impact)
            else:
                # The anchor is above the floor; if it was resting, it is now
                # in free fall (e.g. the floor moved after a monitor change).
                if self.body.y < bounds.floor - 0.01:
                    self.body.on_ground = False

            if self.body.on_ground:
                if apply_ground_friction and not walking:
                    friction = math.exp(-cfg.ground_friction_per_s * h)
                    self.body.vx *= friction
            else:
                drag = math.exp(-cfg.air_drag_per_s * h)
                self.body.vx *= drag

            self.body.vx = _clamp_abs(self.body.vx, cfg.max_speed_px_s)
            self.body.vy = _clamp_abs(self.body.vy, cfg.max_speed_px_s)
            result.substeps += 1

        # Kill microscopic horizontal drift so the window stops repainting.
        if self.body.on_ground and abs(self.body.vx) < 0.5:
            self.body.vx = 0.0

        result.max_impact_speed = result.impact_speed
        self.last_result = result
        return result

    def simulate_to_rest(
        self, bounds: Bounds, dt: float = 1.0 / 120.0, max_seconds: float = 20.0
    ) -> list[Body]:
        """Utility for tests / diagnostics; returns snapshots over time."""

        snapshots: list[Body] = [self.body.snapshot()]
        steps = max(1, int(max_seconds / dt))
        for _ in range(steps):
            self.step(dt, bounds)
            snapshots.append(self.body.snapshot())
            if (
                self.body.on_ground
                and abs(self.body.vx) < 0.01
                and abs(self.body.vy) < 0.01
            ):
                break
        return snapshots


def _clamp_abs(value: float, limit: float) -> float:
    if not math.isfinite(value):
        return 0.0
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


def peak_height_after_bounce(
    initial_up_speed: float, restitution: float, gravity: float
) -> float:
    """Analytic helper used by tests: peak height above the floor."""

    if initial_up_speed <= 0.0 or gravity <= 0.0:
        return 0.0
    rebound = initial_up_speed * restitution
    return (rebound * rebound) / (2.0 * gravity)
