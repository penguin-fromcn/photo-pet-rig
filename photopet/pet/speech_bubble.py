"""Small local speech bubble.  No network, no model calls, no face edits."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .screen_utils import choose_screen_area, current_screen_areas


class SpeechBubble(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text = ""
        self._padding = 12
        self._max_width = 250
        self._tail = 8
        self._font = QFont("Microsoft YaHei UI")
        self._font.setPointSize(10)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._pet_rect: QRect | None = None
        self._last_rect: QRect | None = None

    def show_text(self, text: str, duration_ms: int = 3200, pet_rect: QRect | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        self._text = text[:120]
        if pet_rect is not None:
            self._pet_rect = QRect(pet_rect)
        self._resize_for_text()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(max(400, int(duration_ms)))

    def attach_pet_rect(self, rect: QRect) -> None:
        self._pet_rect = QRect(rect)
        if self.isVisible():
            self._reposition()

    def hide_bubble(self) -> None:
        self._hide_timer.stop()
        self.hide()

    def _resize_for_text(self) -> None:
        metrics = QFontMetrics(self._font)
        max_text_width = self._max_width
        bounds = metrics.boundingRect(
            QRect(0, 0, max_text_width, 2000),
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )
        text_width = max(48, min(max_text_width, bounds.width()))
        text_height = max(metrics.height(), bounds.height())
        width = text_width + self._padding * 2
        height = text_height + self._padding * 2 + self._tail
        self.setFixedSize(width, height)

    def _reposition(self) -> None:
        pet = self._pet_rect
        if pet is None:
            return
        areas = current_screen_areas()
        area = choose_screen_area(pet.center(), areas)
        available = area.available
        x = pet.center().x() - self.width() // 2
        y = pet.top() - self.height() + 6
        if y < available.top() + 2:
            # No space above: place at the top-left/right side inside the work
            # area, still next to the pet instead of off-screen.
            y = min(available.bottom() - self.height(), pet.top() + 24)
        if y + self.height() > available.bottom():
            y = available.bottom() - self.height()
        x = min(available.right() - self.width(), max(available.left(), x))
        self.move(QPoint(x, y))
        self._last_rect = QRect(self.geometry())

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - self._tail - 2.0)
            path = QPainterPath()
            path.addRoundedRect(rect, 12.0, 12.0)
            # Tail pointing down toward the character.
            cx = self.width() * 0.5
            tail = QPainterPath()
            tail.moveTo(cx - 7.0, rect.bottom() - 0.5)
            tail.lineTo(cx + 7.0, rect.bottom() - 0.5)
            tail.lineTo(cx, rect.bottom() + self._tail)
            tail.closeSubpath()
            path = path.united(tail)
            painter.setPen(QPen(QColor(255, 255, 255, 55), 1.0))
            painter.setBrush(QColor(22, 24, 30, 225))
            painter.drawPath(path)
            painter.setPen(QColor(245, 245, 250, 245))
            painter.setFont(self._font)
            text_rect = QRect(
                self._padding,
                self._padding,
                self.width() - self._padding * 2,
                self.height() - self._padding * 2 - self._tail,
            )
            painter.drawText(
                text_rect,
                int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft),
                self._text,
            )
        finally:
            painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._hide_timer.stop()
        super().closeEvent(event)
