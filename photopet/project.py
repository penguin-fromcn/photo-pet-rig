"""Project schema loading and validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

from .constants import (
    DEFAULT_MIRROR_PAIRS,
    DEFAULT_PATCH_JOINTS,
    DEFAULT_ROTATION_RECOMMENDED,
    DEFAULT_ROTATION_SAFE,
    DEFAULT_Z,
    JOINT_INFO,
    REQUIRED_BODY_PARTS,
    SCHEMA,
    STANDARD_BONES,
    STANDARD_PARTS,
)


class ProjectError(ValueError):
    """A project file is missing required data or is internally inconsistent."""


@dataclass
class Bone:
    id: str
    parent: str | None
    pivot: tuple[float, float]


@dataclass
class Part:
    id: str
    bone: str
    z: int
    polygons: list[list[tuple[float, float]]]
    empty: bool = False
    fallback: bool = False


@dataclass
class PatchConfig:
    enabled: bool = True
    joints: list[str] = field(default_factory=lambda: list(DEFAULT_PATCH_JOINTS))
    scale: float = 1.0
    overlap_px: int = 4
    radii: dict[str, tuple[float, float]] = field(default_factory=dict)


@dataclass
class Project:
    path: Path
    raw: dict[str, Any]
    name: str
    slug: str
    image_path: Path
    image_sha256: str
    alpha_path: Path | None
    width: int
    height: int
    bones: dict[str, Bone]
    parts: dict[str, Part]
    face_polygon: list[tuple[float, float]] | None
    patches: PatchConfig
    rotation_recommended: dict[str, list[float]]
    rotation_safe: dict[str, list[float]]
    mirror_pairs: list[list[str]]
    notes: list[str]
    preview_title: str

    @property
    def draw_order(self) -> list[str]:
        return [
            p.id
            for p in sorted(
                (p for p in self.parts.values() if not p.empty),
                key=lambda p: p.z,
            )
        ]

    @property
    def all_parts(self) -> list[Part]:
        return sorted(self.parts.values(), key=lambda p: p.z)


def _resolve(base: Path, value: str | Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _number(value: Any, label: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise ProjectError(f"{label} must be a number") from exc
    if not (n == n) or n in (float("inf"), float("-inf")):
        raise ProjectError(f"{label} must be finite")
    return n


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProjectError(f"{label} must be [x, y]")
    return (_number(value[0], f"{label}[0]"), _number(value[1], f"{label}[1]"))


def _polygon(value: Any, label: str) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise ProjectError(f"{label} must be a polygon with at least 3 points")
    return [_point(p, f"{label}[{i}]") for i, p in enumerate(value)]


def _canvas_point(
    p: tuple[float, float],
    width: int,
    height: int,
    label: str,
    *,
    allow_edge: bool = True,
) -> None:
    x, y = p
    xmax = width if allow_edge else width - 1
    ymax = height if allow_edge else height - 1
    if not (0 <= x <= xmax and 0 <= y <= ymax):
        raise ProjectError(f"{label} is outside the {width}x{height} canvas: {p}")


def sanitize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = slug[:48].strip("-")
    return slug or "pet"


def read_image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return im.size


def _validate_bones(raw: dict[str, Any], width: int, height: int) -> dict[str, Bone]:
    data = raw.get("bones")
    if not isinstance(data, dict) or not data:
        raise ProjectError("bones must be a non-empty object")

    missing = [b for b in STANDARD_BONES if b not in data]
    if missing:
        raise ProjectError(
            "bones is missing standard joints: " + ", ".join(missing)
        )

    bones: dict[str, Bone] = {}
    for bone_id, item in data.items():
        if not isinstance(item, dict):
            raise ProjectError(f"bones.{bone_id} must be an object")
        parent = item.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise ProjectError(f"bones.{bone_id}.parent must be a string or null")
        pivot = _point(item.get("pivot"), f"bones.{bone_id}.pivot")
        _canvas_point(pivot, width, height, f"bones.{bone_id}.pivot")
        bones[bone_id] = Bone(bone_id, parent, pivot)

    if bones["root"].parent is not None:
        raise ProjectError("bones.root.parent must be null")
    for bone in bones.values():
        if bone.parent is not None and bone.parent not in bones:
            raise ProjectError(
                f"bones.{bone.id}.parent references unknown bone {bone.parent!r}"
            )

    # Cycle check
    for bone in bones.values():
        seen: set[str] = set()
        cur: str | None = bone.id
        while cur is not None:
            if cur in seen:
                raise ProjectError(f"bone hierarchy contains a cycle at {cur!r}")
            seen.add(cur)
            cur = bones[cur].parent
    return bones


def _validate_parts(
    raw: dict[str, Any],
    bones: dict[str, Bone],
    width: int,
    height: int,
) -> dict[str, Part]:
    data = raw.get("parts")
    if not isinstance(data, dict):
        raise ProjectError("parts must be an object")

    missing = [p for p in STANDARD_PARTS if p not in data]
    if missing:
        raise ProjectError(
            "parts is missing standard layers: " + ", ".join(missing)
        )
    extra = sorted(set(data) - set(STANDARD_PARTS))
    if extra:
        raise ProjectError(
            "unsupported part ids: " + ", ".join(extra) + ". "
            "Use the standard 14-layer schema documented in README.md."
        )

    parts: dict[str, Part] = {}
    z_seen: dict[int, str] = {}
    for part_id in STANDARD_PARTS:
        item = data[part_id]
        if not isinstance(item, dict):
            raise ProjectError(f"parts.{part_id} must be an object")
        bone = item.get("bone", part_id)
        if bone not in bones:
            raise ProjectError(f"parts.{part_id}.bone references unknown bone {bone!r}")
        if bone != part_id:
            raise ProjectError(
                f"parts.{part_id}.bone must be {part_id!r}; custom bone mappings are not supported."
            )
        z = item.get("z", DEFAULT_Z[part_id])
        if not isinstance(z, int):
            raise ProjectError(f"parts.{part_id}.z must be an integer")
        if z in z_seen:
            raise ProjectError(
                f"parts.{part_id}.z={z} collides with {z_seen[z]}; z values must be unique"
            )
        z_seen[z] = part_id
        empty = bool(item.get("empty", False))
        fallback = bool(item.get("fallback", part_id == "torso"))
        if fallback and part_id != "torso":
            raise ProjectError("only the torso part may be the fallback layer")
        polys_raw = item.get("polygons", [])
        if not isinstance(polys_raw, list):
            raise ProjectError(f"parts.{part_id}.polygons must be a list")
        polygons: list[list[tuple[float, float]]] = []
        for i, poly in enumerate(polys_raw):
            points = _polygon(poly, f"parts.{part_id}.polygons[{i}]")
            for p in points:
                _canvas_point(p, width, height, f"parts.{part_id}.polygons[{i}]")
            polygons.append(points)
        if empty and polygons:
            raise ProjectError(
                f"parts.{part_id} is marked empty but still has polygons"
            )
        if not empty and not polygons:
            raise ProjectError(
                f"parts.{part_id} needs at least one polygon, or mark it empty"
            )
        parts[part_id] = Part(part_id, bone, z, polygons, empty, fallback)

    if not parts["torso"].fallback:
        raise ProjectError("parts.torso.fallback must be true")
    for hand in ("hand_left", "hand_right"):
        if not parts[hand].empty:
            # Visible hands are allowed, but we still require explicit polygons.
            pass
    return parts


def load_project(path: str | Path) -> Project:
    """Load and fully validate a project JSON file."""
    project_path = Path(path).expanduser().resolve()
    if not project_path.is_file():
        raise ProjectError(f"project file not found: {project_path}")
    try:
        raw = json.loads(project_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"invalid project JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectError("project root must be a JSON object")
    if raw.get("schema") != SCHEMA:
        raise ProjectError(
            f"schema must be {SCHEMA!r}, got {raw.get('schema')!r}"
        )

    base = project_path.parent
    if not raw.get("image"):
        raise ProjectError("image is required")
    image_path = _resolve(base, raw["image"])
    if not image_path.is_file():
        raise ProjectError(f"image not found: {image_path}")
    width, height = read_image_size(image_path)

    declared = raw.get("canvas")
    if declared is not None:
        if not isinstance(declared, dict):
            raise ProjectError("canvas must be an object")
        dw = int(declared.get("width", width))
        dh = int(declared.get("height", height))
        if (dw, dh) != (width, height):
            raise ProjectError(
                f"canvas mismatch: project says {dw}x{dh}, image is {width}x{height}. "
                "Use the oriented image size; do not resize the photo."
            )

    alpha_path: Path | None = None
    if raw.get("alpha"):
        alpha_path = _resolve(base, raw["alpha"])
        if not alpha_path.is_file():
            raise ProjectError(f"alpha not found: {alpha_path}")

    name = str(raw.get("name") or image_path.stem).strip() or "pet"
    slug = sanitize_slug(name)
    bones = _validate_bones(raw, width, height)
    parts = _validate_parts(raw, bones, width, height)

    face_polygon = None
    face = raw.get("face")
    if face is not None:
        if not isinstance(face, dict):
            raise ProjectError("face must be an object")
        face_polygon = _polygon(face.get("polygon"), "face.polygon")
        for p in face_polygon:
            _canvas_point(p, width, height, "face.polygon")

    patches_raw = raw.get("patches", {})
    if patches_raw is None:
        patches_raw = {}
    if not isinstance(patches_raw, dict):
        raise ProjectError("patches must be an object")
    patch_joints = patches_raw.get("joints", list(DEFAULT_PATCH_JOINTS))
    if not isinstance(patch_joints, list) or not all(
        isinstance(j, str) for j in patch_joints
    ):
        raise ProjectError("patches.joints must be a list of bone ids")
    for joint in patch_joints:
        if joint not in JOINT_INFO:
            raise ProjectError(
                f"patches.joints contains unsupported joint {joint!r}; "
                f"supported: {', '.join(JOINT_INFO)}"
            )
        if joint not in bones:
            raise ProjectError(f"patches.joints references missing bone {joint!r}")
    scale = _number(patches_raw.get("scale", 1.0), "patches.scale")
    if scale <= 0:
        raise ProjectError("patches.scale must be > 0")
    overlap_px = patches_raw.get("overlap_px", 4)
    if not isinstance(overlap_px, int) or overlap_px < 0 or overlap_px > 40:
        raise ProjectError("patches.overlap_px must be an integer in 0..40")
    patch_radii: dict[str, tuple[float, float]] = {}
    radii_raw = patches_raw.get("radii", {})
    if radii_raw is None:
        radii_raw = {}
    if not isinstance(radii_raw, dict):
        raise ProjectError("patches.radii must be an object")
    for joint, radii in radii_raw.items():
        if joint not in JOINT_INFO:
            raise ProjectError(f"patches.radii has unsupported joint {joint!r}")
        if not isinstance(radii, (list, tuple)) or len(radii) != 2:
            raise ProjectError(f"patches.radii.{joint} must be [rx, ry]")
        rx = _number(radii[0], f"patches.radii.{joint}[0]")
        ry = _number(radii[1], f"patches.radii.{joint}[1]")
        if rx <= 0 or ry <= 0:
            raise ProjectError(f"patches.radii.{joint} must be positive")
        patch_radii[joint] = (rx, ry)
    patch_config = PatchConfig(
        enabled=bool(patches_raw.get("enabled", True)),
        joints=list(patch_joints),
        scale=scale,
        overlap_px=overlap_px,
        radii=patch_radii,
    )

    rec = dict(DEFAULT_ROTATION_RECOMMENDED)
    safe = dict(DEFAULT_ROTATION_SAFE)
    ranges = raw.get("rotation_ranges", {})
    if ranges is not None:
        if not isinstance(ranges, dict):
            raise ProjectError("rotation_ranges must be an object")
        for section, target in (("recommended", rec), ("safe", safe)):
            section_raw = ranges.get(section, {})
            if section_raw is None:
                continue
            if not isinstance(section_raw, dict):
                raise ProjectError(f"rotation_ranges.{section} must be an object")
            for bone, values in section_raw.items():
                if bone not in bones:
                    raise ProjectError(
                        f"rotation_ranges.{section} references missing bone {bone!r}"
                    )
                if not isinstance(values, (list, tuple)) or len(values) != 2:
                    raise ProjectError(
                        f"rotation_ranges.{section}.{bone} must be [min, max]"
                    )
                target[bone] = [
                    _number(values[0], f"rotation_ranges.{section}.{bone}[0]"),
                    _number(values[1], f"rotation_ranges.{section}.{bone}[1]"),
                ]
    for bone in bones:
        rec.setdefault(bone, [0.0, 0.0])
        safe.setdefault(bone, [0.0, 0.0])

    mirror_pairs = raw.get("mirror_pairs", DEFAULT_MIRROR_PAIRS)
    if not isinstance(mirror_pairs, list):
        raise ProjectError("mirror_pairs must be a list")
    clean_pairs: list[list[str]] = []
    for i, pair in enumerate(mirror_pairs):
        if (
            not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(x, str) for x in pair)
        ):
            raise ProjectError(f"mirror_pairs[{i}] must be [left, right]")
        clean_pairs.append([pair[0], pair[1]])

    notes_raw = raw.get("notes", [])
    if isinstance(notes_raw, str):
        notes = [notes_raw]
    elif isinstance(notes_raw, list) and all(isinstance(x, str) for x in notes_raw):
        notes = list(notes_raw)
    elif notes_raw is None:
        notes = []
    else:
        raise ProjectError("notes must be a string or a list of strings")

    preview_raw = raw.get("preview", {})
    if preview_raw is None:
        preview_raw = {}
    if not isinstance(preview_raw, dict):
        raise ProjectError("preview must be an object")
    preview_title = str(preview_raw.get("title", name))

    return Project(
        path=project_path,
        raw=raw,
        name=name,
        slug=slug,
        image_path=image_path,
        image_sha256=_sha256(image_path),
        alpha_path=alpha_path,
        width=width,
        height=height,
        bones=bones,
        parts=parts,
        face_polygon=face_polygon,
        patches=patch_config,
        rotation_recommended=rec,
        rotation_safe=safe,
        mirror_pairs=clean_pairs,
        notes=notes,
        preview_title=preview_title,
    )


def project_summary(project: Project) -> str:
    lines = [
        f"project: {project.name} ({project.slug})",
        f"image:   {project.image_path}  {project.width}x{project.height}",
        f"alpha:   {project.alpha_path or '(none; will try rembg)'}",
        f"parts:   {sum(1 for p in project.parts.values() if not p.empty)} visible / 14",
        f"patches: {len(project.patches.joints) if project.patches.enabled else 0} requested",
        f"face:    {'yes' if project.face_polygon else 'no (identity check disabled)'}",
    ]
    return "\n".join(lines)

