"""Модуль 1. «Сейчас играет»: трек из медиа-сессий Windows, обложка, прогресс, кнопки."""

import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from .. import theme
from ..ui_parts import ElideLabel, IconButton, ProgressBar, Slider
from ..volume import SystemVolume
from ..widget_base import BaseWidget
from .base_module import BaseModule
from .media_backend import MediaBackend

# Значки из шрифта Segoe Fluent Icons / Segoe MDL2 Assets
G_PREV, G_NEXT, G_PLAY, G_PAUSE = "", "", "", ""
G_LEFT, G_RIGHT = "", ""
G_VOL, G_MUTE = "", ""

# Часть идентификатора приложения → понятное название
_SOURCES = [
    ("chrome", "Chrome"), ("msedge", "Edge"), ("yandex", "Яндекс Браузер"), ("browser.exe", "Яндекс Браузер"),
    ("firefox", "Firefox"), ("opera", "Opera"), ("vivaldi", "Vivaldi"), ("brave", "Brave"),
    ("spotify", "Spotify"), ("vk", "VK"), ("telegram", "Telegram"), ("zunemusic", "Медиаплеер"),
]


def source_name(aumid: str) -> str:
    low = aumid.lower()
    for part, name in _SOURCES:
        if part in low:
            return name
    name = aumid.split("!")[-1].split("\\")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name or "Неизвестный источник"


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def dominant_color(img: QImage) -> QColor:
    """Основной цвет обложки: среднее по уменьшенной картинке, где яркие насыщенные
    пиксели весят больше серых. Потом приглушаем, чтобы на фоне читался белый текст."""
    small = img.scaled(24, 24, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_RGB32)
    r = g = b = total = 0.0
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            _h, s, v, _a = c.getHsvF()
            w = 0.08 + s * v
            r += c.redF() * w
            g += c.greenF() * w
            b += c.blueF() * w
            total += w
    if total <= 0:
        return QColor("#303030")
    color = QColor.fromRgbF(r / total, g / total, b / total)
    h, s, v, _a = color.getHsvF()
    return QColor.fromHsvF(max(0.0, h), min(s, 0.75), min(v, 0.40))


