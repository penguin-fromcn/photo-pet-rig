from __future__ import annotations

import math

import pytest

from photopet.pet.physics import Body, Bounds, PhysicsWorld, peak_height_after_bounce
from photopet.pet.settings import PhysicsSettings


def make_world(
    *,
    x: float = 500.0,
    y: float = 0.0,
    vx: float = 0.0,
    vy: float = 0.0,
    on_ground: bool = False,
    bounds: Bounds | None = None,
    **cfg_overrides,
) -> tuple[PhysicsWorld, Bounds]:
    cfg = PhysicsSettings(**cfg_overrides)
    world = PhysicsWorld(
        cfg,
        Body(x=x, y=y, vx=vx, vy=vy, on_ground=on_ground),
    )
    bounds = bounds or Bounds(left=0.0, right=1000.0, floor=800.0)
    return world, bounds


def test_gravity_accelerates_downward_and_moves_body():
    world, bounds = make_world(y=0.0)
    world.step(0.1, bounds)
    assert world.body.vy > 0
    assert world.body.y > 0
    assert not world.body.on_ground


def test_floor_stops_penetration_and_rests():
    world, bounds = make_world(y=0.0, on_ground=False, gravity_px_s2=1800.0)
    for _ in range(1200):
        world.step(1.0 / 120.0, bounds)
        if world.body.on_ground and abs(world.body.vy) < 1e-9:
            break
    assert world.body.y == pytest.approx(bounds.floor, abs=0.01)
    assert world.body.on_ground
    assert abs(world.body.vy) < 1e-6


def test_bounce_heights_decrease_and_settle():
    world, bounds = make_world(
        y=0.0,
        on_ground=False,
        restitution=0.5,
        min_bounce_speed_px_s=20.0,
        gravity_px_s2=1800.0,
    )
    # Start at the floor with a large upward velocity to create clean bounces.
    world.body.y = bounds.floor
    world.body.vy = -1400.0
    world.body.on_ground = False
    apexes: list[float] = []
    rising = True
    last_bounce_count = 0
    for _ in range(1200):
        world.step(1.0 / 240.0, bounds)
        height = bounds.floor - world.body.y
        if rising and world.body.vy >= 0.0:
            apexes.append(height)
            rising = False
        if not rising and world.body.vy < -1.0:
            rising = True
        if world.body.bounce_count != last_bounce_count:
            last_bounce_count = world.body.bounce_count
    assert world.body.on_ground
    assert world.body.bounce_count >= 2
    tall_apexes = [h for h in apexes if h > 1.0]
    assert len(tall_apexes) >= 2
    for earlier, later in zip(tall_apexes, tall_apexes[1:]):
        assert later <= earlier + 1.0


def test_horizontal_wall_clamps_and_bounces():
    bounds = Bounds(left=100.0, right=500.0, floor=800.0)
    world, _ = make_world(x=100.0, y=800.0, vx=-600.0, on_ground=True, bounds=bounds)
    result = world.step(0.05, bounds)
    assert result.wall_hit_left
    assert world.body.x >= bounds.left
    assert world.body.vx > 0.0
    world2, _ = make_world(x=500.0, y=800.0, vx=600.0, on_ground=True, bounds=bounds)
    result2 = world2.step(0.05, bounds)
    assert result2.wall_hit_right
    assert world2.body.x <= bounds.right
    assert world2.body.vx < 0.0


def test_no_tunnelling_with_fast_vertical_speed():
    bounds = Bounds(left=0.0, right=1000.0, floor=200.0)
    world, _ = make_world(y=-50.0, vy=6000.0, on_ground=False, bounds=bounds)
    result = world.step(0.1, bounds)
    assert world.body.y <= bounds.floor + 1e-6
    assert world.body.on_ground or world.body.vy < 0.0
    assert result.landed or result.bounced


def test_horizontal_inertia_survives_landing():
    world, bounds = make_world(
        x=500.0,
        y=799.5,
        vx=420.0,
        vy=0.0,
        on_ground=False,
        bounds=Bounds(left=0.0, right=1000.0, floor=800.0),
        ground_friction_per_s=0.2,
    )
    for _ in range(20):
        world.step(1.0 / 120.0, bounds)
    assert world.body.on_ground
    assert world.body.vx > 250.0  # far from zero: inertia is preserved


def test_floor_above_body_snaps_and_grounds():
    world, bounds = make_world(
        x=500.0,
        y=800.0,
        on_ground=True,
        bounds=Bounds(left=0.0, right=1000.0, floor=800.0),
    )
    world.step(0.016, Bounds(left=0.0, right=1000.0, floor=720.0))
    assert world.body.y == pytest.approx(720.0)
    assert world.body.on_ground


def test_non_finite_values_are_sanitized():
    world, bounds = make_world(x=math.nan, y=math.inf, vx=math.inf, vy=math.nan)
    world.step(0.016, bounds)
    assert math.isfinite(world.body.x)
    assert math.isfinite(world.body.y)
    assert math.isfinite(world.body.vx)
    assert math.isfinite(world.body.vy)


def test_peak_height_formula_matches_simulation():
    gravity = 1800.0
    restitution = 0.5
    initial_up = 900.0
    # peak_height_after_bounce() takes the *impact* speed before restitution.
    assert peak_height_after_bounce(initial_up, restitution, gravity) == pytest.approx(
        (initial_up * restitution) ** 2 / (2 * gravity)
    )
    # A direct 900 px/s launch should peak at v²/(2g).
    world, bounds = make_world(
        y=800.0,
        on_ground=True,
        restitution=restitution,
        gravity_px_s2=gravity,
        min_bounce_speed_px_s=10.0,
    )
    world.body.vy = -initial_up
    world.body.on_ground = False
    peak = 0.0
    for _ in range(400):
        world.step(1.0 / 240.0, bounds)
        peak = max(peak, bounds.floor - world.body.y)
        if world.body.vy >= 0 and peak > 0:
            break
    assert peak == pytest.approx(initial_up**2 / (2 * gravity), rel=0.02)


def test_speed_is_capped():
    world, bounds = make_world(
        y=0.0, vy=20000.0, x=500.0, bounds=Bounds(0.0, 1000.0, 100000.0)
    )
    world.step(0.01, bounds)
    assert abs(world.body.vy) <= world.config.max_speed_px_s + 1e-6

