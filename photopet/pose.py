"""Small pose renderer used for QA previews, not an animation runtime."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .imageops import composite_rgba, enclosed_holes
from .patches import Patch
from .previews import _background, _find_font
from .project import Project


@dataclass
class PoseResult:
    name: str
    rgba: np.ndarray
    holes: list[dict]
    angles: dict[str, float]
    root_translation: tuple[float, float]


def _local(px: float, py: float, angle: float, tx: float = 0.0, ty: float = 0.0) -> np.ndarray:
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)
    T1 = np.array([[1, 0, px + tx], [0, 1, py + ty], [0, 0, 1]], np.float64)
    T0 = np.array([[1, 0, -px], [0, 1, -py], [0, 0, 1]], np.float64)
    return T1 @ R @ T0


def _bone_matrices(project: Project, angles: dict[str, float], root_tr: tuple[float, float]) -> dict[str, np.ndarray]:
    mats: dict[str, np.ndarray] = {}

    def matrix_for(name: str) -> np.ndarray:
        if name in mats:
            return mats[name]
        bone = project.bones[name]
        angle = float(angles.get(name, 0.0))
        tx, ty = root_tr if name == "root" else (0.0, 0.0)
        local = _local(bone.pivot[0], bone.pivot[1], angle, tx, ty)
        mats[name] = local if bone.parent is None else matrix_for(bone.parent) @ local
        return mats[name]

    for name in project.bones:
        matrix_for(name)
    return mats


def render_pose(
    project: Project,
    body_layers: dict[str, np.ndarray],
    patches: list[Patch],
    angles: dict[str, float],
    root_translation: tuple[float, float],
) -> np.ndarray:
    h, w = project.height, project.width
    mats = _bone_matrices(project, angles, root_translation)
    ordered_body = [
        p.id for p in sorted(project.parts.values(), key=lambda p: p.z)
    ]
    out_pm = np.zeros((h, w, 3), np.float32)
    out_a = np.zeros((h, w, 1), np.float32)

    for patch in patches:
        rgba = patch.rgba.astype(np.float32)
        pm = rgba.copy()
        pm[:, :, :3] *= rgba[:, :, 3:4] / 255.0
        M = mats[patch.parent_bone][:2]
        warp = cv2.warpAffine(
            pm, M, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
        )
        a = warp[:, :, 3:4] / 255.0
        out_pm = warp[:, :, :3] + out_pm * (1 - a)
        out_a = warp[:, :, 3:4] + out_a * (1 - a)

    for part_id in ordered_body:
        rgba = body_layers[part_id].astype(np.float32)
        pm = rgba.copy()
        pm[:, :, :3] *= rgba[:, :, 3:4] / 255.0
        M = mats[project.parts[part_id].bone][:2]
        warp = cv2.warpAffine(
            pm, M, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0),
        )
        a = warp[:, :, 3:4] / 255.0
        out_pm = warp[:, :, :3] + out_pm * (1 - a)
        out_a = warp[:, :, 3:4] + out_a * (1 - a)

    rgb = np.where(out_a > 0, out_pm / np.maximum(out_a / 255.0, 1e-6), 0)
    return np.dstack([
        np.clip(np.round(rgb), 0, 255).astype(np.uint8),
        np.clip(np.round(out_a), 0, 255).astype(np.uint8),
    ])


def make_pose_sheet(
    project: Project,
    character: np.ndarray,
    body_layers: dict[str, np.ndarray],
    patches: list[Patch],
    out_path: Path,
) -> list[PoseResult]:
    h, w = character.shape[:2]
    bob = max(3, int(round(h * 0.0032)))
    poses = [
        ("REST", "exact rest composite", {}, (0.0, 0.0)),
        (
            "WALK / A",
            "thigh +/-2.4 deg  /  knee 3.5 deg",
            {
                "torso": 0.6, "head": -0.8,
                "thigh_right": 2.4, "thigh_left": -2.4,
                "shin_right": 3.5, "shin_left": 1.0,
                "foot_right": -1.0, "foot_left": 1.0,
                "upper_arm_right": -0.7, "upper_arm_left": 0.7,
            },
            (0.0, float(-bob)),
        ),
        (
            "WALK / B",
            "mirror of A  /  knee 3.5 deg",
            {
                "torso": -0.6, "head": 0.8,
                "thigh_right": -2.4, "thigh_left": 2.4,
                "shin_right": 1.0, "shin_left": 3.5,
                "foot_right": 1.0, "foot_left": -1.0,
                "upper_arm_right": 0.7, "upper_arm_left": -0.7,
            },
            (0.0, float(-bob)),
        ),
        (
            "CLICK / LAND",
            "head -3.5 deg  /  root +6 px",
            {
                "torso": 1.8, "head": -3.5,
                "thigh_right": 1.8, "thigh_left": -1.8,
                "shin_right": 4.0, "shin_left": 4.0,
                "upper_arm_right": 1.8, "upper_arm_left": -1.8,
            },
            (0.0, 6.0),
        ),
    ]

    results: list[PoseResult] = []
    rendered: list[tuple[str, str, np.ndarray]] = []
    for name, description, angles, root_tr in poses:
        if name == "REST":
            rgba = character
        else:
            rgba = render_pose(project, body_layers, patches, angles, root_tr)
        holes = enclosed_holes(rgba[:, :, 3])
        results.append(PoseResult(name, rgba, holes, angles, root_tr))
        rendered.append((name, description, rgba))

    # Contact sheet
    panel_w, panel_h = 400, 760
    gap, margin, header, footer = 16, 28, 92, 64
    page_w = margin * 2 + 4 * panel_w + 3 * gap
    page_h = header + panel_h + footer
    page = Image.fromarray(_background(page_w, page_h)).convert("RGBA")
    d = ImageDraw.Draw(page)
    f_title = _find_font(30, True)
    f_sub = _find_font(14, False)
    f_panel = _find_font(16, True)
    f_param = _find_font(11, False)
    accent = (255, 154, 84, 255)
    d.text((margin, 24), "POSE TEST  /  " + project.slug, font=f_title, fill=(244, 242, 238))
    d.text((margin, 64), "Small in-plane pivots. Patches are included; this is a QA render, not an animation file.",
           font=f_sub, fill=(158, 158, 157))
    d.rounded_rectangle([margin, 86, margin + 68, 89], radius=2, fill=accent)

    for i, (name, description, rgba) in enumerate(rendered):
        x0 = margin + i * (panel_w + gap)
        y0 = header
        bg = _background(panel_w, panel_h)
        # floor shadow
        sh = np.zeros((panel_h, panel_w), np.float32)
        cv2.ellipse(sh, (panel_w // 2, int(panel_h * 0.88)), (int(panel_w * 0.27), 18), 0, 0, 360, 1, -1)
        sh = cv2.GaussianBlur(sh, (0, 0), 15)
        bg = (bg.astype(np.float32) * (1 - sh[..., None] * 0.55)).astype(np.uint8)
        panel = Image.fromarray(bg).convert("RGBA")
        scale = min((panel_w - 28) / w, (panel_h - 100) / h)
        cw, ch = max(1, int(w * scale)), max(1, int(h * scale))
        char_img = Image.fromarray(rgba).resize((cw, ch), Image.LANCZOS)
        panel.alpha_composite(char_img, ((panel_w - cw) // 2, 66 + (panel_h - 100 - ch) // 2))
        pd = ImageDraw.Draw(panel)
        pd.rectangle([0, 0, panel_w - 1, panel_h - 1], outline=(48, 53, 62, 255), width=1)
        pd.text((14, 12), name, font=f_panel, fill=(243, 241, 236))
        pd.text((14, 34), description, font=f_param, fill=(155, 156, 158))
        page.alpha_composite(panel, (x0, y0))

    footer_text = "Only small pivots are demonstrated. Facial pixels are frozen; crossed/large arm poses are not supported by default."
    d.text((margin, page_h - 42), footer_text, font=f_sub, fill=(140, 142, 145))
    page.convert("RGB").save(out_path, optimize=True)
    return results


