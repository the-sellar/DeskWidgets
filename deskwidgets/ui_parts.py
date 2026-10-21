"""Мелкие элементы интерфейса, общие для виджетов: подпись с «…», полоса прогресса, кнопка-значок."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from .theme import css


class ElideLabel(QLabel):
    """Подпись, которая обрезает длинный текст многоточием, а не растягивает окно."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._color = QColor("#FFFFFF")
        self.clickable = False  # True — подпись реагирует на клик (сигнал clicked)
        # Ignored по горизонтали: ширину диктует раскладка, а не длина текста
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(8)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self._color)
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        p.drawText(self.rect(), int(self.alignment() | Qt.AlignVCenter), text)
        p.end()

    def mousePressEvent(self, e) -> None:
        # В режиме редактирования клик отдаём окну — чтобы его можно было тащить за любое место
        if e.button() == Qt.LeftButton and self.clickable and not getattr(self.window(), "edit_mode", False):
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)


class ProgressBar(QWidget):
    """Тонкая полоса прогресса со скруглёнными краями."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.track = QColor(128, 128, 128, 80)
        self.fill = QColor("#4FC3F7")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v: float) -> None:
        v = max(0.0, min(1.0, v))
        if abs(v - self.value) > 0.0005:
            self.value = v
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = self.height() / 2
        p.setBrush(self.track)
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        if self.value > 0:
            p.setBrush(self.fill)
            p.drawRoundedRect(QRectF(0, 0, max(self.height(), self.width() * self.value), self.height()), r, r)
        p.end()


class Slider(ProgressBar):
    """Ползунок поверх полосы прогресса: клик или перетаскивание задают значение 0..1."""

    moved = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.dragging = False

    def _value_at(self, x: float) -> float:
        d = self.height() * 0.75
        return max(0.0, min(1.0, (x - d / 2) / max(1.0, self.width() - d)))

    def mousePressEvent(self, e) -> None:
        # В режиме редактирования отдаём клик окну — чтобы его можно было тащить
        if e.button() == Qt.LeftButton and not getattr(self.window(), "edit_mode", False):
            self.dragging = True
            self.set_value(self._value_at(e.position().x()))
            self.moved.emit(self.value)
            e.accept()
            return
        e.ignore()

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            self.set_value(self._value_at(e.position().x()))
            self.moved.emit(self.value)
            e.accept()
            return
        e.ignore()

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging:
            self.dragging = False
            e.accept()
            return
        e.ignore()

    def paintEvent(self, _event) -> None:
        # Виджет высокий (чтобы легко попасть мышью), а полоса — тонкая, по центру
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        h = self.height()
        d = h * 0.75                       # диаметр ручки
        th = max(2.0, h / 3)               # толщина полосы
        left, right = d / 2, self.width() - d / 2
        y = (h - th) / 2
        p.setBrush(self.track)
        p.drawRoundedRect(QRectF(left, y, right - left, th), th / 2, th / 2)
        x = left + (right - left) * self.value
        p.setBrush(self.fill)
        p.drawRoundedRect(QRectF(left, y, x - left, th), th / 2, th / 2)
        p.drawEllipse(QRectF(x - d / 2, (h - d) / 2, d, d))
        p.end()


class IconButton(QPushButton):
    """Плоская кнопка со значком из шрифта Segoe Fluent Icons / MDL2."""

    def __init__(self, glyph: str, tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def set_colors(self, text: QColor, accent: QColor, radius: int) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {css(text)}; border-radius: {radius}px; }}"
            f"QPushButton:hover {{ background: {css(accent, 60)}; }}"
            f"QPushButton:pressed {{ background: {css(accent, 110)}; }}"
            f"QPushButton:disabled {{ color: {css(text, 70)}; }}"
        )
