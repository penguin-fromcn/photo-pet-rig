"""Deterministic build: project JSON + photo + alpha -> desktop pet assets."""
from __future__ import annotations

import json
import math
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from .body import build_body_layers, build_labels
from .constants import (
    DEFAULT_MIRROR_PAIRS,
    DEFAULT_ROTATION_RECOMMENDED,
    DEFAULT_ROTATION_SAFE,
    JOINT_INFO,
    SCHEMA,
    STANDARD_BONES,
    STANDARD_PARTS,
    VERSION,
)
from .imageops import (
    alpha_bbox,
    alpha_mismatch,
    composite_rgba,
    enclosed_holes,
    force_face_alpha,
    load_alpha,
    load_embedded_alpha,
    load_rgb,
    polygon_mask,
    save_rgba,
    sha256_array,
    sha256_file,
)
from .patches import Patch, build_patches
from .pose import make_pose_sheet
from .previews import make_layer_sheet, make_main_preview, make_rig_map
from .project import Project, ProjectError, load_project


class BuildError(RuntimeError):
    """The build could not be completed safely."""


@dataclass
class BuildResult:
    folder: Path
    zip_path: Path | None
    character: np.ndarray
    project: Project
    patches: list[Patch]


Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _alpha_from_rembg(project: Project, progress: Progress) -> np.ndarray:
    try:
        from rembg import new_session, remove  # type: ignore
    except ImportError as exc:
        raise BuildError(
            "No alpha supplied and rembg is not installed. Either run "
            "`python -m photopet cutout IMAGE -o cutout.png`, supply `alpha` in the project, "
            "or install the optional dependency with `pip install \"photo-pet-rig[rembg]\"`."
        ) from exc
    progress("running rembg (first run may download a model)")
    rgb = load_rgb(project.image_path)
    try:
        session = new_session("u2net_human_seg")
        result = remove(Image.fromarray(rgb), session=session, post_process_mask=True)
    except Exception as exc:  # rembg raises a variety of download/runtime errors
        raise BuildError(f"rembg failed: {exc}") from exc
    alpha = np.array(result.convert("RGBA").getchannel("A"), np.uint8)
    return alpha


def _resolve_alpha(
    project: Project,
    out_dir: Path,
    cli_alpha: str | Path | None,
    keep_largest: bool,
    progress: Progress,
) -> np.ndarray:
    size = (project.width, project.height)
    explicit = Path(cli_alpha).expanduser().resolve() if cli_alpha else project.alpha_path
    if explicit is not None:
        progress(f"loading alpha: {explicit}")
        alpha = load_alpha(explicit, size)
    else:
        embedded = load_embedded_alpha(project.image_path, size)
        if embedded is not None:
            progress("using embedded alpha from the source image")
            alpha = embedded
        else:
            alpha = _alpha_from_rembg(project, progress)

    if keep_largest:
        from .imageops import keep_largest_component

        before = int((alpha > 0).sum())
        alpha, removed = keep_largest_component(alpha)
        progress(f"kept largest alpha component; removed {removed} other component(s)")
        if int((alpha > 0).sum()) < before * 0.2:
            raise BuildError("keep-largest removed most of the alpha; disable it or fix the matte")

    if int((alpha > 0).sum()) == 0:
        raise BuildError("alpha is empty; remove the background or supply a valid matte")

    if project.face_polygon:
        from .imageops import polygon_mask

        face_mask = polygon_mask([project.face_polygon], (project.height, project.width))
        face_pixels = int(face_mask.sum())
        visible = int((alpha[face_mask] > 0).sum())
        transparent = face_pixels - visible
        if visible == 0:
            raise BuildError(
                "face polygon contains no foreground pixels; draw it inside the visible face/mask"
            )
        if transparent > face_pixels * 0.20:
            raise BuildError(
                "face polygon includes more than 20% transparent background; "
                "shrink it so it stays inside the visible face/mask"
            )
        before = alpha.copy()
        alpha = force_face_alpha(alpha, project.face_polygon)
        forced = int(((before != alpha) & (alpha == 255)).sum())
        if forced:
            progress(f"forced {forced} face-core alpha pixels to 255")
    return alpha


