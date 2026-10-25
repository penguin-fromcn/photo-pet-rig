"""Image and alpha helpers used by the deterministic builder."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
from PIL import Image, ImageOps


class ImageError(ValueError):
    """An input image/alpha is invalid or inconsistent."""


def load_rgb(path: Path) -> np.ndarray:
    """Load an image as RGB, applying EXIF orientation and stripping alpha."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        return np.array(im, dtype=np.uint8)


def load_alpha(path: Path, size: tuple[int, int]) -> np.ndarray:
    """Load an alpha/matte channel and verify it matches the RGB canvas."""
    with Image.open(path) as im:
        if im.mode in ("RGBA", "LA"):
            alpha = im.getchannel("A")
        elif im.mode == "L":
            alpha = im
        elif "transparency" in im.info:
            alpha = im.convert("RGBA").getchannel("A")
        else:
            # A grayscale matte is common; RGB input is accepted only if its channels agree.
            arr = np.array(im.convert("RGB"))
            if not (np.array_equal(arr[:, :, 0], arr[:, :, 1]) and np.array_equal(arr[:, :, 1], arr[:, :, 2])):
                raise ImageError(
                    f"{path} is not an alpha/matte image. Supply a grayscale matte, "
                    "an RGBA cutout, or use the `cutout` command."
                )
            alpha = Image.fromarray(arr[:, :, 0], "L")
        if alpha.size != size:
            raise ImageError(
                f"alpha is {alpha.size[0]}x{alpha.size[1]}, image is {size[0]}x{size[1]}. "
                "Do not resize one without the other."
            )
        return np.array(alpha, dtype=np.uint8)


