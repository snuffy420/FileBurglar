"""File Burglar window: dark theme with orange and neon-yellow accents.

Views refresh only while the window is visible, and only the open tab reads
the database, so the window costs almost nothing while it is in the tray.
"""

import ctypes
import os
import shutil
import threading
import time
import sqlite3
from collections import OrderedDict
from pathlib import Path
from ctypes import wintypes
from datetime import date, datetime

from PyQt6.QtCore import QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect,
                             QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
                             QMenu, QMessageBox, QPushButton, QSizeGrip, QSizePolicy, QSpinBox, QTabWidget,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QStyle)

import storage
import smart
import settings
from appinfo import APP_NAME, APP_VERSION

ORANGE = "#FF8A1F"
NEON = "#E4FF2E"
BG = "#121212"
PANEL = "#1B1B1B"
EDGE = "#2F2F2F"
TEXT = "#E9E9E9"
MUTED = "#8F8F8F"
RED = "#FF5A5A"
GREEN = "#4CD964"

TILE_MENU_STYLE = f"""
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {EDGE}; border-radius: 8px; padding: 4px; font-family: 'Segoe UI'; font-size: 10pt; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; background: transparent; }}
QMenu::item:selected {{ background: {ORANGE}; color: {BG}; }}
"""

# Panels glow dark yellow at the bottom and fade up into the plain panel colour.
PANEL_GRADIENT = f"qlineargradient(x1: 0, y1: 1, x2: 0, y2: 0, stop: 0 #5A4A0A, stop: 0.55 {PANEL}, stop: 1 {PANEL})"

STYLE = f"""
QWidget {{ color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QWidget#root {{ background: qlineargradient(x1: 0, y1: 1, x2: 0, y2: 0, stop: 0 #7A4A12, stop: 0.2 #3B2609, stop: 0.42 {BG}, stop: 1 {BG}); border: 1px solid {EDGE}; border-radius: 14px; }}
QWidget#titlebar {{ background: transparent; }}
QPushButton#winbtn {{ background: transparent; border: none; color: {MUTED}; padding: 4px 10px; font-size: 12pt; }}
QPushButton#winbtn:hover {{ color: {BG}; background: {ORANGE}; }}
QPushButton#closebtn {{ background: transparent; border: none; color: {MUTED}; padding: 4px 10px; font-size: 12pt; }}
QPushButton#closebtn:hover {{ color: {BG}; background: {ORANGE}; }}
QFrame#card {{ background: {PANEL_GRADIENT}; border: 1px solid {EDGE}; border-radius: 10px; }}
QFrame#tile {{ background: {PANEL_GRADIENT}; border: 1px solid {EDGE}; border-radius: 8px; }}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QLabel#title {{ color: {ORANGE}; font-size: 20pt; font-weight: bold; }}
QLabel#subtitle, QLabel#caption {{ color: {MUTED}; }}
QLabel#cardValue {{ color: {NEON}; font-size: 18pt; font-weight: bold; }}
QPushButton {{ background: #222222; border: 1px solid {ORANGE}; border-radius: 6px; padding: 7px 16px; color: {ORANGE}; }}
QPushButton:hover {{ background: {ORANGE}; color: {BG}; }}
QPushButton:checked {{ background: {NEON}; color: {BG}; border-color: {NEON}; }}
QComboBox, QLineEdit {{ background: #1F1F1F; border: 1px solid {EDGE}; border-radius: 6px; padding: 5px 8px; min-width: 120px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {NEON}; }}
QSpinBox {{ background: #1F1F1F; border: 1px solid {EDGE}; border-radius: 6px; padding: 5px 8px; min-width: 90px; color: {TEXT}; }}
QSpinBox:focus {{ border-color: {NEON}; }}
QCheckBox {{ color: {TEXT}; spacing: 8px; }}
QComboBox QAbstractItemView {{ background: #1F1F1F; selection-background-color: {ORANGE}; selection-color: {BG}; }}
QTabWidget::pane {{ border: 1px solid {EDGE}; border-radius: 10px; background: {PANEL_GRADIENT}; top: -1px; }}
QTabBar::tab {{ background: #181818; color: {MUTED}; padding: 8px 20px; border: 1px solid {EDGE}; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 4px; }}
QTabBar::tab:selected {{ color: {NEON}; background: {PANEL}; border-bottom: 2px solid {ORANGE}; }}
QTableWidget {{ background: transparent; alternate-background-color: rgba(31, 31, 31, 140); gridline-color: transparent; border: none;
    selection-background-color: {ORANGE}; selection-color: {BG}; }}
QHeaderView::section {{ background: #181818; color: {ORANGE}; padding: 7px; border: none;
    border-bottom: 1px solid {ORANGE}; font-weight: bold; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #3A3A3A; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {ORANGE}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #3A3A3A; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {ORANGE}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""

# Pause button: bright neon orange while monitoring, greyed out while paused.
ACTIVE_BUTTON = (
    "QPushButton, QPushButton:hover, QPushButton:checked {"
    " background: #FF7A1A; color: #121212; border: 1px solid #FFB36B; font-weight: bold; padding: 7px 16px; }"
    "QPushButton:hover { background: #FF9A3D; }"
)
PAUSED_BUTTON = (
    "QPushButton, QPushButton:hover, QPushButton:checked {"
    " background: #262626; color: #6E6E6E; border: 1px solid #3A3A3A; font-weight: normal; padding: 7px 16px; }"
)


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040  # send to the Recycle Bin instead of deleting for good


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def recycle(path) -> bool:
    """Send a file or folder to the Recycle Bin. False if Windows refused or the operation was aborted."""
    buf = ctypes.create_unicode_buffer(path + "\0")  # SHFileOperation needs a double-null-terminated list
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted


def is_drive_root(path) -> bool:
    return os.path.dirname(path) == path


def human_bytes(n) -> str:
    value = float(abs(n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024


def signed_bytes(n) -> str:
    sign = "+" if n > 0 else "-" if n < 0 else ""
    return sign + human_bytes(n)


def stamp(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "never"


class SortItem(QTableWidgetItem):
    """Table cell that sorts by a number instead of its text."""

    def __init__(self, text, value, align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter):
        super().__init__(text)
        self.value = value
        self.setTextAlignment(align)

    def __lt__(self, other):
        return self.value < other.value


def cell(text, color=None):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    return item


def make_table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(26)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.setShowGrid(False)
    table.setSortingEnabled(True)
    return table


def fit_columns(table, widths):
    """Open with readable column widths (full paths visible) instead of cut-off ones."""
    for c, width in enumerate(widths):
        table.setColumnWidth(c, width)
    table.horizontalHeader().setStretchLastSection(True)


def fill(table, rows):
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, cells in enumerate(rows):
        for c, item in enumerate(cells):
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


class Card(QFrame):
    def __init__(self, caption):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(2)
        self.value = QLabel("0")
        self.value.setObjectName("cardValue")
        label = QLabel(caption)
        label.setObjectName("caption")
        layout.addWidget(self.value)
        layout.addWidget(label)

    def set(self, text):
        self.value.setText(text)


def sync_drive_combo(combo, drives):
    """Rebuild the drive filter when drives appear or go away, keeping the current choice."""
    wanted = [None] + drives
    if [combo.itemData(i) for i in range(combo.count())] == wanted:
        return
    current = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("All drives", None)
    for drive in drives:
        combo.addItem(drive, drive)
    index = combo.findData(current) if current else 0
    combo.setCurrentIndex(max(index, 0))
    combo.blockSignals(False)


class DriveIcon(QWidget):
    """Small hard-drive glyph, drawn as vector so it stays sharp at any size."""

    def __init__(self):
        super().__init__()
        self.color = QColor(MUTED)
        self.setFixedSize(30, 22)

    def set_color(self, color):
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _event):
        from PyQt6.QtGui import QLinearGradient, QRadialGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(1.5, 2.5, 27, 17)
        grad = QLinearGradient(body.topLeft(), body.bottomLeft())
        grad.setColorAt(0.0, QColor("#3A3A3A"))
        grad.setColorAt(1.0, QColor("#161616"))
        p.setPen(QPen(self.color, 1.5))
        p.setBrush(grad)
        p.drawRoundedRect(body, 4, 4)
        # Drive slot and platter line
        p.setPen(QPen(QColor("#0B0B0B"), 1.4))
        p.drawLine(QPointF(6, 16), QPointF(20, 16))
        # Glowing status LED with a soft halo
        halo = QRadialGradient(QPointF(23.5, 10.5), 5)
        halo.setColorAt(0.0, QColor(self.color.red(), self.color.green(), self.color.blue(), 200))
        halo.setColorAt(1.0, QColor(self.color.red(), self.color.green(), self.color.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(23.5, 10.5), 5, 5)
        p.setBrush(self.color)
        p.drawEllipse(QPointF(23.5, 10.5), 2, 2)
        p.end()


INTERNAL_BUS = ("NVMe", "SATA", "RAID", "SAS")


def drive_kind(disk) -> str:
    """NVME, SSD, HDD or EXT for a SMART record. Empty when the type can't be told."""
    if not disk:
        return ""
    bus = disk.get("Bus")
    if bus not in INTERNAL_BUS:
        return "EXT"
    if bus == "NVMe":
        return "NVME"
    media = disk.get("Media")
    if media in ("SSD", "HDD"):
        return media
    return ""


