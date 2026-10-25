"""Generate soft fabric patches behind joints.

These are deliberately synthetic: they are made from nearby visible fabric
pixels, not from the hidden body. They exist only to keep rotations from
opening transparent holes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .constants import JOINT_INFO, PATCH_RADII
from .imageops import ellipse_mask, polygon_mask
from .project import Project


@dataclass
class Patch:
    id: str
    joint: str
    parent_bone: str
    pivot: tuple[float, float]
    rgba: np.ndarray
    radii: tuple[float, float]
    sample_part: str
    source: str = "sampled_nearby_fabric"


SAMPLE_PART = {
    "head": "torso",
    "upper_arm_left": "upper_arm_left",
    "upper_arm_right": "upper_arm_right",
    "forearm_left": "upper_arm_left",
    "forearm_right": "upper_arm_right",
    "thigh_left": "thigh_left",
    "thigh_right": "thigh_right",
    "shin_left": "shin_left",
    "shin_right": "shin_right",
    "foot_left": "shin_left",
    "foot_right": "shin_right",
}

SAMPLE_DIR = {
    "head": (0.0, 1.0),
    "upper_arm_left": (0.20, 1.0),
    "upper_arm_right": (-0.20, 1.0),
    "forearm_left": (0.20, -1.0),
    "forearm_right": (-0.20, -1.0),
    "thigh_left": (0.15, 1.0),
    "thigh_right": (-0.15, 1.0),
    "shin_left": (0.10, 1.0),
    "shin_right": (-0.10, 1.0),
    "foot_left": (0.0, -1.0),
    "foot_right": (0.0, -1.0),
}


def _normalize(v: tuple[float, float]) -> tuple[float, float]:
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n else (0.0, 1.0)


def _patch_radii(project: Project, joint: str) -> tuple[float, float]:
    if joint in project.patches.radii:
        return project.patches.radii[joint]
    fx, fy = PATCH_RADII[joint]
    scale = project.patches.scale * project.height
    return (max(14.0, fx * scale), max(10.0, fy * scale))


def _crop_bounds(cx: float, cy: float, w: int, h: int, width: int, height: int) -> tuple[int, int, int, int]:
    x0 = int(round(cx - w / 2))
    y0 = int(round(cy - h / 2))
    x0 = max(0, min(width - w, x0)) if w < width else 0
    y0 = max(0, min(height - h, y0)) if h < height else 0
    return x0, y0, min(width, x0 + w), min(height, y0 + h)


def _choose_texture(
    project: Project,
    labels: np.ndarray,
    alpha: np.ndarray,
    face_mask: np.ndarray | None,
    joint: str,
    center: tuple[float, float],
    radii: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    """Return (texture, valid_mask) cropped from the chosen nearby fabric."""
    h, w = alpha.shape
    rx, ry = radii
    crop_w = max(8, int(round(rx * 2.0)))
    crop_h = max(8, int(round(ry * 2.0)))
    sample_part = SAMPLE_PART[joint]
    part_index = None
    from .constants import STANDARD_PARTS  # local import keeps constants private-ish
    if sample_part in STANDARD_PARTS:
        part_index = STANDARD_PARTS.index(sample_part) + 1
    part_mask = labels == part_index if part_index is not None else (alpha > 0)

    base_dir = _normalize(SAMPLE_DIR[joint])
    best_score = -1.0
    best_tile = None

    # Try the natural offset and a few nearby angles; choose the tile with
    # the highest coverage of the intended clothing part.
    for dist_mul in (1.20, 1.55, 0.85):
        for angle_deg in (0.0, 22.0, -22.0, 45.0, -45.0):
            a = math.radians(angle_deg)
            dx = base_dir[0] * math.cos(a) - base_dir[1] * math.sin(a)
            dy = base_dir[0] * math.sin(a) + base_dir[1] * math.cos(a)
            dist = max(rx, ry) * dist_mul
            cx = center[0] + dx * dist
            cy = center[1] + dy * dist
            x0, y0, x1, y1 = _crop_bounds(cx, cy, crop_w, crop_h, w, h)
            if x1 <= x0 or y1 <= y0:
                continue
            part_tile = part_mask[y0:y1, x0:x1]
            fg_tile = alpha[y0:y1, x0:x1] > 0
            if face_mask is not None:
                fg_tile &= ~face_mask[y0:y1, x0:x1]
            valid = fg_tile & part_tile
            denom = max(1, valid.size)
            score = float(valid.sum()) / denom
            if score > best_score:
                best_score = score
                best_tile = (
                    x0, y0, x1, y1,
                    part_tile.copy(), fg_tile.copy(), valid.copy(),
                )
    if best_tile is None:
        raise RuntimeError(f"could not sample a patch for joint {joint!r}")

    x0, y0, x1, y1, part_tile, fg_tile, valid = best_tile
    # If the intended part mask is absent in this crop, fall back to foreground.
    if valid.sum() < 20:
        valid = fg_tile
    # Source pixels are attached by the builder to avoid a second file read.
    rgb = getattr(project, "_rgb", None)
    if rgb is None:
        raise RuntimeError("internal error: project._rgb is not set")
    tile = rgb[y0:y1, x0:x1].copy()
    if valid.sum() > 0:
        med = np.median(tile[valid], axis=0)
    else:
        med = np.median(tile.reshape(-1, 3), axis=0)
    fill = np.where(valid[..., None], tile, med[None, None, :])
    return fill.astype(np.uint8), valid


def _resize_texture(texture: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    out = cv2.resize(texture, size, interpolation=cv2.INTER_CUBIC)
    out = cv2.GaussianBlur(out, (0, 0), 2.0)
    return out


def build_patches(
    project: Project,
    rgb: np.ndarray,
    alpha: np.ndarray,
    labels: np.ndarray,
) -> list[Patch]:
    if not project.patches.enabled:
        return []

    # The sampler needs the source pixels; attach them for this build only.
    setattr(project, "_rgb", rgb)
    h, w = alpha.shape
    face_mask = (
        polygon_mask([project.face_polygon], (h, w))
        if project.face_polygon
        else None
    )
    patches: list[Patch] = []
    for joint in project.patches.joints:
        if joint not in JOINT_INFO:
            continue
        patch_name, parent_part, parent_bone = JOINT_INFO[joint]
        child_bone = project.bones.get(joint)
        if child_bone is None or parent_bone not in project.bones:
            continue
        if project.parts.get(joint) is not None and project.parts[joint].empty:
            continue
        radii = _patch_radii(project, joint)
        center = child_bone.pivot
        texture, valid = _choose_texture(
            project, labels, alpha, face_mask, joint, center, radii
        )
        size = (
            max(2, int(round(radii[0] * 2))),
            max(2, int(round(radii[1] * 2))),
        )
        tex = _resize_texture(texture, size)
        # Place the texture in the ellipse bounding box.
        x0 = int(round(center[0] - radii[0]))
        y0 = int(round(center[1] - radii[1]))
        x1 = x0 + size[0]
        y1 = y0 + size[1]
        rgba = np.zeros((h, w, 4), np.uint8)
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(w, x1), min(h, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        tx0, ty0 = cx0 - x0, cy0 - y0
        rgba[cy0:cy1, cx0:cx1, :3] = tex[ty0:ty0 + (cy1 - cy0), tx0:tx0 + (cx1 - cx0)]

        ellipse = ellipse_mask((h, w), center, radii)
        feather = max(3.5, min(radii) * 0.18)
        a = cv2.GaussianBlur((ellipse * 255).astype(np.float32), (0, 0), feather)
        # A patch must never change the outer silhouette. Only fill opaque interior.
        a = np.minimum(a, (alpha == 255).astype(np.float32) * 255)
        a = np.clip(a, 0, 255).astype(np.uint8)
        rgba[:, :, 3] = a
        rgba[a == 0, :3] = 0
        if int((a > 0).sum()) == 0:
            continue
        patches.append(
            Patch(
                id=f"patch_{patch_name}",
                joint=joint,
                parent_bone=parent_bone,
                pivot=center,
                rgba=rgba,
                radii=radii,
                sample_part=SAMPLE_PART[joint],
            )
        )
    return patches
