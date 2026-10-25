"""Turn project polygons into an exclusive label map and body RGBA layers."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .constants import STANDARD_PARTS
from .imageops import defringe_rgba, disk_mask, polygon_mask, rgba_layer
from .project import Project, ProjectError


@dataclass
class LabelResult:
    labels: np.ndarray
    masks: dict[str, np.ndarray]
    overlaps: dict[str, int]
    face_pixels: int
    nearest_filled: int = 0
    polygon_coverage: float = 1.0


# child bone -> parent/body part used for a small rotation overlap
JOINT_PARTNER = {
    "head": "torso",
    "upper_arm_left": "torso",
    "upper_arm_right": "torso",
    "forearm_left": "upper_arm_left",
    "forearm_right": "upper_arm_right",
    "hand_left": "forearm_left",
    "hand_right": "forearm_right",
    "thigh_left": "torso",
    "thigh_right": "torso",
    "shin_left": "thigh_left",
    "shin_right": "thigh_right",
    "foot_left": "shin_left",
    "foot_right": "shin_right",
}


def build_labels(project: Project, alpha: np.ndarray) -> LabelResult:
    """Assign every foreground pixel to exactly one part, topmost z wins."""
    h, w = alpha.shape
    labels = np.zeros((h, w), np.int16)
    index = {part_id: i + 1 for i, part_id in enumerate(STANDARD_PARTS)}
    fg = alpha > 0

    polygon_masks: dict[str, np.ndarray] = {}
    # Higher z first: a sleeve drawn above the torso owns the overlap.
    for part in sorted(project.parts.values(), key=lambda p: p.z, reverse=True):
        if part.empty:
            continue
        mask = polygon_mask(part.polygons, (h, w))
        polygon_masks[part.id] = mask
        take = fg & (labels == 0) & mask
        labels[take] = index[part.id]

    # Anti-aliased silhouette fringes and slightly-too-tight polygons can leave
    # a few foreground pixels unassigned. Assign them to the nearest part rather
    # than dumping them into the torso (which would leave torso-coloured specks
    # all over the character during animation).
    unassigned = fg & (labels == 0)
    nearest_filled = 0
    coverage = 1.0
    if unassigned.any():
        coverage = 1.0 - float(unassigned.sum()) / max(1, int(fg.sum()))
        if coverage < 0.85:
            raise ProjectError(
                f"part polygons cover only {coverage * 100:.1f}% of the alpha. "
                "Expand the polygons or fix the alpha; at least 85% coverage is required."
            )
        best_dist = np.full((h, w), np.inf, np.float32)
        best_label = np.zeros((h, w), np.int16)
        for part in sorted(project.parts.values(), key=lambda p: p.z, reverse=True):
            if part.empty:
                continue
            mask = polygon_masks[part.id]
            # Distance to the nearest pixel of this part's polygon.
            dist = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)
            update = unassigned & (dist < best_dist - 1e-6)
            if update.any():
                best_dist[update] = dist[update]
                best_label[update] = index[part.id]
        fill = unassigned & (best_label > 0)
        labels[fill] = best_label[fill]
        nearest_filled = int(fill.sum())
        if int((fg & (labels == 0)).sum()):
            raise ProjectError("could not assign every foreground pixel to a part")

    # Never let a jaw/neck cut steal visible face pixels from the head.
    face_pixels = 0
    if project.face_polygon:
        face = polygon_mask([project.face_polygon], (h, w))
        face_pixels = int((face & fg).sum())
        if face_pixels == 0:
            raise ProjectError(
                "face.polygon contains no foreground pixels. Draw the face polygon "
                "inside the visible face/head area, or remove it to disable the check."
            )
        if "head" not in project.parts or project.parts["head"].empty:
            raise ProjectError("face.polygon requires a non-empty head part")
        labels[face & fg] = index["head"]

    for part in project.parts.values():
        if part.empty:
            continue
        count = int((labels == index[part.id]).sum())
        if count == 0:
            raise ProjectError(
                f"part {part.id!r} has polygons but captured no foreground pixels. "
                "Check the polygon and the alpha channel."
            )

    masks = {
        part_id: labels == index[part_id]
        for part_id in STANDARD_PARTS
    }

    # Small overlap caps reduce holes at rotations. Only the child receives
    # parent pixels; the parent layer stays purely original.
    overlaps: dict[str, int] = {}
    if project.patches.overlap_px > 0:
        for child, partner in JOINT_PARTNER.items():
            child_part = project.parts.get(child)
            partner_part = project.parts.get(partner)
            if child_part is None or partner_part is None:
                continue
            if child_part.empty or partner_part.empty:
                continue
            bone = project.bones.get(child)
            if bone is None:
                continue
            disk = disk_mask((h, w), bone.pivot, project.patches.overlap_px)
            add = masks[partner] & disk & (alpha == 255)
            added = int((add & ~masks[child]).sum())
            if added:
                masks[child] = masks[child] | add
                overlaps[child] = overlaps.get(child, 0) + added
    return LabelResult(
        labels, masks, overlaps, face_pixels,
        nearest_filled=nearest_filled,
        polygon_coverage=coverage,
    )


def build_body_layers(
    project: Project,
    rgb: np.ndarray,
    alpha: np.ndarray,
    masks: dict[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Create all 14 straight-alpha body layers, then decontaminate their edges."""
    layers: dict[str, np.ndarray] = {}
    for part_id in STANDARD_PARTS:
        part = project.parts[part_id]
        if part.empty:
            h, w = alpha.shape
            layers[part_id] = np.zeros((h, w, 4), np.uint8)
        else:
            layer = rgba_layer(rgb, alpha, masks[part_id])
            layer = defringe_rgba(layer)
            layers[part_id] = layer
    return layers
