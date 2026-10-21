"""Модуль 3. «Таймер выключения»: расписание + быстрый таймер из трея.

Как это работает:
- Раз в секунду (tick) модуль решает, какое ближайшее «задание» (job): быстрый
  таймер или очередной запуск по расписанию — что раньше.
- За N минут до срабатывания показывается окно с обратным отсчётом.
- Если компьютер спал, когда наступило время, — задание пропускается, а не
  выполняется сразу после пробуждения. Сон определяем по «скачку» часов:
  tick вызывается раз в секунду, и если с прошлого вызова прошло больше 30 с,
  значит программа (вместе с компьютером) спала.
"""

import ctypes
import subprocess
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                               QRadioButton, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)

from .. import theme, winutils
from ..logger import log
from .base_module import BaseModule

ACTIONS = {
    "shutdown": "Выключение",
    "restart": "Перезагрузка",
    "sleep": "Сон",
    "hibernate": "Гибернация",
}
ACTION_VERBS = {
    "shutdown": "будет выключен",
    "restart": "будет перезагружен",
    "sleep": "уйдёт в сон",
    "hibernate": "уйдёт в гибернацию",
}
CREATE_NO_WINDOW = 0x08000000
POSTPONE = timedelta(minutes=30)


@dataclass
class Job:
    kind: str            # "quick" (из трея) или "schedule" (по расписанию)
    when: datetime
    action: str
    key: str = ""        # для расписания — исходное время запуска, по нему запоминаем отмену
    warned: bool = False


def parse_hhmm(text: str) -> dtime | None:
    try:
        h, m = text.split(":")
        return dtime(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def execute_action(action: str) -> None:
    log.info("Выполняю действие: %s", action)
    if action == "shutdown":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=CREATE_NO_WINDOW)
    elif action == "restart":
        subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=CREATE_NO_WINDOW)
    elif action == "hibernate":
        subprocess.Popen(["shutdown", "/h"], creationflags=CREATE_NO_WINDOW)
    elif action == "sleep":
        # SetSuspendState(гибернация=False, принудительно=True, запретить_пробуждение=False)
        ctypes.windll.powrprof.SetSuspendState(False, True, False)


class ShutdownNotice(QWidget):
    """Окно-предупреждение поверх всех окон с обратным отсчётом."""

    def __init__(self, module):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.module = module
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)  # углы окна прозрачные — фон рисует стиль со скруглением
        self.setWindowTitle("DeskWidgets — таймер выключения")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.countdown = QLabel()
        self.countdown.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.title)
        lay.addWidget(self.countdown)
        row = QHBoxLayout()
        self.btn_plus = QPushButton("+30 минут")
        self.btn_cancel = QPushButton("Отменить сегодня")
        self.btn_now = QPushButton("Выключить сейчас")
        self.btn_plus.clicked.connect(module.postpone)
        self.btn_cancel.clicked.connect(module.cancel_current)
        self.btn_now.clicked.connect(module.run_now)
        for b in (self.btn_plus, self.btn_cancel, self.btn_now):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        lay.addLayout(row)

    def apply_colors(self, c: dict) -> None:
        bg, text, accent = c["bg"], c["text"], c["accent"]
        self.setStyleSheet(
            f"ShutdownNotice {{ background: {theme.css(bg, 250)}; border: 2px solid {theme.css(accent)};"
            f" border-radius: 12px; }}"
            f"QLabel {{ color: {theme.css(text)}; background: transparent; }}"
            f"QPushButton {{ color: {theme.css(text)}; background: {theme.css(accent, 50)}; border: none;"
            f" border-radius: 6px; padding: 8px 14px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: {theme.css(accent, 110)}; }}"
        )
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.countdown.setStyleSheet("font-size: 30px; font-weight: 700;")

    def update_info(self, job: Job, remaining: float) -> None:
        verb = ACTION_VERBS.get(job.action, "будет выключен")
        self.title.setText(f"Компьютер {verb} через")
        m, s = divmod(int(max(0, remaining)), 60)
        self.countdown.setText(f"{m:02d}:{s:02d}")
        self.btn_cancel.setText("Отменить сегодня" if job.kind == "schedule" else "Отменить")
        self.btn_now.setText(f"{ACTIONS.get(job.action, 'Выключить')} сейчас")

    def show_centered(self) -> None:
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + area.height() // 3 - self.height() // 2)
        self.show()
        self.raise_()


