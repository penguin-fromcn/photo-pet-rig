from __future__ import annotations

import random

import pytest

from photopet.pet.behavior_controller import (
    BehaviorController,
    ClickKind,
    PetState,
    Sensors,
)
from photopet.pet.settings import AnimationSettings, BehaviorSettings


def make_controller(
    *,
    behavior: dict | None = None,
    animation: dict | None = None,
    seed: int = 7,
) -> BehaviorController:
    b_kwargs = {
        "idle_min_s": 0.2,
        "idle_max_s": 0.2,
        "walk_min_s": 0.6,
        "walk_max_s": 0.6,
        "edge_turn_probability": 1.0,
        "random_turn_probability_per_s": 0.0,
        "land_duration_s": 0.1,
    }
    b_kwargs.update(behavior or {})
    b = BehaviorSettings(**b_kwargs)
    a_kwargs = {
        "click_duration_s": 0.2,
        "double_click_duration_s": 0.35,
        "click_cooldown_s": 0.2,
    }
    a_kwargs.update(animation or {})
    a = AnimationSettings(**a_kwargs)
    return BehaviorController(b, a, rng=random.Random(seed))


def sensors(
    *,
    x: float = 400.0,
    on_ground: bool = True,
    left: float = 40.0,
    right: float = 760.0,
    **kwargs,
) -> Sensors:
    return Sensors(
        x=x,
        bounds_left=left,
        bounds_right=right,
        on_ground=on_ground,
        **kwargs,
    )


def walk_soon(ctrl: BehaviorController) -> None:
    ctrl.update(0.25, sensors())
    assert ctrl.state is PetState.WALK


def test_starts_idle_and_walks_after_random_wait():
    ctrl = make_controller()
    assert ctrl.state is PetState.IDLE
    ctrl.update(0.1, sensors())
    assert ctrl.state is PetState.IDLE
    ctrl.update(0.15, sensors())
    assert ctrl.state is PetState.WALK
    assert ctrl.walk_direction in (-1, 1)


def test_walk_ends_and_returns_to_idle():
    ctrl = make_controller()
    walk_soon(ctrl)
    ctrl.update(0.61, sensors())
    assert ctrl.state is PetState.IDLE


def test_pause_suppresses_walking():
    ctrl = make_controller()
    ctrl.set_paused(True)
    assert ctrl.state is PetState.PAUSED
    for _ in range(20):
        ctrl.update(0.5, sensors())
    assert ctrl.state is PetState.PAUSED
    ctrl.set_paused(False)
    assert ctrl.state is PetState.IDLE


def test_drag_has_priority_over_click_and_walk():
    ctrl = make_controller()
    walk_soon(ctrl)
    assert ctrl.trigger_click(ClickKind.SINGLE)
    assert ctrl.state is PetState.CLICK_REACT
    ctrl.begin_user_drag()
    assert ctrl.state is PetState.DRAG
    # A click must not be delivered while actually dragging.
    ctrl.end_user_drag(on_ground=True)
    assert ctrl.state is PetState.IDLE


def test_click_during_air_returns_to_fall_not_walk():
    ctrl = make_controller()
    ctrl.begin_user_drag()
    ctrl.end_user_drag(on_ground=False)
    assert ctrl.state is PetState.FALL
    assert ctrl.trigger_click(ClickKind.SINGLE)
    assert ctrl.state is PetState.CLICK_REACT
    ctrl.update(0.21, sensors(on_ground=False))
    assert ctrl.state is PetState.FALL


def test_click_during_walk_resumes_walk():
    ctrl = make_controller()
    walk_soon(ctrl)
    direction = ctrl.walk_direction
    assert direction in (-1, 1)
    assert ctrl.trigger_click(ClickKind.SINGLE)
    assert ctrl.state is PetState.CLICK_REACT
    ctrl.update(0.1, sensors())
    assert ctrl.state is PetState.CLICK_REACT
    ctrl.update(0.11, sensors())
    assert ctrl.state is PetState.WALK
    assert ctrl.walk_direction == direction


def test_single_click_repeats_are_ignored_and_double_escalates():
    ctrl = make_controller()
    walk_soon(ctrl)
    assert ctrl.trigger_click(ClickKind.SINGLE)
    assert not ctrl.trigger_click(ClickKind.SINGLE)
    assert ctrl.click_kind is ClickKind.SINGLE
    assert ctrl.trigger_click(ClickKind.DOUBLE)
    assert ctrl.click_kind is ClickKind.DOUBLE
    assert not ctrl.trigger_click(ClickKind.DOUBLE)
    ctrl.update(0.4, sensors())
    # Cooldown after the reaction prevents machine-gun retriggering.
    assert not ctrl.trigger_click(ClickKind.SINGLE)
    ctrl.update(0.3, sensors())
    assert ctrl.trigger_click(ClickKind.SINGLE)