class DriveTile(QFrame):
    """Compact drive chip for the top strip: glowing icon, letter, state and free space."""

    clicked = pyqtSignal(str)

    def __init__(self, root):
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.root = root
        self.kind = ""
        self.state = "offline"
        self._temp_args = None
        self.setFixedHeight(44)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 12, 4)
        row.setSpacing(10)
        self.icon = DriveIcon()
        self.glow = QGraphicsDropShadowEffect(self.icon)
        self.glow.setBlurRadius(16)
        self.glow.setOffset(0, 0)
        self.icon.setGraphicsEffect(self.glow)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel(root[:2])
        self.space = QLabel()
        self.space.setStyleSheet("font-size: 7.5pt; color: " + ORANGE + ";")
        text.addWidget(self.title)
        text.addWidget(self.space)
        self.temp = QLabel()
        self.temp.setStyleSheet("font-size: 7.5pt;")
        text.addWidget(self.temp)
        row.addWidget(self.icon)
        row.addLayout(text)
        row.addStretch(1)

    def set_temperature(self, celsius, external, limits, health):
        self._temp_args = (celsius, external, limits, health)
        self._render_temp()

    def _render_temp(self):
        """One line reading TYPE - TEMP - HEALTH. Temperature colour follows the warm and hot limits in Options."""
        if self._temp_args is None:
            return
        celsius, external, limits, health = self._temp_args
        health = health or "Unknown"
        health_color = {"healthy": GREEN, "unhealthy": RED}.get(health.lower(), ORANGE)
        parts = []
        if self.kind:
            parts.append(f'<span style="color: {ORANGE}; font-weight: bold;">{self.kind}</span>')
        if celsius is None:
            parts.append(f'<span style="color: {ORANGE};">no temp data</span>')
        else:
            level = settings.heat_class(celsius, external, limits)
            color = {"cool": NEON, "warm": ORANGE, "hot": RED}[level]
            parts.append(f'<span style="color: {color}; font-weight: bold;">{celsius} \u00B0C</span>')
        parts.append(f'<span style="color: {health_color}; font-weight: bold;">{health}</span>')
        sep = f'<span style="color: {MUTED};">  \u2013  </span>'
        self.temp.setText(sep.join(parts))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.root)
        super().mousePressEvent(event)

    def set_kind(self, kind):
        """Remember the drive type (NVME, SSD, HDD or EXT) and show it at the start of the temperature line."""
        self.kind = kind
        self._render_temp()

    def _title_text(self):
        return f"{self.root[:2]}  \u00B7  {self.state.upper()}"

    def update_info(self, state):
        color = {"ready": NEON, "indexing": ORANGE, "error": RED}.get(state, MUTED)
        self.icon.set_color(color)
        self.glow.setColor(QColor(color))
        self.glow.setEnabled(state != "offline")
        self.state = state
        self.title.setText(self._title_text())
        self.title.setStyleSheet(f"font-weight: bold; color: {color};")
        if state == "offline":
            self.space.setText("not connected")
            return
        try:
            usage = shutil.disk_usage(self.root)
        except OSError:
            self.space.setText("space unavailable")
            return
        self.space.setText(f"{human_bytes(usage.free)} free of {human_bytes(usage.total)}")