def _output_guard(folder: Path, force: bool) -> None:
    if not folder.exists():
        return
    if not force:
        raise BuildError(
            f"output already exists: {folder}. Pass --force to replace it, or choose another --out."
        )
    if not folder.name.endswith("_pet_assets"):
        raise BuildError(
            f"refusing to remove unexpected folder {folder!r}; it does not end with _pet_assets"
        )
    shutil.rmtree(folder)


def _count_alpha(rgba: np.ndarray) -> int:
    return int((rgba[:, :, 3] > 0).sum())


def _layer_meta(path: Path, rgba: np.ndarray) -> dict:
    return {
        "file": path.name if path.parent.name == "patch_layers" else str(path),
        "alpha_pixels": _count_alpha(rgba),
        "bbox": alpha_bbox(rgba[:, :, 3]),
        "sha256": sha256_file(path),
    }


def _face_qa(
    project: Project,
    source_rgb: np.ndarray,
    source_alpha: np.ndarray,
    head: np.ndarray,
) -> dict | None:
    if not project.face_polygon:
        return None
    mask = polygon_mask([project.face_polygon], (project.height, project.width))
    alpha = head[:, :, 3]
    keep = mask & (alpha > 0)
    source_pixels = source_rgb[keep]
    head_pixels = head[:, :, :3][keep]
    equal = bool(np.array_equal(source_pixels, head_pixels))
    face_total = int(mask.sum())
    foreground = int((mask & (source_alpha > 0)).sum())
    return {
        "polygon": [[float(x), float(y)] for x, y in project.face_polygon],
        "pixel_count": int(keep.sum()),
        "polygon_foreground_ratio": round(foreground / face_total, 4) if face_total else 0.0,
        "rgb_equal_to_source": equal,
        "alpha_min": int(alpha[keep].min()) if keep.any() else 0,
        "rgb_sha256": sha256_array(source_pixels),
    }


def _default_limitations(project: Project) -> list[str]:
    limits = list(project.notes)
    empty_hands = [h for h in ("hand_left", "hand_right") if project.parts[h].empty]
    if len(empty_hands) == 2:
        limits.append(
            "Both hand layers are empty because no hand pixels were annotated. "
            "Do not invent hands; use the forearm ends only."
        )
    elif empty_hands:
        limits.append(
            f"{empty_hands[0]} is an empty placeholder. Do not animate a visible hand on that side."
        )
    limits.extend([
        "This is a single-view photo rig. Head yaw/pitch, side views and back views are not authored.",
        "The face is frozen source pixels. No eye, mouth, mask or expression deformation is available.",
        "Rigid layers can show a thin seam or a notch at large joint rotations; the patch layers are synthetic fabric fills, not recovered anatomy.",
        "Patches are invisible at rest but may look repetitive if exposed by extreme poses.",
        "This is a 2D pivot rig, not a 3D or cloth-simulation model.",
    ])
    return limits


def _animation_hints(project: Project) -> dict:
    return {
        "rotation_convention": (
            "Degrees. Positive matches OpenCV getRotationMatrix2D (counter-clockwise in image "
            "coordinates with +Y down). Invert signs in engines where positive rotation is clockwise."
        ),
        "render": "Keep full-canvas offsets; premultiply alpha before filtering/rotation.",
        "idle": {
            "duration_ms": 2600,
            "loop": True,
            "root_y_px": [-1, 2],
            "torso_rotation_deg": [-0.8, 0.8],
            "head_rotation_deg": [-1.5, 1.5],
            "arm_micro_sway_deg": [-0.8, 0.8],
        },
        "walk_in_place": {
            "duration_ms": 720,
            "loop": True,
            "steps_per_cycle": 2,
            "root_y_px": [-5, 0],
            "thigh_deg": [-4.2, 4.2],
            "knee_bend_deg": [0, 6.0],
            "foot_deg": [-2.0, 1.5],
            "arm_sway_deg": [-1.0, 1.0],
            "note": "Small front-view waddle. This is not a profile walking cycle.",
        },
        "drag": {
            "root_rotation_deg": [-3.0, 3.0],
            "head_lag_deg": [-3.0, 3.0],
            "note": "Tilt the root and let the head lag. Return to idle on release.",
        },
        "release_drop": {
            "duration_ms": 260,
            "impact_root_y_px": 6,
            "knee_bend_deg": [0, 8.0],
            "note": "Overshoot down, then settle to idle.",
        },
        "click_feedback": {
            "duration_ms": 190,
            "head_rotation_deg": -3.5,
            "torso_rotation_deg": 1.0,
            "easing": "ease-out-back",
            "note": "Head/body tilt only; never morph or repaint face pixels.",
        },
    }


