"""Generate presentation previews for a completed rig."""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .constants import STANDARD_PARTS
from .imageops import alpha_bbox
from .project import Project


_FONT_CANDIDATES = {
    "regular": [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ],
    "bold": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/PingFang.ttc",
    ],
}

_CJK_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _find_font(size: int, bold: bool = False, cjk: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (_CJK_CANDIDATES if cjk else []) + _FONT_CANDIDATES["bold" if bold else "regular"]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _ascii_only(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    size: int,
    bold: bool = True,
    cjk: bool = False,
) -> ImageFont:
    font = _find_font(size, bold, cjk=cjk)
    while size > 12:
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            break
        size -= 2
        font = _find_font(size, bold, cjk=cjk)
    return font


def _background(width: int, height: int, seed: int = 0) -> np.ndarray:
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None, None]
    top = np.array([13, 17, 24], np.float32)
    bottom = np.array([38, 36, 36], np.float32)
    bg = top * (1 - y) + bottom * y
    bg = np.repeat(bg, width, axis=1)
    glow = np.zeros((height, width), np.float32)
    cv2.circle(glow, (int(width * 0.48), int(height * 0.33)), int(max(width, height) * 0.7), 1, -1)
    glow = cv2.GaussianBlur(glow, (0, 0), max(60, width * 0.16))
    bg += glow[..., None] * np.array([18, 9, 4], np.float32)
    yy, xx = np.indices((height, width))
    chk = ((xx // 64 + yy // 64) % 2).astype(np.float32)
    bg += (chk[..., None] - 0.5) * 4.5
    r = np.sqrt(((xx - width / 2) / (width * 0.72)) ** 2 + ((yy - height * 0.52) / (height * 0.72)) ** 2)
    vig = np.clip(1 - 0.38 * np.clip(r - 0.35, 0, None), 0.55, 1)[..., None]
    return np.clip(bg * vig, 0, 255).astype(np.uint8)


def _character_on_background(character: np.ndarray, width: int, height: int) -> Image.Image:
    bg = _background(width, height)
    sh = np.zeros((height, width), np.float32)
    cv2.ellipse(sh, (int(width / 2), int(height * 0.985)), (int(width * 0.22), max(18, int(height * 0.022))), 0, 0, 360, 1, -1)
    sh = cv2.GaussianBlur(sh, (0, 0), max(10, height * 0.012))
    bg = (bg.astype(np.float32) * (1 - sh[..., None] * 0.55)).astype(np.uint8)
    a = character[:, :, 3:4].astype(np.float32) / 255
    comp = (character[:, :, :3].astype(np.float32) * a + bg * (1 - a)).astype(np.uint8)
    return Image.fromarray(comp).convert("RGBA")


def make_main_preview(project: Project, character: np.ndarray, out_path: Path) -> None:
    height, width = character.shape[:2]
    img = _character_on_background(character, width, height)
    d = ImageDraw.Draw(img)
    kicker = _find_font(max(14, width // 58), True)
    title_text = project.preview_title
    has_cjk = any(os.path.exists(p) for p in _CJK_CANDIDATES)
    if not _ascii_only(title_text) and not has_cjk:
        title_text = "MY PHOTO PET"
    title_font = _fit_font(
        d, title_text, int(width * 0.72), max(24, width // 26), True,
        cjk=not _ascii_only(title_text),
    )
    sub_font = _find_font(max(12, width // 64), False)
    accent = (255, 154, 84, 255)
    margin = max(22, int(width * 0.045))
    d.text((margin, margin), "PHOTO PET RIG  /  DESKTOP PET", font=kicker, fill=accent)
    d.text((margin, margin + int(width // 32)), title_text, font=title_font, fill=(245, 243, 240, 255))
    line_y = margin + int(width // 32) + int(width // 18)
    d.rounded_rectangle([margin, line_y, margin + max(48, int(width * 0.06)), line_y + 4], radius=2, fill=accent)
    visible_layers = sum(1 for p in project.parts.values() if not p.empty)
    sub = f"{width} x {height} px   ·   {visible_layers} body layers"
    d.text((margin, line_y + max(14, int(width * 0.016))), sub, font=sub_font, fill=(188, 186, 182, 255))
    badge = "ORIGINAL FACE PIXELS" if project.face_polygon else "PHOTO CUTOUT"
    d.text((margin, line_y + max(38, int(width * 0.04))), badge, font=sub_font, fill=(132, 132, 132, 255))
    img.convert("RGB").save(out_path, optimize=True)


def _checker(w: int, h: int) -> Image.Image:
    yy, xx = np.indices((h, w))
    m = ((xx // 10 + yy // 10) % 2).astype(bool)
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:] = (27, 31, 38)
    arr[m] = (35, 40, 49)
    return Image.fromarray(arr).convert("RGBA")


def make_layer_sheet(
    project: Project,
    character: np.ndarray,
    layers: dict[str, np.ndarray],
    patches: list,
    out_path: Path,
) -> None:
    h, w = character.shape[:2]
    entries: list[tuple[str, np.ndarray, str]] = [("character_original.png", character, "REFERENCE")]
    for part_id in STANDARD_PARTS:
        entries.append((f"{part_id}.png", layers[part_id], "EMPTY" if project.parts[part_id].empty else "BODY"))
    for patch in patches:
        entries.append((f"patch_layers/{patch.id}.png", patch.rgba, "PATCH"))

    cols, rows = 6, max(2, math.ceil(len(entries) / 6))
    tile_w, tile_h = 268, 340
    gap = 12
    mx, my = max(20, int(w * 0.028)), 132
    sw = mx * 2 + cols * tile_w + (cols - 1) * gap
    sh = my + rows * tile_h + (rows - 1) * gap + 52
    page = Image.fromarray(_background(sw, sh, seed=9)).convert("RGBA")
    d = ImageDraw.Draw(page)
    f_title = _find_font(30, True)
    f_sub = _find_font(15)
    f_name = _find_font(12)
    f_tag = _find_font(10, True)
    accent = (255, 154, 84, 255)
    accent_rgb = accent[:3]
    d.text((mx, 30), "LAYER INVENTORY  /  " + project.slug, font=f_title, fill=(244, 242, 238))
    d.text((mx, 74), f"All assets use the original {w} x {h} canvas. Positions are preserved; do not re-center parts.",
           font=f_sub, fill=(160, 160, 158))
    d.rounded_rectangle([mx, 108, mx + 78, 111], radius=2, fill=accent)
    colors = {"REFERENCE": (110, 198, 255), "BODY": (126, 224, 160), "PATCH": accent_rgb, "EMPTY": (150, 150, 150)}
    thumb_w, thumb_h = 244, 282
    scale = min(thumb_w / w, thumb_h / h)
    dw, dh = max(1, int(w * scale)), max(1, int(h * scale))

    for i, (name, rgba, kind) in enumerate(entries):
        row, col = divmod(i, cols)
        x0 = mx + col * (tile_w + gap)
        y0 = my + row * (tile_h + gap)
        d.rounded_rectangle([x0, y0, x0 + tile_w - 1, y0 + tile_h - 1], radius=9,
                            fill=(25, 29, 36, 255), outline=(44, 49, 58, 255), width=1)
        tile = _checker(thumb_w, thumb_h)
        image = Image.fromarray(rgba).resize((dw, dh), Image.LANCZOS)
        ox, oy = (thumb_w - dw) // 2, (thumb_h - dh) // 2
        tile.alpha_composite(image, (ox, oy))
        td = ImageDraw.Draw(tile)
        td.rectangle([0, 0, thumb_w - 1, thumb_h - 1], outline=(52, 57, 67, 255), width=1)
        bbox = alpha_bbox(rgba[:, :, 3])
        if bbox:
            td.rectangle([ox + bbox[0] * scale, oy + bbox[1] * scale,
                          ox + bbox[2] * scale, oy + bbox[3] * scale],
                         outline=colors[kind] + (150,), width=1)
        page.alpha_composite(tile, (x0 + 12, y0 + 12))
        tag = kind
        tag_w = d.textbbox((0, 0), tag, font=f_tag)[2]
        d.rounded_rectangle([x0 + tile_w - 18 - tag_w, y0 + 18, x0 + tile_w - 10, y0 + 33],
                            radius=7, fill=(16, 18, 23, 210), outline=colors[kind] + (160,), width=1)
        d.text((x0 + tile_w - 14 - tag_w, y0 + 20), tag, font=f_tag, fill=colors[kind])
        short = name.split("/")[-1]
        d.text((x0 + 12, y0 + 300), short, font=f_name, fill=(228, 227, 223))
        ap = int((rgba[:, :, 3] > 0).sum())
        d.text((x0 + 12, y0 + 317), "fully transparent" if ap == 0 else f"{ap:,} alpha px",
               font=f_name, fill=(133, 135, 139))
        if kind == "EMPTY":
            d.text((x0 + 24, y0 + 126), "fully transparent", font=f_name, fill=(143, 143, 143))
            d.text((x0 + 24, y0 + 143), "not visible in source", font=f_name, fill=(105, 105, 105))

    d.text((mx, sh - 34), "Layer composite is pixel-exact with character_original.png; patches stay behind all body parts.",
           font=f_sub, fill=(134, 136, 140))
    page.convert("RGB").save(out_path, optimize=True)


def make_rig_map(project: Project, character: np.ndarray, out_path: Path) -> None:
    h, w = character.shape[:2]
    img = _character_on_background(character, w, h)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    GOLD = (255, 207, 92, 230)
    ORANGE = (255, 139, 92, 230)
    CYAN = (92, 214, 255, 230)
    GREEN = (137, 230, 143, 230)
    VIOLET = (201, 168, 255, 230)
    PINK = (255, 92, 140, 240)
    WHITE = (245, 245, 245, 235)

    def joint(p, color, r=9):
        x, y = p
        d.ellipse([x - r - 2, y - r - 2, x + r + 2, y + r + 2], fill=(14, 17, 23, 220))
        d.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=WHITE, width=2)

    def line(a, b, color, width=5):
        d.line([a, b], fill=color, width=width)

    bones = project.bones
    torso = bones["torso"].pivot
    head = bones["head"].pivot
    line(torso, head, GOLD, 6)
    line(head, (head[0], max(0, head[1] - int(h * 0.13))), GOLD, 5)
    for side, color in (("left", CYAN), ("right", ORANGE)):
        shoulder = bones[f"upper_arm_{side}"].pivot
        elbow = bones[f"forearm_{side}"].pivot
        wrist = bones[f"hand_{side}"].pivot
        line(shoulder, elbow, color, 5)
        line(elbow, wrist, color, 5)
        joint(shoulder, color)
        joint(elbow, color)
        if project.parts[f"hand_{side}"].empty:
            x, y = wrist
            d.ellipse([x - 11, y - 11, x + 11, y + 11], outline=color, width=2)
            d.line([(x - 6, y - 6), (x + 6, y + 6)], fill=color, width=2)
            d.line([(x + 6, y - 6), (x - 6, y + 6)], fill=color, width=2)
        else:
            joint(wrist, color)
    for side, color in (("left", VIOLET), ("right", GREEN)):
        hip = bones[f"thigh_{side}"].pivot
        knee = bones[f"shin_{side}"].pivot
        ankle = bones[f"foot_{side}"].pivot
        line(hip, knee, color, 5)
        line(knee, ankle, color, 5)
        joint(hip, color)
        joint(knee, color)
        joint(ankle, color)
    joint(torso, PINK, 11)
    joint(head, GOLD, 10)
    img.alpha_composite(ov)
    d = ImageDraw.Draw(img)
    f_kicker = _find_font(max(13, w // 64), True)
    f_title = _find_font(max(22, w // 30), True)
    f_label = _find_font(max(12, w // 76), True)
    f_small = _find_font(max(11, w // 90), False)
    accent = (255, 154, 84, 255)
    margin = max(20, int(w * 0.035))
    d.text((margin, margin), "RIG MAP  /  JOINT PIVOTS", font=f_kicker, fill=accent)
    d.text((margin, margin + int(w // 34)), "Spine + arm/leg chains", font=f_title, fill=(244, 242, 238))
    legend = [
        (GOLD, "spine / head"),
        (ORANGE, "right arm (screen left)"),
        (CYAN, "left arm (screen right)"),
        (GREEN, "right leg (screen left)"),
        (VIOLET, "left leg (screen right)"),
    ]
    ly = margin + int(w // 12) + max(18, w // 40)
    for col, text in legend:
        d.line([(margin, ly + 5), (margin + 24, ly + 5)], fill=col, width=4)
        d.text((margin + 32, ly - 4), text, font=f_small, fill=(194, 193, 190))
        ly += max(18, w // 62)
    # A compact label for root / neck only; full coords live in rig.json.
    for text, p, color in (
        ("root", torso, PINK),
        ("head pivot / neck", head, GOLD),
    ):
        x, y = p
        bx = min(w - 210, x + 14)
        by = y - 30
        d.rounded_rectangle([bx, by, bx + 196, by + 24], radius=5, fill=(13, 16, 22, 210))
        d.text((bx + 8, by + 5), text, font=f_label, fill=color)
    d.text((margin, h - margin - 18), "All pivot coordinates are expressed in the original canvas. See rig.json.",
           font=f_small, fill=(155, 156, 158))
    img.convert("RGB").save(out_path, optimize=True)
