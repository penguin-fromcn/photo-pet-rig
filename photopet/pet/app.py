"""Application bootstrap: argument parsing, paths, logging, config, assets."""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import logging.handlers
import os
import shutil
import signal
import sys
import traceback
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .app_icon import ensure_icon_files
from .character_renderer import CharacterRenderer
from .pet_window import PetWindow, _foreground_hwnd
from .rig_loader import AssetLoadError, CharacterAssets, load_character
from .screen_utils import current_screen_areas, describe_areas
from .settings import (
    AppConfig,
    ConfigWarningList,
    app_base_dir,
    config_from_dict,
    load_config,
    resource_base_dir,
    save_config,
)

logger = logging.getLogger("desktop_pet")
_fault_log_handle = None


class Paths:
    def __init__(self, config_path: Path, config_save_path: Path, log_path: Path) -> None:
        self.config_path = config_path
        self.config_save_path = config_save_path
        self.log_path = log_path


def _user_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "PhotoDesktopPet"
    return Path.home() / ".photo_desktop_pet"


def _writable_dir(candidate: Path) -> bool:
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_config_path(cli_config: str | None) -> tuple[Path, Path]:
    """Return ``(read_path, save_path)``.

    Settings are saved next to the executable/project when writable, otherwise
    to ``%LOCALAPPDATA%\\PhotoDesktopPet``.  Bundled read-only resources are
    only used as a template.
    """

    if cli_config:
        path = Path(cli_config).expanduser().resolve()
        return (path, path)
    base = app_base_dir()
    preferred = base / "config.json"
    resource = resource_base_dir() / "config.json"
    if preferred.exists() and os.access(preferred, os.W_OK):
        return (preferred, preferred)
    user = _user_data_dir() / "config.json"
    if _writable_dir(base):
        if not preferred.exists() and resource.exists() and resource != preferred:
            try:
                shutil.copyfile(resource, preferred)
                logger.info("已复制默认配置到 %s", preferred)
            except OSError:
                logger.warning("无法复制默认配置，改用用户目录", exc_info=True)
                return (user, user)
        return (preferred, preferred)
    if user.exists():
        return (user, user)
    if resource.exists():
        return (resource, user)
    return (user, user)


def _resolve_assets_dir(
    cfg: AppConfig, config_path: Path, override: str | None = None
) -> Path:
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return candidate.resolve()
        logger.warning("--assets 目录不存在：%s，改用配置中的目录", candidate)
    configured = Path(cfg.assets.directory)
    if configured.is_absolute():
        return configured
    candidates: list[Path] = []
    # A user-provided assets folder next to a custom config takes precedence.
    candidates.append(config_path.parent / configured)
    candidates.append(app_base_dir() / configured)
    candidates.append(resource_base_dir() / configured)
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_dir():
            return candidate.resolve()
    return (app_base_dir() / configured).resolve()