def _write_rig(
    project: Project,
    out_dir: Path,
    character: np.ndarray,
    layers: dict[str, np.ndarray],
    patches: list[Patch],
    qa: dict,
) -> dict:
    body_parts = sorted(project.parts.values(), key=lambda p: p.z)
    ordered_parts = [p.id for p in body_parts]
    patch_ids = [p.id for p in patches]
    draw_order = patch_ids + ordered_parts

    # Layer hashes/bboxes are computed from the files that will be delivered.
    layer_paths: dict[str, Path] = {
        p: out_dir / f"{p}.png" for p in STANDARD_PARTS
    }
    patch_paths = {p.id: out_dir / "patch_layers" / f"{p.id}.png" for p in patches}
    z_index = {name: i for i, name in enumerate(draw_order)}

    layers_json = []
    for part_id in STANDARD_PARTS:
        part = project.parts[part_id]
        path = layer_paths[part_id]
        rgba = layers[part_id]
        meta = _layer_meta(path, rgba)
        entry = {
            "id": part_id,
            "file": f"{part_id}.png",
            "bone": part.bone,
            "parent_bone": project.bones[part.bone].parent,
            "pivot": list(project.bones[part.bone].pivot),
            "z_index": z_index[part_id],
            "rotation_recommended_deg": project.rotation_recommended[part.bone],
            "rotation_safe_deg": project.rotation_safe[part.bone],
            "alpha_pixels": meta["alpha_pixels"],
            "bbox": meta["bbox"],
            "sha256": meta["sha256"],
            "source_kind": "empty" if part.empty else "original_photo_pixels",
            "visible_at_rest": not part.empty,
            "empty": part.empty,
            "note": (
                "Empty placeholder; no pixels were annotated for this part."
                if part.empty
                else "Original source pixels; edge RGB is decontaminated only where alpha<255."
            ),
        }
        layers_json.append(entry)

    for patch in patches:
        path = patch_paths[patch.id]
        meta = _layer_meta(path, patch.rgba)
        layers_json.append({
            "id": patch.id,
            "file": f"patch_layers/{patch.id}.png",
            "bone": patch.parent_bone,
            "parent_bone": project.bones[patch.parent_bone].parent,
            "pivot": list(patch.pivot),
            "z_index": z_index[patch.id],
            "rotation_recommended_deg": [0.0, 0.0],
            "rotation_safe_deg": [0.0, 0.0],
            "alpha_pixels": meta["alpha_pixels"],
            "bbox": meta["bbox"],
            "sha256": meta["sha256"],
            "source_kind": "synthetic_repair_patch",
            "visible_at_rest": False,
            "empty": False,
            "note": (
                "Synthetic fabric fill sampled from nearby original pixels. "
                "Never visible at rest; only fills joint gaps."
            ),
        })

    layers_json.sort(key=lambda x: x["z_index"])

    bones_json = {}
    for bone_id in STANDARD_BONES:
        bone = project.bones[bone_id]
        layer_file = None
        if bone_id in project.parts and not project.parts[bone_id].empty:
            layer_file = f"{bone_id}.png"
        rec = {
            "parent": bone.parent,
            "pivot": list(bone.pivot),
            "layer": layer_file,
            "rotation_recommended_deg": project.rotation_recommended.get(bone_id, [0.0, 0.0]),
            "rotation_safe_deg": project.rotation_safe.get(bone_id, [0.0, 0.0]),
        }
        if bone_id == "root":
            rec["translation_safe_px"] = {"x": [-60, 60], "y": [-30, 30]}
        if bone_id.startswith("hand_") and project.parts[bone_id].empty:
            rec["empty"] = True
        bones_json[bone_id] = rec

    knee_types = {
        "upper_arm_left": "shoulder",
        "upper_arm_right": "shoulder",
        "forearm_left": "elbow",
        "forearm_right": "elbow",
        "hand_left": "wrist",
        "hand_right": "wrist",
        "thigh_left": "hip",
        "thigh_right": "hip",
        "shin_left": "knee",
        "shin_right": "knee",
        "foot_left": "ankle",
        "foot_right": "ankle",
        "head": "neck_head_pivot",
        "torso": "waist",
        "root": "pelvis_root",
    }
    joints_json = {}
    for bone_id in STANDARD_BONES:
        bone = project.bones[bone_id]
        joints_json[bone_id] = {
            "x": bone.pivot[0],
            "y": bone.pivot[1],
            "kind": knee_types.get(bone_id, "joint"),
        }

    bbox = alpha_bbox(character[:, :, 3]) or [0, 0, project.width - 1, project.height - 1]
    ground_y = int(bbox[3])
    foot_centers = []
    for foot in ("foot_left", "foot_right"):
        fb = layers[foot]
        bb = alpha_bbox(fb[:, :, 3])
        if bb:
            foot_centers.append((bb[0] + bb[2]) / 2)
    anchor_x = float(np.mean(foot_centers)) if foot_centers else (bbox[0] + bbox[2]) / 2
    joints_json["ground_anchor"] = {
        "x": round(anchor_x, 2),
        "y": ground_y,
        "kind": "ground_contact",
    }

    mirror = []
    for pair in project.mirror_pairs:
        mirror.append({
            "left": pair[0],
            "right": pair[1],
            "mirror_axis_x": round(anchor_x, 2),
            "applied": False,
            "note": "Pairing only. Do not mirror source textures; the source pose is asymmetric.",
        })

    visible_layers = [p for p in project.parts.values() if not p.empty]
    source_files = [f"{p.id}.png" for p in visible_layers]
    patch_files = [f"patch_layers/{p.id}.png" for p in patches]
    limitations = _default_limitations(project)

    rig = {
        "schema": SCHEMA,
        "generator": f"photo-pet-rig {VERSION}",
        "generated_at": now_iso(),
        "name": project.name,
        "slug": project.slug,
        "canvas": {
            "width": project.width,
            "height": project.height,
            "origin": "top-left",
            "units": "pixels",
            "preserve_full_canvas": True,
            "do_not_trim_layers": True,
        },
        "source": {
            "file": project.image_path.name,
            "sha256": project.image_sha256,
            "width": project.width,
            "height": project.height,
        },
        "character": {
            "reference": "character_original.png",
            "visible_bbox": bbox,
            "visible_alpha_pixels": _count_alpha(character),
            "ground_y": ground_y,
            "shadow_y": max(0, ground_y - 20),
            "anchor": [round(anchor_x, 2), ground_y],
            "recommended_display_height_px": [260, 420],
            "recommended_scale": [round(260 / project.height, 3), round(420 / project.height, 3)],
            "facing": "front",
            "naming": {
                "left": "character-left = screen right",
                "right": "character-right = screen left",
            },
        },
        "render": {
            "alpha_mode": "straight (non-premultiplied PNG)",
            "blend": "source-over",
            "premultiply_before_transform_and_filtering": True,
            "resampling": "bilinear",
            "rotation_pivot_units": "canvas pixels",
            "note": (
                "Load every layer at (0,0) on the full canvas. Rotate around pivots. "
                "Do not re-center or auto-trim individual layers."
            ),
        },
        "bones": bones_json,
        "joints": joints_json,
        "layers": layers_json,
        "draw_order": draw_order,
        "mirror_pairs": mirror,
        "origin_attribution": {
            "original_photo_pixels": source_files,
            "synthetic_repair_patches": patch_files,
            "empty_layers": [f"{h}.png" for h in ("hand_left", "hand_right") if project.parts[h].empty],
            "patch_generation": (
                "Sampled from nearby original fabric pixels, resized, blurred and color-matched. "
                "No generative model was used for image content."
            ),
            "patches_hidden_at_rest": qa.get("patches_invisible_at_rest", {}).get("exact", False),
            "no_generative_redraw": True,
        },
        "animation_hints": _animation_hints(project),
        "limitations": limitations,
        "qa": qa,
        "previews": {
            "composite": "preview.png",
            "layer_inventory": "preview_layers.png",
            "joint_map": "preview_rig.png",
            "pose_tests": "preview_poses.png",
        },
    }
    (out_dir / "rig.json").write_text(json.dumps(rig, ensure_ascii=False, indent=2), encoding="utf-8")
    return rig


