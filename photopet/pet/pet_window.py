"""Frameless transparent always-on-top pet window and the runtime loop."""

from __future__ import annotations

import ctypes
import json
import logging
import math
import random
import sys
import time
import traceback
from collections import deque
from ctypes import wintypes
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from . import autostart
from .animation_controller import AnimationController, PoseFrame
from .app_icon import build_qicon, ensure_icon_files
from .behavior_controller import (
    BehaviorController,
    ClickKind,
    PetState,
    Sensors,
    StateTransition,
)
from .character_renderer import CharacterRenderer, DisplayLayout
from .interaction_controller import InteractionController, ReleaseOutcome
from .physics import Body, Bounds, PhysicsWorld, StepResult
from .rig_loader import CharacterAssets
from .screen_utils import (
    ScreenArea,
    area_signature,
    choose_screen_area,
    clamp_drag_to_desktop,
    current_screen_areas,
    describe_areas,
    nearest_reachable_position,
    virtual_desktop_rect,
)
from .settings import (
    AppConfig,
    ConfigWarningList,
    config_from_dict,
    save_config,
)
from .settings_dialog import SettingsDialog
from .speech_bubble import SpeechBubble

logger = logging.getLogger(__name__)

WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
HTCLIENT = 1
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


class _WinMSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


def _foreground_hwnd() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None


