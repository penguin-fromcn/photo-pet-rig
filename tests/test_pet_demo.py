"""End-to-end checks for the bundled synthetic demo character."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from photopet.pet.animation_controller import AnimationController  # noqa: E402
from photopet.pet.behavior_controller import PetState  # noqa: E402
from photopet.pet.character_renderer import CharacterRenderer  # noqa: E402
from photopet.pet.rig_loader import load_character  # noqa: E402
from photopet.pet.settings import AppConfig  # noqa: E402


def test_demo_character_is_layered(assets_dir):
    assert (assets_dir / "rig.json").is_file()
    assert (assets_dir / "character_original.png").is_file()
    assets = load_character(assets_dir)
    try:
        assert assets.mode == "layered"
        assert assets.supports_limb_animation
        assert (assets.canvas_width, assets.canvas_height) == (900, 1600)
        assert len(assets.layers) >= 14
        assert "head" in assets.bone_limits
    finally:
        assets.close()


def test_demo_renders_transparent_frame(qapp, assets_dir):
    cfg = AppConfig()
    assets = load_character(assets_dir)
    try:
        animator = AnimationController(cfg.animation, assets.bone_limits)
        renderer = CharacterRenderer(assets, cfg.render, cfg.window)
        renderer.configure(340, 16, 1.0, shadow_enabled=True)
        pose = animator.update(1 / 30, state=PetState.IDLE, on_ground=True)
        frame = renderer.render(pose)
        assert frame.width() > 0 and frame.height() > 0
        for x, y in (
            (0, 0),
            (frame.width() - 1, 0),
            (0, frame.height() - 1),
            (frame.width() - 1, frame.height() - 1),
        ):
            assert frame.pixelColor(x, y).alpha() == 0
        assert renderer.alpha_coverage() > 0
    finally:
        assets.close()