def _write_qa_report(out_dir: Path, rig: dict, qa: dict, layer_hashes: dict[str, str]) -> None:
    report = {
        "generated_at": rig["generated_at"],
        "generator": rig["generator"],
        "source_sha256": rig["source"]["sha256"],
        "canvas": [rig["canvas"]["width"], rig["canvas"]["height"]],
        "summary": {
            "status": "delivered_with_limitations",
            "exact_layer_reassembly": qa["reassembly"]["exact"],
            "face_pixels_unchanged": qa["face"]["rgb_equal_to_source"] if qa.get("face") else None,
            "patches_hidden_at_rest": qa["patches_invisible_at_rest"]["exact"],
            "zero_enclosed_holes_at_rest": not qa["holes"]["rest_enclosed_holes"],
            "empty_body_layers": qa["empty_body_layers"],
        },
        "checks": qa,
        "layer_hashes": layer_hashes,
        "reference_sha256": sha256_file(out_dir / "character_original.png"),
        "notes": [
            "No generative image model is used by the build step.",
            "Patch layers are synthetic fabric fills and are hidden at rest.",
            "A single-view photo cannot provide side/back or expression data.",
        ],
    }
    (out_dir / "qa_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_output_readme(
    project: Project,
    out_dir: Path,
    rig: dict,
    qa: dict,
    patches: list[Patch],
) -> None:
    body_rows = []
    for part_id in STANDARD_PARTS:
        rec = next(x for x in rig["layers"] if x["id"] == part_id)
        if rec["empty"]:
            body_rows.append(f"| `{part_id}.png` | empty | — | — |")
        else:
            bbox = rec["bbox"]
            body_rows.append(
                f"| `{part_id}.png` | {rec['alpha_pixels']:,} | `{bbox}` | "
                f"`{rec['pivot']}` |"
            )
    patch_rows = []
    for patch in patches:
        rec = next(x for x in rig["layers"] if x["id"] == patch.id)
        patch_rows.append(
            f"| `patch_layers/{patch.id}.png` | {rec['alpha_pixels']:,} | "
            f"`{rec['bbox']}` | `{patch.pivot}` |"
        )
    face = qa.get("face")
    face_line = (
        f"Face region: **{face['pixel_count']:,} px**, RGB identical to source: "
        f"**{face['rgb_equal_to_source']}**, alpha min **{face['alpha_min']}**. "
        f"SHA-256: `{face['rgb_sha256']}`."
        if face
        else "No face polygon was supplied, so the face-identity check was skipped."
    )
    lim = "\n".join(f"- {x}" for x in rig["limitations"])
    patch_table = (
        "| File | Alpha px | bbox | Pivot |\n|---|---:|---|---:|\n" + "\n".join(patch_rows)
        if patch_rows
        else "_No patches were requested._"
    )
    md = f"""# {project.name} — 2D photo pet rig

Generated with **photo-pet-rig {VERSION}**.

- Canvas: **{project.width} × {project.height} px**, origin top-left
- Source: `{project.image_path.name}` (SHA-256 `{project.image_sha256}`)
- Visible bounding box: `{rig['character']['visible_bbox']}`
- Ground anchor: `{rig['character']['anchor']}`
- All body layers keep the full original canvas; do not trim or re-center them.

![preview](preview.png)

![pose test](preview_poses.png)

## Body layers

| File | Alpha px | bbox | Pivot |
|---|---:|---|---:|
{chr(10).join(body_rows)}

The two hand files can be empty when the hands are not visible in the photo. Empty hand layers are still present for interface compatibility.

## Repair patches

These are **synthetic** fabric fills sampled from nearby visible pixels. They are rendered behind every body layer and are invisible at rest. They are not recovered hidden anatomy.

{patch_table}

## Draw order

`{' → '.join(rig['draw_order'])}`

## Quality checks

- Layer reassembly: **{qa['reassembly']['exact']}** ({qa['reassembly']['visible_pixels_compared']:,} visible pixels, {qa['reassembly']['alpha_mismatch_pixels_gt1']} alpha errors, {qa['reassembly']['rgb_mismatch_pixels_gt1']} RGB errors)
- Patches invisible at rest: **{qa['patches_invisible_at_rest']['exact']}**
- Enclosed transparent holes at rest: **{len(qa['holes']['rest_enclosed_holes'])}**
- {face_line}

Pose tests are a visual aid; narrow negative space between limbs can look enclosed at some angles. Inspect `preview_poses.png` rather than treating its pixel count as a pass/fail signal.

## Render

Straight alpha PNG. If the engine filters/rotates textures, premultiply alpha first. Load each layer at `(0, 0)` on the full `{project.width} × {project.height}` canvas and rotate around the pivot listed in `rig.json`. Do not auto-trim individual layers.

## Limitations

{lim}

## Reproduce / edit

Edit `project.json` outside this folder, then rebuild with:

```bash
photopet build project.json --out <parent-folder> --force
```

The source photo is not duplicated in this output.
"""
    (out_dir / "README.md").write_text(md, encoding="utf-8")