def _setup_logging(log_path: Path, level: str) -> None:
    # Configure the root logger so module loggers (src.pet_window etc.) write
    # to both the rotating file and the console.
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    root.handlers.clear()
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=1_500_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        pass
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    try:
        if sys.stderr is not None:
            root.addHandler(stream)
    except Exception:
        pass
    # Third-party debug output (notably Pillow's PNG chunk logger) is very
    # noisy at DEBUG and not useful for the pet runtime.
    for noisy in ("PIL", "PIL.PngImagePlugin"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _choose_log_path() -> Path:
    preferred = app_base_dir() / "logs" / "pet.log"
    if _writable_dir(preferred.parent):
        return preferred
    fallback = _user_data_dir() / "logs" / "pet.log"
    _writable_dir(fallback.parent)
    return fallback


def _install_exception_hook(window_ref: list[PetWindow | None]) -> None:
    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "未捕获异常：\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        # Keep the app alive for non-fatal event errors; a fatal startup error
        # is handled before the event loop.
        try:
            window = window_ref[0]
            if window is not None and not window._closing:
                window._show_error_bubble("发生了一个内部错误，已写入日志。")
        except Exception:
            pass

    sys.excepthook = hook


def _load_runtime_config(
    config_path: Path,
) -> tuple[AppConfig, list[str], bool]:
    warnings = ConfigWarningList()
    if not config_path.exists():
        cfg = AppConfig()
        try:
            save_config(config_path, cfg)
            warnings.warn(f"未找到配置，已创建默认配置文件：{config_path}")
        except OSError:
            warnings.warn(f"未找到配置且无法创建 {config_path}，本次使用默认值。")
        return cfg, list(warnings), True
    cfg, ok = load_config(config_path, warnings)
    return cfg, list(warnings), ok


def _print_json(data: object) -> None:
    stream = sys.stdout
    if stream is None or not hasattr(stream, "write"):
        # A PyInstaller windowed build has no stdout.  The same JSON is still
        # written to logs/diagnostics.json by run_diagnostics().
        return
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run_diagnostics(
    config_path: Path,
    assets_dir: Path,
    cfg: AppConfig,
    config_warnings: list[str],
) -> int:
    result: dict[str, object] = {
        "app": "PhotoDesktopPet",
        "version": __version__,
        "python": sys.version,
        "pyside6": pyside_version,
    }
    try:
        assets = load_character(
            assets_dir,
            rig_file=cfg.assets.rig_file,
            character_png=cfg.assets.character_png,
            source_photo=cfg.assets.source_photo,
            require_layered=cfg.assets.require_layered_rig,
        )
    except AssetLoadError as exc:
        result["ok"] = False
        result["fatal_error"] = str(exc)
        _print_json(result)
        return 1
    try:
        result["config_path"] = str(config_path)
        result["config_save_path"] = str(config_path)
        result["config_warnings"] = config_warnings
        result["assets_dir"] = str(assets_dir)
        result["assets"] = assets.diagnostics()
        result["screens"] = describe_areas(current_screen_areas())
        renderer = CharacterRenderer(assets, cfg.render, cfg.window)
        from .animation_controller import AnimationController
        from .behavior_controller import PetState

        animator = AnimationController(cfg.animation, assets.bone_limits)
        pose = animator.update(
            1.0 / 30.0, state=PetState.IDLE, on_ground=True
        )
        layout = renderer.configure(
            cfg.window.display_height_px,
            cfg.window.padding_px,
            QGuiApplication.primaryScreen().devicePixelRatio()
            if QGuiApplication.primaryScreen()
            else 1.0,
            shadow_enabled=cfg.window.shadow,
        )
        frame = renderer.render(pose)
        corners = [
            frame.pixelColor(0, 0).alpha(),
            frame.pixelColor(frame.width() - 1, 0).alpha(),
            frame.pixelColor(0, frame.height() - 1).alpha(),
            frame.pixelColor(frame.width() - 1, frame.height() - 1).alpha(),
        ]
        result["render_test"] = {
            "frame_size": [frame.width(), frame.height()],
            "window_size": [layout.window_width, layout.window_height],
            "corners_alpha": corners,
            "coverage": round(renderer.alpha_coverage(), 4),
            "render_ms": round(renderer.last_render_ms, 3),
            "transparent_background": all(a == 0 for a in corners),
        }
        ensure_icon_files(assets_dir, assets)
        result["ok"] = bool(assets.layers) and all(a == 0 for a in corners)
    finally:
        try:
            assets.close()
        except Exception:
            pass
    _print_json(result)
    log_dir = _choose_log_path().parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "diagnostics.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    return 0 if result.get("ok") else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="PhotoDesktopPet",
        description="真人照片分层 2D 骨骼桌宠（本地运行，不联网）",
    )
    parser.add_argument("--config", help="使用指定的 config.json")
    parser.add_argument(
        "--assets",
        default=None,
        help="覆盖素材目录（包含 rig.json 与同画布分层 PNG）",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="检查素材/屏幕并输出 JSON，然后退出",
    )
    parser.add_argument(
        "--smoke-test",
        nargs="?",
        const=2.5,
        type=float,
        metavar="SECONDS",
        help="启动窗口并在指定秒数后自动退出，同时写自检报告",
    )
    parser.add_argument(
        "--smoke-report",
        default=None,
        help="自检报告 JSON 路径（默认 logs/smoke_report.json）",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="不创建系统托盘图标（调试用）",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="覆盖配置文件中的日志级别",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("PhotoDesktopPet")
    app.setApplicationDisplayName("真人照片桌宠")
    app.setOrganizationName("PhotoDesktopPet")
    app.setQuitOnLastWindowClosed(False)

    log_path = _choose_log_path()
    # We need the config first for log level; read a provisional copy.
    config_read_path, config_save_path = _resolve_config_path(args.config)
    provisional_cfg, _warn, _ok = _load_runtime_config(config_read_path)
    level = args.log_level or provisional_cfg.debug.log_level
    _setup_logging(log_path, level)
    global _fault_log_handle
    try:
        _fault_log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_fault_log_handle)
    except OSError:
        logger.warning("无法为崩溃回溯打开日志文件", exc_info=True)
    logger.info("=" * 72)
    logger.info(
        "PhotoDesktopPet %s 启动 | Python %s | PySide6 %s | platform=%s",
        __version__,
        sys.version.split()[0],
        pyside_version,
        QGuiApplication.platformName(),
    )
    logger.info(
        "路径：config_read=%s config_save=%s log=%s frozen=%s",
        config_read_path,
        config_save_path,
        log_path,
        getattr(sys, "frozen", False),
    )

    cfg, config_warnings, config_ok = _load_runtime_config(config_read_path)
    if config_warnings:
        for warning in config_warnings:
            logger.warning("配置：%s", warning)
    if args.log_level and args.log_level != cfg.debug.log_level:
        cfg.debug.log_level = args.log_level
    assets_dir = _resolve_assets_dir(cfg, config_read_path, args.assets)
    logger.info("素材目录：%s", assets_dir)

    if args.diagnostics:
        code = run_diagnostics(config_read_path, assets_dir, cfg, config_warnings)
        logger.info("诊断完成，退出码=%d", code)
        return code

    foreground_before = _foreground_hwnd()
    window_ref: list[PetWindow | None] = [None]
    _install_exception_hook(window_ref)

    try:
        assets = load_character(
            assets_dir,
            rig_file=cfg.assets.rig_file,
            character_png=cfg.assets.character_png,
            source_photo=cfg.assets.source_photo,
            require_layered=cfg.assets.require_layered_rig,
        )
    except AssetLoadError as exc:
        logger.critical("素材加载失败：%s", exc)
        QMessageBox.critical(
            None,
            "无法启动桌宠",
            "无法加载人物素材，程序无法显示角色。\n\n"
            f"{exc}\n\n日志：{log_path}",
        )
        return 1

    ensure_icon_files(assets_dir, assets)
    window = PetWindow(
        config=cfg,
        assets=assets,
        config_path=config_read_path,
        config_save_path=config_save_path,
        config_warnings=config_warnings,
        log_path=log_path,
        allow_tray=not args.no_tray,
    )
    window_ref[0] = window
    if not config_ok:
        logger.warning("配置文件有误，本次使用默认值；保存设置时会覆盖为有效格式")

    try:
        signal.signal(
            signal.SIGINT,
            lambda *_: window.shutdown("SIGINT"),
        )
    except (ValueError, OSError):
        pass

    window.start(foreground_before=foreground_before)

    if args.smoke_test is not None:
        report_path = (
            Path(args.smoke_report)
            if args.smoke_report
            else log_path.parent / "smoke_report.json"
        )
        logger.info("开始冒烟测试：%.1fs -> %s", args.smoke_test, report_path)
        window.start_smoke_test(args.smoke_test, report_path, foreground_before)

    exit_code = app.exec()
    if args.smoke_test is not None:
        exit_code = window.smoke_exit_code
        logger.info("冒烟测试退出码：%s", exit_code)
    try:
        window.shutdown("event loop finished")
    except Exception:
        pass
    return int(exit_code or 0)
