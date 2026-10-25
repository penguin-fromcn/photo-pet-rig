"""Procedural bone animation built on top of the photo-cutout rig.

All angles are expressed in the rig's convention: positive degrees rotate
counter-clockwise on screen (OpenCV's ``getRotationMatrix2D`` convention).
The Qt renderer negates them because QPainter's positive rotation is clockwise.

The controller blends targets with exponential smoothing so state changes never
snap, and adds a small spring for landing compression.  It does not modify any
pixels: the face in ``head.png`` is always the original photo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .behavior_controller import ClickKind, PetState
from .settings import AnimationSettings

REQUIRED_BONES: tuple[str, ...] = (
    "root",
    "torso",
    "head",
    "upper_arm_left",
    "forearm_left",
    "hand_left",
    "upper_arm_right",
    "forearm_right",
    "hand_right",
    "thigh_left",
    "shin_left",
    "foot_left",
    "thigh_right",
    "shin_right",
    "foot_right",
)

# Conservative fallback if a rig has no safe range information.
DEFAULT_LIMITS: dict[str, tuple[float, float]] = {
    "root": (-5.0, 5.0),
    "torso": (-3.0, 3.0),
    "head": (-5.0, 5.0),
    "upper_arm_left": (-5.0, 5.0),
    "forearm_left": (-4.0, 4.0),
    "hand_left": (-4.0, 4.0),
    "upper_arm_right": (-5.0, 5.0),
    "forearm_right": (-4.0, 4.0),
    "hand_right": (-4.0, 4.0),
    "thigh_left": (-7.0, 7.0),
    "shin_left": (-2.0, 14.0),
    "foot_left": (-5.0, 5.0),
    "thigh_right": (-7.0, 7.0),
    "shin_right": (-2.0, 14.0),
    "foot_right": (-5.0, 5.0),
}


def _clamp(value: float, lo: float, hi: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(hi, max(lo, value))


def _damp(current: float, target: float, rate: float, dt: float) -> float:
    if rate <= 0.0:
        return target
    alpha = 1.0 - math.exp(-rate * dt)
    return current + (target - current) * alpha


@dataclass(frozen=True, slots=True)
class PoseFrame:
    """Per-frame transform output consumed by :mod:`character_renderer`."""

    bones: dict[str, float]
    root_offset_x: float = 0.0
    root_offset_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    shadow_alpha: float = 0.2
    shadow_scale: float = 1.0
    state: str = PetState.IDLE.value

    def angle(self, bone: str) -> float:
        return self.bones.get(bone, 0.0)


@dataclass
class AnimationController:
    settings: AnimationSettings = field(default_factory=AnimationSettings)
    limits: dict[str, tuple[float, float]] = field(default_factory=dict)
    state: PetState = PetState.IDLE
    click_kind: ClickKind | None = None

    def __post_init__(self) -> None:
        self.limits = {
            **DEFAULT_LIMITS,
            **(self.limits or {}),
        }
        self._state_time = 0.0
        self._total_time = 0.0
        self._walk_phase = 0.0
        self._click_elapsed = 0.0
        self._click_kind = ClickKind.SINGLE
        self._click_triggered = False
        self._bones: dict[str, float] = {name: 0.0 for name in REQUIRED_BONES}
        self._root_x = 0.0
        self._root_y = 0.0
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._squash = 0.0
        self._squash_vel = 0.0
        self._shadow_alpha = 0.2
        self._shadow_scale = 1.0
        self._last_pose = self._make_pose()
        self._last_landing_impact = 0.0
        self._last_prev_state = self.state

    # ------------------------------------------------------------------
    # External events
    # ------------------------------------------------------------------
    def trigger_click(self, kind: ClickKind) -> None:
        self._click_kind = kind
        self.click_kind = kind
        self._click_elapsed = 0.0
        self._click_triggered = True
        strength = 0.5 if kind is ClickKind.SINGLE else 0.85
        self._squash = min(1.2, self._squash + strength)
        self._squash_vel += 1.2

    def trigger_landing(self, impact_speed: float) -> None:
        impact = max(0.0, float(impact_speed))
        self._last_landing_impact = impact
        amount = _clamp(0.18 + impact / 1100.0, 0.18, 1.0)
        self._squash = min(1.25, self._squash + amount)
        self._squash_vel += 0.8

    def reset(self) -> None:
        self.__post_init__()

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------
    def update(
        self,
        dt: float,
        *,
        state: PetState,
        on_ground: bool,
        vx: float = 0.0,
        vy: float = 0.0,
        drag_vx: float = 0.0,
        drag_vy: float = 0.0,
        landed: bool = False,
        impact_speed: float = 0.0,
    ) -> PoseFrame:
        if not math.isfinite(dt) or dt <= 0.0:
            return self._last_pose

        if state is not self.state:
            self._enter_state(state)
        if landed:
            self.trigger_landing(impact_speed)

        self._state_time += dt
        self._total_time += dt
        if state is PetState.WALK:
            self._walk_phase += (
                dt * max(0.05, self.settings.walk_cycle_hz) * math.tau
            )
        if state is PetState.CLICK_REACT:
            self._click_elapsed += dt

        self._step_spring(dt)

        targets = self._targets(
            state=state,
            on_ground=on_ground,
            vx=vx,
            vy=vy,
            drag_vx=drag_vx,
            drag_vy=drag_vy,
        )
        rate = max(0.1, self.settings.smoothing_per_s)
        for name in REQUIRED_BONES:
            self._bones[name] = _damp(
                self._bones[name], targets["bones"].get(name, 0.0), rate, dt
            )
            lo, hi = self.limits.get(name, (-30.0, 30.0))
            self._bones[name] = _clamp(self._bones[name], lo, hi)

        self._root_x = _clamp(
            _damp(self._root_x, targets["root_x"], rate, dt), -60.0, 60.0
        )
        self._root_y = _clamp(
            _damp(self._root_y, targets["root_y"], rate, dt), -45.0, 45.0
        )
        self._scale_x = _clamp(
            _damp(self._scale_x, targets["scale_x"], rate * 1.2, dt), 0.82, 1.15
        )
        self._scale_y = _clamp(
            _damp(self._scale_y, targets["scale_y"], rate * 1.2, dt), 0.82, 1.15
        )
        self._shadow_alpha = _damp(
            self._shadow_alpha, targets["shadow_alpha"], 6.0, dt
        )
        self._shadow_scale = _damp(
            self._shadow_scale, targets["shadow_scale"], 6.0, dt
        )

        self._last_pose = self._make_pose()
        return self._last_pose

    def _enter_state(self, new_state: PetState) -> None:
        previous = self.state
        self.state = new_state
        self._state_time = 0.0
        if new_state is not PetState.CLICK_REACT:
            self.click_kind = None
            self._click_triggered = False
        if new_state is PetState.WALK:
            self._walk_phase = 0.0
        if new_state is PetState.CLICK_REACT:
            self._click_elapsed = 0.0
            if previous is not PetState.CLICK_REACT:
                self._click_kind = ClickKind.SINGLE
                self._click_triggered = False
            if not self._click_triggered:
                self.trigger_click(self._click_kind)
        self._last_prev_state = previous

    # ------------------------------------------------------------------
    # Target generation
    # ------------------------------------------------------------------
    def _targets(
        self,
        *,
        state: PetState,
        on_ground: bool,
        vx: float,
        vy: float,
        drag_vx: float,
        drag_vy: float,
    ) -> dict[str, object]:
        motion = _clamp(self.settings.motion_scale, 0.0, 2.0)
        bones = {name: 0.0 for name in REQUIRED_BONES}
        root_x = 0.0
        root_y = 0.0
        scale_x = 1.0
        scale_y = 1.0
        shadow_alpha = 0.22 if on_ground else 0.07
        shadow_scale = 1.0 if on_ground else 0.8

        if state is PetState.IDLE:
            if self.settings.idle_breathing:
                phase = self._state_time * math.tau / 2.6
                breath = math.sin(phase)
                bones["torso"] = self.settings.idle_breath_deg * breath * motion
                bones["head"] = (
                    self.settings.idle_head_deg
                    * math.sin(phase - 0.45)
                    * motion
                )
                bones["upper_arm_left"] = (
                    self.settings.idle_arm_deg * 0.7 * breath * motion
                )
                bones["upper_arm_right"] = (
                    -self.settings.idle_arm_deg * 0.5 * breath * motion
                )
                bones["forearm_left"] = (
                    self.settings.idle_arm_deg * 0.35 * math.sin(phase - 0.5) * motion
                )
                bones["forearm_right"] = (
                    -self.settings.idle_arm_deg * 0.3 * math.sin(phase - 0.5) * motion
                )
                root_y = (
                    -self.settings.idle_bob_canvas_px
                    * 0.5
                    * (1.0 - math.cos(phase))
                    * motion
                )
            shadow_alpha = 0.22
        elif state is PetState.WALK:
            phase = self._walk_phase
            s = math.sin(phase)
            # Alternate legs.  Knees only bend on the swinging side.
            left_swing = max(0.0, s)
            right_swing = max(0.0, -s)
            thigh = self.settings.walk_thigh_deg * motion
            knee = self.settings.walk_knee_deg * motion
            foot = self.settings.walk_foot_deg * motion
            bones["thigh_left"] = thigh * s
            bones["thigh_right"] = -thigh * s
            bones["shin_left"] = knee * left_swing
            bones["shin_right"] = knee * right_swing
            bones["foot_left"] = -0.4 * bones["thigh_left"] - 0.2 * bones["shin_left"]
            bones["foot_right"] = (
                -0.4 * bones["thigh_right"] - 0.2 * bones["shin_right"]
            )
            bones["foot_left"] = _clamp(bones["foot_left"], -foot, foot)
            bones["foot_right"] = _clamp(bones["foot_right"], -foot, foot)

            sway = math.sin(phase)
            bones["torso"] = self.settings.walk_torso_deg * sway * 0.7 * motion
            bones["head"] = (
                -self.settings.walk_head_deg * math.sin(phase + 0.25) * motion
            )
            # Counter-swing arms and a delayed forearm follow-through.
            bones["upper_arm_left"] = -self.settings.walk_arm_deg * s * motion
            bones["upper_arm_right"] = self.settings.walk_arm_deg * s * motion
            bones["forearm_left"] = (
                -self.settings.walk_forearm_deg * math.sin(phase - 0.55) * motion
            )
            bones["forearm_right"] = (
                self.settings.walk_forearm_deg * math.sin(phase - 0.55) * motion
            )
            bones["root"] = 0.35 * math.sin(phase) * motion
            root_y = (
                -self.settings.walk_bob_canvas_px
                * (0.5 - 0.5 * math.cos(2.0 * phase))
                * motion
            )
            root_x = 0.5 * math.sin(phase) * motion
            # Tiny contact compression, mostly hidden at 30fps but keeps the
            # gait from feeling mechanically rigid.
            scale_y = 1.0 - 0.004 * (0.5 + 0.5 * math.cos(2.0 * phase)) * motion
            scale_x = 1.0 + 0.003 * (0.5 + 0.5 * math.cos(2.0 * phase)) * motion
            shadow_alpha = 0.22
        elif state is PetState.DRAG:
            vx_c = _clamp(drag_vx * motion, -1800.0, 1800.0)
            vy_c = _clamp(drag_vy * motion, -1800.0, 1800.0)
            root_angle = _clamp(-vx_c * 0.0028, -3.0, 3.0)
            bones["root"] = root_angle
            bones["torso"] = root_angle * 0.55
            bones["head"] = _clamp(
                -vx_c * 0.0017 + vy_c * 0.0004, -2.5, 2.5
            )
            lags = _clamp(-vx_c * 0.0012, -2.0, 2.0)
            bones["upper_arm_left"] = lags
            bones["upper_arm_right"] = lags
            bones["forearm_left"] = lags * 0.8
            bones["forearm_right"] = lags * 0.8
            bones["thigh_left"] = _clamp(-vx_c * 0.0008, -1.8, 1.8)
            bones["thigh_right"] = bones["thigh_left"] * 0.8
            bones["shin_left"] = 1.2
            bones["shin_right"] = 1.2
            root_x = _clamp(-vx_c * 0.03, -40.0, 40.0)
            root_y = _clamp(-vy_c * 0.024, -24.0, 24.0)
            stretch = _clamp(abs(vy_c) * 0.000035, 0.0, 0.03)
            scale_y = 1.0 + stretch
            scale_x = 1.0 - stretch * 0.5
            shadow_alpha = 0.05
            shadow_scale = 0.65
        elif state is PetState.FALL:
            speed = math.hypot(vx, vy)
            factor = _clamp(speed / 1400.0, 0.0, 1.0)
            lean = _clamp(-vx * 0.0015, -3.0, 3.0)
            bones["root"] = lean
            bones["torso"] = lean * 0.4
            bones["head"] = _clamp(
                -lean * 0.5 + _clamp(vy * 0.0004, -1.0, 1.0), -2.5, 2.5
            )
            bones["thigh_left"] = 1.1 + 0.8 * factor
            bones["thigh_right"] = -1.1 - 0.8 * factor
            bones["shin_left"] = 2.0 + 2.2 * factor
            bones["shin_right"] = 2.0 + 2.2 * factor
            bones["foot_left"] = -1.0
            bones["foot_right"] = -1.0
            bones["upper_arm_left"] = 1.0 + 0.6 * factor
            bones["upper_arm_right"] = -1.0 - 0.6 * factor
            bones["forearm_left"] = 0.6
            bones["forearm_right"] = 0.6
            scale_y = 1.0 + 0.018 * factor
            scale_x = 1.0 - 0.009 * factor
            root_y = _clamp(-vy * 0.002, -6.0, 6.0)
            shadow_alpha = 0.05
            shadow_scale = 0.7
        elif state is PetState.LAND:
            compression = max(0.0, self._squash)
            bones["head"] = -self.settings.land_tilt_deg * 0.5 * compression
            bones["torso"] = self.settings.land_tilt_deg * 0.55 * compression
            bones["root"] = -self.settings.land_tilt_deg * 0.2 * compression
            bones["thigh_left"] = 0.8 + 1.2 * compression
            bones["thigh_right"] = -0.8 - 1.2 * compression
            bones["shin_left"] = 2.0 + 4.5 * compression
            bones["shin_right"] = 2.0 + 4.5 * compression
            bones["foot_left"] = -1.0
            bones["foot_right"] = -1.0
            bones["upper_arm_left"] = 0.8
            bones["upper_arm_right"] = -0.8
            shadow_alpha = 0.24
        elif state is PetState.CLICK_REACT:
            t = self._click_elapsed
            energy = math.exp(-2.6 * t)
            wave = math.sin(math.tau * 2.0 * t)
            big = 1.25 if self._click_kind is ClickKind.DOUBLE else 1.0
            head_amp = 3.6 if self._click_kind is ClickKind.SINGLE else 4.3
            bones["head"] = -head_amp * energy * wave * big
            bones["torso"] = 1.0 * energy * math.sin(
                math.tau * 2.0 * t + 0.35
            ) * big
            bones["root"] = 1.0 * energy * math.sin(
                math.tau * 2.0 * t + 0.15
            ) * big
            bones["upper_arm_left"] = 0.9 * energy * math.sin(
                math.tau * 2.0 * t + 1.2
            ) * big
            bones["upper_arm_right"] = -0.9 * energy * math.sin(
                math.tau * 2.0 * t + 1.2
            ) * big
            bones["forearm_left"] = 0.5 * energy * math.sin(
                math.tau * 2.0 * t + 0.8
            ) * big
            bones["forearm_right"] = -0.5 * energy * math.sin(
                math.tau * 2.0 * t + 0.8
            ) * big
            pulse = abs(math.sin(math.tau * 2.0 * t)) * energy
            bones["shin_left"] = 1.2 + 2.0 * pulse
            bones["shin_right"] = 1.2 + 2.0 * pulse
            bones["thigh_left"] = 0.5 * pulse
            bones["thigh_right"] = -0.5 * pulse
            root_y = -3.0 * pulse * big
            scale_y = 1.0 + 0.012 * pulse
            if not on_ground:
                shadow_alpha = 0.05
                shadow_scale = 0.7
        elif state is PetState.PAUSED:
            # Natural standing pose: every target is zero.  Smoothing returns
            # the character to the unmodified photo pose.
            pass

        # Landing spring affects all states where the body is grounded, so a
        # bounce visibly compresses even while the click overlay is active.
        compression = self._squash
        if abs(compression) > 0.0005:
            depth = _clamp(1.0 - self.settings.land_squash, 0.0, 0.25)
            scale_y *= 1.0 - (depth * 1.35) * compression
            scale_x *= 1.0 + (depth * 0.72) * compression
            bones["shin_left"] += 3.0 * max(0.0, compression)
            bones["shin_right"] += 3.0 * max(0.0, compression)
            bones["head"] += -0.5 * compression
            bones["torso"] += 0.45 * compression

        return {
            "bones": bones,
            "root_x": root_x,
            "root_y": root_y,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "shadow_alpha": shadow_alpha,
            "shadow_scale": shadow_scale,
        }

    def _step_spring(self, dt: float) -> None:
        # Two semi-implicit steps keep the spring stable at low frame rates.
        sub = max(1.0 / 240.0, dt / 2.0)
        for _ in range(2):
            accel = -160.0 * self._squash - 17.0 * self._squash_vel
            self._squash_vel += accel * sub
            self._squash += self._squash_vel * sub
            self._squash = _clamp(self._squash, -0.45, 1.3)
        if abs(self._squash) < 0.0005 and abs(self._squash_vel) < 0.005:
            self._squash = 0.0
            self._squash_vel = 0.0

    def _make_pose(self) -> PoseFrame:
        angles = {name: round(value, 5) for name, value in self._bones.items()}
        return PoseFrame(
            bones=angles,
            root_offset_x=round(self._root_x, 4),
            root_offset_y=round(self._root_y, 4),
            scale_x=round(self._scale_x, 5),
            scale_y=round(self._scale_y, 5),
            shadow_alpha=round(_clamp(self._shadow_alpha, 0.0, 0.4), 4),
            shadow_scale=round(self._shadow_scale, 4),
            state=self.state.value,
        )

    @property
    def last_pose(self) -> PoseFrame:
        return self._last_pose

    @property
    def squash(self) -> float:
        return self._squash

    def is_settled(self) -> bool:
        """True when no meaningful motion remains (used to stop the timer)."""

        if self._squash_vel > 0.01 or abs(self._squash) > 0.005:
            return False
        if abs(self._root_x) > 0.05 or abs(self._root_y) > 0.05:
            return False
        if abs(self._scale_x - 1.0) > 0.002 or abs(self._scale_y - 1.0) > 0.002:
            return False
        return all(abs(v) < 0.05 for v in self._bones.values())

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "state_time": round(self._state_time, 3),
            "bones": {k: round(v, 3) for k, v in self._bones.items()},
            "root_x": round(self._root_x, 3),
            "root_y": round(self._root_y, 3),
            "scale": [round(self._scale_x, 4), round(self._scale_y, 4)],
            "squash": round(self._squash, 4),
            "click_kind": (
                self.click_kind.value
                if self.state is PetState.CLICK_REACT and self.click_kind
                else None
            ),
            "settled": self.is_settled(),
        }