def test_click_while_paused_returns_to_paused():
    ctrl = make_controller()
    ctrl.set_paused(True)
    assert ctrl.trigger_click(ClickKind.SINGLE)
    ctrl.update(0.21, sensors())
    assert ctrl.state is PetState.PAUSED


def test_fall_then_land_then_idle():
    ctrl = make_controller()
    ctrl.begin_user_drag()
    ctrl.end_user_drag(on_ground=False)
    assert ctrl.state is PetState.FALL
    ctrl.update(0.02, sensors(on_ground=True, landed=True, impact_speed=500.0))
    assert ctrl.state is PetState.LAND
    ctrl.update(0.05, sensors(on_ground=True))
    assert ctrl.state is PetState.LAND
    ctrl.update(0.06, sensors(on_ground=True))
    assert ctrl.state is PetState.IDLE


def test_landing_while_paused_returns_to_paused():
    ctrl = make_controller()
    ctrl.set_paused(True)
    ctrl.begin_user_drag()
    ctrl.end_user_drag(on_ground=False)
    assert ctrl.state is PetState.FALL
    ctrl.update(0.02, sensors(on_ground=True, landed=True, impact_speed=300.0))
    assert ctrl.state is PetState.LAND
    ctrl.update(0.12, sensors(on_ground=True))
    assert ctrl.state is PetState.PAUSED


def test_never_enters_walk_while_airborne():
    ctrl = make_controller()
    ctrl.begin_user_drag()
    ctrl.end_user_drag(on_ground=False)
    for _ in range(100):
        ctrl.update(0.3, sensors(on_ground=False))
        assert ctrl.state is not PetState.WALK


def test_airborne_state_overrides_idle():
    ctrl = make_controller()
    assert ctrl.state is PetState.IDLE
    ctrl.update(0.01, sensors(on_ground=False))
    assert ctrl.state is PetState.FALL


def test_edge_turn_probability_one_reverses_direction():
    ctrl = make_controller(behavior={"edge_turn_probability": 1.0})
    walk_soon(ctrl)
    ctrl._walk_direction = 1
    ctrl._walk_remaining = 3.0
    ctrl._edge_cooldown = 0.0
    ctrl.update(0.016, sensors(x=730.0, left=40.0, right=760.0))
    assert ctrl.state is PetState.WALK
    assert ctrl.walk_direction == -1


def test_edge_stop_probability_zero_goes_idle():
    ctrl = make_controller(behavior={"edge_turn_probability": 0.0})
    walk_soon(ctrl)
    ctrl._walk_direction = 1
    ctrl._walk_remaining = 3.0
    ctrl._edge_cooldown = 0.0
    ctrl.update(0.016, sensors(x=730.0, left=40.0, right=760.0))
    assert ctrl.state is PetState.IDLE


def test_direction_choice_avoids_edge():
    ctrl = make_controller(behavior={"edge_turn_probability": 1.0})
    ctrl.update(0.25, sensors(x=45.0, left=40.0, right=760.0))
    assert ctrl.state is PetState.WALK
    assert ctrl.walk_direction == 1


def test_double_click_from_walk_returns_to_walk():
    ctrl = make_controller()
    walk_soon(ctrl)
    direction = ctrl.walk_direction
    assert ctrl.trigger_click(ClickKind.SINGLE)
    assert ctrl.trigger_click(ClickKind.DOUBLE)
    ctrl.update(0.36, sensors())
    assert ctrl.state is PetState.WALK
    assert ctrl.walk_direction == direction


def test_no_click_after_cooldown_blocks_machine_gun():
    ctrl = make_controller(behavior={}, animation={"click_duration_s": 0.1, "click_cooldown_s": 0.5})
    walk_soon(ctrl)
    assert ctrl.trigger_click(ClickKind.SINGLE)
    ctrl.update(0.11, sensors())
    assert ctrl.state is PetState.WALK
    assert not ctrl.trigger_click(ClickKind.SINGLE)
    ctrl.update(0.5, sensors())
    assert ctrl.trigger_click(ClickKind.SINGLE)


def test_click_on_generic_transient_finishes_on_ground():
    ctrl = make_controller()
    ctrl.begin_user_drag()
    ctrl.end_user_drag(on_ground=True)
    assert ctrl.state is PetState.IDLE
    assert ctrl.trigger_click(ClickKind.SINGLE)
    ctrl.update(0.5, sensors())
    assert ctrl.state is PetState.IDLE

