# -*- coding: utf-8 -*-
"""Generate the public synthetic mannequin used by the example and tests.

This is deliberately a flat illustration, not a photo. It exercises the full
pipeline geometry: 14 layers, visible hands, joint patches, and canvas alignment.

Run from the repository root:
    python examples/make_mannequin.py
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 900, 1600
S = 4
CW, CH = W * S, H * S
OUT = Path(__file__).resolve().parent / "mannequin"

SKIN = (222, 166, 132, 255)
SKIN_SHADOW = (191, 133, 104, 255)
HAIR = (38, 31, 32, 255)
HOODIE = (44, 51, 64, 255)
HOODIE_DARK = (31, 37, 47, 255)
HOODIE_LIGHT = (55, 64, 79, 255)
PANTS = (31, 50, 74, 255)
PANTS_DARK = (23, 38, 58, 255)
SHOE = (230, 231, 227, 255)
SHOE_SOLE = (207, 204, 195, 255)

PARTS = [
    "thigh_right", "thigh_left", "shin_right", "shin_left",
    "foot_right", "foot_left", "torso",
    "upper_arm_right", "upper_arm_left", "forearm_right", "forearm_left",
    "hand_right", "hand_left", "head",
]

BONES = {
    "root": {"parent": None, "pivot": [450, 815]},
    "torso": {"parent": "root", "pivot": [450, 815]},
    "head": {"parent": "torso", "pivot": [450, 350]},
    "upper_arm_right": {"parent": "torso", "pivot": [312, 392]},
    "forearm_right": {"parent": "upper_arm_right", "pivot": [214, 590]},
    "hand_right": {"parent": "forearm_right", "pivot": [188, 790]},
    "upper_arm_left": {"parent": "torso", "pivot": [588, 392]},
    "forearm_left": {"parent": "upper_arm_left", "pivot": [686, 590]},
    "hand_left": {"parent": "forearm_left", "pivot": [712, 790]},
    "thigh_right": {"parent": "root", "pivot": [402, 815]},
    "shin_right": {"parent": "thigh_right", "pivot": [368, 1080]},
    "foot_right": {"parent": "shin_right", "pivot": [365, 1400]},
    "thigh_left": {"parent": "root", "pivot": [498, 815]},
    "shin_left": {"parent": "thigh_left", "pivot": [532, 1080]},
    "foot_left": {"parent": "shin_left", "pivot": [535, 1400]},
}

TITLE = "Mannequin Demo"
FACE = [(372, 202), (440, 184), (512, 202), (540, 270), (500, 318), (450, 330), (392, 316), (360, 270)]


def sc(v):
    return int(round(v * S))


def draw_capsule(d: ImageDraw.ImageDraw, p1, p2, width, fill):
    x1, y1 = sc(p1[0]), sc(p1[1])
    x2, y2 = sc(p2[0]), sc(p2[1])
    w = sc(width)
    d.line([(x1, y1), (x2, y2)], fill=fill, width=w, joint="curve")
    r = w // 2
    for x, y in ((x1, y1), (x2, y2)):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def new_layer():
    return Image.new("RGBA", (CW, CH), (0, 0, 0, 0))


def part_polygon(mask: np.ndarray) -> list[list[int]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise RuntimeError("empty part mask")
    c = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(c, 3.5, True).reshape(-1, 2)
    pts = [[int(round(x / S)), int(round(y / S))] for x, y in approx]
    # Guard against tiny numerical edge excursions.
    for p in pts:
        p[0] = max(0, min(W, p[0]))
        p[1] = max(0, min(H, p[1]))
    return pts


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    layers = {name: new_layer() for name in PARTS}

    # Legs ---------------------------------------------------------------
    for side, color, dark in (("right", PANTS_DARK, PANTS), ("left", PANTS, PANTS_DARK)):
        thigh = layers[f"thigh_{side}"]
        d = ImageDraw.Draw(thigh)
        if side == "right":
            draw_capsule(d, (405, 820), (370, 1110), 124, color)
        else:
            draw_capsule(d, (495, 820), (530, 1110), 124, color)
        # subtle inner seam / fold
        d.line([(sc(450), sc(815)), (sc(450), sc(1040))], fill=dark, width=sc(3))

        shin = layers[f"shin_{side}"]
        d = ImageDraw.Draw(shin)
        if side == "right":
            draw_capsule(d, (370, 1120), (364, 1435), 100, color)
            d.line([(sc(330), sc(1180)), (sc(360), sc(1390))], fill=dark, width=sc(2))
        else:
            draw_capsule(d, (530, 1120), (536, 1435), 100, color)
            d.line([(sc(570), sc(1180)), (sc(540), sc(1390))], fill=dark, width=sc(2))

        foot = layers[f"foot_{side}"]
        d = ImageDraw.Draw(foot)
        if side == "right":
            x0, x1 = 280, 430
        else:
            x0, x1 = 470, 620
        # shoe body
        d.rounded_rectangle([sc(x0), sc(1390), sc(x1), sc(1518)], radius=sc(34), fill=SHOE)
        d.rounded_rectangle([sc(x0 + 4), sc(1478), sc(x1 - 4), sc(1535)], radius=sc(18), fill=SHOE_SOLE)
        # simple laces
        for yy in (1425, 1450, 1475):
            d.line([(sc(x0 + 35), sc(yy)), (sc(x1 - 35), sc(yy))], fill=(169, 166, 156, 255), width=sc(3))
        # toe cap
        d.arc([sc(x0 + 6), sc(1435), sc(x1 - 6), sc(1512)], 20, 160, fill=(190, 187, 179, 255), width=sc(3))

    # Torso --------------------------------------------------------------
    torso = layers["torso"]
    d = ImageDraw.Draw(torso)
    body = [
        (344, 368), (410, 350), (450, 366), (490, 350), (556, 368),
        (586, 430), (598, 708), (612, 800), (596, 828),
        (304, 828), (288, 800), (302, 708), (314, 430),
    ]
    d.polygon([(sc(x), sc(y)) for x, y in body], fill=HOODIE)
    # shoulders / hood drape
    d.ellipse([sc(310), sc(334), sc(590), sc(455)], fill=HOODIE_DARK)
    d.rounded_rectangle([sc(348), sc(344), sc(552), sc(392)], radius=sc(22), fill=HOODIE_LIGHT)
    # hoodie body shading
    d.polygon([(sc(344), sc(400)), (sc(450), sc(430)), (sc(556), sc(400)),
               (sc(580), sc(760)), (sc(560), sc(816)), (sc(340), sc(816)), (sc(320), sc(760))],
              fill=(48, 56, 70, 255))
    # kangaroo pocket seam
    d.arc([sc(335), sc(560), sc(565), sc(780)], 10, 170, fill=HOODIE_DARK, width=sc(5))
    # drawstrings
    d.line([(sc(420), sc(390)), (sc(428), sc(525))], fill=(182, 178, 167, 255), width=sc(4))
    d.line([(sc(480), sc(390)), (sc(472), sc(520))], fill=(182, 178, 167, 255), width=sc(4))
    d.ellipse([sc(424), sc(510), sc(436), sc(522)], fill=(209, 204, 192, 255))
    # hem
    d.rounded_rectangle([sc(305), sc(800), sc(595), sc(835)], radius=sc(14), fill=HOODIE_DARK)

    # Arms ---------------------------------------------------------------
    for side, shoulder, elbow in (
        ("right", (312, 392), (214, 590)),
        ("left", (588, 392), (686, 590)),
    ):
        ua = layers[f"upper_arm_{side}"]
        d = ImageDraw.Draw(ua)
        draw_capsule(d, shoulder, elbow, 94, HOODIE)
        d.line([(sc(shoulder[0]), sc(shoulder[1])), (sc(elbow[0]), sc(elbow[1]))],
               fill=HOODIE_LIGHT, width=sc(3))
        fa = layers[f"forearm_{side}"]
        d = ImageDraw.Draw(fa)
        wrist = (188, 790) if side == "right" else (712, 790)
        draw_capsule(d, elbow, (wrist[0], wrist[1] - 10), 78, HOODIE)
        # cuff
        cuff = (wrist[0] + (8 if side == "left" else -8), wrist[1] - 20)
        d.ellipse([sc(cuff[0] - 42), sc(cuff[1] - 18), sc(cuff[0] + 42), sc(cuff[1] + 18)],
                  fill=HOODIE_DARK)
        # exposed wrist skin
        d.ellipse([sc(wrist[0] - 31), sc(wrist[1] - 22), sc(wrist[0] + 31), sc(wrist[1] + 18)],
                  fill=SKIN)
        hand = layers[f"hand_{side}"]
        d = ImageDraw.Draw(hand)
        hx = wrist[0] + (12 if side == "left" else -12)
        d.ellipse([sc(hx - 38), sc(790), sc(hx + 38), sc(875)], fill=SKIN)
        d.arc([sc(hx - 30), sc(808), sc(hx + 28), sc(856)], 30, 150, fill=SKIN_SHADOW, width=sc(2))
        # thumb
        tx = hx - 30 if side == "left" else hx + 30
        d.ellipse([sc(tx - 14), sc(798), sc(tx + 14), sc(830)], fill=SKIN)

    # Head ---------------------------------------------------------------
    head = layers["head"]
    d = ImageDraw.Draw(head)
    # neck (goes behind the collar / torso)
    d.rounded_rectangle([sc(412), sc(300), sc(488), sc(380)], radius=sc(20), fill=SKIN)
    d.rectangle([sc(405), sc(300), sc(495), sc(362)], fill=SKIN)
    # ears
    d.ellipse([sc(329), sc(230), sc(368), sc(286)], fill=SKIN)
    d.ellipse([sc(532), sc(230), sc(571), sc(286)], fill=SKIN)
    # face
    d.ellipse([sc(348), sc(135), sc(552), sc(355)], fill=SKIN)
    # hair
    d.pieslice([sc(344), sc(112), sc(556), sc(344)], 180, 360, fill=HAIR)
    d.ellipse([sc(338), sc(175), sc(390), sc(332)], fill=HAIR)
    d.ellipse([sc(510), sc(175), sc(562), sc(332)], fill=HAIR)
    d.pieslice([sc(365), sc(145), sc(535), sc(275)], 185, 355, fill=HAIR)
    # eyes
    for ex in (408, 492):
        d.ellipse([sc(ex - 27), sc(228), sc(ex + 27), sc(248)], fill=(246, 240, 230, 255))
        d.ellipse([sc(ex - 9), sc(230), sc(ex + 9), sc(247)], fill=(38, 35, 36, 255))
        d.ellipse([sc(ex + 1), sc(233), sc(ex + 6), sc(238)], fill=(245, 245, 245, 255))
        d.arc([sc(ex - 33), sc(211), sc(ex + 33), sc(239)], 200, 340, fill=HAIR, width=sc(4))
    # nose + mouth
    d.line([(sc(450), sc(250)), (sc(444), sc(280)), (sc(457), sc(283))],
           fill=SKIN_SHADOW, width=sc(3))
    d.arc([sc(420), sc(287), sc(480), sc(322)], 15, 165, fill=(116, 63, 62, 255), width=sc(4))
    # cheek light
    d.ellipse([sc(370), sc(255), sc(410), sc(285)], fill=(232, 177, 145, 255))
    d.ellipse([sc(490), sc(255), sc(530), sc(285)], fill=(232, 177, 145, 255))

    # Compose source in the same z order as the project.
    z_order = [
        "thigh_right", "thigh_left", "shin_right", "shin_left", "foot_right", "foot_left",
        "torso", "upper_arm_right", "upper_arm_left", "forearm_right", "forearm_left",
        "hand_right", "hand_left", "head",
    ]
    body = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    for name in z_order:
        body.alpha_composite(layers[name])
    body = body.resize((W, H), Image.LANCZOS)
    alpha = body.getchannel("A")

    # Night studio background.
    yy, xx = np.indices((H, W)).astype(np.float32)
    bg = np.zeros((H, W, 3), np.uint8)
    bg[:, :, 0] = 21 + 16 * (yy / H)
    bg[:, :, 1] = 29 + 12 * (yy / H)
    bg[:, :, 2] = 38 + 10 * (yy / H)
    r = np.sqrt(((xx - W * 0.5) / (W * 0.8)) ** 2 + ((yy - H * 0.38) / (H * 0.55)) ** 2)
    glow = np.clip(1 - r, 0, 1) ** 2
    bg = np.clip(bg + glow[..., None] * np.array([55, 26, 15]), 0, 255)
    # floor
    floor_y = int(H * 0.905)
    bg[floor_y:] = (bg[floor_y:] * 0.72 + np.array([50, 43, 38]) * 0.28).astype(np.uint8)
    # shadow
    sh = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(sh)
    sd.ellipse([270, 1500, 630, 1575], fill=120)
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    shadow = np.array(sh, np.float32)[..., None] / 255
    bg = (bg * (1 - 0.48 * shadow)).astype(np.uint8)
    bg_img = Image.fromarray(bg, "RGB").convert("RGBA")
    bg_img.alpha_composite(body)
    photo = bg_img.convert("RGB")

    photo.save(OUT / "photo.png", optimize=True)
    alpha.resize((W, H), Image.LANCZOS).save(OUT / "alpha.png", optimize=True)

    # Project polygons from the actual layer masks.
    masks = {}
    for name in PARTS:
        a = layers[name].getchannel("A")
        masks[name] = (np.array(a) > 0).astype(np.uint8)
    parts = {}
    for name in PARTS:
        poly = part_polygon(masks[name])
        parts[name] = {
            "bone": name,
            "polygons": [poly],
            "empty": False,
            "fallback": name == "torso",
        }

    project = {
        "schema": "photopet/project@1",
        "name": "mannequin_demo",
        "image": "photo.png",
        "alpha": "alpha.png",
        "canvas": {"width": W, "height": H},
        "face": {"polygon": FACE},
        "bones": BONES,
        "parts": parts,
        "patches": {
            "enabled": True,
            "joints": [
                "head", "upper_arm_left", "upper_arm_right",
                "forearm_left", "forearm_right",
                "thigh_left", "thigh_right", "shin_left", "shin_right",
                "foot_left", "foot_right",
            ],
            "scale": 1.0,
            "overlap_px": 8,
        },
        "preview": {"title": TITLE},
        "notes": [
            "Synthetic mannequin generated by examples/make_mannequin.py. "
            "It tests geometry only; it is not a real person or a result produced from a photo."
        ],
    }
    (OUT / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(
        "# Mannequin demo\n\n"
        "Synthetic, public-domain-style test character generated by `examples/make_mannequin.py`.\n"
        "It is intentionally flat illustration art, not a photo, and it exists only to\n"
        "exercise all 14 layers, joint patches, alignment and QA.\n\n"
        "Rebuild the output with:\n\n"
        "```bash\nphotopet build examples/mannequin/project.json --out /tmp/photopet-demo --force\n```\n",
        encoding="utf-8",
    )
    print(f"wrote synthetic example to {OUT}")


if __name__ == "__main__":
    main()

