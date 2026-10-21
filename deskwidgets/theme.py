"""Цвета, темы, пресеты оформления и иконка программы (рисуется кодом, файл не нужен)."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap

from . import winutils

# Ключи оформления, которые сохраняются в пресет
PRESET_KEYS = ["theme", "custom_bg", "bg_opacity", "hover_opacity", "radius", "border",
               "font_family", "font_size", "text_color", "accent_color"]

BUILTIN_PRESETS: dict[str, dict] = {
    "Минимализм": {
        "theme": "system", "custom_bg": "#FFFFFF", "bg_opacity": 70, "hover_opacity": 15,
        "radius": 8, "border": False, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "", "accent_color": "#607D8B",
    },
    "Тёмный прозрачный": {
        "theme": "dark", "custom_bg": "#000000", "bg_opacity": 45, "hover_opacity": 10,
        "radius": 14, "border": False, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "#FFFFFF", "accent_color": "#80CBC4",
    },
    "Геймерский": {
        "theme": "custom", "custom_bg": "#0D0221", "bg_opacity": 88, "hover_opacity": 20,
        "radius": 4, "border": True, "font_family": "Consolas", "font_size": 10,
        "text_color": "#E0FFE8", "accent_color": "#00FF9C",
    },
    "Стекло": {
        "theme": "light", "custom_bg": "#FFFFFF", "bg_opacity": 55, "hover_opacity": 15,
        "radius": 16, "border": True, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "", "accent_color": "#0078D4",
    },
    "Океан": {
        "theme": "custom", "custom_bg": "#0B2638", "bg_opacity": 85, "hover_opacity": 15,
        "radius": 12, "border": True, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "#E3F6FF", "accent_color": "#2EC4F1",
    },
    "Лес": {
        "theme": "custom", "custom_bg": "#13261A", "bg_opacity": 85, "hover_opacity": 15,
        "radius": 12, "border": False, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "#E8F5E9", "accent_color": "#7CD992",
    },
    "Закат": {
        "theme": "custom", "custom_bg": "#2B1420", "bg_opacity": 88, "hover_opacity": 15,
        "radius": 14, "border": True, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "#FFEDE6", "accent_color": "#FF8A5B",
    },
    "Неон": {
        "theme": "custom", "custom_bg": "#120024", "bg_opacity": 90, "hover_opacity": 20,
        "radius": 10, "border": True, "font_family": "Segoe UI Semibold", "font_size": 10,
        "text_color": "#F8E9FF", "accent_color": "#FF2BD6",
    },
    "Чёрный": {
        "theme": "custom", "custom_bg": "#000000", "bg_opacity": 95, "hover_opacity": 12,
        "radius": 10, "border": False, "font_family": "Segoe UI", "font_size": 10,
        "text_color": "#FFFFFF", "accent_color": "#FFFFFF",
    },
}


def luminance(c: QColor) -> float:
    """Яркость цвета 0..1 (по формуле восприятия: зелёный кажется ярче синего)."""
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Смесь двух цветов: t=0 → a, t=1 → b."""
    return QColor(round(a.red() + (b.red() - a.red()) * t),
                  round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def readable_text(bg: QColor) -> QColor:
    return QColor("#111111") if luminance(bg) > 0.55 else QColor("#F5F5F5")


def resolve_colors(appearance: dict) -> dict[str, QColor]:
    """Превращает настройки «Внешнего вида» в конкретные цвета для рисования."""
    theme = appearance.get("theme", "dark")
    if theme == "system":
        theme = "dark" if winutils.system_uses_dark_theme() else "light"
    if theme == "light":
        bg, text = QColor("#F4F4F4"), QColor("#1B1B1B")
    elif theme == "custom":
        bg = QColor(appearance.get("custom_bg") or "#1F2A36")
        text = readable_text(bg)
    else:
        bg, text = QColor("#1C1C1E"), QColor("#F2F2F2")
    if appearance.get("text_color"):
        c = QColor(appearance["text_color"])
        if c.isValid():
            text = c
    accent = QColor(appearance.get("accent_color") or "#4FC3F7")
    if not accent.isValid():
        accent = QColor("#4FC3F7")
    return make_palette(bg, text, accent)


def make_palette(bg: QColor, text: QColor, accent: QColor) -> dict[str, QColor]:
    light = luminance(bg) > 0.55
    return {
        "bg": bg,
        "text": text,
        "dim": mix(text, bg, 0.38),          # второстепенный текст
        "accent": accent,
        "warn": QColor("#B78100") if light else QColor("#FFD54F"),
        "crit": QColor("#D32F2F") if light else QColor("#FF6E6E"),
        "ok": QColor("#2E9E4F") if light else QColor("#5BD27A"),
        "off": QColor("#8A8A8A"),
    }


def css(c: QColor, alpha: int | None = None) -> str:
    """QColor → строка для таблиц стилей Qt (rgba(...))."""
    a = c.alpha() if alpha is None else alpha
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


# ---------- иконка ----------

def paint_icon(size: int) -> QPixmap:
    """Иконка: синий скруглённый квадрат с тремя «виджетами»-полосками."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#4FC3F7"))
    grad.setColorAt(1, QColor("#1565C0"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    r = size * 0.22
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    p.setBrush(QColor(255, 255, 255, 235))
    m = size * 0.2
    h = size * 0.14
    widths = [0.6, 0.45, 0.6]
    for i, w in enumerate(widths):
        y = m + i * (h + size * 0.08)
        p.drawRoundedRect(QRectF(m, y, (size - 2 * m) * w / 0.6, h), h / 2, h / 2)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(paint_icon(s))
    return icon
