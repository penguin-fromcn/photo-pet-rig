"""Command line interface for photo-pet-rig."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .builder import BuildError, build
from .constants import VERSION
from .imageops import alpha_bbox, load_rgb, save_rgba
from .previews import _find_font
from .project import ProjectError, load_project, project_summary


def desktop_dir() -> Path:
    """Best-effort Desktop directory across Windows/macOS/Linux."""
    candidates = []
    home = Path.home()
    if os.name == "nt":
        if os.environ.get("USERPROFILE"):
            candidates.append(Path(os.environ["USERPROFILE"]) / "Desktop")
        if os.environ.get("OneDrive"):
            candidates.append(Path(os.environ["OneDrive"]) / "Desktop")
        candidates.append(home / "Desktop")
        candidates.append(home / "OneDrive" / "Desktop")
    else:
        candidates.append(home / "Desktop")
    for p in candidates:
        if p.is_dir():
            return p
    target = candidates[0]
    target.mkdir(parents=True, exist_ok=True)
    return target


def _cmd_inspect(args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise BuildError(f"image not found: {image_path}")
    rgb = load_rgb(image_path)
    h, w = rgb.shape[:2]
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    font = _find_font(13, False)
    font_bold = _find_font(15, True)
    scale = max(1, int(round(max(w, h) / 1600)))
    step = max(10, int(args.grid))
    canvas = Image.fromarray(rgb).resize((w * scale, h * scale), Image.LANCZOS)
    d = ImageDraw.Draw(canvas)
    for x in range(0, w + 1, step):
        xx = x * scale
        d.line([(xx, 0), (xx, canvas.height)], fill=(0, 210, 255, 110), width=1)
        if x % (step * 2) == 0:
            d.text((xx + 2, 4), str(x), font=font, fill=(0, 235, 255))
    for y in range(0, h + 1, step):
        yy = y * scale
        d.line([(0, yy), (canvas.width, yy)], fill=(255, 220, 0, 110), width=1)
        if y % (step * 2) == 0:
            d.text((4, yy + 1), str(y), font=font, fill=(255, 235, 70))
    canvas.save(out / "grid.png", optimize=True)

    # Reference crops at native resolution. They intentionally overlap.
    crops = {
        "head": (int(w * 0.22), 0, int(w * 0.78), int(h * 0.42)),
        "torso": (int(w * 0.10), int(h * 0.25), int(w * 0.90), int(h * 0.72)),
        "legs": (int(w * 0.06), int(h * 0.55), int(w * 0.94), h),
    }
    crop_dir = out / "crops"
    crop_dir.mkdir(exist_ok=True)
    for name, (x0, y0, x1, y1) in crops.items():
        crop = Image.fromarray(rgb).crop((x0, y0, x1, y1))
        crop.save(crop_dir / f"{name}.png")
        # a second, gridded crop for coordinate reading
        cw, ch = crop.size
        cscale = max(1, int(round(max(cw, ch) / 900)))
        cimg = crop.resize((cw * cscale, ch * cscale), Image.LANCZOS)
        cd = ImageDraw.Draw(cimg)
        for gx in range(0, cw + 1, step):
            xx = gx * cscale
            cd.line([(xx, 0), (xx, cimg.height)], fill=(0, 210, 255, 90), width=1)
            if gx % (step * 2) == 0:
                cd.text((xx + 1, 1), str(gx + x0), font=font, fill=(0, 235, 255))
        for gy in range(0, ch + 1, step):
            yy = gy * cscale
            cd.line([(0, yy), (cimg.width, yy)], fill=(255, 220, 0, 90), width=1)
            if gy % (step * 2) == 0:
                cd.text((1, yy + 1), str(gy + y0), font=font, fill=(255, 235, 70))
        cimg.save(crop_dir / f"{name}_grid.png", optimize=True)

    readme = f"""# Annotation reference

Image: {image_path}
Canvas: {w} x {h} px
Coordinate origin: top-left. X grows right, Y grows down. All project coordinates are absolute pixels.

- grid.png: full image with a {step}px grid.
- crops/head*.png, torso*.png, legs*.png: reference crops. The *_grid variants label coordinates in the full-image coordinate system.

Use this file with prompts/AGENT.md. Write project.json in the original image coordinate space; do not resize the photo.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    print(f"wrote annotation reference to {out}")
    print(f"canvas: {w}x{h}; use absolute top-left coordinates")
    return 0