def _save_patch_layers(out_dir: Path, patches: list[Patch]) -> list[Path]:
    pdir = out_dir / "patch_layers"
    pdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for patch in patches:
        path = pdir / f"{patch.id}.png"
        save_rgba(path, patch.rgba)
        paths.append(path)
    return paths


def build(
    project_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    alpha: str | Path | None = None,
    keep_largest: bool = False,
    force: bool = False,
    make_zip: bool = True,
    no_patches: bool = False,
    progress: Progress | None = None,
) -> BuildResult:
    """Build all assets, previews, metadata and optionally a ZIP."""
    progress = progress or _noop
    project = load_project(project_path)
    if out_dir is None:
        raise BuildError("out_dir is required")
    out_parent = Path(out_dir).expanduser().resolve()
    out_parent.mkdir(parents=True, exist_ok=True)
    folder = out_parent / f"{project.slug}_pet_assets"
    zip_path = out_parent / f"{folder.name}.zip"
    if make_zip and zip_path.exists() and not force:
        raise BuildError(
            f"output ZIP already exists: {zip_path}. Pass --force to replace it."
        )
    _output_guard(folder, force)
    folder.mkdir(parents=True, exist_ok=False)

    progress(f"project: {project.name} ({project.width}x{project.height})")
    rgb = load_rgb(project.image_path)
    alpha_arr = _resolve_alpha(project, folder, alpha, keep_largest, progress)
    Image.fromarray(alpha_arr).save(folder / "alpha_used.png", optimize=True)

    labels_result = build_labels(project, alpha_arr)
    progress(
        f"labels: {labels_result.face_pixels:,} face px; "
        f"overlap caps on {len(labels_result.overlaps)} joints"
    )
    layers = build_body_layers(project, rgb, alpha_arr, labels_result.masks)
    for part_id, rgba in layers.items():
        save_rgba(folder / f"{part_id}.png", rgba)
        progress(f"saved {part_id}.png")

    if no_patches:
        patches: list[Patch] = []
    else:
        patches = build_patches(project, rgb, alpha_arr, labels_result.labels)
    _save_patch_layers(folder, patches)
    progress(f"patches: {len(patches)}")

    body_order = [p.id for p in sorted(project.parts.values(), key=lambda p: p.z)]
    character = composite_rgba([p.rgba for p in patches] + [layers[p] for p in body_order])
    save_rgba(folder / "character_original.png", character)

    without_patches = composite_rgba([layers[p] for p in body_order])
    alpha_err, rgb_err = alpha_mismatch(character, without_patches)
    rest_holes = enclosed_holes(character[:, :, 3])
    patch_hidden = bool(alpha_err == 0 and rgb_err == 0)
    if not patch_hidden:
        raise BuildError(
            "patch layers changed the static composite; refusing to ship a rig whose rest pose "
            f"is not exact ({alpha_err} alpha, {rgb_err} RGB mismatches)."
        )

    # Full reassembly check, independent of the two composites above.
    reassembled = composite_rgba([p.rgba for p in patches] + [layers[p] for p in body_order])
    a_err, r_err = alpha_mismatch(character, reassembled)
    face_qa = _face_qa(project, rgb, alpha_arr, layers["head"])
    empty_body_layers = [p for p in STANDARD_PARTS if project.parts[p].empty]
    alpha_components = int(cv2.connectedComponents((character[:, :, 3] > 0).astype(np.uint8), 8)[0] - 1)

    progress("rendering pose test")
    pose_results = make_pose_sheet(
        project, character, layers, patches, folder / "preview_poses.png"
    )
    pose_holes = {r.name: r.holes for r in pose_results}

    qa = {
        "all_asset_layers_rgba": True,
        "all_layers_same_canvas": all(
            a.shape[:2] == (project.height, project.width) for a in layers.values()
        ),
        "body_layer_pngs": len(STANDARD_PARTS),
        "empty_body_layers": empty_body_layers,
        "patch_count": len(patches),
        "reassembly": {
            "visible_pixels_compared": int((character[:, :, 3] > 0).sum()),
            "alpha_mismatch_pixels_gt1": a_err,
            "rgb_mismatch_pixels_gt1": r_err,
            "exact": bool(a_err == 0 and r_err == 0),
        },
        "patches_invisible_at_rest": {
            "alpha_mismatch_pixels_gt1": alpha_err,
            "rgb_mismatch_pixels_gt1": rgb_err,
            "exact": patch_hidden,
        },
        "holes": {
            "rest_enclosed_holes": rest_holes,
            "zero_enclosed_holes_at_rest": not rest_holes,
            "pose_tests_informational": pose_holes,
            "pose_test_note": (
                "Pose renders are for visual QA. A narrow gap between the legs or arms "
                "can become an enclosed negative space at some angles; this is not the same "
                "as a hole inside a body layer. Check preview_poses.png visually."
            ),
        },
        "face": face_qa,
        "background": {
            "corner_alpha": [
                int(character[0, 0, 3]),
                int(character[0, -1, 3]),
                int(character[-1, 0, 3]),
                int(character[-1, -1, 3]),
            ],
            "alpha_components": alpha_components,
        },
        "edge_cleanup": (
            "RGB was replaced by nearby interior colour only for pixels with 0 < alpha < 255. "
            "Alpha and all opaque pixels are unchanged."
        ),
        "known_unsupported": [
            "visible hands if their layers are empty",
            "natural large arm swing if the source pose is crossed",
            "profile/back views",
            "facial expression animation",
            "seam-free large-angle rotation",
        ],
    }

    rig = _write_rig(project, folder, character, layers, patches, qa)
    layer_hashes = {}
    for part_id in STANDARD_PARTS:
        layer_hashes[f"{part_id}.png"] = sha256_file(folder / f"{part_id}.png")
    for patch in patches:
        layer_hashes[f"patch_layers/{patch.id}.png"] = sha256_file(folder / "patch_layers" / f"{patch.id}.png")
    _write_qa_report(folder, rig, qa, layer_hashes)

    progress("rendering previews")
    make_main_preview(project, character, folder / "preview.png")
    make_layer_sheet(project, character, layers, patches, folder / "preview_layers.png")
    make_rig_map(project, character, folder / "preview_rig.png")
    _write_output_readme(project, folder, rig, qa, patches)

    zip_path = None
    if make_zip:
        zip_path = out_parent / f"{folder.name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=str(Path(folder.name) / path.relative_to(folder)))
        progress(f"zip: {zip_path}")

    return BuildResult(folder, zip_path, character, project, patches)
