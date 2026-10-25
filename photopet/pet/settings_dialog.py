"""Settings dialog (Chinese UI)."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .settings import DEFAULT_BUBBLE_LINES, AppConfig


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: AppConfig,
        *,
        mode_label: str,
        warnings: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("桌宠设置")
        self.setWindowIcon(QIcon())
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setMinimumWidth(500)
        self.resize(520, 620)
        self._source = config
        self._working = copy.deepcopy(config)
        self._mode_label = mode_label
        self._warnings = list(warnings or [])
        self._build_ui()
        self._load_from_config()
        self._center_on_parent()
        self._clamp_to_screen()

    def _clamp_to_screen(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(self.width(), max(420, available.width() - 40))
        height = min(self.height(), max(420, available.height() - 40))
        self.resize(width, height)

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            geo = self.frameGeometry()
            pg = parent.frameGeometry()
            geo.moveCenter(pg.center())
            self.move(geo.topLeft())

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 12)
        outer.setSpacing(10)

        title = QLabel("角色与行为设置")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        outer.addWidget(title)
        hint = QLabel(
            f"当前模式：<b>{self._mode_label}</b><br>"
            "设置会写入 config.json；高级骨骼/物理参数可直接编辑该文件。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #777;")
        outer.addWidget(hint)
        if self._warnings:
            warn = QLabel("配置警告：" + "；".join(self._warnings[:4]))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a26100; background: #fff4d6; padding: 6px; border-radius: 4px;")
            outer.addWidget(warn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setSpacing(10)

        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        self.height_slider = QSlider(Qt.Orientation.Horizontal)
        self.height_slider.setRange(160, 900)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(160, 900)
        self.height_spin.setSuffix(" px")
        row = QHBoxLayout()
        row.addWidget(self.height_slider, 1)
        row.addWidget(self.height_spin)
        self.height_slider.valueChanged.connect(self.height_spin.setValue)
        self.height_spin.valueChanged.connect(self.height_slider.setValue)
        appearance_form.addRow("角色显示高度：", row)
        size_hint = QLabel("数值是 1279×2275 画布的高度；可见人物约为该值的 88%。")
        size_hint.setStyleSheet("color: #888; font-size: 11px;")
        size_hint.setWordWrap(True)
        appearance_form.addRow("", size_hint)
        self.shadow_check = QCheckBox("显示脚底柔和接触阴影")
        appearance_form.addRow("", self.shadow_check)
        self.bubble_check = QCheckBox("显示本地文字气泡（不联网）")
        appearance_form.addRow("", self.bubble_check)
        form.addWidget(appearance)

        performance = QGroupBox("性能")
        performance_form = QFormLayout(performance)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(10, 60)
        self.fps_spin.setSuffix(" FPS")
        performance_form.addRow("目标帧率：", self.fps_spin)
        self.idle_fps_spin = QSpinBox()
        self.idle_fps_spin.setRange(5, 60)
        self.idle_fps_spin.setSuffix(" FPS")
        performance_form.addRow("待机帧率：", self.idle_fps_spin)
        perf_hint = QLabel("静止/暂停时会自动降帧或暂停定时器，避免持续占用 CPU。")
        perf_hint.setStyleSheet("color: #888; font-size: 11px;")
        perf_hint.setWordWrap(True)
        performance_form.addRow("", perf_hint)
        form.addWidget(performance)

        behaviour = QGroupBox("走动与物理")
        behaviour_form = QFormLayout(behaviour)
        self.auto_walk_check = QCheckBox("允许自主走动与边缘转向")
        behaviour_form.addRow("", self.auto_walk_check)
        self.walk_speed_spin = QSpinBox()
        self.walk_speed_spin.setRange(5, 240)
        self.walk_speed_spin.setSuffix(" px/s")
        behaviour_form.addRow("走路速度：", self.walk_speed_spin)
        self.gravity_spin = QDoubleSpinBox()
        self.gravity_spin.setRange(300.0, 6000.0)
        self.gravity_spin.setDecimals(0)
        self.gravity_spin.setSingleStep(100.0)
        self.gravity_spin.setSuffix(" px/s²")
        behaviour_form.addRow("重力加速度：", self.gravity_spin)
        self.restitution_spin = QDoubleSpinBox()
        self.restitution_spin.setRange(0.0, 0.85)
        self.restitution_spin.setDecimals(2)
        self.restitution_spin.setSingleStep(0.05)
        behaviour_form.addRow("落地弹跳系数：", self.restitution_spin)
        form.addWidget(behaviour)

        animation = QGroupBox("动画")
        animation_form = QFormLayout(animation)
        self.idle_breath_check = QCheckBox("待机呼吸/轻微摆动")
        animation_form.addRow("", self.idle_breath_check)
        self.motion_scale_spin = QDoubleSpinBox()
        self.motion_scale_spin.setRange(0.0, 1.8)
        self.motion_scale_spin.setDecimals(2)
        self.motion_scale_spin.setSingleStep(0.1)
        self.motion_scale_spin.setSuffix(" ×")
        animation_form.addRow("整体动作幅度：", self.motion_scale_spin)
        self.walk_cycle_spin = QDoubleSpinBox()
        self.walk_cycle_spin.setRange(0.4, 3.0)
        self.walk_cycle_spin.setDecimals(2)
        self.walk_cycle_spin.setSingleStep(0.1)
        self.walk_cycle_spin.setSuffix(" Hz")
        animation_form.addRow("走路循环频率：", self.walk_cycle_spin)
        anim_hint = QLabel(
            "该素材为正面交叉手臂照片：手臂只能在小安全角度内摆动，"
            "无法还原自然摆臂、侧身或手部动作。"
        )
        anim_hint.setStyleSheet("color: #888; font-size: 11px;")
        anim_hint.setWordWrap(True)
        animation_form.addRow("", anim_hint)
        form.addWidget(animation)

        startup = QGroupBox("启动")
        startup_form = QFormLayout(startup)
        self.autostart_check = QCheckBox("登录 Windows 时自动启动桌宠（默认关闭）")
        self.autostart_check.setChecked(autostart.is_enabled())
        startup_form.addRow("", self.autostart_check)
        self.autostart_status = QLabel("注册表状态：" + autostart.status_text())
        self.autostart_status.setStyleSheet("color: #888; font-size: 11px;")
        startup_form.addRow("", self.autostart_status)
        form.addWidget(startup)

        form.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.reset_button = QPushButton("恢复默认")
        buttons.addButton(self.reset_button, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self.reset_button.clicked.connect(self._on_reset)
        outer.addWidget(buttons)

    def _load_from_config(self) -> None:
        cfg = self._working
        self.height_spin.setValue(cfg.window.display_height_px)
        self.shadow_check.setChecked(cfg.window.shadow)
        self.bubble_check.setChecked(cfg.bubble.enabled)
        self.fps_spin.setValue(cfg.render.fps)
        self.idle_fps_spin.setValue(cfg.render.idle_fps)
        self.auto_walk_check.setChecked(cfg.behavior.enabled)
        self.walk_speed_spin.setValue(int(round(cfg.behavior.walk_speed_px_s)))
        self.gravity_spin.setValue(cfg.physics.gravity_px_s2)
        self.restitution_spin.setValue(cfg.physics.restitution)
        self.idle_breath_check.setChecked(cfg.animation.idle_breathing)
        self.motion_scale_spin.setValue(cfg.animation.motion_scale)
        self.walk_cycle_spin.setValue(cfg.animation.walk_cycle_hz)

    def _on_reset(self) -> None:
        confirm = QMessageBox.question(
            self,
            "恢复默认",
            "将本页所有设置恢复为默认值？点击“保存”后才会写入配置文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm is not QMessageBox.StandardButton.Yes:
            return
        self._working = AppConfig()
        self._load_from_config()

    def _on_accept(self) -> None:
        cfg = self._working
        cfg.window.display_height_px = self.height_spin.value()
        cfg.window.shadow = self.shadow_check.isChecked()
        cfg.bubble.enabled = self.bubble_check.isChecked()
        cfg.render.fps = self.fps_spin.value()
        cfg.render.idle_fps = min(self.idle_fps_spin.value(), cfg.render.fps)
        cfg.behavior.enabled = self.auto_walk_check.isChecked()
        cfg.behavior.walk_speed_px_s = float(self.walk_speed_spin.value())
        cfg.physics.gravity_px_s2 = float(self.gravity_spin.value())
        cfg.physics.restitution = float(self.restitution_spin.value())
        cfg.animation.idle_breathing = self.idle_breath_check.isChecked()
        cfg.animation.motion_scale = float(self.motion_scale_spin.value())
        cfg.animation.walk_cycle_hz = float(self.walk_cycle_spin.value())

        want_autostart = self.autostart_check.isChecked()
        if want_autostart != autostart.is_enabled():
            ok, message = autostart.set_enabled(want_autostart)
            if not ok:
                QMessageBox.warning(self, "开机启动设置失败", message)
                return
            cfg.autostart.enabled = want_autostart
        else:
            cfg.autostart.enabled = want_autostart

        if not cfg.bubble.lines:
            cfg.bubble.lines = list(DEFAULT_BUBBLE_LINES)
        self.accept()

    def result_config(self) -> AppConfig:
        return self._working
