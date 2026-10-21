"""Базовый класс окна-виджета. Всё общее для «Сейчас играет» и «Монитора»:

- безрамочное окно без кнопки на панели задач, поверх окон или «на рабочем столе»;
- полупрозрачный скруглённый фон (рисуем сами в paintEvent);
- прозрачность при наведении + пропуск кликов (Ctrl — временно отключить);
- режим редактирования: перетаскивание, изменение размера за углы с сохранением
  пропорций, прилипание к краям экрана и к другим виджетам, рамка;
- сохранение позиции/размера/монитора в настройках.

Масштабирование устроено так: у каждого виджета есть «базовый размер»
(base_size) при масштабе 1.0. Размер окна = базовый × scale, а наследник
в apply_style() пересчитывает шрифты и отступы через self.px(...).
"""

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from . import theme, winutils
from .theme_menu import make_theme_menu

MIN_SCALE, MAX_SCALE = 0.6, 3.0
SNAP = 10          # порог прилипания, px
CORNER = 16        # размер «уголка» для изменения размера, px


def screen_id(screen) -> str:
    """Отпечаток монитора (производитель|модель|серийник) — чтобы узнать его после переподключения."""
    return f"{screen.manufacturer()}|{screen.model()}|{screen.serialNumber()}"


def find_screen(pos: dict):
    """Ищет монитор, сохранённый в настройках. None — если такой сейчас не подключён."""
    screens = QGuiApplication.screens()
    name, sid = pos.get("screen"), pos.get("screen_id")
    for s in screens:
        if s.name() == name and screen_id(s) == sid:
            return s
    same_id = [s for s in screens if screen_id(s) == sid]
    if len(same_id) == 1:
        return same_id[0]
    for s in screens:
        if s.name() == name:
            return s
    return None


def default_screen():
    """«Второй монитор» — первый неосновной. Если его нет — основной."""
    primary = QGuiApplication.primaryScreen()
    others = [s for s in QGuiApplication.screens() if s is not primary]
    return others[0] if others else primary


def clamp_rect(r: QRect, area: QRect) -> QRect:
    """Сдвигает прямоугольник так, чтобы он целиком был внутри area."""
    x = max(area.left(), min(r.x(), area.x() + area.width() - r.width()))
    y = max(area.top(), min(r.y(), area.y() + area.height() - r.height()))
    return QRect(x, y, r.width(), r.height())