class NowPlayingWidget(BaseWidget):
    BASE_SIZE = (340, 138)

    def __init__(self, app, module):
        super().__init__(app, "music", "Сейчас играет")
        self.module = module
        self._cover_img: QImage | None = None
        self._cover_id = ""

        root = QVBoxLayout(self)
        self.root = root

        # --- верх: обложка + текст ---
        top = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setAlignment(Qt.AlignCenter)
        top.addWidget(self.cover)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.title_lbl = ElideLabel()
        self.artist_lbl = ElideLabel()
        src_row = QHBoxLayout()
        src_row.setSpacing(2)
        self.source_lbl = ElideLabel()
        self.btn_sess_prev = IconButton(G_LEFT, "Предыдущий источник")
        self.sess_lbl = QLabel()
        self.btn_sess_next = IconButton(G_RIGHT, "Следующий источник")
        self.btn_sess_prev.clicked.connect(lambda: module.switch_session(-1))
        self.btn_sess_next.clicked.connect(lambda: module.switch_session(+1))
        src_row.addWidget(self.source_lbl, 1)
        src_row.addWidget(self.btn_sess_prev)
        src_row.addWidget(self.sess_lbl)
        src_row.addWidget(self.btn_sess_next)
        text_col.addWidget(self.title_lbl)
        text_col.addWidget(self.artist_lbl)
        text_col.addLayout(src_row)
        top.addLayout(text_col, 1)
        root.addLayout(top)

        # --- прогресс ---
        self.progress_row = QHBoxLayout()
        self.pos_lbl = QLabel("0:00")
        self.bar = ProgressBar()
        self.dur_lbl = QLabel("0:00")
        self.dur_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.progress_row.addWidget(self.pos_lbl)
        self.progress_row.addWidget(self.bar, 1)
        self.progress_row.addWidget(self.dur_lbl)
        root.addLayout(self.progress_row)

        # --- кнопки ---
        controls = QHBoxLayout()
        self.btn_prev = IconButton(G_PREV, "Назад")
        self.btn_play = IconButton(G_PLAY, "Играть / пауза")
        self.btn_next = IconButton(G_NEXT, "Вперёд")
        self.btn_prev.clicked.connect(lambda: module.command("prev"))
        self.btn_play.clicked.connect(lambda: module.command("toggle"))
        self.btn_next.clicked.connect(lambda: module.command("next"))
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            controls.addWidget(b)
        controls.addStretch(1)
        # --- громкость: значок (клик = выкл. звук), ползунок, проценты ---
        self.btn_vol = IconButton(G_VOL, "Выключить / включить звук")
        self.btn_vol.clicked.connect(module.toggle_mute)
        self.vol_slider = Slider()
        self.vol_slider.setToolTip("Громкость системы (также колесо мыши над виджетом)")
        self.vol_slider.moved.connect(lambda v: module.set_volume(round(v * 100)))
        self.vol_lbl = QLabel("—")
        self.vol_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls.addWidget(self.btn_vol)
        controls.addWidget(self.vol_slider, 1)
        controls.addWidget(self.vol_lbl)
        root.addLayout(controls)

        self.set_scale(1.0)

    # ---------- размеры и стиль ----------

    def base_size(self) -> QSize:
        h = self.BASE_SIZE[1]
        if not self.cfg.get("music.show_progress", True):
            h -= 20
        return QSize(self.BASE_SIZE[0], h)

    def apply_style(self) -> None:
        c = self.palette_colors()
        m = self.px(10)
        self.root.setContentsMargins(m, m, m, self.px(6))
        self.root.setSpacing(self.px(6))
        self.cover.setFixedSize(self.px(64), self.px(64))
        self.cover.setVisible(self.cfg.get("music.show_cover", True))

        self.title_lbl.setFont(self.make_font(1.15, bold=True))
        self.artist_lbl.setFont(self.make_font(1.0))
        self.source_lbl.setFont(self.make_font(0.82))
        self.title_lbl.set_color(c["text"])
        self.artist_lbl.set_color(c["text"])
        self.source_lbl.set_color(c["dim"])
        self.source_lbl.setVisible(self.cfg.get("music.show_source", True))

        small = self.make_font(0.78)
        for lbl in (self.pos_lbl, self.dur_lbl, self.sess_lbl):
            lbl.setFont(small)
            lbl.setStyleSheet(f"color: {theme.css(c['dim'])}; background: transparent;")
        self.pos_lbl.setFixedWidth(self.px(48))
        self.dur_lbl.setFixedWidth(self.px(48))
        self.bar.setFixedHeight(self.px(4))
        self.bar.fill = QColor(c["accent"])
        track = QColor(c["text"])
        track.setAlpha(50)
        self.bar.track = track
        show_progress = self.cfg.get("music.show_progress", True)
        for wdg in (self.pos_lbl, self.bar, self.dur_lbl):
            wdg.setVisible(show_progress)

        radius = self.px(6)
        for b in (self.btn_prev, self.btn_next):
            b.setFont(self.icon_font(1.0))
            b.setFixedSize(self.px(36), self.px(28))
            b.set_colors(c["text"], c["accent"], radius)
        self.btn_play.setFont(self.icon_font(1.25))
        self.btn_play.setFixedSize(self.px(44), self.px(28))
        self.btn_play.set_colors(c["text"], c["accent"], radius)
        for b in (self.btn_sess_prev, self.btn_sess_next):
            b.setFont(self.icon_font(0.65))
            b.setFixedSize(self.px(16), self.px(16))
            b.set_colors(c["dim"], c["accent"], self.px(3))

        show_vol = self.cfg.get("music.show_volume", True)
        self.btn_vol.setFont(self.icon_font(0.9))
        self.btn_vol.setFixedSize(self.px(26), self.px(26))
        self.btn_vol.set_colors(c["text"], c["accent"], radius)
        self.vol_slider.setFixedHeight(self.px(12))
        self.vol_slider.setMinimumWidth(self.px(60))
        self.vol_slider.fill = QColor(c["accent"])
        self.vol_slider.track = track
        self.vol_lbl.setFont(self.make_font(0.8))
        self.vol_lbl.setFixedWidth(self.px(34))
        self.vol_lbl.setStyleSheet(f"color: {theme.css(c['text'])}; background: transparent;")
        for wdg in (self.btn_vol, self.vol_slider, self.vol_lbl):
            wdg.setVisible(show_vol)
        self._render_cover()
        self.update()

    # ---------- данные ----------

    def show_session(self, s: dict | None, index: int, count: int) -> None:
        """Показать сессию s (или «Ничего не играет», если None)."""
        multi = count > 1
        for wdg in (self.btn_sess_prev, self.sess_lbl, self.btn_sess_next):
            wdg.setVisible(multi)
        self.sess_lbl.setText(f"{index + 1}/{count}" if multi else "")

        if s is None:
            self.title_lbl.setText("Ничего не играет")
            self.artist_lbl.setText("")
            self.source_lbl.setText("")
            self.btn_play.setText(G_PLAY)
            for b in (self.btn_prev, self.btn_play, self.btn_next):
                b.setEnabled(False)
            self.set_cover(None, "")
            self.pos_lbl.setText("")
            self.dur_lbl.setText("")
            self.bar.set_value(0)
            return

        self.title_lbl.setText(s["title"] or "Без названия")
        self.artist_lbl.setText(s["artist"] or s["album"] or "")
        browser = source_name(s["aumid"])
        self.source_lbl.setText(f"{s['site']} · {browser}" if s.get("site") else browser)
        self.btn_play.setText(G_PAUSE if s["playing"] else G_PLAY)
        self.btn_prev.setEnabled(s["can_prev"])
        self.btn_next.setEnabled(s["can_next"])
        self.btn_play.setEnabled(s["can_toggle"])
        self.set_cover(s.get("thumb"), s.get("thumb_id", ""))
        self.refresh_progress(s)

    def refresh_progress(self, s: dict | None) -> None:
        if s is None or s["duration"] <= 0:
            self.pos_lbl.setText("")
            self.dur_lbl.setText("")
            self.bar.set_value(0)
            return
        pos = s["position"]
        if s["playing"]:
            pos += time.monotonic() - s["snapshot"]
        pos = min(pos, s["duration"])
        self.pos_lbl.setText(fmt_time(pos))
        self.dur_lbl.setText(fmt_time(s["duration"]))
        self.bar.set_value(pos / s["duration"])

    def show_volume(self, percent: int | None, muted: bool) -> None:
        if percent is None:
            self.vol_lbl.setText("—")
            return
        self.btn_vol.setText(G_MUTE if muted or percent == 0 else G_VOL)
        self.vol_lbl.setText("выкл" if muted else f"{percent}%")
        if not self.vol_slider.dragging:
            self.vol_slider.set_value(percent / 100)

    def set_cover(self, data: bytes | None, cover_id: str) -> None:
        if cover_id == self._cover_id:
            return
        self._cover_id = cover_id
        img = QImage()
        if data and img.loadFromData(data):
            self._cover_img = img
        else:
            self._cover_img = None
        self._update_adaptive_bg()
        self._render_cover()

    def _update_adaptive_bg(self) -> None:
        if self.cfg.get("music.adaptive_bg", False) and self._cover_img is not None:
            bg = dominant_color(self._cover_img)
            text = theme.readable_text(bg)
            self.color_override = {"bg": bg, "text": text, "dim": theme.mix(text, bg, 0.35)}
        else:
            self.color_override = None
        self.apply_style_if_ready()

    def apply_style_if_ready(self) -> None:
        if hasattr(self, "btn_play"):
            self.apply_style()

    def _render_cover(self) -> None:
        if not hasattr(self, "cover"):
            return
        size = self.px(64)
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(size * dpr), round(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        radius = self.px(8)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
        p.setClipPath(path)
        c = self.palette_colors()
        if self._cover_img is not None:
            # «Обрезка по центру»: картинка 16:9 (YouTube) заполняет квадрат
            scaled = self._cover_img.scaled(round(size * dpr), round(size * dpr),
                                            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            w, h = scaled.width() / dpr, scaled.height() / dpr
            p.drawImage(QRectF((size - w) / 2, (size - h) / 2, w, h), scaled)
        else:
            placeholder = QColor(c["accent"])
            placeholder.setAlpha(70)
            p.fillRect(QRectF(0, 0, size, size), placeholder)
            f = QFont("Segoe UI Symbol")
            f.setPixelSize(round(size * 0.5))
            p.setFont(f)
            p.setPen(c["text"])
            p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "♪")
        p.end()
        self.cover.setPixmap(pm)

    # ---------- колесо мыши = громкость ----------

    def wheelEvent(self, e) -> None:
        steps = self.wheel_steps(e)
        if steps:
            self.module.wheel_volume(steps)
        e.accept()


class NowPlayingModule(BaseModule):
    key = "music"
    title = "Сейчас играет"

    def __init__(self, app):
        super().__init__(app)
        self.backend = MediaBackend()
        self.widget = NowPlayingWidget(app, self)
        self.sessions: list[dict] = []
        self.current: dict | None = None
        self._selected_key: tuple | None = None  # выбранный стрелками источник
        self.volume = SystemVolume()
        self.backend.updated.connect(self._on_update)
        self.backend.failed.connect(self._on_failed)

    def widgets(self) -> list:
        return [self.widget]

    def start(self) -> None:
        self.widget.show_session(None, 0, 0)
        self._refresh_volume()
        self._apply_idle()
        self.backend.start()

    def stop(self) -> None:
        self.backend.stop()
        super().stop()

    def tick(self) -> None:
        if self.widget.isVisible():
            self.widget.refresh_progress(self.current)
            if self.cfg.get("music.show_volume", True):
                self._refresh_volume()

    # ---------- громкость ----------

    def _refresh_volume(self) -> None:
        self.widget.show_volume(*self.volume.get())

    def set_volume(self, percent: int) -> None:
        self.volume.set(percent)
        self._refresh_volume()

    def toggle_mute(self) -> None:
        self.volume.toggle_mute()
        self._refresh_volume()

    def wheel_volume(self, steps: int) -> None:
        self.volume.step(steps)
        self._refresh_volume()

    # ---------- данные от фонового потока ----------

    def _on_update(self, sessions: list) -> None:
        try:
            # Сессии без названия и не играющие (например, закрытый плеер) не показываем
            items = [s for s in sessions if s["title"] or s["playing"]]
            counters: dict[str, int] = {}
            for s in items:  # ключ (приложение, номер) различает две вкладки одного браузера
                n = counters.get(s["aumid"], 0)
                counters[s["aumid"]] = n + 1
                s["key"] = (s["aumid"], n)
            self.sessions = items
            idx = self._choose()
            self.current = items[idx] if items else None
            self.widget.show_session(self.current, idx, len(items))
            self._apply_idle()
        except Exception as e:  # noqa: BLE001
            self.report(e, "_on_update")

    def _on_failed(self, message: str) -> None:
        self.widget.title_lbl.setText("Медиа-сессии недоступны")
        self.widget.artist_lbl.setText(message)
        self.widget.set_content_visible(True)

    def _choose(self) -> int:
        """Какую сессию показывать: выбранную вручную → играющую → «текущую» по мнению Windows → первую."""
        if not self.sessions:
            return 0
        if self._selected_key:
            for i, s in enumerate(self.sessions):
                if s["key"] == self._selected_key:
                    return i
            self._selected_key = None  # выбранная пропала
        for i, s in enumerate(self.sessions):
            if s["playing"]:
                return i
        for i, s in enumerate(self.sessions):
            if s["is_current"]:
                return i
        return 0

    def _apply_idle(self) -> None:
        has_track = self.current is not None
        show_text = self.cfg.get("music.when_idle", "hide") == "text"
        self.widget.set_content_visible(has_track or show_text)

    # ---------- действия пользователя ----------

    def switch_session(self, delta: int) -> None:
        if len(self.sessions) < 2 or self.current is None:
            return
        idx = self.sessions.index(self.current)
        new = self.sessions[(idx + delta) % len(self.sessions)]
        self._selected_key = new["key"]
        self.current = new
        self.widget.show_session(new, self.sessions.index(new), len(self.sessions))

    def command(self, action: str) -> None:
        if self.current is None:
            return
        self.backend.command(self.current["index"], action)
        if action == "toggle":  # сразу меняем значок, не дожидаясь ответа плеера
            self.current["position"] = self.current["position"] + (
                time.monotonic() - self.current["snapshot"] if self.current["playing"] else 0)
            self.current["snapshot"] = time.monotonic()
            self.current["playing"] = not self.current["playing"]
            self.widget.btn_play.setText(G_PAUSE if self.current["playing"] else G_PLAY)

    def on_config_changed(self, key: str) -> None:
        if key.startswith("music."):
            if key == "music.adaptive_bg":
                self.widget._update_adaptive_bg()
            self.widget.refresh_size()
            self._apply_idle()
            if not self.widget.has_saved_pos():
                self.app.place_widgets()