class PetWindow(QWidget):
    def __init__(
        self,
        *,
        config: AppConfig,
        assets: CharacterAssets,
        config_path: Path,
        config_save_path: Path,
        config_warnings: list[str] | None = None,
        log_path: Path | None = None,
        allow_tray: bool = True,
    ) -> None:
        super().__init__(None)
        self.config = config
        self.assets = assets
        self.config_path = Path(config_path)
        self.config_save_path = Path(config_save_path)
        self.config_warnings = list(config_warnings or [])
        self.log_path = Path(log_path) if log_path else None
        self._allow_tray = allow_tray
        self._closing = False
        self._smoke_mode = False
        self.smoke_exit_code = 0
        self._random = random.Random()
        self._last_spoken = ""
        self._settings_dialog: SettingsDialog | None = None
        self._frame_errors = 0
        self._config_dirty = False
        self._screen_error_reported = False
        self._native_error_reported = False
        self._native_ready = False
        self._left_pressed = False
        self._double_click_gesture = False
        self._drag_pointer_offset = QPointF(0.0, 0.0)
        self._drag_start_body = (0.0, 0.0)
        self._drag_vx = 0.0
        self._drag_vy = 0.0
        self._last_pose = PoseFrame(bones={})
        self._frame: QImage | None = None
        self._frame_count = 0
        self._fps_window: deque[float] = deque(maxlen=90)
        self._last_tick = time.perf_counter()
        self._last_interval: int | None = None
        self._screen_dirty = True
        self._screens_connected: set[int] = set()
        self._screen_objects: list[object] = []
        self._tray: QSystemTrayIcon | None = None
        self._tray_icon: QIcon | None = None
        self._smoke_report_path: Path | None = None
        self._smoke_foreground_before: int | None = None
        self._smoke_started_at = 0.0
        self._started_at = time.perf_counter()
        self._status_message = ""
        self._last_menu_open = 0.0

        self.setWindowTitle("真人照片桌宠")
        self._configure_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setWindowOpacity(config.window.opacity)

        # Screens / layout / initial position.
        areas = current_screen_areas()
        primary = QGuiApplication.primaryScreen()
        if primary is not None:
            initial_area = choose_screen_area(
                QPointF(primary.availableGeometry().center()), areas
            )
        else:
            initial_area = choose_screen_area(QPointF(0, 0), areas)
        self._areas: list[ScreenArea] = areas
        self._current_area: ScreenArea = initial_area
        self._area_signature = area_signature(initial_area)

        self.renderer = CharacterRenderer(assets, config.render, config.window)
        self._layout: DisplayLayout = self.renderer.configure(
            config.window.display_height_px,
            config.window.padding_px,
            initial_area.dpr,
            shadow_enabled=config.window.shadow,
        )
        self.setFixedSize(self._layout.window_size)
        self._recompute_extents()

        self._bounds: Bounds = self._compute_bounds()
        start_x = min(
            self._bounds.right, max(self._bounds.left, initial_area.available.center().x())
        )
        body = Body(
            x=float(start_x),
            y=float(self._bounds.floor),
            vx=0.0,
            vy=0.0,
            on_ground=True,
        )
        self.physics = PhysicsWorld(config.physics, body)
        self.behavior = BehaviorController(config.behavior, config.animation)
        self.animator = AnimationController(config.animation, assets.bone_limits)
        self.interaction = InteractionController(
            drag_threshold_px=config.interaction.drag_threshold_px,
            window_s=config.interaction.release_velocity_window_s,
            velocity_gain=config.interaction.release_velocity_gain,
            max_release_speed_px_s=config.interaction.max_release_speed_px_s,
        )
        self.bubble = SpeechBubble()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_tick)

        self._build_actions()
        self._connect_screen_signals()
        self._sync_window_position(force=True)
        self._last_pose = self.animator.last_pose
        self._render_frame()
        logger.info(
            "窗口初始化完成：mode=%s canvas=%sx%s anchor=%s layout=%sx%s",
            assets.mode,
            assets.canvas_width,
            assets.canvas_height,
            assets.anchor,
            self._layout.window_width,
            self._layout.window_height,
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def _configure_window_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if self.config.window.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.config.window.no_activate:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)

    def _build_actions(self) -> None:
        self._menu = QMenu(self)
        self._menu.setToolTipsVisible(True)

        self.pause_action = QAction("暂停自主活动", self)
        self.pause_action.setToolTip("暂停走路与自主活动；拖拽和点击仍然可用")
        self.pause_action.triggered.connect(self._toggle_pause)
        self._menu.addAction(self.pause_action)

        self.settings_action = QAction("设置…", self)
        self.settings_action.triggered.connect(self._open_settings)
        self._menu.addAction(self.settings_action)

        self.hello_action = QAction("打个招呼", self)
        self.hello_action.triggered.connect(lambda: self._say_random())
        self._menu.addAction(self.hello_action)

        self.recenter_action = QAction("回到主屏底部", self)
        self.recenter_action.triggered.connect(self._recenter)
        self._menu.addAction(self.recenter_action)

        self.autostart_action = QAction("开机启动", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart.is_enabled())
        self.autostart_action.triggered.connect(self._toggle_autostart)
        self._menu.addAction(self.autostart_action)

        self._menu.addSeparator()
        self.diagnostics_action = QAction("素材诊断…", self)
        self.diagnostics_action.triggered.connect(self._show_diagnostics)
        self._menu.addAction(self.diagnostics_action)

        self.exit_action = QAction("退出", self)
        self.exit_action.triggered.connect(lambda: self.shutdown("menu exit"))
        self._menu.addAction(self.exit_action)

    def _connect_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)
        app.primaryScreenChanged.connect(self._on_screens_changed)
        for screen in app.screens():
            self._watch_screen(screen)

    def _watch_screen(self, screen) -> None:
        if screen is None:
            return
        sid = id(screen)
        if sid in self._screens_connected:
            return
        try:
            screen.availableGeometryChanged.connect(self._on_screens_changed)
            screen.geometryChanged.connect(self._on_screens_changed)
            screen.logicalDotsPerInchChanged.connect(self._on_screens_changed)
            screen.virtualGeometryChanged.connect(self._on_screens_changed)
        except Exception:
            pass
        self._screens_connected.add(sid)
        self._screen_objects.append(screen)

    def _on_screens_changed(self, *args) -> None:
        self._screen_dirty = True
        if self._closing or not self.isVisible():
            return
        if self._timer.isActive():
            self._wake()
            return
        # The timer may have stopped because the pet was paused/settled.  A
        # display change still needs one immediate refresh, otherwise the
        # window can remain on a removed monitor until another user event.
        QTimer.singleShot(0, self._one_shot_refresh)

    def _one_shot_refresh(self) -> None:
        if self._closing or not self.isVisible():
            return
        self._refresh_screen_state(force=True)
        self._sync_window_position(force=True)
        self._render_frame()
        self._adjust_timer()

    def _recompute_extents(self) -> None:
        scale = self._layout.display_scale
        x0, _y0, x1, _y1 = self.assets.visible_bbox
        ax, ay = self.assets.anchor
        self._left_extent = max(0.0, ax - x0) * scale
        self._right_extent = max(0.0, x1 - ax) * scale
        self._top_extent = max(0.0, ay - _y0) * scale

    def _compute_bounds(self, area: ScreenArea | None = None) -> Bounds:
        area = area or self._current_area
        margin = max(0.0, self.config.physics.collision_margin_px)
        left = area.available.left() + self._left_extent + margin
        right = area.available.right() - self._right_extent - margin
        if left > right:
            center = float(area.available.center().x())
            left = right = center
        return Bounds(left=left, right=right, floor=float(area.available.bottom()))

    @property
    def drag_half_width(self) -> float:
        return (
            max(self._left_extent, self._right_extent)
            + max(0.0, self.config.physics.collision_margin_px)
        )

    # ------------------------------------------------------------------
    # Runtime start / screen synchronization
    # ------------------------------------------------------------------
    def start(self, *, foreground_before: int | None = None) -> None:
        self._smoke_foreground_before = foreground_before
        self._refresh_screen_state(force=True)
        self._render_frame()
        # Create the native handle first so the no-activate/tool-window style is
        # applied before the first visible frame.
        try:
            self.winId()
        except Exception:
            pass
        self._apply_native_styles()
        self.show()
        QApplication.processEvents()
        self._apply_native_styles()
        self._setup_tray()
        self._start_autostart_sync()
        self._last_tick = time.perf_counter()
        self._started_at = self._last_tick
        self._adjust_timer(force=True)
        self._sync_actions()
        logger.info(
            "桌宠已启动：screen=%s work=%s dpr=%.3f visible=%s",
            self._current_area.name,
            self._current_area.available.getRect(),
            self._current_area.dpr,
            self.isVisible(),
        )
        if self.assets.mode != "layered":
            QTimer.singleShot(450, self._show_degraded_notice)

    def _apply_native_styles(self) -> None:
        if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
            return
        try:
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            ex_style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
            new_style = ex_style | WS_EX_TOOLWINDOW
            if self.config.window.no_activate:
                new_style |= WS_EX_NOACTIVATE
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new_style)
            if self.config.window.always_on_top:
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                )
            self._native_ready = True
        except Exception:
            logger.warning("应用 Windows 窗口样式失败（不影响基本显示）", exc_info=True)

    def _start_autostart_sync(self) -> None:
        if not self.config.autostart.enabled:
            return
        if not autostart.is_enabled():
            ok, message = autostart.set_enabled(True)
            if ok:
                logger.info("已同步开机启动项：%s", message)
            else:
                logger.warning("同步开机启动项失败：%s", message)
        if self.autostart_action is not None:
            self.autostart_action.setChecked(autostart.is_enabled())

    def _refresh_screen_state(self, *, force: bool = False) -> bool:
        """Refresh monitor bounds / DPR and keep the pet reachable."""

        areas = current_screen_areas()
        if not areas:
            return False
        self._areas = areas
        point = QPointF(self.physics.body.x, self.physics.body.y)
        area = choose_screen_area(point, areas, fallback=self._current_area)
        signature = area_signature(area)
        if not force and signature == self._area_signature:
            self._screen_dirty = False
            return False
        changed = signature != self._area_signature
        self._current_area = area
        self._area_signature = signature
        self._watch_screen(QGuiApplication.screenAt(point.toPoint()) or QGuiApplication.primaryScreen())
        self._layout = self.renderer.configure(
            self.config.window.display_height_px,
            self.config.window.padding_px,
            area.dpr,
            shadow_enabled=self.config.window.shadow,
        )
        if self.size() != self._layout.window_size:
            self.setFixedSize(self._layout.window_size)
        self._recompute_extents()
        self._bounds = self._compute_bounds(area)
        body = self.physics.body
        if self.behavior.state is not PetState.DRAG:
            body.x = min(self._bounds.right, max(self._bounds.left, body.x))
            if body.y > self._bounds.floor:
                body.y = self._bounds.floor
                body.vy = 0.0
                body.on_ground = True
            elif body.y < self._bounds.floor - 0.5:
                body.on_ground = False
        self._screen_dirty = False
        self._sync_window_position(force=True)
        if changed:
            logger.info(
                "显示器变化：screen=%s work=%s dpr=%.2f",
                area.name,
                area.available.getRect(),
                area.dpr,
            )
        return True

    def _sync_window_position(self, *, force: bool = False) -> None:
        layout = self._layout
        x = int(round(self.physics.body.x - layout.anchor_local_x))
        y = int(round(self.physics.body.y - layout.anchor_local_y))
        if force or x != self.x() or y != self.y():
            self.move(x, y)
        if self.bubble.isVisible():
            self.bubble.attach_pet_rect(self._bubble_anchor_rect())

    def _bubble_anchor_rect(self) -> QRect:
        """Small 2x2 rect at the top-center of the visible character.

        Using the whole window rect would place the bubble far above the head
        because the canvas has a lot of transparent padding.
        """

        layout = self._layout
        scale = layout.display_scale
        x0, y0, x1, _y1 = self.assets.visible_bbox
        center_canvas = (x0 + x1) * 0.5
        top_y = self.y() + layout.padding_px + y0 * scale
        center_x = self.x() + layout.padding_px + center_canvas * scale
        return QRect(int(round(center_x)) - 1, int(round(top_y)), 2, 2)

    # ------------------------------------------------------------------
    # Timer / update loop
    # ------------------------------------------------------------------
    def _wake(self) -> None:
        if self._closing:
            return
        self._last_tick = time.perf_counter()
        interval = self._desired_interval()
        if interval is None:
            return
        if not self._timer.isActive() and self.isVisible():
            self._timer.start(interval)
            self._last_interval = interval
        elif self._timer.isActive() and self._timer.interval() != interval:
            self._timer.setInterval(interval)
            self._last_interval = interval

    def _desired_interval(self) -> int | None:
        if self._closing or not self.isVisible():
            return None
        body = self.physics.body
        speed = math.hypot(body.vx, body.vy)
        anim_moving = not self.animator.is_settled()
        if (
            self.behavior.state is PetState.PAUSED
            and body.on_ground
            and speed < 0.5
            and not anim_moving
        ):
            return None
        if self.behavior.paused and body.on_ground and speed < 0.5 and not anim_moving:
            return None
        if (
            self.behavior.state is PetState.IDLE
            and body.on_ground
            and speed < 0.5
            and not anim_moving
            and not self.config.animation.idle_breathing
        ):
            return None
        if self.behavior.state is PetState.IDLE:
            fps = max(1, min(self.config.render.idle_fps, self.config.render.fps))
            return max(16, int(round(1000.0 / fps)))
        fps = max(1, self.config.render.fps)
        return max(8, int(round(1000.0 / fps)))

    def _adjust_timer(self, *, force: bool = False) -> None:
        interval = self._desired_interval()
        if interval is None:
            if self._timer.isActive():
                self._timer.stop()
            self._last_interval = None
            return
        if not self._timer.isActive():
            self._timer.start(interval)
            self._last_interval = interval
            self._last_tick = time.perf_counter()
        elif force or self._timer.interval() != interval:
            self._timer.start(interval)
            self._last_interval = interval

    def _on_tick(self) -> None:
        if self._closing:
            return
        try:
            now = time.perf_counter()
            dt = now - self._last_tick
            self._last_tick = now
            if not math.isfinite(dt) or dt <= 0.0:
                dt = 1.0 / max(1, self.config.render.fps)
            if dt > 0.5:
                logger.debug("帧间隔异常 %.3fs，按 1/30s 处理", dt)
                dt = 1.0 / 30.0
            dt = min(dt, 0.05)

            self._refresh_screen_state()
            self._step(dt)
            self._render_frame()
            self._track_fps(now)
            self._adjust_timer()
            self._frame_errors = 0
        except Exception:
            self._frame_errors += 1
            logger.exception("帧更新异常（第 %d 次）", self._frame_errors)
            if self._frame_errors == 1:
                self._show_error_bubble("更新出了点问题，已记录日志。")
            if self._frame_errors >= 8:
                self._timer.stop()
                logger.error("连续帧异常过多，已停止动画定时器")

    def _step(self, dt: float) -> None:
        body = self.physics.body
        behavior = self.behavior
        walking = behavior.is_walking and body.on_ground
        if behavior.state is not PetState.DRAG:
            if walking:
                target = behavior.desired_walk_speed
                max_delta = max(1.0, behavior.behavior.walk_accel_px_s2 * dt)
                body.vx += max(-max_delta, min(max_delta, target - body.vx))
            self._bounds = self._compute_bounds()
            result = self.physics.step(
                dt,
                self._bounds,
                walking=walking,
                apply_ground_friction=True,
            )
            body.x = min(self._bounds.right, max(self._bounds.left, body.x))
        else:
            result = StepResult()

        sensors = Sensors(
            x=body.x,
            bounds_left=self._bounds.left,
            bounds_right=self._bounds.right,
            on_ground=body.on_ground,
            vy=body.vy,
            landed=result.landed,
            impact_speed=result.impact_speed,
            wall_hit_left=result.wall_hit_left,
            wall_hit_right=result.wall_hit_right,
        )
        behavior.update(dt, sensors)

        if behavior.state is PetState.DRAG:
            now = time.perf_counter()
            self._drag_vx, self._drag_vy = self.interaction.velocity_at(now)
        else:
            self._drag_vx = 0.0
            self._drag_vy = 0.0

        self._last_pose = self.animator.update(
            dt,
            state=behavior.state,
            on_ground=body.on_ground,
            vx=body.vx,
            vy=body.vy,
            drag_vx=self._drag_vx,
            drag_vy=self._drag_vy,
            landed=result.landed,
            impact_speed=result.impact_speed,
        )
        self._sync_window_position()

    def _render_frame(self) -> None:
        try:
            self._frame = self.renderer.render(self._last_pose)
            self._frame_count += 1
            if self.isVisible():
                self.update()
        except Exception:
            logger.exception("渲染帧失败")
            raise

    def _track_fps(self, now: float) -> None:
        self._fps_window.append(now)
        if len(self._fps_window) >= 2:
            elapsed = self._fps_window[-1] - self._fps_window[0]
            if elapsed > 0:
                self._measured_fps = (len(self._fps_window) - 1) / elapsed

    @property
    def measured_fps(self) -> float:
        return getattr(self, "_measured_fps", 0.0)

    # ------------------------------------------------------------------
    # Painting / native hit testing
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        if self._frame is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.drawImage(0, 0, self._frame)
        finally:
            painter.end()

    def nativeEvent(self, eventType, message):  # noqa: N802
        if (
            sys.platform == "win32"
            and self._native_ready
            and self.config.window.click_through_transparent
        ):
            try:
                if bytes(eventType) in (
                    b"windows_generic_MSG",
                    b"windows_dispatcher_MSG",
                ):
                    try:
                        address = int(message)
                    except (TypeError, ValueError):
                        address = int(message.__int__())  # type: ignore[union-attr]
                    msg = _WinMSG.from_address(address)
                    if msg.message == WM_NCHITTEST:
                        if self._left_pressed:
                            return True, HTCLIENT
                        # WM_NCHITTEST's lParam carries screen coordinates in
                        # signed 16-bit components.  Do not rely on the live
                        # cursor position: WindowFromPoint/automation can ask
                        # about arbitrary points, and a lagging QCursor would
                        # produce the wrong answer.
                        sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                        sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                        local = self._physical_screen_to_local(sx, sy)
                        if local is None:
                            local = self.mapFromGlobal(QCursor.pos())
                        if self.renderer.hit_test(QPointF(local)):
                            return True, HTCLIENT
                        return True, HTTRANSPARENT
            except Exception:
                if not self._native_error_reported:
                    self._native_error_reported = True
                    logger.warning("逐像素点击穿透失效，退回矩形命中", exc_info=True)
        return super().nativeEvent(eventType, message)

    def _physical_screen_to_local(self, screen_x: int, screen_y: int) -> QPointF | None:
        """Map a physical Win32 screen point into this widget's logical coords."""

        if sys.platform != "win32":
            return None
        try:
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            client = wintypes.RECT()
            if not user32.GetClientRect(hwnd, ctypes.byref(client)):
                return None
            origin = wintypes.POINT(0, 0)
            if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
                return None
            width_phys = client.right - client.left
            height_phys = client.bottom - client.top
            if width_phys <= 0 or height_phys <= 0 or self.width() <= 0 or self.height() <= 0:
                return None
            local_x = (screen_x - origin.x) * self.width() / width_phys
            local_y = (screen_y - origin.y) * self.height() / height_phys
            return QPointF(local_x, local_y)
        except Exception:
            return None

    def _native_hit_test_diagnostic(self) -> dict[str, object]:
        """Verify real OS hit testing on an opaque and a transparent pixel.

        ``WindowFromPoint`` sends WM_NCHITTEST to this window, so this checks
        the actual click-through path rather than only the Qt-level hit test.
        """

        result: dict[str, object] = {"available": False}
        if (
            sys.platform != "win32"
            or not self._native_ready
            or not self.config.window.click_through_transparent
            or QGuiApplication.platformName() != "windows"
        ):
            return result
        frame = self.renderer.last_frame
        layout = self.renderer.layout
        if frame is None or layout is None:
            return result
        try:
            user32 = ctypes.windll.user32
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.WindowFromPoint.restype = wintypes.HWND
            hwnd = int(self.winId())
            client = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(client))
            origin = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(origin))
            width_phys = max(1, client.right - client.left)
            height_phys = max(1, client.bottom - client.top)
            dpr = max(1.0, float(layout.render_dpr))
            # Find an opaque pixel around the torso region.
            px = py = None
            for y in range(int(frame.height() * 0.40), int(frame.height() * 0.58), 2):
                for x in range(0, frame.width(), 2):
                    if frame.pixelColor(x, y).alpha() > 245:
                        px, py = x, y
                        break
                if px is not None:
                    break
            if px is None:
                result["reason"] = "no opaque sample found"
                return result
            local_x = px / dpr
            local_y = py / dpr
            opaque_phys = wintypes.POINT(
                origin.x + int(round(local_x * width_phys / max(1, self.width()))),
                origin.y + int(round(local_y * height_phys / max(1, self.height()))),
            )
            transparent_phys = wintypes.POINT(origin.x + 2, origin.y + 2)
            opaque_hwnd = int(user32.WindowFromPoint(opaque_phys) or 0)
            transparent_hwnd = int(user32.WindowFromPoint(transparent_phys) or 0)
            ex_style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
            result.update(
                {
                    "available": True,
                    "hwnd": hwnd,
                    "opaque_sample_local": [round(local_x, 2), round(local_y, 2)],
                    "opaque_window": opaque_hwnd,
                    "transparent_window": transparent_hwnd,
                    "hits_opaque": opaque_hwnd == hwnd,
                    "passes_through_transparent": transparent_hwnd != hwnd,
                    "no_activate_style": (
                        bool(ex_style & WS_EX_NOACTIVATE)
                        if self.config.window.no_activate
                        else True
                    ),
                }
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _begin_press(self, event, *, double: bool = False) -> None:
        if self._closing:
            return
        if self.interaction.active:
            self.interaction.cancel()
        self._left_pressed = True
        self._double_click_gesture = double
        self._drag_vx = 0.0
        self._drag_vy = 0.0
        global_pos = event.globalPosition()
        local_pos = event.position()
        now = time.perf_counter()
        self.interaction.press(global_pos.x(), global_pos.y(), now)
        body = self.physics.body
        self._drag_pointer_offset = QPointF(
            body.x - global_pos.x(), body.y - global_pos.y()
        )
        self._drag_start_body = (body.x, body.y)
        self._press_global = QPointF(global_pos)
        self._press_local = QPointF(local_pos)
        self.behavior.begin_user_drag()
        self.bubble.hide_bubble()
        logger.debug("按下：global=%s state=%s", global_pos, self.behavior.state.value)
        self._wake()
        self._adjust_timer(force=True)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_press(event, double=False)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            # The menu is opened on release; accepting here prevents any
            # default handling without showing a menu on mouse-down.
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_press(event, double=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._left_pressed:
            return
        now = time.perf_counter()
        global_pos = event.globalPosition()
        moved = self.interaction.move(global_pos.x(), global_pos.y(), now)
        if not moved:
            event.accept()
            return
        desired_x = global_pos.x() + self._drag_pointer_offset.x()
        desired_y = global_pos.y() + self._drag_pointer_offset.y()
        if self.config.interaction.clamp_drag_to_virtual_desktop:
            x, y = clamp_drag_to_desktop(
                desired_x,
                desired_y,
                self._areas,
                self.drag_half_width,
            )
        else:
            x, y = desired_x, desired_y
        body = self.physics.body
        body.x = x
        body.y = y
        body.on_ground = False
        self._drag_vx, self._drag_vy = self.interaction.velocity_at(now)
        self._sync_window_position()
        self._wake()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and not self._left_pressed:
            # Explicit right-click fallback: on some no-activate layered
            # windows Qt may not deliver a QContextMenuEvent, so open the menu
            # here as well.  contextMenuEvent() suppresses the duplicate.
            self._open_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or not self._left_pressed:
            super().mouseReleaseEvent(event)
            return
        global_pos = event.globalPosition()
        now = time.perf_counter()
        outcome = self.interaction.release(global_pos.x(), global_pos.y(), now)
        self._left_pressed = False
        was_double = self._double_click_gesture
        self._double_click_gesture = False
        self._drag_vx = 0.0
        self._drag_vy = 0.0
        if outcome is None:
            self.behavior.end_user_drag(on_ground=True)
            self._wake()
            event.accept()
            return

        # Refresh monitor in case the drag crossed a screen boundary.  This
        # must happen while the state is still DRAG so the position is not
        # clamped prematurely.
        self._refresh_screen_state(force=True)
        bounds = self._compute_bounds()
        body = self.physics.body

        if outcome.is_click:
            # Never move the character for a click.  Restore the original
            # anchor in case tiny movements arrived below the drag threshold.
            body.x, body.y = self._drag_start_body
            body.x = min(bounds.right, max(bounds.left, body.x))
            if abs(body.y - bounds.floor) < 1.0:
                body.y = bounds.floor
                on_ground = True
            else:
                on_ground = False
            body.on_ground = on_ground
            kind = ClickKind.DOUBLE if was_double else ClickKind.SINGLE
            self._finish_click(kind, on_ground=on_ground)
        else:
            self._finish_drag(outcome, bounds, was_double)
        self._wake()
        self._adjust_timer(force=True)
        event.accept()

    def _finish_click(self, kind: ClickKind, *, on_ground: bool) -> None:
        accepted = self.behavior.trigger_click(kind)
        if not accepted:
            logger.debug("点击被状态机忽略（防重复）：%s", kind.value)
            return
        self.animator.trigger_click(kind)
        body = self.physics.body
        hop = (
            self.config.animation.double_click_hop_speed_px_s
            if kind is ClickKind.DOUBLE
            else self.config.animation.click_hop_speed_px_s
        )
        if kind is ClickKind.DOUBLE:
            # A double click should be clearly stronger even if the first
            # click already lifted the pet off the floor.
            body.vy = min(body.vy, -hop)
            body.on_ground = False
            body.vx *= 0.35
        elif on_ground:
            body.vy = -hop
            body.on_ground = False
            body.vx *= 0.35
        else:
            body.vx *= 0.8
        logger.info(
            "点击反馈：kind=%s on_ground=%s state=%s",
            kind.value,
            on_ground,
            self.behavior.state.value,
        )
        self._say_click(kind)
        self._wake()

    def _finish_drag(
        self,
        outcome: ReleaseOutcome,
        bounds: Bounds,
        was_double: bool,
    ) -> None:
        body = self.physics.body
        # The final release position may differ from the last move sample.
        desired_x = outcome.position[0] + self._drag_pointer_offset.x()
        desired_y = outcome.position[1] + self._drag_pointer_offset.y()
        if self.config.interaction.clamp_drag_to_virtual_desktop:
            x, y = clamp_drag_to_desktop(
                desired_x, desired_y, self._areas, self.drag_half_width
            )
        else:
            x, y = desired_x, desired_y
        body.x = min(bounds.right, max(bounds.left, x))
        if y > bounds.floor:
            y = bounds.floor
        body.y = y
        vx, vy = outcome.velocity
        body.vx = vx
        body.vy = vy
        on_ground = (
            abs(body.y - bounds.floor) < 0.75
            and vy >= -1.0
            and body.y >= bounds.floor - 0.75
        )
        body.on_ground = on_ground
        self.behavior.end_user_drag(on_ground=on_ground)
        if not on_ground:
            # No click animation on a real drag; the state machine enters FALL.
            logger.info(
                "拖拽释放：v=(%.0f, %.0f) state=%s",
                vx,
                vy,
                self.behavior.state.value,
            )
        else:
            logger.info(
                "拖拽释放（落地）：v=(%.0f, %.0f) state=%s",
                vx,
                vy,
                self.behavior.state.value,
            )
        # Keep vx: ground friction is applied by the physics step, so a fast
        # sideways drag retains horizontal inertia instead of snapping to 0.

    # ------------------------------------------------------------------
    # Context menu / tray
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if time.perf_counter() - self._last_menu_open < 0.5:
            event.accept()
            return
        self._open_context_menu(event.globalPos())

    def _open_context_menu(self, global_pos) -> None:
        if self._left_pressed:
            return
        now = time.perf_counter()
        if now - self._last_menu_open < 0.25:
            return
        self._last_menu_open = now
        logger.info("打开右键菜单")
        self._sync_actions()
        self._menu.exec(global_pos)

    def _setup_tray(self) -> None:
        if not self._allow_tray or not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统托盘不可用；仍可使用右键菜单。")
            return
        icon = self._build_icon()
        if icon is None:
            logger.warning("无法创建托盘图标，跳过系统托盘。")
            return
        self._tray_icon = icon
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(
            "真人照片桌宠 · " + self.assets.mode_label + "\n右键角色可打开设置"
        )
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        logger.info("系统托盘已创建")

    def _build_icon(self) -> QIcon | None:
        try:
            png_path, ico_path = ensure_icon_files(self.assets.assets_dir, self.assets)
            if ico_path is not None and ico_path.is_file():
                icon = QIcon(str(ico_path))
                if not icon.isNull():
                    return icon
        except Exception:
            logger.debug("无法读取/生成图标文件", exc_info=True)
        try:
            return build_qicon(self.assets)
        except Exception:
            logger.debug("无法在内存中构建图标", exc_info=True)
            return None

    def _on_tray_activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._sync_actions()
            self._menu.popup(QCursor.pos())

    def _sync_actions(self) -> None:
        paused = self.behavior.paused
        text = "恢复自主活动" if paused else "暂停自主活动"
        if self.pause_action.text() != text:
            self.pause_action.setText(text)
        self.pause_action.setCheckable(False)
        enabled = autostart.is_enabled()
        if self.autostart_action.isChecked() != enabled:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(enabled)
            self.autostart_action.blockSignals(False)
        if self._tray is not None:
            suffix = "（已暂停）" if paused else ""
            self._tray.setToolTip(
                f"真人照片桌宠 · {self.assets.mode_label}{suffix}\n右键打开设置"
            )

    def _toggle_pause(self) -> None:
        paused = not self.behavior.paused
        self.behavior.set_paused(paused)
        if paused and self.physics.body.on_ground:
            # "Pause movement" should stop residual sliding immediately, not
            # merely suppress the next autonomous walk decision.
            self.physics.body.vx = 0.0
        self._sync_actions()
        if paused:
            self._show_bubble("我先休息一下。")
        else:
            self._show_bubble("继续陪你～")
        logger.info("用户%s自主活动", "暂停" if paused else "恢复")
        self._wake()
        self._adjust_timer(force=True)

    def _toggle_autostart(self, checked: bool) -> None:
        ok, message = autostart.set_enabled(bool(checked))
        if not ok:
            QMessageBox.warning(self, "开机启动设置失败", message)
            self._sync_actions()
            return
        self.config.autostart.enabled = bool(checked)
        self._config_dirty = True
        self._save_config_safely()
        logger.info(message)
        self._sync_actions()

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------
    def _say_random(self) -> None:
        lines = self.config.bubble.lines
        if not lines:
            return
        line = self._random.choice(lines)
        if line == self._last_spoken and len(lines) > 1:
            line = self._random.choice([item for item in lines if item != line])
        self._last_spoken = line
        self._show_bubble(line)

    def _say_click(self, kind: ClickKind) -> None:
        if kind is ClickKind.DOUBLE:
            lines = self.config.bubble.double_click_lines or self.config.bubble.lines
        else:
            lines = self.config.bubble.lines
        if not lines:
            return
        line = self._random.choice(lines)
        if line == self._last_spoken and len(lines) > 1:
            line = self._random.choice([item for item in lines if item != line])
        self._last_spoken = line
        self._show_bubble(line)

    def _show_bubble(self, text: str, *, duration: int | None = None, force: bool = False) -> None:
        if not (force or self.config.bubble.enabled):
            return
        self.bubble.show_text(
            text,
            duration if duration is not None else self.config.bubble.duration_ms,
            self._bubble_anchor_rect(),
        )

    def _show_error_bubble(self, text: str) -> None:
        self._show_bubble(text, duration=5200, force=True)

    def _show_degraded_notice(self) -> None:
        reason = self.assets.degraded_reason or "分层素材不可用"
        short = reason.strip().splitlines()[0][:170]
        message = (
            "无法实现真实分肢动画，已改用整图降级模式。"
            "仍支持整体移动、重力、拖拽和点击。\n"
            f"原因：{short}\n右键角色 → 素材诊断 查看详情。"
        )
        logger.error("降级模式提示：%s", message)
        if self.config.bubble.enabled:
            self._show_bubble(message, duration=10000, force=True)
        else:
            QMessageBox.warning(self, "已进入整图降级模式", message)
        if self._tray is not None:
            self._tray.setToolTip(
                "真人照片桌宠 · 整图降级模式\n无法实现分肢动画。右键打开设置"
            )

    def _recenter(self) -> None:
        if self.behavior.state is PetState.DRAG:
            return
        primary = QGuiApplication.primaryScreen()
        target = QPointF(primary.availableGeometry().center()) if primary else QPointF(0, 0)
        area = choose_screen_area(target, self._areas, fallback=self._current_area)
        self._current_area = area
        self._area_signature = area_signature(area)
        self._bounds = self._compute_bounds(area)
        body = self.physics.body
        body.x = min(self._bounds.right, max(self._bounds.left, target.x()))
        body.y = self._bounds.floor
        body.vx = 0.0
        body.vy = 0.0
        body.on_ground = True
        self.behavior.force_recenter()
        self.interaction.cancel()
        self._refresh_screen_state(force=True)
        self._sync_window_position(force=True)
        self._render_frame()
        self._show_bubble("我回来啦～")
        logger.info("已回到主屏底部：%s", self._current_area.name)
        self._wake()

    def _open_settings(self) -> None:
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(
            self.config,
            mode_label=self.assets.mode_label,
            warnings=self.config_warnings,
            parent=self,
        )
        self._settings_dialog = dialog
        try:
            result = dialog.exec()
        finally:
            self._settings_dialog = None
        if result != QDialog.DialogCode.Accepted:
            return
        new_config = dialog.result_config()
        try:
            self.apply_config(new_config, persist=True)
        except Exception:
            logger.exception("应用设置失败")
            QMessageBox.warning(self, "设置失败", "应用设置时发生错误，详见日志。")
            return
        self._show_bubble("设置已保存。")
        self._wake()

    def apply_config(self, new_config: AppConfig, *, persist: bool = True) -> None:
        warnings = ConfigWarningList()
        validated = config_from_dict(new_config.to_dict(), warnings)
        if warnings:
            logger.warning("设置校验：%s", "；".join(warnings))
        old_paused = self.behavior.paused
        self.config = validated
        self.render_settings = validated.render
        self.window_settings = validated.window
        self.renderer.render_settings = validated.render
        self.renderer.window_settings = validated.window
        self.renderer.configure(
            validated.window.display_height_px,
            validated.window.padding_px,
            self._current_area.dpr,
            shadow_enabled=validated.window.shadow,
        )
        self._layout = self.renderer.layout  # type: ignore[assignment]
        self.setFixedSize(self._layout.window_size)
        self._recompute_extents()
        self._bounds = self._compute_bounds()
        self.physics.config = validated.physics
        body = self.physics.body
        body.x = min(self._bounds.right, max(self._bounds.left, body.x))
        if body.y > self._bounds.floor:
            body.y = self._bounds.floor
            body.vy = 0.0
            body.on_ground = True
        self.behavior.behavior = validated.behavior
        self.behavior.animation = validated.animation
        self.behavior.set_autonomous_enabled(validated.behavior.enabled)
        if old_paused and not self.behavior.paused:
            self.behavior.set_paused(True)
        self.animator.settings = validated.animation
        self.interaction.drag_threshold_px = validated.interaction.drag_threshold_px
        self.interaction.window_s = validated.interaction.release_velocity_window_s
        self.interaction.tracker.window_s = validated.interaction.release_velocity_window_s
        self.interaction.velocity_gain = validated.interaction.release_velocity_gain
        self.interaction.max_release_speed_px_s = (
            validated.interaction.max_release_speed_px_s
        )
        self.setWindowOpacity(validated.window.opacity)
        if not validated.bubble.enabled:
            self.bubble.hide_bubble()
        self._config_dirty = True
        self._refresh_screen_state(force=True)
        self._sync_window_position(force=True)
        self._render_frame()
        self._sync_actions()
        if persist:
            self._save_config_safely()
        self._adjust_timer(force=True)
        logger.info(
            "设置已应用：height=%d fps=%d idle_fps=%d walk=%s gravity=%.0f",
            validated.window.display_height_px,
            validated.render.fps,
            validated.render.idle_fps,
            validated.behavior.enabled,
            validated.physics.gravity_px_s2,
        )

    def _save_config_safely(self) -> None:
        try:
            save_config(self.config_save_path, self.config)
            self._config_dirty = False
            logger.info("配置已保存：%s", self.config_save_path)
        except OSError as exc:
            logger.exception("保存配置失败")
            QMessageBox.warning(
                self,
                "保存配置失败",
                f"无法写入 {self.config_save_path}：\n{exc}\n\n本次设置只在当前运行中生效。",
            )

    def _show_diagnostics(self) -> None:
        diagnostics = self.diagnostics()
        box = QMessageBox(self)
        box.setWindowTitle("素材诊断")
        box.setIcon(
            QMessageBox.Icon.Information
            if self.assets.mode == "layered"
            else QMessageBox.Icon.Warning
        )
        box.setText(
            f"当前模式：{self.assets.mode_label}\n"
            f"真实分肢动画：{'可用' if self.assets.supports_limb_animation else '不可用'}\n"
            f"层级数：{len(self.assets.layers)}　骨骼数：{len(self.assets.bones)}"
        )
        if self.assets.mode != "layered":
            box.setInformativeText(
                "分层素材不可用，已降级为完整人物 PNG。仍然支持整体移动、重力、"
                "拖拽、落地和点击反馈，但没有四肢摆动。"
            )
        else:
            box.setInformativeText(
                "使用分层 PNG + rig.json 的骨骼旋转。请注意：素材为正面交叉手臂照片，"
                "手臂只在小安全角度内摆动；手掌像素在原照片中被完全遮挡。"
            )
        detail_lines = [
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
        ]
        if self.assets.warnings:
            detail_lines.append(
                "\n素材警告：\n" + "\n".join(f"- {w}" for w in self.assets.warnings)
            )
        if self.assets.errors:
            detail_lines.append(
                "\n素材错误：\n" + "\n".join(f"- {e}" for e in self.assets.errors)
            )
        box.setDetailedText("\n".join(detail_lines))
        if self.log_path is not None:
            box.setInformativeText(
                box.informativeText() + f"\n\n日志：{self.log_path}"
            )
        box.exec()

    def diagnostics(self) -> dict[str, object]:
        layout = self._layout
        return {
            "app": "PhotoDesktopPet",
            "assets": self.assets.diagnostics(),
            "config_path": str(self.config_path),
            "config_save_path": str(self.config_save_path),
            "config_warnings": self.config_warnings,
            "screen": {
                "current": self._current_area.name,
                "work": list(self._current_area.available.getRect()),
                "dpr": self._current_area.dpr,
                "all": describe_areas(self._areas),
            },
            "layout": {
                "display_height": layout.display_height_px,
                "window": [layout.window_width, layout.window_height],
                "anchor_local": [layout.anchor_local_x, layout.anchor_local_y],
                "render_dpr": layout.render_dpr,
                "window_geometry": list(self.frameGeometry().getRect()),
            },
            "physics": {
                "x": round(self.physics.body.x, 2),
                "y": round(self.physics.body.y, 2),
                "vx": round(self.physics.body.vx, 2),
                "vy": round(self.physics.body.vy, 2),
                "on_ground": self.physics.body.on_ground,
                "bounds": [
                    round(self._bounds.left, 2),
                    round(self._bounds.right, 2),
                    round(self._bounds.floor, 2),
                ],
            },
            "behavior": self.behavior.snapshot(),
            "animation": self.animator.snapshot(),
            "runtime": {
                "frames": self._frame_count,
                "measured_fps": round(self.measured_fps, 2),
                "last_render_ms": round(self.renderer.last_render_ms, 3),
                "average_render_ms": round(self.renderer.average_render_ms, 3),
                "alpha_coverage": round(self.renderer.alpha_coverage(), 4),
                "timer_interval_ms": self._timer.interval() if self._timer.isActive() else None,
                "paused": self.behavior.paused,
                "uptime_s": round(time.perf_counter() - self._started_at, 2),
                "tray": bool(self._tray is not None and self._tray.isVisible()),
                "status": self._status_message,
            },
            "recent_transitions": [
                {
                    "at": round(t.at, 3),
                    "from": t.from_state.value,
                    "to": t.to_state.value,
                    "reason": t.reason,
                }
                for t in self.behavior.recent_transitions(12)
            ],
        }

    # ------------------------------------------------------------------
    # Smoke test / shutdown
    # ------------------------------------------------------------------
    def start_smoke_test(
        self, seconds: float, report_path: Path, foreground_before: int | None = None
    ) -> None:
        self._smoke_mode = True
        self._smoke_foreground_before = foreground_before
        self._smoke_report_path = Path(report_path)
        self._smoke_started_at = time.perf_counter()
        QTimer.singleShot(max(300, int(seconds * 1000)), self._finish_smoke)

    def _finish_smoke(self) -> None:
        if self._closing:
            return
        report_path = self._smoke_report_path
        report: dict[str, object] = {
            "ok": False,
            "mode": self.assets.mode,
            "mode_label": self.assets.mode_label,
            "supports_limb_animation": self.assets.supports_limb_animation,
            "window_visible": self.isVisible(),
            "foreground_before": self._smoke_foreground_before,
            "foreground_after": _foreground_hwnd(),
            "diagnostics": self.diagnostics(),
        }
        checks: dict[str, bool] = {}
        try:
            frame = self.renderer.last_frame
            checks["frame_exists"] = frame is not None and not frame.isNull()
            if frame is not None:
                corners = [
                    frame.pixelColor(0, 0).alpha(),
                    frame.pixelColor(frame.width() - 1, 0).alpha(),
                    frame.pixelColor(0, frame.height() - 1).alpha(),
                    frame.pixelColor(frame.width() - 1, frame.height() - 1).alpha(),
                ]
                coverage = self.renderer.alpha_coverage()
                checks["frame_corners_transparent"] = all(a == 0 for a in corners)
                checks["frame_nonempty"] = coverage > 0.015
                base = (
                    report_path.parent
                    if report_path is not None
                    else Path(getattr(self, "log_path", ".")).parent
                )
                base.mkdir(parents=True, exist_ok=True)
                frame_path = base / "smoke_frame.png"
                checks["frame_saved"] = bool(frame.save(str(frame_path)))
                report["frame"] = {
                    "size": [frame.width(), frame.height()],
                    "dpr": frame.devicePixelRatio(),
                    "corners_alpha": corners,
                    "alpha_coverage": round(coverage, 4),
                    "path": str(frame_path),
                }
                report["screenshot"] = self._capture_window_region(base / "smoke_screen.png")
            checks["window_visible"] = self.isVisible()
            checks["assets_loaded"] = bool(self.assets.layers)
            checks["no_fatal_frame_error"] = self._frame_errors == 0
            native_hit = self._native_hit_test_diagnostic()
            report["native_hit_test"] = native_hit
            if native_hit.get("available"):
                checks["native_hit_test_ok"] = bool(
                    native_hit.get("hits_opaque")
                    and native_hit.get("passes_through_transparent")
                    and native_hit.get("no_activate_style", True)
                )
            report["checks"] = checks
            report["ok"] = all(checks.values())
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()
        try:
            if report_path is not None:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except OSError:
            logger.exception("写入冒烟测试报告失败")
        self.smoke_exit_code = 0 if report.get("ok") else 1
        logger.info("冒烟测试完成：ok=%s checks=%s", report.get("ok"), checks)
        QTimer.singleShot(80, lambda: self.shutdown("smoke test finished"))

    def _capture_window_region(self, path: Path) -> dict[str, object]:
        try:
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return {"ok": False, "reason": "no screen"}
            geo = self.frameGeometry()
            sg = screen.geometry()
            x = geo.x() - sg.x()
            y = geo.y() - sg.y()
            pix: QPixmap = screen.grabWindow(0, x, y, geo.width(), geo.height())
            ok = pix.save(str(path))
            return {
                "ok": bool(ok),
                "path": str(path),
                "size": [pix.width(), pix.height()],
            }
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    def shutdown(self, reason: str = "user exit") -> None:
        if self._closing:
            return
        self._closing = True
        logger.info("开始退出：%s", reason)
        try:
            self._timer.stop()
        except Exception:
            pass
        if self._tray is not None:
            try:
                self._tray.hide()
                self._tray.setVisible(False)
            except Exception:
                pass
            self._tray = None
        try:
            self.bubble.hide_bubble()
            self.bubble.close()
        except Exception:
            pass
        if getattr(self, "_config_dirty", False):
            try:
                save_config(self.config_save_path, self.config)
                logger.info("退出前保存配置：%s", self.config_save_path)
            except OSError:
                logger.exception("退出前保存配置失败")
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._closing:
            self._closing = True
            self._timer.stop()
            if self._tray is not None:
                self._tray.hide()
                self._tray = None
            try:
                self.bubble.close()
            except Exception:
                pass
            if getattr(self, "_config_dirty", False):
                try:
                    save_config(self.config_save_path, self.config)
                except OSError:
                    logger.exception("closeEvent 保存配置失败")
        event.accept()