class Toast(QWidget):
    """Small themed alert that fades in, stays for a few seconds, then fades out. Click it to dismiss."""

    def __init__(self, title, body, color, on_gone):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(330)
        self._on_gone = on_gone
        self._leaving = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("toast")
        panel.setStyleSheet(f"QFrame#toast {{ background: {PANEL_GRADIENT}; border: 1px solid {color}; border-radius: 10px; }}")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(14, 10, 14, 12)
        inner.setSpacing(4)
        head = QLabel(title)
        head.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 10pt;")
        text = QLabel(body)
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {TEXT}; font-size: 9pt;")
        inner.addWidget(head)
        inner.addWidget(text)
        outer.addWidget(panel)
        self.setWindowOpacity(0.0)
        self._fade = self._animate(0.0, 1.0, 350)
        self._fade.start()
        QTimer.singleShot(7000, self._fade_out)

    def _animate(self, start, end, ms):
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        return anim

    def mousePressEvent(self, event):
        self._fade_out()

    def _fade_out(self):
        if self._leaving:
            return
        self._leaving = True
        self._out = self._animate(self.windowOpacity(), 0.0, 400)
        self._out.finished.connect(self._close_now)
        self._out.start()

    def _close_now(self):
        self._on_gone(self)
        self.close()
        self.deleteLater()


class AppDialog(QDialog):
    """Frameless dialog that matches the main window: same gradient, title bar and close button."""

    def __init__(self, parent, title, width=420):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QWidget()
        root.setObjectName("root")
        outer.addWidget(root)
        self.body = QVBoxLayout(root)
        self.body.setContentsMargins(20, 10, 20, 18)
        self.body.setSpacing(10)
        label = QLabel(title.upper())
        label.setObjectName("title")
        label.setStyleSheet(f"font-size: 13pt; color: {ORANGE}; font-weight: bold;")
        bar = TitleBar(self, left=[label])
        bar.max_btn.hide()
        self.body.addWidget(bar)


class OptionsDialog(AppDialog):
    """Temperature limits for internal and external drives. Colours on the tiles follow these values."""

    def __init__(self, parent, limits):
        super().__init__(parent, "Options")
        self.limits = limits
        layout = self.body
        self.spins = {}
        for kind, label in (("internal", "Internal drives"), ("external", "External drives")):
            heading = QLabel(label)
            heading.setStyleSheet(f"color: {NEON}; font-weight: bold;")
            layout.addWidget(heading)
            grid = QGridLayout()
            for row, (key, text) in enumerate((("warm", "Warm above (\u00B0C)"), ("hot", "Hot above (\u00B0C)"))):
                spin = QSpinBox()
                spin.setRange(20, 90)
                spin.setValue(int(limits[kind][key]))
                self.spins[(kind, key)] = spin
                grid.addWidget(QLabel(text), row, 0)
                grid.addWidget(spin, row, 1)
            layout.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

    def _save(self):
        for (kind, key), spin in self.spins.items():
            self.limits[kind][key] = spin.value()
        self.accept()


class DriveDialog(AppDialog):
    """Details for one drive: identity, health and the SMART values, plus a tray toggle."""

    def __init__(self, parent, root, disk, in_tray, on_tray, monitored, on_monitor, error_message="", on_retry=None):
        super().__init__(parent, f"Drive {root[:2]}", width=440)
        layout = self.body
        if error_message:
            error_label = QLabel("Monitoring/indexing error:\n" + error_message)
            error_label.setTextFormat(Qt.TextFormat.PlainText)
            error_label.setWordWrap(True)
            error_label.setStyleSheet(f"color: {RED};")
            error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(error_label)
            if on_retry is not None:
                retry_btn = QPushButton("Retry scan")
                retry_btn.clicked.connect(lambda: on_retry(root))
                retry_btn.clicked.connect(self.accept)
                layout.addWidget(retry_btn)
        if not disk:
            layout.addWidget(QLabel("Windows does not report SMART data for this drive."))
        else:
            rows = [
                ("Name", disk.get("Name") or "unknown"),
                ("Bus", disk.get("Bus") or "unknown"),
                ("Media", disk.get("Media") or "unknown"),
                ("Health", disk.get("Health") or "unknown"),
                ("Temperature", f"{disk['Temperature']} \u00B0C" if disk.get("Temperature") is not None
                 else "no data (permissions or device support)"),
                ("Wear", f"{disk['Wear']} %" if disk.get("Wear") is not None else "no data"),
                ("Power-on hours", f"{disk['PowerOnHours']:,}" if disk.get("PowerOnHours") is not None else "no data"),
            ]
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            for row, (label, value) in enumerate(rows):
                caption = QLabel(label)
                caption.setStyleSheet(f"color: {MUTED};")
                grid.addWidget(caption, row, 0)
                grid.addWidget(QLabel(str(value)), row, 1)
            layout.addLayout(grid)
        self.tray_box = QCheckBox("Show this drive in the tray menu")
        self.tray_box.setChecked(in_tray)
        self.tray_box.toggled.connect(lambda checked: on_tray(root, checked))
        layout.addWidget(self.tray_box)
        self.monitor_box = QCheckBox("Monitor this drive for changes")
        self.monitor_box.setChecked(monitored)
        self.monitor_box.toggled.connect(lambda checked: on_monitor(root, checked))
        layout.addWidget(self.monitor_box)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)


