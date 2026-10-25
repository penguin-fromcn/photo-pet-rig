from __future__ import annotations

import json
from pathlib import Path

from photopet.pet.settings import (
    AppConfig,
    ConfigWarningList,
    PhysicsSettings,
    config_from_dict,
    load_config,
    save_config,
)


def test_default_config_round_trip(tmp_path: Path):
    cfg = AppConfig()
    path = tmp_path / "config.json"
    save_config(path, cfg)
    warning_list = ConfigWarningList()
    loaded, ok = load_config(path, warning_list)
    assert ok
    assert loaded.to_dict() == cfg.to_dict()
    assert not warning_list


def test_missing_config_uses_defaults_and_warns(tmp_path: Path):
    warnings = ConfigWarningList()
    cfg, ok = load_config(tmp_path / "absent.json", warnings)
    assert not ok
    assert cfg.window.display_height_px == AppConfig().window.display_height_px
    assert warnings


def test_broken_json_uses_defaults_and_reports_line(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"window": {"fps": 30,}}', encoding="utf-8")
    warnings = ConfigWarningList()
    cfg, ok = load_config(path, warnings)
    assert not ok
    assert any("JSON" in w and "行" in w for w in warnings)
    assert cfg.render.fps == 30


def test_out_of_range_values_are_clamped():
    warnings = ConfigWarningList()
    cfg = config_from_dict(
        {
            "window": {"display_height_px": 5000},
            "render": {"fps": 999, "idle_fps": 1},
            "physics": {"restitution": 5.0, "gravity_px_s2": -100},
            "behavior": {"idle_min_s": 20, "idle_max_s": 2},
            "bubble": {"lines": []},
        },
        warnings,
    )
    assert cfg.window.display_height_px == 900
    assert cfg.render.fps == 60
    assert cfg.render.idle_fps == 5
    assert cfg.physics.restitution == 0.9
    assert cfg.physics.gravity_px_s2 == 100.0
    assert cfg.behavior.idle_min_s == 2.0
    assert cfg.behavior.idle_max_s == 20.0
    assert cfg.bubble.lines
    assert warnings


def test_invalid_types_fall_back_to_defaults():
    warnings = ConfigWarningList()
    cfg = config_from_dict(
        {
            "window": {"display_height_px": "big", "shadow": "yes"},
            "behavior": {"enabled": 1},
            "bubble": {"lines": "hello"},
        },
        warnings,
    )
    default = AppConfig()
    assert cfg.window.display_height_px == default.window.display_height_px
    assert cfg.window.shadow == default.window.shadow
    assert cfg.behavior.enabled == default.behavior.enabled
    assert cfg.bubble.lines == default.bubble.lines
    assert len(warnings) >= 4


def test_absolute_asset_paths_are_discouraged():
    warnings = ConfigWarningList()
    cfg = config_from_dict(
        {
            "assets": {
                "rig_file": "C:/Users/someone/rig.json",
                "character_png": "/tmp/character.png",
            }
        },
        warnings,
    )
    assert cfg.assets.rig_file == "rig.json"
    assert cfg.assets.character_png == "character_original.png"
    assert any("绝对路径" in w for w in warnings)


def test_bubble_line_limit_and_truncation():
    warnings = ConfigWarningList()
    cfg = config_from_dict(
        {"bubble": {"lines": ["好" * 300, "  ", "在呢"]}},
        warnings,
    )
    assert len(cfg.bubble.lines[0]) <= 120
    assert "在呢" in cfg.bubble.lines
    assert any("截断" in w for w in warnings)


def test_saving_is_atomic_and_utf8(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = AppConfig()
    cfg.bubble.lines = ["中文台词"]
    save_config(path, cfg)
    text = path.read_text(encoding="utf-8")
    assert "中文台词" in text
    assert not list(tmp_path.glob("*.tmp"))
    # Ensure it is valid JSON and not ASCII-escaped.
    assert json.loads(text)["bubble"]["lines"] == ["中文台词"]


def test_utf8_bom_config_is_accepted(tmp_path: Path):
    path = tmp_path / "config.json"
    payload = {"window": {"display_height_px": 470}}
    path.write_text(json.dumps(payload), encoding="utf-8-sig")
    warnings = ConfigWarningList()
    cfg, ok = load_config(path, warnings)
    assert ok
    assert cfg.window.display_height_px == 470
    assert not warnings


def test_physics_settings_defaults_are_sane():
    physics = PhysicsSettings()
    assert 0.0 < physics.restitution < 1.0
    assert physics.min_bounce_speed_px_s > 0
    assert physics.fixed_step_s <= 0.02


def test_public_example_config_loads_without_warnings():
    root = Path(__file__).resolve().parents[1]
    path = root / "examples" / "pet_config.example.json"
    warnings = ConfigWarningList()
    cfg, ok = load_config(path, warnings)
    assert ok
    assert not warnings
    assert cfg.assets.directory == "examples/demo_character"
    assert (root / cfg.assets.directory / "rig.json").is_file()