class QuickTimerDialog(QDialog):
    """«Своё время»: через N минут или в указанное время."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Таймер выключения")
        self.setWindowIcon(theme.app_icon())
        lay = QVBoxLayout(self)
        self.rb_after = QRadioButton("Выключить через")
        self.rb_at = QRadioButton("Выключить в")
        group = QButtonGroup(self)
        group.addButton(self.rb_after)
        group.addButton(self.rb_at)
        self.rb_after.setChecked(True)

        row1 = QHBoxLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 24 * 60)
        self.minutes.setValue(45)
        self.minutes.setSuffix(" мин")
        row1.addWidget(self.rb_after)
        row1.addWidget(self.minutes)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        self.at_time = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.at_time.setDisplayFormat("HH:mm")
        row2.addWidget(self.rb_at)
        row2.addWidget(self.at_time)
        row2.addStretch(1)

        self.minutes.valueChanged.connect(lambda: self.rb_after.setChecked(True))
        self.at_time.timeChanged.connect(lambda: self.rb_at.setChecked(True))
        lay.addLayout(row1)
        lay.addLayout(row2)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def result_datetime(self) -> datetime:
        now = datetime.now()
        if self.rb_after.isChecked():
            return now + timedelta(minutes=self.minutes.value())
        t = self.at_time.time()
        dt = datetime.combine(now.date(), dtime(t.hour(), t.minute()))
        if dt <= now:  # время уже прошло сегодня → завтра
            dt += timedelta(days=1)
        return dt


class ShutdownModule(BaseModule):
    key = "shutdown"
    title = "Таймер выключения"

    def __init__(self, app):
        super().__init__(app)
        self.job: Job | None = None
        self.quick_at: datetime | None = None
        self.notice: ShutdownNotice | None = None
        self._last_wall: datetime | None = None

    # ---------- жизненный цикл ----------

    def stop(self) -> None:
        self._close_notice()

    def on_config_changed(self, key: str) -> None:
        if key.startswith("shutdown.") and key != "shutdown.skip_keys":
            self.job = None  # расписание поменялось — пересчитаем на следующем тике
            self._close_notice()
        elif key.startswith("appearance") and self.notice:
            self.notice.apply_colors(self.app.colors)

    # ---------- расписание ----------

    def _next_occurrence(self, now: datetime) -> datetime | None:
        days = self.cfg.get("shutdown.days", [True] * 7)
        skip = set(self.cfg.get("shutdown.skip_keys", []))
        for d in range(8):
            day = now.date() + timedelta(days=d)
            wd = day.weekday()  # 0 = понедельник
            if not days[wd]:
                continue
            t = parse_hhmm(self.cfg.get("shutdown.weekend_time" if wd >= 5 else "shutdown.weekday_time"))
            if t is None:
                continue
            dt = datetime.combine(day, t)
            if dt <= now or dt.isoformat(timespec="minutes") in skip:
                continue
            return dt
        return None

    def _ensure_job(self, now: datetime) -> Job | None:
        if self.job is None:
            candidates = []
            if self.quick_at:
                candidates.append(Job("quick", self.quick_at, "shutdown"))
            if self.cfg.get("shutdown.schedule_enabled", False):
                occ = self._next_occurrence(now)
                if occ:
                    candidates.append(Job("schedule", occ, self.cfg.get("shutdown.action", "shutdown"),
                                          key=occ.isoformat(timespec="minutes")))
            self.job = min(candidates, key=lambda j: j.when) if candidates else None
        return self.job

    def tick(self) -> None:
        now = datetime.now()
        slept = self._last_wall is not None and (now - self._last_wall).total_seconds() > 30
        self._last_wall = now
        job = self._ensure_job(now)
        if job is None:
            self._close_notice()
            return

        if job.when <= now and (slept or (now - job.when).total_seconds() > 120):
            # Время наступило, пока компьютер спал (или программа «висела») — не выполняем
            log.info("Пропуск задания %s на %s: компьютер спал", job.kind, job.when)
            self._finish(job)
            self.app.tray.notify("Таймер выключения",
                                 f"{ACTIONS[job.action]} в {job.when:%H:%M} пропущено: компьютер был в спящем режиме.")
            return

        remaining = (job.when - now).total_seconds()
        if remaining <= 0:
            if self.cfg.get("shutdown.skip_fullscreen", True) and winutils.any_fullscreen():
                job.when = now + POSTPONE
                job.warned = False
                if job.kind == "quick":
                    self.quick_at = job.when
                self._close_notice()
                self.app.tray.notify("Таймер выключения",
                                     f"Открыто полноэкранное приложение — {ACTIONS[job.action].lower()} "
                                     f"перенесено на {job.when:%H:%M}.")
                return
            self._finish(job)
            execute_action(job.action)
            return

        warn = self.cfg.get("shutdown.warn_minutes", 5) * 60
        if remaining <= warn:
            if self.notice is None:
                self.notice = ShutdownNotice(self)
                self.notice.apply_colors(self.app.colors)
            self.notice.update_info(job, remaining)
            if not job.warned or not self.notice.isVisible():
                job.warned = True
                self.notice.show_centered()
        else:
            self._close_notice()

    def _finish(self, job: Job) -> None:
        if job.kind == "quick":
            self.quick_at = None
        self.job = None
        self._close_notice()

    def _close_notice(self) -> None:
        if self.notice is not None:
            self.notice.hide()

    def _remember_skip(self, key: str) -> None:
        keys = list(self.cfg.get("shutdown.skip_keys", []))
        keys.append(key)
        self.cfg.set("shutdown.skip_keys", keys[-10:])  # храним только последние

    # ---------- кнопки окна-предупреждения ----------

    def postpone(self) -> None:
        if self.job is None:
            return
        self.job.when = max(self.job.when, datetime.now()) + POSTPONE
        self.job.warned = False
        if self.job.kind == "quick":
            self.quick_at = self.job.when
        self._close_notice()
        self.app.tray.notify("Таймер выключения", f"Перенесено на {self.job.when:%H:%M}.")

    def cancel_current(self) -> None:
        job = self.job
        if job is None:
            return
        if job.kind == "schedule":
            self._remember_skip(job.key)
        self._finish(job)
        self.app.tray.notify("Таймер выключения", "Отменено.")

    def run_now(self) -> None:
        job = self.job
        if job is None:
            return
        self._finish(job)
        execute_action(job.action)

    # ---------- трей ----------

    def set_quick_at(self, when: datetime) -> None:
        self.quick_at = when
        self.job = None
        self._close_notice()
        self.app.tray.notify("Таймер выключения", f"Компьютер будет выключен в {when:%H:%M}.")

    def set_quick_in(self, minutes: int) -> None:
        self.set_quick_at(datetime.now() + timedelta(minutes=minutes))

    def ask_custom_time(self) -> None:
        dlg = QuickTimerDialog()
        if dlg.exec() == QDialog.Accepted:
            self.set_quick_at(dlg.result_datetime())

    def cancel_from_tray(self) -> None:
        had_quick = self.quick_at is not None
        self.quick_at = None
        job = self.job
        # Если уже идёт обратный отсчёт по расписанию — «Отменить» отменяет и его
        if job and job.kind == "schedule" and job.warned:
            self._remember_skip(job.key)
            had_quick = True
        self.job = None
        self._close_notice()
        self.app.tray.notify("Таймер выключения", "Таймер отменён." if had_quick else "Быстрый таймер не был запущен.")

    def peek_job(self) -> Job | None:
        """Ближайшее задание (для отсчёта на виджете «Монитор»)."""
        return self._ensure_job(datetime.now())

    def has_pending(self) -> bool:
        return self.quick_at is not None or (self.job is not None and self.job.warned)

    def status_text(self) -> str:
        job = self._ensure_job(datetime.now())
        if job is None:
            return "Выключение не запланировано"
        today = datetime.now().date()
        if job.when.date() == today:
            day = "сегодня"
        elif job.when.date() == today + timedelta(days=1):
            day = "завтра"
        else:
            day = job.when.strftime("%d.%m")
        return f"{ACTIONS[job.action]}: {day} в {job.when:%H:%M}"