def load_embedded_alpha(path: Path, size: tuple[int, int]) -> np.ndarray | None:
    """Use an alpha channel already present in the source image, if any."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGBA", "LA") and "transparency" not in im.info:
            return None
        alpha = im.convert("RGBA").getchannel("A")
        if alpha.size != size:
            raise ImageError(
                f"embedded alpha is {alpha.size[0]}x{alpha.size[1]}, image is {size[0]}x{size[1]}"
            )
        arr = np.array(alpha, dtype=np.uint8)
        if int(arr.min()) == 255:
            return None
        return arr


def keep_largest_component(alpha: np.ndarray, threshold: int = 24) -> tuple[np.ndarray, int]:
    """Keep the largest coarse foreground component while retaining its soft fringe."""
    seed = (alpha >= threshold).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    if n <= 1:
        return alpha.copy(), 0
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = (labels == idx).astype(np.uint8)
    # Include the 1-3 px soft fringe around the kept component, then clip.
    keep = cv2.dilate(keep, np.ones((5, 5), np.uint8), iterations=1)
    out = alpha.copy()
    out[keep == 0] = 0
    removed = n - 2
    return out, max(0, removed)


def fill_small_holes(alpha: np.ndarray, max_area: int = 0) -> np.ndarray:
    """Fill enclosed fully-transparent holes smaller than max_area."""
    if max_area <= 0:
        return alpha.copy()
    solid = (alpha > 0).astype(np.uint8)
    inv = (1 - solid).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    h, w = alpha.shape
    out = alpha.copy()
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if x == 0 or y == 0 or x + ww == w or y + hh == h:
            continue
        if area <= max_area:
            out[labels == i] = 255
    return out


def force_face_alpha(alpha: np.ndarray, face_polygon: Sequence[Sequence[float]] | None) -> np.ndarray:
    """Force the annotated face core to opaque alpha without touching RGB."""
    if not face_polygon:
        return alpha.copy()
    out = alpha.copy()
    mask = polygon_mask([face_polygon], alpha.shape)
    out[mask] = 255
    return out


def polygon_mask(polygons: Sequence[Sequence[Sequence[float]]], shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    for poly in polygons:
        pts = np.asarray(poly, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3:
            raise ImageError("a polygon needs at least 3 [x, y] points")
        cv2.fillPoly(mask, [np.round(pts).astype(np.int32)], 1)
    return mask.astype(bool)


def disk_mask(shape: tuple[int, int], center: tuple[float, float], radius: float) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    cv2.circle(mask, (int(round(center[0])), int(round(center[1]))), max(1, int(round(radius))), 1, -1)
    return mask.astype(bool)


def ellipse_mask(shape: tuple[int, int], center: tuple[float, float], radii: tuple[float, float]) -> np.ndarray:
    mask = np.zeros(shape, np.uint8)
    cv2.ellipse(
        mask,
        (int(round(center[0])), int(round(center[1]))),
        (max(1, int(round(radii[0]))), max(1, int(round(radii[1])))),
        0, 0, 360, 1, -1,
    )
    return mask.astype(bool)


def alpha_bbox(alpha: np.ndarray) -> list[int] | None:
    ys, xs = np.where(alpha > 0)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def bbox_of_masks(*masks: np.ndarray) -> list[int] | None:
    union = np.zeros_like(masks[0], bool)
    for m in masks:
        union |= m
    ys, xs = np.where(union)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def defringe_rgba(rgba: np.ndarray) -> np.ndarray:
    """Replace RGB only in semi-transparent edge pixels with nearby interior RGB.

    Alpha is unchanged. Fully opaque interior pixels and all pixels inside the
    face core remain byte-identical to the source.
    """
    out = rgba.copy()
    rgb = out[:, :, :3].astype(np.float32)
    alpha = out[:, :, 3]
    known = alpha == 255
    for _ in range(3):
        kf = known.astype(np.float32)
        sum_rgb = cv2.boxFilter(
            rgb * kf[..., None], -1, (3, 3), normalize=False,
            borderType=cv2.BORDER_CONSTANT,
        )
        sum_w = cv2.boxFilter(
            kf, -1, (3, 3), normalize=False,
            borderType=cv2.BORDER_CONSTANT,
        )
        fill = (~known) & (alpha > 0) & (sum_w > 0.01)
        if not fill.any():
            break
        rgb[fill] = sum_rgb[fill] / sum_w[fill, None]
        known = known | fill
    rgb[alpha == 0] = 0
    out[:, :, :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    return out


def composite_rgba(layers: Sequence[np.ndarray]) -> np.ndarray:
    """Composite straight-alpha RGBA arrays, back to front, over transparency."""
    if not layers:
        raise ImageError("cannot composite zero layers")
    h, w = layers[0].shape[:2]
    out_pm = np.zeros((h, w, 3), np.float32)
    out_a = np.zeros((h, w), np.float32)
    for rgba in layers:
        if rgba.shape[:2] != (h, w):
            raise ImageError("all layers must share the same canvas")
        a = rgba[:, :, 3].astype(np.float32) / 255.0
        out_pm = rgba[:, :, :3].astype(np.float32) * a[..., None] + out_pm * (1 - a[..., None])
        out_a = rgba[:, :, 3].astype(np.float32) + out_a * (1 - a)
    rgb = np.where(
        out_a[..., None] > 0,
        out_pm / np.maximum(out_a[..., None] / 255.0, 1e-6),
        0,
    )
    return np.dstack([
        np.clip(np.round(rgb), 0, 255).astype(np.uint8),
        np.clip(np.round(out_a), 0, 255).astype(np.uint8),
    ])


def alpha_mismatch(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    da = np.abs(a[:, :, 3].astype(int) - b[:, :, 3].astype(int))
    visible = (a[:, :, 3] > 0) | (b[:, :, 3] > 0)
    dr = np.abs(a[:, :, :3].astype(int) - b[:, :, :3].astype(int)).max(axis=2)
    return int((da > 1).sum()), int((dr[visible] > 1).sum())


def enclosed_holes(alpha: np.ndarray, min_area: int = 6) -> list[dict]:
    transparent = (alpha < 128).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(transparent, 8)
    holes: list[dict] = []
    h, w = alpha.shape
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if x == 0 or y == 0 or x + ww == w or y + hh == h:
            continue
        if area >= min_area:
            holes.append({"area": int(area), "bbox": [int(x), int(y), int(ww), int(hh)]})
    return holes


def rgba_layer(rgb: np.ndarray, alpha: np.ndarray, mask: np.ndarray) -> np.ndarray:
    a = np.where(mask, alpha, 0).astype(np.uint8)
    out = np.zeros((rgb.shape[0], rgb.shape[1], 4), np.uint8)
    out[:, :, :3] = rgb
    out[:, :, 3] = a
    return out


def save_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba).save(path, optimize=True, compress_level=6)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_array(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def foreground_from_alpha(alpha: np.ndarray) -> np.ndarray:
    return alpha > 0


def tight_bbox_from_polygons(polygons: Iterable[Sequence[Sequence[float]]], shape: tuple[int, int]) -> list[int] | None:
    mask = polygon_mask(list(polygons), shape)
    return alpha_bbox(mask.astype(np.uint8) * 255)
