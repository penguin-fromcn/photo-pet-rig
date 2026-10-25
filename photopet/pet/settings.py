"""Application configuration: defaults, parsing, validation and atomic saving.

The configuration is deliberately kept in a human-editable JSON file.  All
values are validated and clamped so that a typo cannot crash the pet or push
the simulation into an unstable state.  Invalid values are reported as
warnings instead of being silently ignored.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

CONFIG_VERSION = 1

DEFAULT_BUBBLE_LINES = [
    "在呢～",
    "记得喝水。",
    "坐久了就起来走两步。",
    "今天也辛苦啦。",
    "我会一直在这里。",
    "别熬夜太晚。",
]
DEFAULT_DOUBLE_CLICK_LINES = [
    "嘿！接住我！",
    "看我跳得高不高！",
    "醒醒，别走神～",
]


class ConfigWarningList(list[str]):
    """A list of human readable warnings produced while parsing config."""

    def warn(self, message: str) -> None:
        if message not in self:
            self.append(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_absolute_path(value: str) -> bool:
    """Cross-platform absolute/rooted path check.

    ``Path('/tmp/x').is_absolute()`` is False on Windows because it lacks a
    drive letter, so treat a leading slash/backslash as absolute too.
    """

    text = str(value).strip()
    if text.startswith(("/", "\\")):
        return True
    return Path(text).is_absolute()


def _read_bool(
    raw: dict[str, Any],
    key: str,
    default: bool,
    path: str,
    warnings: ConfigWarningList,
) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    warnings.warn(f"{path}.{key} 应为 true/false，已使用默认值 {default}。")
    return default


def _read_int(
    raw: dict[str, Any],
    key: str,
    default: int,
    lo: int,
    hi: int,
    path: str,
    warnings: ConfigWarningList,
) -> int:
    value = raw.get(key, default)
    if not _is_number(value):
        warnings.warn(f"{path}.{key} 应为数字，已使用默认值 {default}。")
        return default
    if not math.isfinite(float(value)):
        warnings.warn(f"{path}.{key} 不是有限数字，已使用默认值 {default}。")
        return default
    ivalue = int(round(float(value)))
    if ivalue < lo or ivalue > hi:
        clamped = min(hi, max(lo, ivalue))
        warnings.warn(
            f"{path}.{key} = {ivalue} 超出范围 {lo}..{hi}，已调整为 {clamped}。"
        )
        return clamped
    return ivalue


def _read_float(
    raw: dict[str, Any],
    key: str,
    default: float,
    lo: float,
    hi: float,
    path: str,
    warnings: ConfigWarningList,
) -> float:
    value = raw.get(key, default)
    if not _is_number(value):
        warnings.warn(f"{path}.{key} 应为数字，已使用默认值 {default}。")
        return default
    fvalue = float(value)
    if not math.isfinite(fvalue):
        warnings.warn(f"{path}.{key} 不是有限数字，已使用默认值 {default}。")
        return default
    if fvalue < lo or fvalue > hi:
        clamped = min(hi, max(lo, fvalue))
        warnings.warn(
            f"{path}.{key} = {fvalue} 超出范围 {lo}..{hi}，已调整为 {clamped}。"
        )
        return clamped
    return fvalue


def _read_str(
    raw: dict[str, Any],
    key: str,
    default: str,
    path: str,
    warnings: ConfigWarningList,
    choices: Iterable[str] | None = None,
) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        warnings.warn(f"{path}.{key} 应为非空字符串，已使用默认值。")
        return default
    value = value.strip()
    if choices is not None and value not in set(choices):
        warnings.warn(f"{path}.{key} 的取值 {value!r} 不受支持，已使用默认值。")
        return default
    return value


def _read_lines(
    raw: dict[str, Any],
    key: str,
    default: list[str],
    path: str,
    warnings: ConfigWarningList,
) -> list[str]:
    value = raw.get(key, default)
    if not isinstance(value, list):
        warnings.warn(f"{path}.{key} 应为字符串数组，已使用默认台词。")
        return list(default)
    lines: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            line = item.strip()
            if len(line) > 120:
                line = line[:120]
                warnings.warn(f"{path}.{key} 中有台词过长，已截断到 120 字。")
            lines.append(line)
    lines = lines[:80]
    if not lines:
        warnings.warn(f"{path}.{key} 为空，已使用默认台词。")
        return list(default)
    return lines


@dataclass(slots=True)
class AssetsSettings:
    directory: str = "assets"
    rig_file: str = "rig.json"
    character_png: str = "character_original.png"
    source_photo: str = "source_photo.jpg"
    require_layered_rig: bool = False


@dataclass(slots=True)
class WindowSettings:
    display_height_px: int = 340
    padding_px: int = 16
    always_on_top: bool = True
    click_through_transparent: bool = True
    no_activate: bool = True
    opacity: float = 1.0
    shadow: bool = True


@dataclass(slots=True)
class RenderSettings:
    fps: int = 30
    idle_fps: int = 15
    max_dpr: float = 2.0


@dataclass(slots=True)
class PhysicsSettings:
    gravity_px_s2: float = 1800.0
    air_drag_per_s: float = 0.12
    ground_friction_per_s: float = 6.5
    restitution: float = 0.42
    wall_restitution: float = 0.45
    min_bounce_speed_px_s: float = 45.0
    max_speed_px_s: float = 3200.0
    fixed_step_s: float = 1.0 / 120.0
    collision_margin_px: float = 4.0


@dataclass(slots=True)
class InteractionSettings:
    drag_threshold_px: float = 5.0
    release_velocity_window_s: float = 0.12
    release_velocity_gain: float = 1.0
    max_release_speed_px_s: float = 2700.0
    clamp_drag_to_virtual_desktop: bool = True


@dataclass(slots=True)
class BehaviorSettings:
    enabled: bool = True
    walk_speed_px_s: float = 46.0
    walk_accel_px_s2: float = 240.0
    idle_min_s: float = 4.0
    idle_max_s: float = 10.0
    walk_min_s: float = 2.2
    walk_max_s: float = 7.5
    edge_margin_px: float = 64.0
    edge_turn_probability: float = 0.9
    random_turn_probability_per_s: float = 0.045
    land_duration_s: float = 0.34
    resume_walk_after_click: bool = True


@dataclass(slots=True)
class AnimationSettings:
    motion_scale: float = 1.0
    idle_breathing: bool = True
    walk_cycle_hz: float = 1.25
    walk_thigh_deg: float = 4.3
    walk_knee_deg: float = 6.0
    walk_foot_deg: float = 2.0
    walk_arm_deg: float = 1.8
    walk_forearm_deg: float = 1.0
    walk_torso_deg: float = 1.0
    walk_head_deg: float = 1.25
    walk_bob_canvas_px: float = 17.0
    idle_breath_deg: float = 0.65
    idle_head_deg: float = 1.25
    idle_bob_canvas_px: float = 7.0
    idle_arm_deg: float = 0.55
    click_hop_speed_px_s: float = 255.0
    double_click_hop_speed_px_s: float = 520.0
    click_duration_s: float = 0.5
    double_click_duration_s: float = 0.82
    land_squash: float = 0.93
    land_tilt_deg: float = 2.2
    smoothing_per_s: float = 18.0
    click_cooldown_s: float = 0.2


@dataclass(slots=True)
class BubbleSettings:
    enabled: bool = True
    duration_ms: int = 3200
    lines: list[str] = field(default_factory=lambda: list(DEFAULT_BUBBLE_LINES))
    double_click_lines: list[str] = field(
        default_factory=lambda: list(DEFAULT_DOUBLE_CLICK_LINES)
    )


@dataclass(slots=True)
class AutostartSettings:
    enabled: bool = False


@dataclass(slots=True)
class DebugSettings:
    log_level: str = "INFO"
    log_to_file: bool = True


@dataclass(slots=True)
class AppConfig:
    version: int = CONFIG_VERSION
    assets: AssetsSettings = field(default_factory=AssetsSettings)
    window: WindowSettings = field(default_factory=WindowSettings)
    render: RenderSettings = field(default_factory=RenderSettings)
    physics: PhysicsSettings = field(default_factory=PhysicsSettings)
    interaction: InteractionSettings = field(default_factory=InteractionSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    animation: AnimationSettings = field(default_factory=AnimationSettings)
    bubble: BubbleSettings = field(default_factory=BubbleSettings)
    autostart: AutostartSettings = field(default_factory=AutostartSettings)
    debug: DebugSettings = field(default_factory=DebugSettings)

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, (list, tuple)):
                return [convert(v) for v in value]
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if hasattr(value, "__dataclass_fields__"):
                return {
                    f.name: convert(getattr(value, f.name))
                    for f in fields(value)
                }
            return value

        result = convert(self)
        # Keep a stable, friendly key order.
        order = [
            "version",
            "assets",
            "window",
            "render",
            "physics",
            "interaction",
            "behavior",
            "animation",
            "bubble",
            "autostart",
            "debug",
        ]
        return {key: result[key] for key in order if key in result}


def _expect_dict(
    raw: dict[str, Any], key: str, path: str, warnings: ConfigWarningList
) -> dict[str, Any]:
    value = raw.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.warn(f"{path}.{key} 应为对象，已使用默认值。")
        return {}
    return value


def config_from_dict(
    data: dict[str, Any], warnings: ConfigWarningList | None = None
) -> AppConfig:
    """Build a validated config from a JSON-like dict."""

    if warnings is None:
        warnings = ConfigWarningList()
    if not isinstance(data, dict):
        warnings.warn("配置根节点必须是对象，全部使用默认值。")
        data = {}

    cfg = AppConfig()

    a = _expect_dict(data, "assets", "assets", warnings)
    cfg.assets.directory = _read_str(
        a, "directory", cfg.assets.directory, "assets", warnings
    )
    cfg.assets.rig_file = _read_str(
        a, "rig_file", cfg.assets.rig_file, "assets", warnings
    )
    cfg.assets.character_png = _read_str(
        a, "character_png", cfg.assets.character_png, "assets", warnings
    )
    cfg.assets.source_photo = _read_str(
        a, "source_photo", cfg.assets.source_photo, "assets", warnings
    )
    cfg.assets.require_layered_rig = _read_bool(
        a,
        "require_layered_rig",
        cfg.assets.require_layered_rig,
        "assets",
        warnings,
    )
    if _is_absolute_path(cfg.assets.rig_file):
        warnings.warn("assets.rig_file 不应是绝对路径，已改用 rig.json。")
        cfg.assets.rig_file = "rig.json"
    if _is_absolute_path(cfg.assets.character_png):
        warnings.warn(
            "assets.character_png 不应是绝对路径，已改用 character_original.png。"
        )
        cfg.assets.character_png = "character_original.png"

    w = _expect_dict(data, "window", "window", warnings)
    cfg.window.display_height_px = _read_int(
        w, "display_height_px", cfg.window.display_height_px, 160, 900,
        "window", warnings,
    )
    cfg.window.padding_px = _read_int(
        w, "padding_px", cfg.window.padding_px, 0, 64, "window", warnings
    )
    cfg.window.always_on_top = _read_bool(
        w, "always_on_top", cfg.window.always_on_top, "window", warnings
    )
    cfg.window.click_through_transparent = _read_bool(
        w,
        "click_through_transparent",
        cfg.window.click_through_transparent,
        "window",
        warnings,
    )
    cfg.window.no_activate = _read_bool(
        w, "no_activate", cfg.window.no_activate, "window", warnings
    )
    cfg.window.opacity = _read_float(
        w, "opacity", cfg.window.opacity, 0.2, 1.0, "window", warnings
    )
    cfg.window.shadow = _read_bool(
        w, "shadow", cfg.window.shadow, "window", warnings
    )

    r = _expect_dict(data, "render", "render", warnings)
    cfg.render.fps = _read_int(r, "fps", cfg.render.fps, 10, 60, "render", warnings)
    cfg.render.idle_fps = _read_int(
        r, "idle_fps", cfg.render.idle_fps, 5, 60, "render", warnings
    )
    cfg.render.max_dpr = _read_float(
        r, "max_dpr", cfg.render.max_dpr, 1.0, 4.0, "render", warnings
    )

    p = _expect_dict(data, "physics", "physics", warnings)
    cfg.physics.gravity_px_s2 = _read_float(
        p, "gravity_px_s2", cfg.physics.gravity_px_s2, 100.0, 6000.0,
        "physics", warnings,
    )
    cfg.physics.air_drag_per_s = _read_float(
        p, "air_drag_per_s", cfg.physics.air_drag_per_s, 0.0, 5.0,
        "physics", warnings,
    )
    cfg.physics.ground_friction_per_s = _read_float(
        p,
        "ground_friction_per_s",
        cfg.physics.ground_friction_per_s,
        0.0,
        30.0,
        "physics",
        warnings,
    )
    cfg.physics.restitution = _read_float(
        p, "restitution", cfg.physics.restitution, 0.0, 0.9,
        "physics", warnings,
    )
    cfg.physics.wall_restitution = _read_float(
        p, "wall_restitution", cfg.physics.wall_restitution, 0.0, 0.9,
        "physics", warnings,
    )
    cfg.physics.min_bounce_speed_px_s = _read_float(
        p,
        "min_bounce_speed_px_s",
        cfg.physics.min_bounce_speed_px_s,
        0.0,
        300.0,
        "physics",
        warnings,
    )
    cfg.physics.max_speed_px_s = _read_float(
        p, "max_speed_px_s", cfg.physics.max_speed_px_s, 300.0, 8000.0,
        "physics", warnings,
    )
    cfg.physics.fixed_step_s = _read_float(
        p, "fixed_step_s", cfg.physics.fixed_step_s, 1.0 / 480.0, 0.05,
        "physics", warnings,
    )
    cfg.physics.collision_margin_px = _read_float(
        p,
        "collision_margin_px",
        cfg.physics.collision_margin_px,
        0.0,
        80.0,
        "physics",
        warnings,
    )

    i = _expect_dict(data, "interaction", "interaction", warnings)
    cfg.interaction.drag_threshold_px = _read_float(
        i,
        "drag_threshold_px",
        cfg.interaction.drag_threshold_px,
        1.0,
        50.0,
        "interaction",
        warnings,
    )
    cfg.interaction.release_velocity_window_s = _read_float(
        i,
        "release_velocity_window_s",
        cfg.interaction.release_velocity_window_s,
        0.03,
        0.5,
        "interaction",
        warnings,
    )
    cfg.interaction.release_velocity_gain = _read_float(
        i,
        "release_velocity_gain",
        cfg.interaction.release_velocity_gain,
        0.1,
        2.0,
        "interaction",
        warnings,
    )
    cfg.interaction.max_release_speed_px_s = _read_float(
        i,
        "max_release_speed_px_s",
        cfg.interaction.max_release_speed_px_s,
        200.0,
        8000.0,
        "interaction",
        warnings,
    )
    cfg.interaction.clamp_drag_to_virtual_desktop = _read_bool(
        i,
        "clamp_drag_to_virtual_desktop",
        cfg.interaction.clamp_drag_to_virtual_desktop,
        "interaction",
        warnings,
    )

    b = _expect_dict(data, "behavior", "behavior", warnings)
    cfg.behavior.enabled = _read_bool(
        b, "enabled", cfg.behavior.enabled, "behavior", warnings
    )
    cfg.behavior.walk_speed_px_s = _read_float(
        b, "walk_speed_px_s", cfg.behavior.walk_speed_px_s, 5.0, 240.0,
        "behavior", warnings,
    )
    cfg.behavior.walk_accel_px_s2 = _read_float(
        b, "walk_accel_px_s2", cfg.behavior.walk_accel_px_s2, 20.0, 2000.0,
        "behavior", warnings,
    )
    cfg.behavior.idle_min_s = _read_float(
        b, "idle_min_s", cfg.behavior.idle_min_s, 0.2, 120.0,
        "behavior", warnings,
    )
    cfg.behavior.idle_max_s = _read_float(
        b, "idle_max_s", cfg.behavior.idle_max_s, 0.2, 300.0,
        "behavior", warnings,
    )
    if cfg.behavior.idle_max_s < cfg.behavior.idle_min_s:
        warnings.warn("behavior.idle_max_s 小于 idle_min_s，已交换二者。")
        cfg.behavior.idle_min_s, cfg.behavior.idle_max_s = (
            cfg.behavior.idle_max_s,
            cfg.behavior.idle_min_s,
        )
    cfg.behavior.walk_min_s = _read_float(
        b, "walk_min_s", cfg.behavior.walk_min_s, 0.2, 120.0,
        "behavior", warnings,
    )
    cfg.behavior.walk_max_s = _read_float(
        b, "walk_max_s", cfg.behavior.walk_max_s, 0.2, 180.0,
        "behavior", warnings,
    )
    if cfg.behavior.walk_max_s < cfg.behavior.walk_min_s:
        warnings.warn("behavior.walk_max_s 小于 walk_min_s，已交换二者。")
        cfg.behavior.walk_min_s, cfg.behavior.walk_max_s = (
            cfg.behavior.walk_max_s,
            cfg.behavior.walk_min_s,
        )
    cfg.behavior.edge_margin_px = _read_float(
        b, "edge_margin_px", cfg.behavior.edge_margin_px, 0.0, 300.0,
        "behavior", warnings,
    )
    cfg.behavior.edge_turn_probability = _read_float(
        b,
        "edge_turn_probability",
        cfg.behavior.edge_turn_probability,
        0.0,
        1.0,
        "behavior",
        warnings,
    )
    cfg.behavior.random_turn_probability_per_s = _read_float(
        b,
        "random_turn_probability_per_s",
        cfg.behavior.random_turn_probability_per_s,
        0.0,
        2.0,
        "behavior",
        warnings,
    )
    cfg.behavior.land_duration_s = _read_float(
        b, "land_duration_s", cfg.behavior.land_duration_s, 0.05, 2.0,
        "behavior", warnings,
    )
    cfg.behavior.resume_walk_after_click = _read_bool(
        b,
        "resume_walk_after_click",
        cfg.behavior.resume_walk_after_click,
        "behavior",
        warnings,
    )

    an = _expect_dict(data, "animation", "animation", warnings)
    cfg.animation.motion_scale = _read_float(
        an, "motion_scale", cfg.animation.motion_scale, 0.0, 2.0,
        "animation", warnings,
    )
    cfg.animation.idle_breathing = _read_bool(
        an, "idle_breathing", cfg.animation.idle_breathing, "animation", warnings
    )
    cfg.animation.walk_cycle_hz = _read_float(
        an, "walk_cycle_hz", cfg.animation.walk_cycle_hz, 0.2, 4.0,
        "animation", warnings,
    )
    cfg.animation.walk_thigh_deg = _read_float(
        an, "walk_thigh_deg", cfg.animation.walk_thigh_deg, 0.0, 7.0,
        "animation", warnings,
    )
    cfg.animation.walk_knee_deg = _read_float(
        an, "walk_knee_deg", cfg.animation.walk_knee_deg, 0.0, 14.0,
        "animation", warnings,
    )
    cfg.animation.walk_foot_deg = _read_float(
        an, "walk_foot_deg", cfg.animation.walk_foot_deg, 0.0, 5.0,
        "animation", warnings,
    )
    cfg.animation.walk_arm_deg = _read_float(
        an, "walk_arm_deg", cfg.animation.walk_arm_deg, 0.0, 5.0,
        "animation", warnings,
    )
    cfg.animation.walk_forearm_deg = _read_float(
        an, "walk_forearm_deg", cfg.animation.walk_forearm_deg, 0.0, 4.0,
        "animation", warnings,
    )
    cfg.animation.walk_torso_deg = _read_float(
        an, "walk_torso_deg", cfg.animation.walk_torso_deg, 0.0, 3.0,
        "animation", warnings,
    )
    cfg.animation.walk_head_deg = _read_float(
        an, "walk_head_deg", cfg.animation.walk_head_deg, 0.0, 5.0,
        "animation", warnings,
    )
    cfg.animation.walk_bob_canvas_px = _read_float(
        an, "walk_bob_canvas_px", cfg.animation.walk_bob_canvas_px, 0.0, 30.0,
        "animation", warnings,
    )
    cfg.animation.idle_breath_deg = _read_float(
        an, "idle_breath_deg", cfg.animation.idle_breath_deg, 0.0, 3.0,
        "animation", warnings,
    )
    cfg.animation.idle_head_deg = _read_float(
        an, "idle_head_deg", cfg.animation.idle_head_deg, 0.0, 5.0,
        "animation", warnings,
    )
    cfg.animation.idle_bob_canvas_px = _read_float(
        an, "idle_bob_canvas_px", cfg.animation.idle_bob_canvas_px, 0.0, 30.0,
        "animation", warnings,
    )
    cfg.animation.idle_arm_deg = _read_float(
        an, "idle_arm_deg", cfg.animation.idle_arm_deg, 0.0, 4.0,
        "animation", warnings,
    )
    cfg.animation.click_hop_speed_px_s = _read_float(
        an,
        "click_hop_speed_px_s",
        cfg.animation.click_hop_speed_px_s,
        0.0,
        1200.0,
        "animation",
        warnings,
    )
    cfg.animation.double_click_hop_speed_px_s = _read_float(
        an,
        "double_click_hop_speed_px_s",
        cfg.animation.double_click_hop_speed_px_s,
        0.0,
        1500.0,
        "animation",
        warnings,
    )
    cfg.animation.click_duration_s = _read_float(
        an, "click_duration_s", cfg.animation.click_duration_s, 0.1, 3.0,
        "animation", warnings,
    )
    cfg.animation.double_click_duration_s = _read_float(
        an,
        "double_click_duration_s",
        cfg.animation.double_click_duration_s,
        0.1,
        4.0,
        "animation",
        warnings,
    )
    cfg.animation.land_squash = _read_float(
        an, "land_squash", cfg.animation.land_squash, 0.75, 1.0,
        "animation", warnings,
    )
    cfg.animation.land_tilt_deg = _read_float(
        an, "land_tilt_deg", cfg.animation.land_tilt_deg, 0.0, 5.0,
        "animation", warnings,
    )
    cfg.animation.smoothing_per_s = _read_float(
        an, "smoothing_per_s", cfg.animation.smoothing_per_s, 1.0, 60.0,
        "animation", warnings,
    )
    cfg.animation.click_cooldown_s = _read_float(
        an, "click_cooldown_s", cfg.animation.click_cooldown_s, 0.0, 2.0,
        "animation", warnings,
    )

    bu = _expect_dict(data, "bubble", "bubble", warnings)
    cfg.bubble.enabled = _read_bool(
        bu, "enabled", cfg.bubble.enabled, "bubble", warnings
    )
    cfg.bubble.duration_ms = _read_int(
        bu, "duration_ms", cfg.bubble.duration_ms, 500, 20000, "bubble", warnings
    )
    cfg.bubble.lines = _read_lines(
        bu, "lines", cfg.bubble.lines, "bubble", warnings
    )
    cfg.bubble.double_click_lines = _read_lines(
        bu,
        "double_click_lines",
        cfg.bubble.double_click_lines,
        "bubble",
        warnings,
    )

    au = _expect_dict(data, "autostart", "autostart", warnings)
    cfg.autostart.enabled = _read_bool(
        au, "enabled", cfg.autostart.enabled, "autostart", warnings
    )

    d = _expect_dict(data, "debug", "debug", warnings)
    cfg.debug.log_level = _read_str(
        d,
        "log_level",
        cfg.debug.log_level,
        "debug",
        warnings,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ).upper()
    cfg.debug.log_to_file = _read_bool(
        d, "log_to_file", cfg.debug.log_to_file, "debug", warnings
    )

    return cfg


def load_config(
    path: Path, warnings: ConfigWarningList | None = None
) -> tuple[AppConfig, bool]:
    """Load config from *path*.

    Returns ``(config, loaded_ok)``.  Missing or invalid files yield defaults
    plus warnings; the app keeps running rather than overwriting the user's
    file.  The caller is responsible for reporting warnings.
    """

    if warnings is None:
        warnings = ConfigWarningList()
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        warnings.warn(f"未找到配置文件 {path}，使用内置默认值。")
        return AppConfig(), False
    except OSError as exc:
        warnings.warn(f"无法读取配置文件 {path}: {exc}，使用内置默认值。")
        return AppConfig(), False

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        warnings.warn(
            f"配置文件 JSON 格式错误（第 {exc.lineno} 行第 {exc.colno} 列："
            f"{exc.msg}），使用内置默认值。"
        )
        return AppConfig(), False

    if not isinstance(data, dict):
        warnings.warn("配置文件根节点必须是 JSON 对象，使用内置默认值。")
        return AppConfig(), False

    return config_from_dict(data, warnings), True


def save_config(path: Path, cfg: AppConfig) -> None:
    """Write config atomically so a crash cannot truncate the file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n"
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def app_base_dir() -> Path:
    """Directory used for default config/logs and relative asset paths.

    * frozen executable: next to the .exe;
    * ``photopet.pet`` package: the repository/application root;
    * otherwise (legacy ``src`` checkout): the directory above ``src``.

    ``PHOTOPET_BASE_DIR`` can override this for embedded/test launches.
    """

    override = os.environ.get("PHOTOPET_BASE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    here = Path(__file__).resolve()
    # <repo>/photopet/pet/settings.py -> <repo>
    if here.parent.name == "pet" and here.parent.parent.name == "photopet":
        return here.parents[2]
    return here.parent.parent


def resource_base_dir() -> Path:
    """Read-only bundled resource root (PyInstaller extracts to ``_MEIPASS``)."""

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return app_base_dir()


def default_config_path() -> Path:
    return app_base_dir() / "config.json"
