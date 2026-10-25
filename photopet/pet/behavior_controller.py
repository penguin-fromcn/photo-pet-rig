"""Behaviour state machine for the desktop pet.

States and priority
-------------------
Exit > DRAG > CLICK_REACT > airborne physics (FALL / LAND) > WALK > IDLE.
PAUSED is a session flag layered on top: it suppresses autonomous walking but
never blocks user dragging, physics or click feedback.

The controller is pure Python (no Qt, no wall clock).  The window feeds it a
``Sensors`` snapshot each frame and reads ``state`` / ``walk_direction``.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .settings import AnimationSettings, BehaviorSettings


class PetState(str, Enum):
    IDLE = "IDLE"
    WALK = "WALK"
    DRAG = "DRAG"
    FALL = "FALL"
    LAND = "LAND"
    CLICK_REACT = "CLICK_REACT"
    PAUSED = "PAUSED"


class ClickKind(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"


@dataclass(frozen=True, slots=True)
class Sensors:
    x: float = 0.0
    bounds_left: float = 0.0
    bounds_right: float = 1920.0
    on_ground: bool = True
    vy: float = 0.0
    landed: bool = False
    impact_speed: float = 0.0
    wall_hit_left: bool = False
    wall_hit_right: bool = False


@dataclass(frozen=True, slots=True)
class StateTransition:
    at: float
    from_state: PetState
    to_state: PetState
    reason: str


@dataclass
class BehaviorController:
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    animation: AnimationSettings = field(default_factory=AnimationSettings)
    rng: random.Random = field(default_factory=random.Random)
    state: PetState = PetState.IDLE
    paused: bool = False
    transition_history: deque[StateTransition] = field(
        default_factory=lambda: deque(maxlen=80)
    )
    events: deque[str] = field(default_factory=lambda: deque(maxlen=80))

    def __post_init__(self) -> None:
        self._clock: float = 0.0
        self._state_entered_at: float = 0.0
        self._walk_direction: int = 0
        self._idle_remaining: float = self._random_idle()
        self._walk_remaining: float = 0.0
        self._land_remaining: float = 0.0
        self._click_remaining: float = 0.0
        self._click_kind: ClickKind | None = None
        self._resume_after_click: PetState | None = None
        self._resume_direction: int = 0
        self._resume_walk_remaining: float = 0.0
        self._last_click_clock: float = -100.0
        self._last_click_ended_clock: float = -100.0
        self._edge_cooldown: float = 0.0
        self._turn_cooldown: float = 0.0
        self._drag_active = False
        self._before_drag_state = PetState.IDLE
        self._before_drag_direction = 0
        self._before_drag_walk_remaining = 0.0
        self._before_drag_idle_remaining = 0.0
        self._before_drag_land_remaining = 0.0
        self._before_drag_click_remaining = 0.0
        self._before_drag_click_kind: ClickKind | None = None
        if not self.behavior.enabled:
            self._set_state(PetState.PAUSED, "autonomous behaviour disabled")

    # ------------------------------------------------------------------
    # Public read-only view
    # ------------------------------------------------------------------
    @property
    def clock(self) -> float:
        return self._clock

    @property
    def state_time(self) -> float:
        return max(0.0, self._clock - self._state_entered_at)

    @property
    def walk_direction(self) -> int:
        if self.state is PetState.WALK and not self.paused:
            return self._walk_direction
        return 0

    @property
    def is_walking(self) -> bool:
        return self.state is PetState.WALK and not self.paused

    @property
    def click_kind(self) -> ClickKind | None:
        return self._click_kind

    @property
    def land_remaining(self) -> float:
        return max(0.0, self._land_remaining)

    @property
    def idle_remaining(self) -> float:
        return max(0.0, self._idle_remaining)

    @property
    def walk_remaining(self) -> float:
        return max(0.0, self._walk_remaining)

    @property
    def drag_active(self) -> bool:
        return self.state is PetState.DRAG

    @property
    def desired_walk_speed(self) -> float:
        if self.state is PetState.WALK and not self.paused:
            return self._walk_direction * self.behavior.walk_speed_px_s
        return 0.0

    @property
    def is_in_air(self) -> bool:
        return self.state in (PetState.FALL, PetState.LAND)

    def drain_events(self) -> list[str]:
        items = list(self.events)
        self.events.clear()
        return items

    def recent_transitions(self, count: int = 12) -> list[StateTransition]:
        return list(self.transition_history)[-count:]

    # ------------------------------------------------------------------
    # State transition helpers
    # ------------------------------------------------------------------
    def _set_state(self, new_state: PetState, reason: str) -> None:
        if new_state is self.state:
            return
        old = self.state
        self.state = new_state
        self._state_entered_at = self._clock
        self.transition_history.append(
            StateTransition(self._clock, old, new_state, reason)
        )
        self.events.append(f"{old.value}->{new_state.value}:{reason}")

    def _random_idle(self) -> float:
        lo = max(0.2, float(self.behavior.idle_min_s))
        hi = max(lo, float(self.behavior.idle_max_s))
        return self.rng.uniform(lo, hi)

    def _random_walk(self) -> float:
        lo = max(0.2, float(self.behavior.walk_min_s))
        hi = max(lo, float(self.behavior.walk_max_s))
        return self.rng.uniform(lo, hi)

    def _enter_idle(self, reason: str, *, schedule: bool = True) -> None:
        self._set_state(PetState.IDLE, reason)
        self._walk_direction = 0
        self._walk_remaining = 0.0
        if schedule:
            self._idle_remaining = self._random_idle()
        self._idle_remaining = max(0.0, self._idle_remaining)

    def _enter_paused(self, reason: str) -> None:
        self._set_state(PetState.PAUSED, reason)
        self._walk_direction = 0
        self._walk_remaining = 0.0

    def _enter_fall(self, reason: str) -> None:
        self._set_state(PetState.FALL, reason)
        self._walk_direction = 0
        self._walk_remaining = 0.0
        self._resume_after_click = None

    def _enter_land(self, reason: str) -> None:
        self._set_state(PetState.LAND, reason)
        self._walk_direction = 0
        self._walk_remaining = 0.0
        self._land_remaining = max(0.05, float(self.behavior.land_duration_s))

    def _choose_direction(self, sensors: Sensors) -> int:
        candidates = [-1, 1]
        if self.rng.random() < 0.5:
            candidates.reverse()
        for direction in candidates:
            edge = self._edge_ahead(sensors, direction)
            if edge == 0:
                return direction
        # Extremely narrow work area: pick a direction (physics will clamp).
        return 1 if self.rng.random() < 0.5 else -1

    def _edge_ahead(self, sensors: Sensors, direction: int) -> int:
        margin = max(0.0, float(self.behavior.edge_margin_px))
        if direction < 0 and sensors.x <= sensors.bounds_left + margin:
            return -1
        if direction > 0 and sensors.x >= sensors.bounds_right - margin:
            return 1
        return 0

    def _start_walk(self, sensors: Sensors, reason: str = "idle timer") -> None:
        self._set_state(PetState.WALK, reason)
        self._walk_direction = self._choose_direction(sensors)
        self._walk_remaining = self._random_walk()
        self._edge_cooldown = 0.25
        self._turn_cooldown = 0.8
        self.events.append(f"walk-start:{self._walk_direction}")

    def _reverse_direction(self, reason: str) -> None:
        if self._walk_direction == 0:
            self._walk_direction = 1 if self.rng.random() < 0.5 else -1
        else:
            self._walk_direction *= -1
        self._edge_cooldown = 0.65
        self._turn_cooldown = 1.0
        self.events.append(f"turn:{self._walk_direction}:{reason}")

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def begin_user_drag(self) -> bool:
        """Enter DRAG.  Returns False if a drag is already active."""

        if self.state is PetState.DRAG:
            return False
        # Remember the behaviour that was interrupted.  A click release uses
        # this to return to the exact pre-press behaviour.
        self._before_drag_state = self.state
        self._before_drag_direction = self._walk_direction
        self._before_drag_walk_remaining = self._walk_remaining
        self._before_drag_idle_remaining = self._idle_remaining
        self._before_drag_land_remaining = self._land_remaining
        self._before_drag_click_remaining = self._click_remaining
        self._before_drag_click_kind = self._click_kind
        self._set_state(PetState.DRAG, "mouse press")
        self._walk_direction = 0
        self._drag_active = True
        return True

    def end_user_drag(self, *, on_ground: bool) -> None:
        """Physics resumes after a real drag (not a click)."""

        if self.state is not PetState.DRAG:
            return
        self._drag_active = False
        if not on_ground:
            self._enter_fall("released in air")
        elif self.paused:
            self._enter_paused("released while paused")
        else:
            self._enter_idle("released on ground")

    def _restore_before_click(self) -> None:
        """Undo the temporary DRAG state when a press turned out to be a click."""

        if self.state is PetState.DRAG:
            self._drag_active = False
            previous = self._before_drag_state
            if previous is PetState.DRAG:
                previous = PetState.IDLE
            if previous is PetState.WALK:
                self._walk_direction = self._before_drag_direction
                self._walk_remaining = self._before_drag_walk_remaining
            elif previous is PetState.LAND:
                self._land_remaining = self._before_drag_land_remaining
            elif previous is PetState.CLICK_REACT:
                self._click_remaining = self._before_drag_click_remaining
                self._click_kind = self._before_drag_click_kind
            elif previous is PetState.IDLE:
                self._idle_remaining = self._before_drag_idle_remaining
            elif previous is PetState.PAUSED:
                pass
            self._set_state(previous, "press was a click")

    def trigger_click(self, kind: ClickKind) -> bool:
        """Start click feedback.  Returns whether the click was accepted.

        Repeated single clicks while CLICK_REACT is active are ignored.  A
        double click may escalate an in-progress single reaction exactly once.
        """

        self._restore_before_click()

        if self.state is PetState.DRAG:
            return False

        if self.state is PetState.CLICK_REACT:
            if kind is ClickKind.SINGLE:
                return False
            if self._click_kind is ClickKind.DOUBLE:
                return False
        else:
            cooldown = max(0.0, self.animation.click_cooldown_s)
            if self._clock - self._last_click_ended_clock < cooldown:
                return False

        if kind is ClickKind.SINGLE and self._clock - self._last_click_clock < 0.08:
            return False

        # Capture the state to return to after the reaction.
        if self.state is not PetState.CLICK_REACT:
            self._resume_after_click = None
            self._resume_direction = 0
            self._resume_walk_remaining = 0.0
            if self.state is PetState.WALK and self.behavior.resume_walk_after_click:
                self._resume_after_click = PetState.WALK
                self._resume_direction = self._walk_direction
                self._resume_walk_remaining = self._walk_remaining
            elif self.state is PetState.IDLE:
                self._resume_after_click = PetState.IDLE

        self._set_state(PetState.CLICK_REACT, f"{kind.value} click")
        self._walk_direction = 0
        self._walk_remaining = self._resume_walk_remaining
        self._click_kind = kind
        self._click_remaining = max(
            0.1,
            float(
                self.animation.double_click_duration_s
                if kind is ClickKind.DOUBLE
                else self.animation.click_duration_s
            ),
        )
        self._last_click_clock = self._clock
        self.events.append(f"click:{kind.value}")
        return True

    def cancel_click(self) -> None:
        """End click feedback immediately (e.g. settings reset / hide)."""

        if self.state is not PetState.CLICK_REACT:
            return
        self._click_remaining = 0.0
        self._last_click_ended_clock = self._clock
        self._finish_click()

    def _finish_click(self) -> None:
        self._last_click_ended_clock = self._clock
        grounded = self._last_sensors.on_ground
        if self.paused:
            if grounded:
                self._enter_paused("click finished while paused")
            else:
                self._enter_fall("click finished in air while paused")
            return
        if self._resume_after_click is PetState.WALK and self.behavior.enabled:
            if not grounded:
                self._enter_fall("click finished in air")
                return
            if self._resume_walk_remaining > 0.05:
                self._set_state(PetState.WALK, "resume walk after click")
                self._walk_direction = (
                    self._resume_direction
                    if self._resume_direction in (-1, 1)
                    else self._choose_direction(self._last_sensors)
                )
                self._walk_remaining = self._resume_walk_remaining
                self._edge_cooldown = 0.2
                return
        if self._resume_after_click is PetState.IDLE:
            if not grounded:
                self._enter_fall("click finished in air")
                return
            self._set_state(PetState.IDLE, "resume idle after click")
            self._idle_remaining = max(0.0, self._idle_remaining)
            return
        if grounded:
            self._enter_idle("click finished", schedule=True)
        else:
            self._enter_fall("click finished in air")
        self._last_click_ended_clock = self._clock

    def set_paused(self, paused: bool) -> None:
        if self.paused == paused:
            return
        self.paused = paused
        if paused:
            self.events.append("paused")
            if self.state in (PetState.IDLE, PetState.WALK):
                self._enter_paused("user paused")
        else:
            self.events.append("resumed")
            if self.state is PetState.PAUSED:
                self._enter_idle("user resumed", schedule=True)
                # Resume promptly instead of making the user wait another
                # full random idle interval.
                self._idle_remaining = min(self._idle_remaining, 0.6)

    def set_autonomous_enabled(self, enabled: bool) -> None:
        self.behavior.enabled = enabled
        if not enabled and self.state is PetState.WALK:
            self._enter_idle("autonomous walking disabled", schedule=True)
        elif enabled and self.state is PetState.PAUSED and not self.paused:
            self._enter_idle("autonomous walking enabled", schedule=True)

    def force_recenter(self) -> None:
        """Called after the window was moved manually; stop walking cleanly."""

        if self.state is PetState.WALK:
            self._enter_idle("recenter", schedule=True)
        self._walk_direction = 0

    # ------------------------------------------------------------------
    # Frame update
    # ------------------------------------------------------------------
    _last_sensors: Sensors = field(default_factory=Sensors, init=False)

    def update(self, dt: float, sensors: Sensors) -> None:
        if not math.isfinite(dt) or dt <= 0.0:
            return
        self._clock += dt
        self._last_sensors = sensors
        self._edge_cooldown = max(0.0, self._edge_cooldown - dt)
        self._turn_cooldown = max(0.0, self._turn_cooldown - dt)

        # 1. Synchronise with the physical world.  Airborne physics outranks
        #    autonomous behaviour, but not drag/click.
        if sensors.landed and self.state is PetState.FALL:
            self._enter_land("floor contact")
        elif not sensors.on_ground and self.state in (
            PetState.IDLE,
            PetState.WALK,
            PetState.PAUSED,
        ):
            self._enter_fall("left the floor")
        elif sensors.on_ground and self.state is PetState.FALL:
            self._enter_land("floor contact")

        # 2. Session pause suppresses only autonomous states.
        if self.paused:
            if self.state in (PetState.IDLE, PetState.WALK):
                self._enter_paused("paused")
        else:
            if self.state is PetState.PAUSED:
                if not sensors.on_ground:
                    self._enter_fall("resumed in the air")
                else:
                    self._enter_idle("resumed", schedule=True)

        # 3. Per-state logic.
        if self.state is PetState.IDLE:
            self._idle_remaining -= dt
            if self._idle_remaining <= 0.0:
                if self.behavior.enabled and not self.paused:
                    self._start_walk(sensors, "idle timer expired")
                else:
                    # Avoid spinning every frame when autonomous walking is
                    # disabled.
                    self._idle_remaining = 1.0
            return

        if self.state is PetState.WALK:
            self._walk_remaining -= dt
            if sensors.wall_hit_left and self._walk_direction < 0:
                self._reverse_direction("left wall")
            elif sensors.wall_hit_right and self._walk_direction > 0:
                self._reverse_direction("right wall")

            edge = self._edge_ahead(sensors, self._walk_direction)
            if edge != 0 and self._edge_cooldown <= 0.0:
                if self.rng.random() < max(
                    0.0, min(1.0, self.behavior.edge_turn_probability)
                ):
                    self._reverse_direction("screen edge")
                    # If the other edge is also close, stop instead of
                    # oscillating on a very narrow screen.
                    if self._edge_ahead(sensors, self._walk_direction) != 0:
                        self._enter_idle("no room to walk", schedule=True)
                        return
                else:
                    self._enter_idle("edge stop (probability)", schedule=True)
                    return

            if (
                self._turn_cooldown <= 0.0
                and self.behavior.random_turn_probability_per_s > 0.0
            ):
                p = 1.0 - math.exp(
                    -self.behavior.random_turn_probability_per_s * dt
                )
                if self.rng.random() < p:
                    self._reverse_direction("random turn")

            if self._walk_remaining <= 0.0:
                self._enter_idle("walk timer expired", schedule=True)
            return

        if self.state is PetState.CLICK_REACT:
            self._click_remaining -= dt
            if self._click_remaining <= 0.0:
                self._finish_click()
            return

        if self.state is PetState.LAND:
            self._land_remaining -= dt
            if self._land_remaining <= 0.0:
                if not sensors.on_ground:
                    self._enter_fall("bounce continued")
                elif self.paused:
                    self._enter_paused("landed while paused")
                else:
                    self._enter_idle("landing settled", schedule=True)
            return

        if self.state is PetState.FALL:
            # Wait for the physics controller to report the floor contact.
            return

        if self.state is PetState.DRAG:
            return

        if self.state is PetState.PAUSED:
            if not sensors.on_ground:
                self._enter_fall("paused and airborne")
            return

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "paused": self.paused,
            "clock": round(self._clock, 3),
            "state_time": round(self.state_time, 3),
            "walk_direction": self._walk_direction,
            "walk_remaining": round(self._walk_remaining, 3),
            "idle_remaining": round(self._idle_remaining, 3),
            "land_remaining": round(self._land_remaining, 3),
            "click_remaining": round(self._click_remaining, 3),
            "click_kind": self._click_kind.value if self._click_kind else None,
            "resume_after_click": (
                self._resume_after_click.value if self._resume_after_click else None
            ),
        }
