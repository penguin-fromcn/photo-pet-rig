"""Loading and validating the layered PNG rig.

Design goals
------------
* Never modify the supplied assets.
* Validate rig.json strictly enough to catch broken rigs early.
* Produce a clear, user-facing reason when layered animation is unavailable.
* Fall back to the supplied complete character PNG.  Degraded mode still
  supports dragging, gravity and whole-body animation, but not limb animation.

Pillow is used for image decoding / alpha cropping.  The renderer converts the
cropped RGBA crops to premultiplied Qt images.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

REQUIRED_LAYER_BONES: tuple[str, ...] = (
    "torso",
    "head",
    "upper_arm_left",
    "forearm_left",
    "upper_arm_right",
    "forearm_right",
    "thigh_left",
    "shin_left",
    "foot_left",
    "thigh_right",
    "shin_right",
    "foot_right",
)

OPTIONAL_BONE_BONES: tuple[str, ...] = ("hand_left", "hand_right")

DEFAULT_SAFE_LIMITS: dict[str, tuple[float, float]] = {
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


class AssetLoadError(Exception):
    """The usable character assets cannot be loaded at all."""


class LayeredRigError(Exception):
    """The layered rig is unusable; the full PNG may still work."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _vec2(value: Any, name: str) -> tuple[float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(_is_number(v) for v in value)
    ):
        raise LayeredRigError(f"rig.json 中 {name} 必须是 [x, y] 数字坐标。")
    result = (float(value[0]), float(value[1]))
    if not all(math.isfinite(v) for v in result):
        raise LayeredRigError(f"rig.json 中 {name} 包含非有限数字。")
    return result


def _safe_limits(raw: dict[str, Any], name: str) -> tuple[float, float]:
    for key in ("rotation_safe_deg", "rotation_recommended_deg"):
        value = raw.get(key)
        if (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(_is_number(v) and math.isfinite(float(v)) for v in value)
        ):
            lo, hi = float(value[0]), float(value[1])
            if lo > hi:
                lo, hi = hi, lo
            return (lo, hi)
    return DEFAULT_SAFE_LIMITS.get(name, (-30.0, 30.0))


@dataclass(frozen=True, slots=True)
class BoneDef:
    name: str
    parent: str | None
    pivot: tuple[float, float]
    safe_limits: tuple[float, float]


@dataclass(frozen=True, slots=True)
class LayerDef:
    id: str
    file: str
    bone: str
    z_index: int
    pivot: tuple[float, float]
    empty: bool = False
    source_kind: str = ""


@dataclass(slots=True)
class LoadedLayer:
    definition: LayerDef
    image: Image.Image
    crop_offset: tuple[int, int]
    crop_size: tuple[int, int]

    def close(self) -> None:
        try:
            self.image.close()
        except Exception:
            pass


@dataclass(slots=True)
class CharacterAssets:
    mode: str  # "layered" or "degraded"
    canvas_width: int
    canvas_height: int
    anchor: tuple[float, float]
    ground_y: float
    visible_bbox: tuple[int, int, int, int]
    layers: list[LoadedLayer]
    draw_order: list[str]
    layer_map: dict[str, LoadedLayer]
    bones: dict[str, BoneDef]
    bone_limits: dict[str, tuple[float, float]]
    assets_dir: Path
    rig_path: Path | None
    character_path: Path
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    degraded_reason: str = ""
    source_photo: Path | None = None

    @property
    def supports_limb_animation(self) -> bool:
        return self.mode == "layered"

    @property
    def visible_width(self) -> float:
        return float(self.visible_bbox[2] - self.visible_bbox[0] + 1)

    @property
    def visible_height(self) -> float:
        return float(self.visible_bbox[3] - self.visible_bbox[1] + 1)

    @property
    def mode_label(self) -> str:
        return "分层骨骼模式" if self.mode == "layered" else "整图降级模式"

    def close(self) -> None:
        for layer in self.layers:
            layer.close()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mode_label": self.mode_label,
            "supports_limb_animation": self.supports_limb_animation,
            "canvas": [self.canvas_width, self.canvas_height],
            "anchor": list(self.anchor),
            "ground_y": self.ground_y,
            "visible_bbox": list(self.visible_bbox),
            "layer_count": len(self.layers),
            "draw_order": list(self.draw_order),
            "bones": list(self.bones),
            "bone_limits": {
                k: [round(v[0], 3), round(v[1], 3)]
                for k, v in self.bone_limits.items()
            },
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "degraded_reason": self.degraded_reason,
            "rig_path": str(self.rig_path) if self.rig_path else None,
            "character_path": str(self.character_path),
            "source_photo": str(self.source_photo) if self.source_photo else None,
        }


