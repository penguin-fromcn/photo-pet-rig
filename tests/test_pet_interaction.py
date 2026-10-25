from __future__ import annotations

import pytest

from photopet.pet.interaction_controller import (
    InteractionController,
    PointerVelocityTracker,
)


def test_stationary_press_release_is_click():
    c = InteractionController(drag_threshold_px=5)
    assert c.press(100, 100, 0.0)
    outcome = c.release(100, 100, 0.12)
    assert outcome is not None
    assert outcome.is_click
    assert not outcome.moved
    assert outcome.velocity == (0.0, 0.0)


def test_small_jitter_is_still_a_click():
    c = InteractionController(drag_threshold_px=6)
    c.press(100, 100, 0.0)
    c.move(103, 102, 0.03)
    outcome = c.release(103, 102, 0.09)
    assert outcome is not None
    assert outcome.is_click
    assert not outcome.moved


def test_large_movement_is_a_drag_and_keeps_release_velocity():
    c = InteractionController(
        drag_threshold_px=5,
        window_s=0.15,
        velocity_gain=1.0,
        max_release_speed_px_s=3000.0,
    )
    c.press(100, 200, 0.0)
    c.move(120, 200, 0.05)
    c.move(160, 200, 0.10)
    c.move(200, 200, 0.15)
    outcome = c.release(200, 200, 0.15)
    assert outcome is not None
    assert not outcome.is_click
    assert outcome.moved
    assert outcome.velocity[0] > 500.0
    assert abs(outcome.velocity[1]) < 1.0


def test_upward_release_yields_negative_vy():
    c = InteractionController(window_s=0.15)
    c.press(400, 500, 0.0)
    c.move(400, 470, 0.03)
    c.move(400, 420, 0.06)
    c.move(400, 360, 0.09)
    outcome = c.release(400, 360, 0.09)
    assert outcome is not None
    assert outcome.velocity[1] < -1000.0


def test_stale_tracker_does_not_create_throw_velocity():
    tracker = PointerVelocityTracker(window_s=0.12)
    tracker.add(0.0, 0.0, 0.0)
    tracker.add(0.02, 100.0, 0.0)
    assert tracker.velocity(0.02)[0] > 0
    # After the user has held still for much longer than the window, there is
    # no recent evidence of motion.
    assert tracker.velocity(2.0) == (0.0, 0.0)


def test_tracker_speed_is_capped():
    tracker = PointerVelocityTracker(window_s=0.2)
    tracker.add(0.0, 0.0, 0.0)
    tracker.add(0.01, 10000.0, 10000.0)
    vx, vy = tracker.velocity(0.01, max_speed=500.0)
    assert (vx * vx + vy * vy) ** 0.5 == pytest.approx(500.0, rel=1e-6)


def test_press_requires_fresh_interaction():
    c = InteractionController()
    assert c.press(0, 0, 0.0)
    assert not c.press(1, 1, 0.1)
    c.cancel()
    assert c.press(1, 1, 0.2)


def test_outcome_reports_offset_and_duration():
    c = InteractionController(drag_threshold_px=4)
    c.press(10, 20, 1.0)
    c.move(60, 40, 1.05)
    outcome = c.release(60, 40, 1.1)
    assert outcome is not None
    assert outcome.duration == pytest.approx(0.1)
    assert outcome.start_position == (10.0, 20.0)
    assert outcome.position == (60.0, 40.0)
    assert outcome.drag_offset == (50.0, 20.0)