class BaseWidget(QWidget):
    BASE_SIZE = (340, 120)

    def __init__(self, app, key: str, title: str):
        super().__init__(None)
        self.app = app
        self.cfg = app.cfg
        self.key = key
        self.setWindowTitle(f"DeskWidgets — {title}")
        self.setWindowIcon(theme.app_icon())
        # Прозрачный фон окна: всё, что не нарисовали сами, — полностью прозрачно
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # не отбирать фокус у других окон при показе
        self.setMouseTracking(True)                       # mouseMoveEvent без нажатой кнопки (для курсора-стрелок)

        self.scale = 1.0
        self.colors = dict(app.colors)
        self.color_override: dict | None = None  # для «фона по обложке»
        self.edit_mode = False
        self._drag: dict | None = None
        self._content_wanted = True   # модуль хочет показываться (например, что-то играет)
        self._fs_hidden = False       # скрыт из-за полноэкранного окна
        self._faded = False
        self._wheel_accum = 0

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._apply_window_flags()

    # ================= для наследников =================

    def base_size(self) -> QSize:
        return QSize(*self.BASE_SIZE)

    def apply_style(self) -> None:
        """Пересчитать шрифты, отступы и цвета под self.scale и self.palette_colors()."""

    def px(self, value: float) -> int:
        """Размер в пикселях с учётом масштаба виджета."""
        return max(1, round(value * self.scale))

    def make_font(self, mult: float = 1.0, bold: bool = False) -> QFont:
        """Шрифт из настроек: размер = (размер из настроек) × mult × масштаб."""
        a = self.cfg.get("appearance")
        f = QFont(a.get("font_family") or "Segoe UI")
        f.setPixelSize(max(6, round(a.get("font_size", 10) * 1.33 * mult * self.scale)))
        f.setBold(bold)
        return f

    def icon_font(self, mult: float = 1.0) -> QFont:
        """Шрифт со значками Windows (Segoe Fluent Icons в Win11, Segoe MDL2 Assets в Win10)."""
        f = QFont()
        f.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
        f.setPixelSize(max(6, round(14 * mult * self.scale)))
        return f

    def palette_colors(self) -> dict:
        """Текущие цвета (с учётом подмены «под обложку»)."""
        if self.color_override:
            c = dict(self.colors)
            c.update(self.color_override)
            return c
        return self.colors

    # ================= флаги окна =================

    def _apply_window_flags(self) -> None:
        layer = self.cfg.get("general.layer", "top")
        flags = (Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                 | Qt.NoDropShadowWindowHint)
        # Qt.Tool — окно-«инструмент»: нет кнопки на панели задач и в Alt+Tab
        flags |= Qt.WindowStaysOnTopHint if layer == "top" else Qt.WindowStaysOnBottomHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)  # пересоздаёт системное окно — стиль «сквозь клики» сбрасывается
        self._faded = False
        self.setWindowOpacity(1.0)
        if was_visible:
            self.show()

    def reapply_layer(self) -> None:
        self._apply_window_flags()

    # ================= тема и масштаб =================

    def apply_theme(self, colors: dict) -> None:
        self.colors = dict(colors)
        self.apply_style()
        self.update()

    def set_scale(self, scale: float) -> None:
        self.scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))
        b = self.base_size()
        self.setFixedSize(round(b.width() * self.scale), round(b.height() * self.scale))
        self.apply_style()
        self.update()

    def refresh_size(self) -> None:
        """Вызывать, если поменялся base_size (например, скрыли строку в «Мониторе»)."""
        self.set_scale(self.scale)

    # ================= видимость =================

    def set_content_visible(self, visible: bool) -> None:
        self._content_wanted = visible
        self.refresh_visibility()

    def set_fullscreen_hidden(self, hidden: bool) -> None:
        if hidden != self._fs_hidden:
            self._fs_hidden = hidden
            self.refresh_visibility()

    def refresh_visibility(self) -> None:
        if self.edit_mode:
            want = self.app.widgets_visible  # при редактировании показываем всегда, чтобы можно было двигать
        else:
            want = self.app.widgets_visible and self._content_wanted and not self._fs_hidden
        if want and not self.isVisible():
            self.show()
        elif not want and self.isVisible():
            self.hide()

    def set_edit_mode(self, on: bool) -> None:
        self.edit_mode = on
        if on:
            self._set_faded(False)
        self.setCursor(Qt.ArrowCursor)
        self.refresh_visibility()
        self.update()

    @property
    def pinned(self) -> bool:
        return bool(self.cfg.get(f"widgets.{self.key}.pinned", False))

    # ================= прозрачность при наведении =================

    def update_hover(self, cursor: QPoint, interact_key: bool) -> None:
        """Вызывается приложением ~15 раз в секунду.

        Опрашиваем положение курсора таймером, а не событиями мыши: когда окно
        «сквозное» для кликов, события мыши ему просто не приходят.
        """
        if not self.isVisible():
            return
        enabled = (self.cfg.get("general.hover_fade", True)
                   and self.cfg.get(f"widgets.{self.key}.hover_fade", True)  # можно отключить для отдельного виджета
                   and not self.edit_mode)
        inside = self.frameGeometry().contains(cursor)
        self._set_faded(enabled and inside and not interact_key)

    def _set_faded(self, fade: bool) -> None:
        if fade == self._faded:
            return
        self._faded = fade
        target = self.cfg.get("appearance.hover_opacity", 15) / 100 if fade else 1.0
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(max(0.02, target))
        self._anim.start()
        try:
            winutils.set_click_through(int(self.winId()), fade)
        except Exception:
            pass

    # ================= позиция =================

    def has_saved_pos(self) -> bool:
        return isinstance(self.cfg.get(f"widgets.{self.key}.pos"), dict)

    def restore_saved_position(self) -> None:
        """Ставит виджет туда, где его оставили. Если того монитора нет — на основной,
        сохраняя «привязку» к ближайшему углу, и следим, чтобы окно не вылезло за экран."""
        pos = self.cfg.get(f"widgets.{self.key}.pos")
        screen = find_screen(pos) or QGuiApplication.primaryScreen()
        g = screen.geometry()
        w, h = self.width(), self.height()
        x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
        sw, sh = int(pos.get("sw", g.width())), int(pos.get("sh", g.height()))
        if (sw, sh) != (g.width(), g.height()):
            # Другое разрешение: сохраняем расстояние до ближайшего края
            if x + w / 2 > sw / 2:
                x = g.width() - (sw - x)
            if y + h / 2 > sh / 2:
                y = g.height() - (sh - y)
        rect = QRect(g.x() + x, g.y() + y, w, h)
        self.move(clamp_rect(rect, screen.availableGeometry()).topLeft())

    def save_position(self) -> None:
        screen = QGuiApplication.screenAt(self.geometry().center()) or self.screen()
        g = screen.geometry()
        self.cfg.set(f"widgets.{self.key}.pos", {
            "screen": screen.name(), "screen_id": screen_id(screen),
            "x": self.x() - g.x(), "y": self.y() - g.y(), "sw": g.width(), "sh": g.height(),
        })
        self.cfg.set(f"widgets.{self.key}.scale", round(self.scale, 3))

    # ================= рисование =================

    def paintEvent(self, _event) -> None:
        a = self.cfg.get("appearance")
        c = self.palette_colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        radius = a.get("radius", 12) * self.scale
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        bg = QColor(c["bg"])
        # Минимум 1/255: у полностью прозрачных пикселей Windows не ловит клики
        bg.setAlpha(max(1, round(255 * a.get("bg_opacity", 80) / 100)))
        p.setBrush(bg)
        if a.get("border", True):
            border = QColor(c["accent"])
            border.setAlpha(110)
            p.setPen(QPen(border, 1))
        else:
            p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, radius, radius)

        if self.edit_mode:
            pen = QPen(c["accent"], 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
            if self.pinned:
                p.setFont(self.icon_font(0.9))
                p.setPen(c["accent"])
                p.drawText(QRectF(self.width() - self.px(24), self.px(4), self.px(20), self.px(20)),
                           Qt.AlignCenter, "")  # значок «булавка»
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(c["accent"])
                s = 6
                w, h = self.width(), self.height()
                for cx, cy in ((1, 1), (w - s - 1, 1), (1, h - s - 1), (w - s - 1, h - s - 1)):
                    p.drawRect(QRectF(cx, cy, s, s))
        p.end()

    # ================= мышь: перетаскивание и размер =================

    def _corner_at(self, pos: QPoint) -> str | None:
        w, h = self.width(), self.height()
        left, right = pos.x() < CORNER, pos.x() > w - CORNER
        top, bottom = pos.y() < CORNER, pos.y() > h - CORNER
        if top and left:
            return "tl"
        if top and right:
            return "tr"
        if bottom and left:
            return "bl"
        if bottom and right:
            return "br"
        return None

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.edit_mode and not self.pinned:
            local = e.position().toPoint()
            corner = self._corner_at(local)
            self._drag = {
                "mode": "resize" if corner else "move",
                "corner": corner,
                "start": e.globalPosition().toPoint(),
                "geom": self.geometry(),
            }
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag:
            delta = e.globalPosition().toPoint() - self._drag["start"]
            geom: QRect = self._drag["geom"]
            if self._drag["mode"] == "move":
                new = self._snap(geom.translated(delta))
                self.move(new.topLeft())
            else:
                corner = self._drag["corner"]
                dx = delta.x() if corner in ("tr", "br") else -delta.x()
                self.set_scale((geom.width() + dx) / self.base_size().width())
                w, h = self.width(), self.height()
                # Противоположный угол остаётся на месте
                x = geom.left() if corner in ("tr", "br") else geom.right() - w + 1
                y = geom.top() if corner in ("bl", "br") else geom.bottom() - h + 1
                self.move(x, y)
            e.accept()
            return
        if self.edit_mode:
            if self.pinned:
                self.setCursor(Qt.ArrowCursor)
            else:
                corner = self._corner_at(e.position().toPoint())
                if corner in ("tl", "br"):
                    self.setCursor(Qt.SizeFDiagCursor)
                elif corner in ("tr", "bl"):
                    self.setCursor(Qt.SizeBDiagCursor)
                else:
                    self.setCursor(Qt.SizeAllCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag and e.button() == Qt.LeftButton:
            self._drag = None
            self.save_position()
            self.app.on_widget_moved(self)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _snap(self, r: QRect) -> QRect:
        """Прилипание: если край окна ближе SNAP пикселей к краю экрана или другого виджета — притягиваем."""
        screen = QGuiApplication.screenAt(r.center()) or self.screen()
        x_targets, y_targets = [], []
        for area in (screen.availableGeometry(), screen.geometry()):
            x_targets += [area.x(), area.x() + area.width()]
            y_targets += [area.y(), area.y() + area.height()]
        for other in self.app.all_widgets():
            if other is self or not other.isVisible():
                continue
            g = other.geometry()
            x_targets += [g.x(), g.x() + g.width()]
            y_targets += [g.y(), g.y() + g.height()]

        def best_shift(edges, targets):
            best = None
            for edge in edges:
                for t in targets:
                    d = t - edge
                    if abs(d) <= SNAP and (best is None or abs(d) < abs(best)):
                        best = d
            return best or 0

        dx = best_shift([r.x(), r.x() + r.width()], x_targets)
        dy = best_shift([r.y(), r.y() + r.height()], y_targets)
        return r.translated(dx, dy)

    # ================= контекстное меню =================

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        act_edit = menu.addAction("Режим редактирования")
        act_edit.setCheckable(True)
        act_edit.setChecked(self.edit_mode)
        act_pin = menu.addAction("Закрепить")
        act_pin.setCheckable(True)
        act_pin.setChecked(self.pinned)
        act_reset = menu.addAction("Вернуть на место по умолчанию")
        menu.addSeparator()
        make_theme_menu(menu, self.app)
        act_settings = menu.addAction("Настройки…")
        chosen = menu.exec(e.globalPos())
        if chosen is act_edit:
            self.app.set_edit_mode(not self.edit_mode)
        elif chosen is act_pin:
            self.cfg.set(f"widgets.{self.key}.pinned", not self.pinned)
            self.update()
        elif chosen is act_reset:
            self.cfg.set(f"widgets.{self.key}.pos", None)
            self.cfg.set(f"widgets.{self.key}.scale", 1.0)
            self.set_scale(1.0)
            self.app.place_widgets()
        elif chosen is act_settings:
            self.app.show_settings()

    def wheel_steps(self, e) -> int:
        """Колесо мыши → целые «щелчки» (тачпады присылают мелкие дельты, копим их)."""
        self._wheel_accum += e.angleDelta().y()
        steps = int(self._wheel_accum / 120)
        self._wheel_accum -= steps * 120
        return steps


def cursor_pos() -> QPoint:
    return QCursor.pos()