def _read_rig(
    rig_path: Path,
) -> tuple[
    int,
    int,
    dict[str, BoneDef],
    dict[str, tuple[float, float]],
    list[LayerDef],
    dict[str, LayerDef],
    list[str],
    tuple[float, float],
    float,
    tuple[int, int, int, int] | None,
    dict[str, Any],
    list[str],
]:
    try:
        raw_text = rig_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LayeredRigError(f"无法读取 rig.json：{exc}") from exc
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LayeredRigError(
            f"rig.json JSON 格式错误（第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}）。"
        ) from exc
    if not isinstance(raw, dict):
        raise LayeredRigError("rig.json 根节点必须是对象。")

    for required_key in ("canvas", "bones", "layers", "draw_order"):
        if required_key not in raw:
            raise LayeredRigError(f"rig.json 缺少必需字段 {required_key!r}。")

    canvas = raw.get("canvas")
    if not isinstance(canvas, dict):
        raise LayeredRigError("rig.json 的 canvas 必须是对象。")
    width = canvas.get("width")
    height = canvas.get("height")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
    ):
        raise LayeredRigError("rig.json 的 canvas.width / canvas.height 必须是正整数。")

    raw_bones = raw.get("bones")
    if not isinstance(raw_bones, dict) or not raw_bones:
        raise LayeredRigError("rig.json 的 bones 必须是非空对象。")

    bones: dict[str, BoneDef] = {}
    limits: dict[str, tuple[float, float]] = {}
    for name, value in raw_bones.items():
        if not isinstance(name, str) or not name:
            raise LayeredRigError("rig.json 的 bones 中存在无效骨骼名。")
        if not isinstance(value, dict):
            raise LayeredRigError(f"骨骼 {name} 的定义必须是对象。")
        parent = value.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise LayeredRigError(f"骨骼 {name} 的 parent 必须是字符串或 null。")
        if parent is not None and parent not in raw_bones:
            raise LayeredRigError(f"骨骼 {name} 引用了不存在的父骨骼 {parent}。")
        pivot = _vec2(value.get("pivot"), f"bones.{name}.pivot")
        safe = _safe_limits(value, name)
        bones[name] = BoneDef(name=name, parent=parent, pivot=pivot, safe_limits=safe)
        limits[name] = safe

    # Detect parent cycles (a rig with a cycle would hang the renderer).
    for name in bones:
        seen: set[str] = set()
        current: str | None = name
        while current is not None:
            if current in seen:
                raise LayeredRigError(f"骨骼层级存在循环：{name} -> ... -> {current}。")
            seen.add(current)
            current = bones[current].parent

    required_bone_names = set(REQUIRED_LAYER_BONES) | {"root"}
    missing = sorted(required_bone_names - set(bones))
    warnings: list[str] = []
    if missing:
        raise LayeredRigError("rig.json 缺少必需骨骼：" + "、".join(missing) + "。")

    raw_layers = raw.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        raise LayeredRigError("rig.json 的 layers 必须是非空数组。")

    layer_defs: list[LayerDef] = []
    layer_map: dict[str, LayerDef] = {}
    for index, value in enumerate(raw_layers):
        if not isinstance(value, dict):
            raise LayeredRigError(f"layers[{index}] 必须是对象。")
        layer_id = value.get("id")
        file_name = value.get("file")
        bone = value.get("bone")
        if not isinstance(layer_id, str) or not layer_id:
            raise LayeredRigError(f"layers[{index}] 缺少有效 id。")
        if layer_id in layer_map:
            raise LayeredRigError(f"layers 中存在重复 id {layer_id!r}。")
        if not isinstance(file_name, str) or not file_name:
            raise LayeredRigError(f"图层 {layer_id} 缺少有效 file。")
        if not isinstance(bone, str) or bone not in bones:
            raise LayeredRigError(f"图层 {layer_id} 引用了不存在的骨骼 {bone!r}。")
        pivot_raw = value.get("pivot", bones[bone].pivot)
        pivot = _vec2(pivot_raw, f"layers.{layer_id}.pivot")
        z_raw = value.get("z_index", index)
        if not isinstance(z_raw, int) or isinstance(z_raw, bool):
            z_raw = index
        empty = bool(value.get("empty", False))
        definition = LayerDef(
            id=layer_id,
            file=file_name,
            bone=bone,
            z_index=z_raw,
            pivot=pivot,
            empty=empty,
            source_kind=str(value.get("source_kind", "")),
        )
        layer_defs.append(definition)
        layer_map[layer_id] = definition

    raw_order = raw.get("draw_order")
    if not isinstance(raw_order, list) or not all(
        isinstance(item, str) for item in raw_order
    ):
        raise LayeredRigError("rig.json 的 draw_order 必须是字符串数组。")
    draw_order: list[str] = []
    for item in raw_order:
        if item not in layer_map:
            raise LayeredRigError(f"draw_order 引用了不存在的图层 {item!r}。")
        if item in draw_order:
            raise LayeredRigError(f"draw_order 中图层 {item!r} 重复。")
        draw_order.append(item)

    # Verify that every required body bone has a layer in the draw order.
    layer_by_bone: dict[str, list[LayerDef]] = {}
    for definition in layer_defs:
        layer_by_bone.setdefault(definition.bone, []).append(definition)
    missing_layers: list[str] = []
    for bone_name in REQUIRED_LAYER_BONES:
        candidates = layer_by_bone.get(bone_name, [])
        if not candidates:
            missing_layers.append(bone_name)
            continue
        chosen = next(
            (c for c in candidates if c.id == bone_name),
            candidates[0],
        )
        if chosen.id not in draw_order:
            raise LayeredRigError(
                f"必需身体图层 {chosen.id!r} 未被加入 draw_order。"
            )
    if missing_layers:
        raise LayeredRigError(
            "rig.json 缺少必需身体图层：" + "、".join(missing_layers) + "。"
        )

    for bone_name in OPTIONAL_BONE_BONES:
        if bone_name not in bones:
            warnings.append(f"缺少可选骨骼 {bone_name}，对应手部图层不会显示。")
        elif not layer_by_bone.get(bone_name):
            warnings.append(f"缺少可选手部图层 {bone_name}，不会绘制手部。")

    character = raw.get("character", {})
    if not isinstance(character, dict):
        character = {}
    anchor_raw = character.get("anchor")
    try:
        anchor = _vec2(anchor_raw, "character.anchor")
    except LayeredRigError:
        anchor = (width * 0.5, float(height - 1))
    ground_raw = character.get("ground_y", anchor[1])
    if not _is_number(ground_raw) or not math.isfinite(float(ground_raw)):
        ground_raw = anchor[1]
    ground_y = float(ground_raw)
    if not (0.0 <= ground_y <= float(height) + 8.0):
        raise LayeredRigError(
            f"rig.json 的 character.ground_y={ground_y} 超出画布高度 {height}。"
        )
    visible_bbox: tuple[int, int, int, int] | None = None
    bbox_raw = character.get("visible_bbox")
    if (
        isinstance(bbox_raw, (list, tuple))
        and len(bbox_raw) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in bbox_raw)
    ):
        x0, y0, x1, y1 = bbox_raw
        if 0 <= x0 <= x1 < width and 0 <= y0 <= y1 < height:
            visible_bbox = (x0, y0, x1, y1)
    if visible_bbox is None:
        warnings.append("rig.json 未提供有效的 character.visible_bbox，将自动计算。")
    if not (0 <= anchor[0] <= width and 0 <= anchor[1] <= height + 8):
        warnings.append("rig.json 的 character.anchor 超出画布，可能导致对齐异常。")

    return (
        width,
        height,
        bones,
        limits,
        layer_defs,
        layer_map,
        draw_order,
        anchor,
        ground_y,
        visible_bbox,
        raw,
        warnings,
    )