def _cmd_cutout(args: argparse.Namespace) -> int:
    try:
        from rembg import new_session, remove  # type: ignore
    except ImportError as exc:
        raise BuildError(
            "rembg is not installed. Install it with: pip install \"photo-pet-rig[rembg]\""
        ) from exc
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise BuildError(f"image not found: {image_path}")
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = load_rgb(image_path)
    session = new_session(args.model)
    result = remove(Image.fromarray(rgb), session=session, post_process_mask=True)
    rgba = np.array(result.convert("RGBA"))
    if args.keep_largest:
        from .imageops import keep_largest_component

        alpha, _ = keep_largest_component(rgba[:, :, 3])
        rgba[:, :, 3] = alpha
    save_rgba(out, rgba)
    print(f"wrote {out} ({rgba.shape[1]}x{rgba.shape[0]}, {int((rgba[:,:,3]>0).sum()):,} alpha px)")
    print("Next: inspect the alpha, then put this file in project.alpha and run build.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    print(project_summary(project))
    print("project validation: OK")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    out_parent = Path(args.out).expanduser().resolve() if args.out else desktop_dir()
    result = build(
        args.project,
        out_parent,
        alpha=args.alpha,
        keep_largest=args.keep_largest,
        force=args.force,
        make_zip=not args.no_zip,
        no_patches=args.no_patches,
        progress=lambda s: print(f"[build] {s}"),
    )
    print("")
    print(f"folder: {result.folder}")
    if result.zip_path:
        print(f"zip:    {result.zip_path}")
    print(f"layers: {len(result.project.parts)} body PNGs; {len(result.patches)} patches")
    print("Done. Open README.md and preview.png in the output folder.")
    return 0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _cmd_pet(args: argparse.Namespace) -> int:
    """Launch the optional PySide6 desktop runtime."""
    try:
        from .pet.app import main as pet_main
    except ImportError as exc:
        raise BuildError(
            "桌面运行时依赖未安装。请运行："
            "pip install -e \".[pet]\"  (或 pip install PySide6 Pillow)"
        ) from exc

    config = Path(args.config).expanduser().resolve() if args.config else None
    if config is None:
        config = (Path.cwd() / "pet_config.json").resolve()
        sample = _repo_root() / "examples" / "pet_config.example.json"
        if not config.exists() and sample.is_file():
            shutil.copyfile(sample, config)
            print(f"created {config} from {sample}")

    assets = Path(args.assets).expanduser().resolve() if args.assets else None
    if assets is None:
        demo = _repo_root() / "examples" / "demo_character"
        if demo.is_dir():
            assets = demo

    argv = ["--config", str(config)]
    if assets is not None:
        argv.extend(["--assets", str(assets)])
    if args.diagnostics:
        argv.append("--diagnostics")
    if args.smoke_test is not None:
        argv.extend(["--smoke-test", str(args.smoke_test)])
        if args.smoke_report:
            argv.extend(["--smoke-report", str(Path(args.smoke_report).resolve())])
    if args.no_tray:
        argv.append("--no-tray")
    if args.log_level:
        argv.extend(["--log-level", args.log_level])
    return int(pet_main(argv))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="photopet",
        description="Turn a full-body photo into a pivot-rigged 2D desktop pet.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="create a coordinate grid and reference crops")
    inspect.add_argument("image")
    inspect.add_argument("-o", "--out", default="inspect")
    inspect.add_argument("--grid", type=int, default=50, help="grid spacing in pixels (default 50)")
    inspect.set_defaults(func=_cmd_inspect)

    cutout = sub.add_parser("cutout", help="remove the background with rembg (optional)")
    cutout.add_argument("image")
    cutout.add_argument("-o", "--out", default="cutout.png")
    cutout.add_argument("--model", default="u2net_human_seg")
    cutout.add_argument("--keep-largest", action="store_true")
    cutout.set_defaults(func=_cmd_cutout)

    validate = sub.add_parser("validate", help="validate project JSON without building")
    validate.add_argument("project")
    validate.set_defaults(func=_cmd_validate)

    b = sub.add_parser("build", help="build layers, patches, rig.json and previews")
    b.add_argument("project")
    b.add_argument("--out", help="parent output directory (default: Desktop)")
    b.add_argument("--alpha", help="override the project alpha/matte path")
    b.add_argument("--keep-largest", action="store_true", help="keep only the largest alpha component")
    b.add_argument("--force", action="store_true", help="replace an existing <slug>_pet_assets folder")
    b.add_argument("--no-zip", action="store_true", help="do not create a ZIP beside the output folder")
    b.add_argument("--no-patches", action="store_true", help="do not generate repair patches")
    b.set_defaults(func=_cmd_build)

    pet = sub.add_parser(
        "pet",
        help="launch the desktop pet runtime (requires the [pet] extra)",
        description="Launch the PySide6 desktop pet using a built character folder.",
    )
    pet.add_argument(
        "--assets",
        default=None,
        help="character folder with rig.json + full-canvas layers (default: examples/demo_character)",
    )
    pet.add_argument(
        "--config",
        default=None,
        help="config.json to use/create (default: ./pet_config.json)",
    )
    pet.add_argument(
        "--diagnostics",
        action="store_true",
        help="check assets/screens and print JSON, then exit",
    )
    pet.add_argument(
        "--smoke-test",
        nargs="?",
        const=2.5,
        type=float,
        metavar="SECONDS",
        help="show the window for N seconds, write a self-check report, then exit",
    )
    pet.add_argument(
        "--smoke-report",
        default=None,
        help="path for the smoke-test JSON report",
    )
    pet.add_argument(
        "--no-tray",
        action="store_true",
        help="do not create the tray icon (debugging)",
    )
    pet.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="override the configured log level",
    )
    pet.set_defaults(func=_cmd_pet)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (BuildError, ProjectError, ValueError, FileNotFoundError, PermissionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
