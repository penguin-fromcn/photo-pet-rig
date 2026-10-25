"""Application / tray icon built from the supplied head layer.

This only crops and scales the supplied PNG; it never repaints the face.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QImage, QPixmap


def _head_pil(assets) -> Image.Image | None:
    for layer in assets.layers:
        if layer.definition.bone == "head":
            return layer.image.copy()
    for layer in assets.layers:
        if layer.definition.bone == "root":
            return layer.image.copy()
    return None


def _compose_icon_pil(assets) -> Image.Image:
    head = _head_pil(assets)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    # Quiet dark plate so the black hood remains legible on both light and
    # dark Windows taskbars.  This is decoration, not character content.
    draw.ellipse((8, 8, 248, 248), fill=(28, 30, 36, 235), outline=(220, 195, 160, 220), width=4)
    if head is None:
        return canvas
    bbox = head.getchannel("A").getbbox()
    if bbox is not None:
        head = head.crop(bbox)
    # Fit the head inside the circular plate.
    max_w, max_h = 196, 202
    scale = min(max_w / head.width, max_h / head.height)
    new_size = (max(1, int(head.width * scale)), max(1, int(head.height * scale)))
    head = head.resize(new_size, Image.Resampling.LANCZOS)
    x = (256 - head.width) // 2
    y = (256 - head.height) // 2 + 4
    canvas.alpha_composite(head, (x, y))
    return canvas


def build_qicon(assets) -> QIcon:
    pil = _compose_icon_pil(assets)
    data = pil.tobytes("raw", "RGBA")
    image = QImage(
        data,
        pil.width,
        pil.height,
        pil.width * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()
    pixmap = QPixmap.fromImage(image)
    return QIcon(pixmap)


def ensure_icon_files(assets_dir: Path, assets) -> tuple[Path | None, Path | None]:
    """Create PNG/ICO icon artifacts if they do not exist.

    Icons are derived from the supplied assets only and stored inside the
    project's assets folder.  Errors are non-fatal.
    """

    assets_dir = Path(assets_dir)
    png_path = assets_dir / "app_icon.png"
    ico_path = assets_dir / "app_icon.ico"
    try:
        if not png_path.exists() or not ico_path.exists():
            pil = _compose_icon_pil(assets)
            pil.save(png_path)
            pil.save(
                ico_path,
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )
        return png_path, ico_path
    except Exception:
        return (png_path if png_path.exists() else None, ico_path if ico_path.exists() else None)