def _safe_asset_path(assets_dir: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or str(relative).startswith(("/", "\\")):
        raise LayeredRigError(
            f"图层文件必须使用资源目录内的相对路径，不能使用绝对路径：{relative!r}。"
        )
    root = assets_dir.resolve()
    candidate = (root / relative).resolve()
    try:
        common = Path(os.path.commonpath([str(root), str(candidate)]))
    except ValueError as exc:
        raise LayeredRigError(f"图层路径 {relative!r} 不合法。") from exc
    if os.path.normcase(str(common)) != os.path.normcase(str(root)):
        raise LayeredRigError(f"图层路径 {relative!r} 越出资源目录。")
    return candidate


def _load_layer_image(
    path: Path, canvas_width: int, canvas_height: int, relative: str
) -> tuple[Image.Image, tuple[int, int], tuple[int, int], int, int, int, int] | None:
    """Load one layer and crop to its non-transparent bounding box.

    Returns ``None`` for a fully transparent image.
    """

    if not path.is_file():
        raise LayeredRigError(f"缺少图层文件：{relative}")
    try:
        with Image.open(path) as opened:
            opened.load()
            if opened.size != (canvas_width, canvas_height):
                raise LayeredRigError(
                    f"图层 {relative} 的尺寸为 {opened.size[0]}×{opened.size[1]}，"
                    f"与 rig.json 画布 {canvas_width}×{canvas_height} 不一致。"
                    f"请使用未裁边、保留完整画布的 PNG。"
                )
            image = opened.convert("RGBA")
    except LayeredRigError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise LayeredRigError(f"无法解码图层 {relative}：{exc}") from exc

    alpha_bbox = image.getchannel("A").getbbox()
    if alpha_bbox is None:
        image.close()
        return None

    pad = 2
    left = max(0, alpha_bbox[0] - pad)
    top = max(0, alpha_bbox[1] - pad)
    right = min(canvas_width, alpha_bbox[2] + pad)
    bottom = min(canvas_height, alpha_bbox[3] + pad)
    crop = image.crop((left, top, right, bottom))
    image.close()
    if crop.width <= 0 or crop.height <= 0:
        crop.close()
        raise LayeredRigError(f"图层 {relative} 的有效透明区域为空。")
    return crop, (left, top), (crop.width, crop.height), left, top, right, bottom


def _load_fallback_character(path: Path) -> tuple[Image.Image, tuple[int, int], tuple[int, int], int, int]:
    if not path.is_file():
        raise AssetLoadError(f"完整人物 PNG 不存在：{path}")
    try:
        with Image.open(path) as opened:
            opened.load()
            image = opened.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AssetLoadError(f"无法解码完整人物 PNG {path.name}：{exc}") from exc
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        image.close()
        raise AssetLoadError(f"完整人物 PNG {path.name} 完全透明，没有可显示的人物。")
    pad = 2
    left = max(0, bbox[0] - pad)
    top = max(0, bbox[1] - pad)
    right = min(image.width, bbox[2] + pad)
    bottom = min(image.height, bbox[3] + pad)
    crop = image.crop((left, top, right, bottom))
    width, height = image.size
    image.close()
    # Inclusive bbox, matching the rig convention.
    return crop, (left, top), (crop.width, crop.height), width, height


def load_character(
    assets_dir: Path,
    *,
    rig_file: str = "rig.json",
    character_png: str = "character_original.png",
    source_photo: str = "source_photo.jpg",
    require_layered: bool = False,
) -> CharacterAssets:
    """Load the layered rig or degrade to the complete character PNG."""

    assets_dir = Path(assets_dir)
    rig_path = assets_dir / rig_file
    character_path = assets_dir / character_png
    source_path = assets_dir / source_photo if source_photo else None
    errors: list[str] = []
    warnings: list[str] = []

    layered: CharacterAssets | None = None
    try:
        layered = _load_layered(
            assets_dir=assets_dir,
            rig_path=rig_path,
            character_path=character_path,
            warnings=warnings,
        )
    except LayeredRigError as exc:
        errors.append(str(exc))

    if layered is not None:
        layered.errors.extend(errors)
        if source_path and source_path.is_file():
            layered.source_photo = source_path
        return layered

    if require_layered:
        raise AssetLoadError(
            "配置要求必须使用分层 rig，但分层素材加载失败："
            + (errors[-1] if errors else "未知错误")
        )

    reason = errors[-1] if errors else "分层素材不可用。"
    try:
        fallback = _load_degraded(
            assets_dir=assets_dir,
            character_path=character_path,
            warnings=warnings,
            reason=reason,
        )
    except AssetLoadError as exc:
        raise AssetLoadError(
            "无法加载分层人物素材，也无法加载完整人物 PNG，程序无法显示角色。\n"
            f"分层素材错误：{reason}\n完整人物错误：{exc}"
        ) from exc

    fallback.errors = errors
    fallback.source_photo = source_path if source_path and source_path.is_file() else None
    return fallback


def _load_layered(
    *,
    assets_dir: Path,
    rig_path: Path,
    character_path: Path,
    warnings: list[str],
) -> CharacterAssets:
    (
        width,
        height,
        bones,
        limits,
        layer_defs,
        layer_map,
        draw_order,
        anchor,
        ground_y,
        rig_bbox,
        raw,
        rig_warnings,
    ) = _read_rig(rig_path)
    warnings.extend(rig_warnings)

    loaded_layers: list[LoadedLayer] = []
    loaded_map: dict[str, LoadedLayer] = {}
    try:
        for layer_def in layer_defs:
            if layer_def.id not in draw_order:
                # Not drawn; still validate the path to catch broken rigs,
                # but do not waste memory loading unused layers.
                continue
            is_required = layer_def.bone in REQUIRED_LAYER_BONES
            is_optional_body = layer_def.bone in OPTIONAL_BONE_BONES
            path = _safe_asset_path(assets_dir, layer_def.file)
            try:
                loaded = _load_layer_image(path, width, height, layer_def.file)
            except LayeredRigError as exc:
                if is_required:
                    raise
                warnings.append(f"跳过不可用图层 {layer_def.id}：{exc}")
                continue
            if loaded is None:
                if is_required:
                    raise LayeredRigError(
                        f"必需身体图层 {layer_def.id}（{layer_def.file}）完全透明，"
                        "无法实现分肢动画。"
                    )
                if is_optional_body:
                    warnings.append(
                        f"手部图层 {layer_def.id} 完全透明（源照片中手掌被遮挡），已跳过。"
                    )
                else:
                    warnings.append(f"补片图层 {layer_def.id} 完全透明，已跳过。")
                continue
            image, offset, crop_size, *_ = loaded
            item = LoadedLayer(
                definition=layer_def,
                image=image,
                crop_offset=offset,
                crop_size=crop_size,
            )
            loaded_layers.append(item)
            loaded_map[layer_def.id] = item
    except Exception:
        for layer in loaded_layers:
            layer.close()
        raise

    # Ensure the required body bones actually have a drawable layer.
    for bone_name in REQUIRED_LAYER_BONES:
        definitions = [
            d for d in layer_defs if d.bone == bone_name and d.id in draw_order
        ]
        if not any(d.id in loaded_map for d in definitions):
            for layer in loaded_layers:
                layer.close()
            raise LayeredRigError(
                f"必需身体部件 {bone_name} 的图层未能加载，无法安全显示人物。"
            )

    if not loaded_layers:
        raise LayeredRigError("分层素材没有加载到任何可见图层。")

    # Compute / verify the visible bbox from the actual body layers.
    all_visible: list[tuple[int, int, int, int]] = []
    for layer in loaded_layers:
        if layer.definition.bone not in REQUIRED_LAYER_BONES:
            continue
        bbox = layer.image.getchannel("A").getbbox()
        if bbox is None:
            continue
        ox, oy = layer.crop_offset
        all_visible.append(
            (bbox[0] + ox, bbox[1] + oy, bbox[2] - 1 + ox, bbox[3] - 1 + oy)
        )
    if all_visible:
        auto_bbox = (
            min(b[0] for b in all_visible),
            min(b[1] for b in all_visible),
            max(b[2] for b in all_visible),
            max(b[3] for b in all_visible),
        )
        if rig_bbox is None:
            rig_bbox = auto_bbox
        else:
            # Warn (but do not fail) if the metadata disagrees with pixels.
            if any(abs(rig_bbox[i] - auto_bbox[i]) > 2 for i in range(4)):
                warnings.append(
                    "rig.json 的 visible_bbox 与实际身体图层像素范围不一致，"
                    "已使用由图层计算出的包围盒。"
                )
            rig_bbox = auto_bbox
    if rig_bbox is None:
        # Fall back to the complete portrait for geometry only.
        rig_bbox = (0, 0, width - 1, height - 1)

    # The anchor must be inside the canvas; clamp defensively.
    anchor_x = min(float(width), max(0.0, anchor[0]))
    anchor_y = min(float(height), max(0.0, anchor[1]))

    assets = CharacterAssets(
        mode="layered",
        canvas_width=width,
        canvas_height=height,
        anchor=(anchor_x, anchor_y),
        ground_y=ground_y,
        visible_bbox=rig_bbox,
        layers=loaded_layers,
        draw_order=draw_order,
        layer_map=loaded_map,
        bones=bones,
        bone_limits=limits,
        assets_dir=assets_dir,
        rig_path=rig_path,
        character_path=character_path,
        warnings=warnings,
        degraded_reason="",
    )
    return assets


def _load_degraded(
    *,
    assets_dir: Path,
    character_path: Path,
    warnings: list[str],
    reason: str,
) -> CharacterAssets:
    crop, offset, crop_size, width, height = _load_fallback_character(character_path)
    # Determine the true visible bbox after padding.
    alpha_bbox = crop.getchannel("A").getbbox()
    if alpha_bbox is None:
        crop.close()
        raise AssetLoadError("完整人物 PNG 完全透明。")
    ox, oy = offset
    visible_bbox = (
        ox + alpha_bbox[0],
        oy + alpha_bbox[1],
        ox + alpha_bbox[2] - 1,
        oy + alpha_bbox[3] - 1,
    )
    anchor = (
        (visible_bbox[0] + visible_bbox[2]) * 0.5,
        float(visible_bbox[3]),
    )
    definition = LayerDef(
        id="__character_full__",
        file=character_path.name,
        bone="root",
        z_index=0,
        pivot=anchor,
        empty=False,
        source_kind="full_character_fallback",
    )
    loaded = LoadedLayer(
        definition=definition,
        image=crop,
        crop_offset=offset,
        crop_size=crop_size,
    )
    root_bone = BoneDef(
        name="root",
        parent=None,
        pivot=anchor,
        safe_limits=(-5.0, 5.0),
    )
    warnings = list(warnings)
    warnings.append(
        "当前为整图降级模式：只能整体移动/倾斜/压缩，无法实现真实的分肢动画。"
    )
    warnings.append("分层素材错误：" + reason)
    return CharacterAssets(
        mode="degraded",
        canvas_width=width,
        canvas_height=height,
        anchor=anchor,
        ground_y=anchor[1],
        visible_bbox=visible_bbox,
        layers=[loaded],
        draw_order=[definition.id],
        layer_map={definition.id: loaded},
        bones={"root": root_bone},
        bone_limits={"root": (-5.0, 5.0)},
        assets_dir=assets_dir,
        rig_path=None,
        character_path=character_path,
        warnings=warnings,
        degraded_reason=reason,
    )