class TitleBar(QWidget):
    """Custom title bar for the frameless window: drag to move, plus minimize and close buttons."""

    def __init__(self, window, left=(), right=()):
        super().__init__()
        self.setObjectName("titlebar")
        self.window = window
        self._drag = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for widget in left:
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        for i, widget in enumerate(right):
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
            if i < len(right) - 1:
                row.addSpacing(10)
        if right:
            row.addSpacing(14)
        min_btn = QPushButton("\u2013")
        min_btn.setObjectName("winbtn")
        min_btn.setToolTip("Minimize")
        min_btn.clicked.connect(window.showMinimized)
        self.max_btn = QPushButton("\u25A1")
        self.max_btn.setObjectName("winbtn")
        self.max_btn.setToolTip("Maximize")
        self.max_btn.clicked.connect(self._toggle_max)
        close_btn = QPushButton("\u2715")
        close_btn.setObjectName("closebtn")
        close_btn.setToolTip("Hide to tray")
        close_btn.clicked.connect(window.close)
        row.addWidget(min_btn)
        row.addWidget(self.max_btn)
        row.addWidget(close_btn)

    def _toggle_max(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        self.max_btn.setToolTip("Restore" if self.window.isMaximized() else "Maximize")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None


class ExplorerWindow(QWidget):
    """Lazy, cancellable index browser; never aggregates the tree on the UI thread."""
    _query_result = pyqtSignal(int, object)
    PAGE_SIZE = 500

    def __init__(self, con):
        super().__init__()
        self.db_path = con.execute("PRAGMA database_list").fetchone()[2]
        self.current = None
        self.offset = 0
        self._busy = False
        self._pending = None
        self._request_id = 0
        self._cancel = threading.Event()
        self._cache = OrderedDict()
        self._item_icons = {
            "drive": self.style().standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon),
            "folder": self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            "file": self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon),
        }
        self.setWindowTitle("File Burglar Explorer")
        self.setWindowIcon(QApplication.instance().windowIcon())
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1040, 680)
        self.setMinimumSize(800, 480)
        self.setStyleSheet(STYLE + """
        QWidget#root { background: #141414; }
        QFrame#browserPanel { background: #1B1B1B; border: 1px solid #303030; border-radius: 10px; }
        QLabel#browserTitle { font-size: 22pt; font-weight: bold; color: #FF8A1F; }
        QPushButton#crumb { border: none; background: transparent; color: #C6C6C6; padding: 5px 8px; }
        QPushButton#crumb:hover { background: #302519; color: #FF8A1F; }
        QPushButton#secondary { border-color: #3A3A3A; color: #C8C8C8; background: #242424; }
        QPushButton:disabled { color: #666666; border-color: #303030; background: #1C1C1C; }
        QTableWidget { alternate-background-color: #202020; selection-background-color: #49321E; selection-color: #FFFFFF; }
        QTableWidget::item { padding: 4px 10px; border: none; }
        QHeaderView::section { color: #AAAAAA; background: #1B1B1B; border-bottom: 1px solid #373737; padding: 12px; }
        """)
        body = QWidget()
        body.setObjectName("root")
        frame = QVBoxLayout(self)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.addWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(24, 8, 24, 16)
        layout.setSpacing(16)
        layout.addWidget(TitleBar(self))
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Explorer")
        title.setObjectName("browserTitle")
        subtitle = QLabel("Your indexed storage, at a glance")
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        heading.addLayout(titles)
        heading.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in this location…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(250)
        heading.addWidget(self.search)
        layout.addLayout(heading)
        nav = QHBoxLayout()
        self.up_btn = QPushButton("↑  Up")
        self.up_btn.setObjectName("secondary")
        self.up_btn.clicked.connect(self._go_up)
        nav.addWidget(self.up_btn)
        self.breadcrumbs = QHBoxLayout()
        self.breadcrumbs.setSpacing(2)
        nav.addLayout(self.breadcrumbs, 1)
        self.reload_btn = QPushButton("Refresh")
        self.reload_btn.setObjectName("secondary")
        self.reload_btn.clicked.connect(lambda: self.load(force=True))
        nav.addWidget(self.reload_btn)
        layout.addLayout(nav)
        card = QFrame()
        card.setObjectName("browserPanel")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        actions = QHBoxLayout()
        self.location_label = QLabel("ALL DRIVES")
        self.location_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.location_label.setStyleSheet("color: #FF8A1F; font-weight: bold; padding: 8px;")
        actions.addWidget(self.location_label)
        actions.addStretch()
        self.new_btn = QPushButton("+  New folder")
        self.rename_btn = QPushButton("Rename")
        self.delete_btn = QPushButton("Delete")
        for btn, handler in ((self.new_btn,self._new_folder), (self.rename_btn,self._rename), (self.delete_btn,self._delete)):
            btn.setObjectName("secondary")
            btn.clicked.connect(handler)
            actions.addWidget(btn)
        card_layout.addLayout(actions)
        self.table = make_table(["Name", "Type", "Total size", "Files"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col, width in ((1,100),(2,150),(3,120)):
            self.table.setColumnWidth(col,width)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.itemDoubleClicked.connect(self._open_item)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        card_layout.addWidget(self.table)
        layout.addWidget(card, 1)
        footer = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("subtitle")
        footer.addWidget(self.status_label, 1)
        self.prev_btn = QPushButton("← Previous")
        self.next_btn = QPushButton("Next →")
        for btn in (self.prev_btn,self.next_btn):
            btn.setObjectName("secondary")
            footer.addWidget(btn)
        self.prev_btn.clicked.connect(lambda: self._page(-1))
        self.next_btn.clicked.connect(lambda: self._page(1))
        layout.addLayout(footer)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._search_changed)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self._query_result.connect(self._receive)
        self._update_buttons()
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

    def _search_changed(self):
        self.offset = 0
        self.load()

    def _navigate(self, path):
        self.current = path
        self.offset = 0
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search_timer.stop()
        self.load()

    def _page(self, direction):
        self.offset = max(0, self.offset + direction * self.PAGE_SIZE)
        self.load()

    def _breadcrumbs(self):
        while self.breadcrumbs.count():
            item = self.breadcrumbs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        paths = [("All drives", None)]
        if self.current:
            parts = []
            path = self.current
            while True:
                parts.append((os.path.basename(path.rstrip("\\")) or path, path))
                parent = os.path.dirname(path)
                if parent == path:
                    break
                path = parent
            paths += list(reversed(parts))[-4:]
        for label, path in paths:
            btn = QPushButton(label)
            btn.setObjectName("crumb")
            btn.setMaximumWidth(160)
            btn.setToolTip(path or "All indexed drives")
            btn.clicked.connect(lambda checked=False, p=path: self._navigate(p))
            self.breadcrumbs.addWidget(btn)
            if (label,path) != paths[-1]:
                sep = QLabel("›")
                sep.setObjectName("subtitle")
                self.breadcrumbs.addWidget(sep)
        self.breadcrumbs.addStretch()

    def load(self, force=False):
        if force:
            self._cache.clear()
        self._request_id += 1
        request = self._request_id
        key = (self.current, self.search.text().strip(), self.offset)
        self._breadcrumbs()
        self.location_label.setText("ALL DRIVES" if self.current is None else self.current)
        self.location_label.setToolTip(self.current or "All indexed drives")
        self.up_btn.setEnabled(self.current is not None)
        self.table.clearSelection()
        self._update_buttons()
        cached = self._cache.get(key)
        if cached and time.monotonic()-cached[0] < 20 and not force:
            self._cache.move_to_end(key)
            self._render(cached[1],cached[2])
            self._pending = None
            if self._busy:
                self._cancel.set()
            return
        self.table.setRowCount(0)
        self.status_label.setText("Loading indexed items…")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        if self._busy:
            self._pending = (request,key)
            self._cancel.set()
        else:
            self._start_query(request,key)

    def _start_query(self, request, key):
        self._busy = True
        cancel = self._cancel = threading.Event()
        def work():
            con = None
            try:
                from indexer import lower_thread_priority
                lower_thread_priority()
                con = sqlite3.connect(Path(self.db_path).as_uri()+"?mode=ro", uri=True, timeout=3)
                con.execute("PRAGMA cache_size=-1024")
                con.set_progress_handler(lambda: int(cancel.is_set()), 1000)
                rows, more = storage.explorer_page(con, *key, limit=self.PAGE_SIZE)
                result = (key,rows,more,None)
            except Exception as exc:
                result = (key,[],False,str(exc))
            finally:
                if con is not None:
                    con.close()
            self._query_result.emit(request,result)
        threading.Thread(target=work,name="fb-explorer",daemon=True).start()

    def _receive(self, request, result):
        self._busy = False
        key,rows,more,error = result
        if request == self._request_id:
            if error:
                self.status_label.setText("Could not load this location. Try Refresh.")
                self.status_label.setToolTip(error)
            else:
                self._cache[key] = (time.monotonic(),rows,more)
                while len(self._cache)>8:
                    self._cache.popitem(last=False)
                if self.isVisible():
                    self._render(rows,more)
        if self._pending:
            request,key = self._pending
            self._pending = None
            self._start_query(request,key)

    def _render(self, entries, more):
        rows = [self._row(path,kind,name,size,count) for kind,path,name,size,count in entries]
        self.table.setUpdatesEnabled(False)
        try:
            fill(self.table,rows)
            self.table.sortByColumn(2,Qt.SortOrder.DescendingOrder)
        finally:
            self.table.setUpdatesEnabled(True)
        first = self.offset+1 if rows else 0
        self.status_label.setText(f"{first:,}–{self.offset+len(rows):,} items  ·  Indexed snapshot" if rows else "No indexed items in this location")
        self.status_label.setToolTip("Refresh to update. Large locations are split into pages to keep memory use low.")
        self.prev_btn.setEnabled(self.offset>0)
        self.next_btn.setEnabled(more)
        self._update_buttons()

    def _show_level(self):
        self.offset = 0
        self.load()

    def _row(self, path, kind, name, size, count):
        name_cell = cell(name, TEXT)
        name_cell.setData(Qt.ItemDataRole.UserRole,(kind,path))
        name_cell.setToolTip(path)
        name_cell.setIcon(self._item_icons[kind])
        return [name_cell,cell(kind.capitalize(), MUTED),SortItem(human_bytes(size),size),
                SortItem(f"{count:,}",count) if count is not None else SortItem("—",0)]

    def _selected(self):
        """(kind, path) for each selected row."""
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def _update_buttons(self):
        selected = self._selected()
        only_one = len(selected) == 1
        self.new_btn.setEnabled(self.current is not None)
        self.rename_btn.setEnabled(only_one and not is_drive_root(selected[0][1]))
        self.delete_btn.setEnabled(any(not is_drive_root(path) for _kind, path in selected))

    def _open_item(self, item):
        kind, path = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if kind in ("folder", "drive"):
            self._navigate(path)
        else:
            try:
                os.startfile(path)
            except OSError as err:
                self._fail(err)

    def _go_up(self):
        if self.current is None:
            return
        parent = os.path.dirname(self.current)
        self._navigate(None if parent == self.current else parent)

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            os.mkdir(os.path.join(self.current, name))
        except OSError as err:
            self._fail(err)
            return
        self._changed()

    def _rename(self):
        _kind, path = self._selected()[0]
        old = os.path.basename(path)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old)
        name = name.strip()
        if not ok or not name or name == old:
            return
        try:
            os.rename(path, os.path.join(os.path.dirname(path), name))
        except OSError as err:
            self._fail(err)
            return
        self._changed()

    def _delete(self):
        targets = [path for _kind, path in self._selected() if not is_drive_root(path)]
        if not targets:
            return
        answer = QMessageBox.question(
            self, "Delete", f"Send {len(targets)} item(s) to the Recycle Bin?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        failed = [path for path in targets if not recycle(path)]
        if failed:
            QMessageBox.warning(self, "Delete", "Could not delete:\n" + "\n".join(failed))
        self._changed()

    def _changed(self):
        """Reload now, and again once the watcher has seen the change."""
        self.load(force=True)
        QTimer.singleShot(2000, lambda: self.load(force=True) if self.isVisible() else None)

    def _fail(self, err):
        QMessageBox.warning(self, "File Burglar", str(err))

    def closeEvent(self, event):
        # Cancel hidden work; the window itself can be reopened from the tray app.
        self._request_id += 1
        self._pending = None
        self._cancel.set()
        event.ignore()
        self.hide()


class MainWindow(QMainWindow):
    _smart_result = pyqtSignal(object)
    drives_changed = pyqtSignal()  # emitted on the UI thread when the SMART map or the tray set changes

    def __init__(self, engine, db_path):
        super().__init__()
        self.engine = engine
        self.con = storage.connect(db_path)  # read side for the report and log, used on the UI thread
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1200, 780)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(STYLE)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22, 10, 22, 8)
        outer.setSpacing(14)

        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(32, 32)
        title = QLabel(APP_NAME.upper())
        title.setObjectName("title")
        version = QLabel(APP_VERSION)
        version.setObjectName("subtitle")
        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        brand_row.addWidget(self.brand_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_row.addWidget(version, 0, Qt.AlignmentFlag.AlignVCenter)
        self.pause_btn = QPushButton()
        self.pause_btn.setCheckable(True)
        self.pause_btn.setMinimumWidth(190)
        self.pause_btn.toggled.connect(self._toggle_pause)
        self._style_pause(False)

        self.explorer_btn = QPushButton("Explorer")
        self.explorer_btn.clicked.connect(self._open_explorer)
        self.explorer = None
        self.about_btn = QPushButton("About")
        self.about_btn.clicked.connect(self._open_about)
        self.options_btn = QPushButton("Options")
        self.options_btn.clicked.connect(self._open_options)
        outer.addWidget(TitleBar(self, left=[brand], right=[self.explorer_btn, self.options_btn, self.about_btn, self.pause_btn]))

        # Drive tiles share one full-width row and stretch to fit, so nothing scrolls.
        self.tiles = {}
        self.strip = QHBoxLayout()
        self.strip.setSpacing(8)
        outer.addLayout(self.strip)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.card_drives = Card("drives online")
        self.card_files = Card("files indexed")
        self.card_size = Card("indexed size")
        self.card_today = Card("changes today")
        self.card_held = Card("waiting to apply")
        for card in (self.card_drives, self.card_files, self.card_size, self.card_today, self.card_held):
            cards.addWidget(card)
        outer.addLayout(cards)
        self.scan_status = QLabel()
        self.scan_status.setObjectName("subtitle")
        outer.addWidget(self.scan_status)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._drives_tab(), "Drives")
        self.tabs.addTab(self._report_tab(), "Folder report")
        self.tabs.addTab(self._log_tab(), "Activity log")
        self.tabs.currentChanged.connect(lambda _i: self.refresh())
        outer.addWidget(self.tabs, 1)

        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        outer.addLayout(grip_row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self._disks = {}
        self._limits = settings.load()
        self._smart_busy = False
        self._heat_levels = {}
        self._toasts = []
        self._smart_result.connect(self._store_smart)
        self.smart_timer = QTimer(self)
        self.smart_timer.timeout.connect(self._poll_smart)
        self.smart_timer.start(60000)
        self._poll_smart()
        self.refresh()

    def _filter_row(self, *widgets):
        bar = QHBoxLayout()
        bar.setSpacing(10)
        for widget in widgets:
            bar.addWidget(widget)
        bar.addStretch(1)
        return bar

    def _drives_tab(self):
        self.drives_table = make_table(["Drive", "Status", "Files", "Size", "Last scan", "Last change", "Waiting", "Watch"])
        # Short columns keep a fixed width; the time and watch columns share the rest, so nothing scrolls.
        header = self.drives_table.horizontalHeader()
        for col, width in ((0, 80), (1, 130), (2, 110), (3, 120), (6, 100)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.drives_table.setColumnWidth(col, width)
        for col in (4, 5, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addWidget(self.drives_table)
        return page

    def _report_tab(self):
        self.period = QComboBox()
        for label, days in (("Today", 0), ("Last 7 days", 7), ("Last 30 days", 30), ("All time", None)):
            self.period.addItem(label, days)
        self.report_drive = QComboBox()
        self.report_drive.addItem("All drives", None)
        self.period.currentIndexChanged.connect(lambda _i: self._refresh_report())
        self.report_drive.currentIndexChanged.connect(lambda _i: self._refresh_report())
        self.report_table = make_table(["Folder", "Changes", "Written", "Freed", "Last change"])
        fit_columns(self.report_table, [780, 110, 130, 130, 180])
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addLayout(self._filter_row(QLabel("Period"), self.period, QLabel("Drive"), self.report_drive))
        layout.addWidget(self.report_table)
        return page

    def _log_tab(self):
        self.log_drive = QComboBox()
        self.log_drive.addItem("All drives", None)
        self.log_action = QComboBox()
        for label, value in (("All changes", None), ("Added", "added"), ("Changed", "changed"),
                             ("Removed", "removed")):
            self.log_action.addItem(label, value)
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("Filter by path")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.setMinimumWidth(260)
        self.log_drive.currentIndexChanged.connect(lambda _i: self._refresh_log())
        self.log_action.currentIndexChanged.connect(lambda _i: self._refresh_log())
        self.log_search.textChanged.connect(lambda _t: self._refresh_log())
        self.log_table = make_table(["Time", "Drive", "Path", "Change", "Size", "Action"])
        fit_columns(self.log_table, [160, 80, 760, 130, 120])
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.addLayout(self._filter_row(QLabel("Drive"), self.log_drive, QLabel("Action"),
                                          self.log_action, self.log_search))
        layout.addWidget(self.log_table)
        return page

    def _open_about(self):
        from about_dialog import AboutDialog
        dialog = AboutDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _open_explorer(self):
        if self.explorer is None:
            self.explorer = ExplorerWindow(self.con)
        self.explorer.show()
        self.explorer.load()
        self.explorer.raise_()
        self.explorer.activateWindow()

    def _toggle_pause(self, paused):
        self.engine.set_paused(paused)
        self._style_pause(paused)

    def _style_pause(self, paused):
        btn = self.pause_btn
        btn.setText("Enable Monitoring" if paused else "Pause monitoring")
        if paused:
            btn.setStyleSheet(PAUSED_BUTTON)
            btn.setGraphicsEffect(None)
        else:
            btn.setStyleSheet(ACTIVE_BUTTON)
            glow = QGraphicsDropShadowEffect(btn)
            glow.setBlurRadius(30)
            glow.setOffset(0, 0)
            glow.setColor(QColor(255, 122, 26, 230))
            btn.setGraphicsEffect(glow)

    def _refresh_strip(self, snap):
        for root in [r for r in self.tiles if r not in snap]:
            tile = self.tiles.pop(root)
            self.strip.removeWidget(tile)
            tile.deleteLater()
        for root in sorted(snap):
            if root not in self.tiles:
                tile = DriveTile(root)
                tile.clicked.connect(self._open_drive)
                tile.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                tile.customContextMenuRequested.connect(
                    lambda pos, r=root, t=tile: self._show_tile_menu(r, t.mapToGlobal(pos)))
                self.tiles[root] = tile
                self.strip.addWidget(tile)
            self.tiles[root].update_info(snap[root]["state"])
            disk = self._disks.get(root[:2])
            self.tiles[root].set_kind(drive_kind(disk))
            external = bool(disk) and disk.get("Bus") not in ("NVMe", "SATA", "RAID", "SAS")
            self.tiles[root].set_temperature(disk.get("Temperature") if disk else None, external, self._limits,
                                             disk.get("Health") if disk else None)

    def _open_options(self):
        dialog = OptionsDialog(self, self._limits)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            settings.save(self._limits)
            self.refresh()

    def _open_drive(self, root):
        disk = self._disks.get(root[:2])
        info = self.engine.snapshot().get(root, {})
        dialog = DriveDialog(self, root, disk, root[:2] in self._limits["tray_drives"], self._set_tray_drive,
                             root[:2] not in self._limits["unmonitored_drives"], self._set_monitored,
                             error_message=info.get("message", "") if info.get("state") == "error" else "",
                             on_retry=self.engine.retry_drive)
        dialog.exec()

    def _show_tile_menu(self, root, global_pos):
        # Right-click on a drive tile: remove it from the tray and/or stop monitoring it.
        in_tray = root[:2] in self._limits["tray_drives"]
        monitored = root[:2] not in self._limits["unmonitored_drives"]
        menu = QMenu(self)
        menu.setStyleSheet(TILE_MENU_STYLE)
        tray_action = QAction("Remove from tray" if in_tray else "Show in tray", menu)
        tray_action.triggered.connect(lambda: self._remove_from_tray(root) if in_tray else self._set_tray_drive(root, True))
        menu.addAction(tray_action)
        monitor_action = QAction("Stop monitoring" if monitored else "Start monitoring", menu)
        monitor_action.triggered.connect(lambda: self._set_monitored(root, not monitored))
        menu.addAction(monitor_action)
        menu.exec(global_pos)

    def _remove_from_tray(self, root):
        # Tray visibility does not change drive monitoring.
        self._set_tray_drive(root, False)

    def _set_tray_drive(self, root, enabled):
        letters = [x for x in self._limits["tray_drives"] if x != root[:2]]
        if enabled:
            letters.append(root[:2])
        self._limits["tray_drives"] = letters
        settings.save(self._limits)
        self.drives_changed.emit()

    def _set_monitored(self, root, enabled):
        letters = [x for x in self._limits["unmonitored_drives"] if x != root[:2]]
        if not enabled:
            letters.append(root[:2])
        self._limits["unmonitored_drives"] = letters
        settings.save(self._limits)
        self.drives_changed.emit()

    def _poll_smart(self):
        # PowerShell takes a moment, so it runs on a worker thread and the table only reads the last result.
        if self._smart_busy:
            return
        self._smart_busy = True

        def work():
            disks = smart.letters_map()
            self._smart_result.emit(disks)

        threading.Thread(target=work, daemon=True).start()

    def _store_smart(self, disks):
        self._disks = disks
        self._smart_busy = False
        self._check_heat(disks)
        self.drives_changed.emit()

    def _check_heat(self, disks):
        """Alert once each time a drive rises into warm or hot, not on every poll."""
        rank = {"cool": 0, "warm": 1, "hot": 2}
        for letter, disk in disks.items():
            celsius = disk.get("Temperature")
            if celsius is None:
                self._heat_levels.pop(letter, None)
                continue
            external = disk.get("Bus") not in ("NVMe", "SATA", "RAID", "SAS")
            level = settings.heat_class(celsius, external, self._limits)
            previous = self._heat_levels.get(letter, "cool")
            self._heat_levels[letter] = level
            if level != "cool" and rank[level] > rank[previous]:
                self._show_alert(letter, level, celsius)

    def _show_alert(self, letter, level, celsius):
        color = RED if level == "hot" else ORANGE
        if level == "hot":
            title = f"DRIVE {letter} IS HOT"
            body = f"{celsius} \u00B0C. Check airflow and cooling before it damages the drive."
        else:
            title = f"DRIVE {letter} IS GETTING WARM"
            body = f"{celsius} \u00B0C. It is above the warm limit set in Options."
        toast = Toast(title, body, color, self._toast_gone)
        self._toasts.append(toast)
        self._place_toasts()
        toast.show()

    def _place_toasts(self):
        """Stack alerts in the bottom-right corner, newest at the bottom."""
        area = self.screen().availableGeometry()
        y = area.bottom() - 16
        for toast in reversed(self._toasts):
            toast.adjustSize()
            y -= toast.height()
            toast.move(area.right() - toast.width() - 16, y)
            y -= 10

    def _toast_gone(self, toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._place_toasts()

    def refresh(self):
        if not self.isVisible() or self.isMinimized():
            return
        snap = self.engine.snapshot()
        self._refresh_cards(snap)
        self._refresh_strip(snap)
        self._refresh_drives(snap)
        drives = sorted({root[:2] for root in snap})
        sync_drive_combo(self.report_drive, drives)
        sync_drive_combo(self.log_drive, drives)
        index = self.tabs.currentIndex()
        if index == 1:
            self._refresh_report()
        elif index == 2:
            self._refresh_log()

    def _refresh_cards(self, snap):
        ready = [s for s in snap.values() if s["state"] in ("ready", "indexing")]
        online = [s for s in snap.values() if s["state"] in ("ready", "indexing")]
        today = self.con.execute(
            "SELECT COALESCE(SUM(events), 0) FROM folder_daily WHERE day = ?", (date.today().isoformat(),)
        ).fetchone()[0]
        self.card_drives.set(f"{len(online)} of {len(snap)}")
        self.card_files.set(f"{sum(s['files'] for s in ready):,}")
        self.card_size.set(human_bytes(sum(s["bytes"] for s in ready)))
        self.card_today.set(f"{today:,}")
        self.card_held.set(f"{sum(s['held'] for s in snap.values()):,}")
        scanning = [s for s in snap.values() if s["state"] == "indexing"]
        if scanning:
            mode = "Fast startup scan" if any(s.get("scan_mode") == "startup" for s in scanning) else "Background scan"
            checked = sum(s.get("scanned_files",0) for s in scanning)
            self.scan_status.setText(f"{mode}  ·  {len(scanning)} drive(s)  ·  {checked:,} files checked")
        else:
            pending = sum(s.get("folder_updates",0) for s in snap.values())
            self.scan_status.setText("Monitoring paused" if self.engine.paused else
                                    f"Live monitoring · updating {pending} changed folder(s)" if pending else
                                    "Live monitoring · no drive scan in progress")

    def _refresh_drives(self, snap):
        colors = {"ready": NEON, "indexing": ORANGE, "error": RED, "offline": MUTED}
        rows = []
        for root in sorted(snap):
            s = snap[root]
            status = s["state"].upper()  # the error text lives in the tooltip so the column stays narrow
            paused = root in self.engine.paused_drives
            status_cell = cell(status, colors.get(s["state"], TEXT))
            status_cell.setToolTip(s["message"] or status)
            rows.append([
                cell(root[:2], TEXT),
                status_cell,
                SortItem(f"{s['files']:,}", s["files"]),
                SortItem(human_bytes(s["bytes"]), s["bytes"]),
                cell(stamp(s["last_scan"])),
                cell(stamp(s["last_event"])),
                SortItem(f"{s['held']:,}", s["held"]),
                cell("Paused" if paused else "Watching", MUTED if paused else NEON),
            ])
        fill(self.drives_table, rows)

    def _refresh_report(self):
        days = self.period.currentData()
        drive = self.report_drive.currentData()
        sql = ("SELECT folder, SUM(events), SUM(added), SUM(freed), MAX(last_ts) FROM folder_daily "
               "WHERE day >= ?")
        args = [storage.day_start(days)]
        if drive:
            sql += " AND drive = ?"
            args.append(drive)
        sql += " GROUP BY folder ORDER BY SUM(added) + SUM(freed) DESC LIMIT 300"
        rows = []
        for folder, events, added, freed, last in self.con.execute(sql, args).fetchall():
            rows.append([
                cell(folder, TEXT),
                SortItem(f"{events:,}", events),
                SortItem(human_bytes(added), added),
                SortItem(human_bytes(freed), freed),
                cell(stamp(last)),
            ])
        fill(self.report_table, rows)

    def _refresh_log(self):
        drive = self.log_drive.currentData()
        action = self.log_action.currentData()
        text = self.log_search.text().strip()
        sql = "SELECT ts, path, action, delta, size FROM file_log WHERE 1 = 1"
        args = []
        if drive:
            lo, hi = storage.drive_range(drive + "\\")
            sql += " AND path >= ? AND path < ?"
            args += [lo, hi]
        if action:
            sql += " AND action = ?"
            args.append(action)
        if text:
            sql += " AND path LIKE ?"
            args.append(f"%{text}%")
        sql += " ORDER BY id DESC LIMIT 500"
        colors = {"added": NEON, "changed": TEXT, "removed": ORANGE}
        rows = []
        for ts, path, act, delta, _size in self.con.execute(sql, args).fetchall():
            rows.append([
                cell(stamp(ts)),
                cell(path[:2], TEXT),
                cell(path, TEXT),
                SortItem(signed_bytes(delta), delta),
                SortItem(human_bytes(_size), _size),
                cell(act.capitalize(), colors.get(act, TEXT)),
            ])
        fill(self.log_table, rows)

    def closeEvent(self, event):
        # Closing the window only hides it; the tray icon keeps monitoring.
        event.ignore()
        self.hide()
