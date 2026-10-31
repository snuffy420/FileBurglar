"""File Burglar entry point: single instance, tray icon, autostart and wiring."""

import ctypes
import os
import sys
import subprocess
import sqlite3
from ctypes import wintypes

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

import settings
import storage
import startup
from appinfo import APP_NAME, AUTOSTART_VALUE, MUTEX_NAME
from engine import Engine
from ui import MainWindow, ORANGE, NEON, RED, BG, PANEL, EDGE, TEXT, MUTED
from watcher import DriveManager

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ERROR_ALREADY_EXISTS = 183
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

_mutex_handle = None  # kept alive for the life of the process


def take_single_instance() -> bool:
    """Return False if another File Burglar already owns the named mutex."""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not _mutex_handle:
        raise ctypes.WinError()
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_as_admin() -> bool:
    """Start this program again with administrator rights. Returns False if the user declined the UAC prompt."""
    arguments = sys.argv[1:] if getattr(sys, "frozen", False) else [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    args = subprocess.list2cmdline(arguments)
    ctypes.windll.shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, os.getcwd(), 1)
    return bool(result and result > 32)  # ShellExecute returns a value above 32 on success


def lower_process_priority() -> None:
    # The app should never compete with games or other work for the CPU.
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--background"])
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    script = os.path.abspath(__file__)
    return subprocess.list2cmdline([pythonw, script, "--background"])


def autostart_enabled() -> bool:
    return startup.enabled()


def startup_target():
    if getattr(sys, "frozen", False):
        return sys.executable, ["--background"]
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pythonw if os.path.isfile(pythonw) else sys.executable), [os.path.abspath(__file__), "--background"]


def set_autostart(enabled: bool) -> None:
    startup.set_enabled(enabled, *startup_target())


def _draw_icon(size: int) -> QPixmap:
    """Large, high-contrast burglar magnifier, drawn specifically for each DPI."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64.0, size / 64.0)
    p.setPen(Qt.PenStyle.NoPen)
    # A dark lens gives the face contrast on both light and dark taskbars.
    p.setBrush(QColor("#141B24"))
    p.drawEllipse(QRectF(4, 4, 45, 45))
    # Wide orange handle with a dark edge to preserve its silhouette.
    p.setPen(QPen(QColor("#141B24"), 13, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(42, 42), QPointF(57, 57))
    p.setPen(QPen(QColor("#FF9B21"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(42, 42), QPointF(57, 57))
    p.setPen(Qt.PenStyle.NoPen)
    # Face and stocking cap, all contained inside the glass.
    p.setBrush(QColor("#FFE0B2"))
    p.drawRoundedRect(QRectF(14, 17, 26, 24), 11, 11)
    p.setBrush(QColor("#5B697B"))
    cap = QPainterPath()
    cap.moveTo(13, 23)
    cap.cubicTo(12, 5, 36, 7, 39, 20)
    cap.lineTo(40, 23)
    cap.closeSubpath()
    p.drawPath(cap)
    p.setBrush(QColor("#A8B5C5"))
    p.drawRoundedRect(QRectF(12, 19, 29, 5), 2, 2)
    # Oversized black domino mask and bright eyes remain readable at 16px.
    p.setBrush(QColor("#111923"))
    p.drawRoundedRect(QRectF(12, 25, 30, 10), 5, 5)
    p.setBrush(QColor("#F6FAFF"))
    p.drawEllipse(QRectF(17, 28, 7, 4))
    p.drawEllipse(QRectF(30, 28, 7, 4))
    if size >= 32:
        p.setPen(QPen(QColor("#825032"), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(24, 37), QPointF(30, 37))
    # The lens dominates the mark instead of sitting inside a second border.
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("#FF9B21"), 4.5))
    p.drawEllipse(QRectF(4, 4, 45, 45))
    if size >= 32:
        p.setPen(QPen(QColor("#FFD18A"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(7, 7, 39, 39), 45 * 16, 90 * 16)
    p.end()
    return pixmap


def tray_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):  # one frame per size keeps the tray icon crisp at any DPI
        icon.addPixmap(_draw_icon(size))
    return icon


def _draw_temp_icon(size: int, celsius, color: str) -> QPixmap:
    """Same badge as the app icon, with the drive temperature in the centre."""
    u = size / 64.0
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rim = max(1.0, 2.5 * u)
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#2E2E2E"))
    grad.setColorAt(1.0, QColor(BG))
    p.setBrush(QBrush(grad))
    p.setPen(QPen(QColor(ORANGE), rim))
    p.drawRoundedRect(QRectF(rim, rim, size - 2 * rim, size - 2 * rim), 14 * u, 14 * u)
    p.setPen(QColor(color))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(max(8, int(36 * u)))
    p.setFont(font)
    text = "--" if celsius is None else f"{celsius}"
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return pixmap


def temp_icon(celsius, color: str) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        icon.addPixmap(_draw_temp_icon(size, celsius, color))
    return icon


# Tray menus use the app's panel colours, orange hover and muted disabled text.
MENU_STYLE = f"""
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid {EDGE}; border-radius: 8px; padding: 4px; font-family: 'Segoe UI'; font-size: 10pt; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; background: transparent; }}
QMenu::item:selected {{ background: {ORANGE}; color: {BG}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::item:disabled:selected {{ background: transparent; color: {MUTED}; }}
QMenu::separator {{ height: 1px; background: {EDGE}; margin: 4px 8px; }}
"""


class Tray:
    def __init__(self, app, window, engine):
        self.app = app
        self.window = window
        window.tray=self
        self.engine = engine
        self.icon = QSystemTrayIcon(tray_icon(), app)
        self.icon.setToolTip(APP_NAME)
        menu = QMenu()
        open_action = QAction("Open File Burglar", menu)
        open_action.triggered.connect(self.show_window)
        menu.addAction(open_action)
        about_action = QAction("About / Help / Donate", menu)
        about_action.triggered.connect(window._open_about)
        menu.addAction(about_action)
        update_action=menu.addAction('Check for updates')
        update_action.triggered.connect(lambda:window.features.check_updates(True))
        self.work_action=menu.addAction('Game/work mode')
        self.work_action.setCheckable(True);self.work_action.setChecked(window._limits.get('game_mode',False))
        self.work_action.toggled.connect(window.features.work_button.setChecked)
        window.features.work_button.toggled.connect(self.work_action.setChecked)
        self.pause_action = QAction("Pause monitoring", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._toggle_pause)
        menu.addAction(self.pause_action)
        self.autostart_action = QAction("Start with Windows", menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(autostart_enabled())
        self.autostart_action.toggled.connect(self._set_autostart)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        self.drive_actions = []
        self.drive_icons = {}  # one tray icon per selected drive, keyed like 'E:'
        self.drive_menus = {}  # each drive icon's right-click menu, kept alive here
        self.drive_anchor = QAction("", menu)
        self.drive_anchor.setVisible(False)
        menu.addAction(self.drive_anchor)
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)
        self.menu = menu
        menu.setStyleSheet(MENU_STYLE)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()
        window.pause_btn.toggled.connect(self._sync_pause_from_window)
        window.drives_changed.connect(self.refresh_drives)
        self.refresh_drives()

    def _set_autostart(self, enabled):
        try:
            set_autostart(enabled)
        except (OSError, subprocess.SubprocessError) as error:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(not enabled)
            self.autostart_action.blockSignals(False)
            QMessageBox.warning(self.window, APP_NAME, str(error))

    def refresh_drives(self):
        # One read-only line per tray drive, placed above the Quit item; the tooltip repeats them.
        for action in self.drive_actions:
            self.menu.removeAction(action)
            action.deleteLater()
        self.drive_actions = []
        lines = []
        disks = self.window._disks
        for root in settings.load()["tray_drives"]:  # stored as 'E:' already
            disk = disks.get(root)
            if disk:
                temp = disk.get("Temperature")
                temp_text = f"{temp} \u00B0C" if temp is not None else "temp: no data"
                text = f"{root}  {disk.get('Health') or 'unknown'}  \u00B7  {temp_text}"
            else:
                text = f"{root}  no SMART data"
            action = QAction(text, self.menu)
            action.setEnabled(False)
            self.menu.insertAction(self.drive_anchor, action)
            self.drive_actions.append(action)
            lines.append(text)
        self.icon.setToolTip("\n".join([APP_NAME] + lines))
        self._sync_drive_icons()

    def _sync_drive_icons(self):
        # Keep one icon per tray drive; each shows its live temperature and turns red when hot.
        online={root[:2] for root,s in self.window.engine.snapshot().items() if s['state'] not in ('offline','released','ejecting') and root not in self.window.engine.released_roots}
        selected = [r for r in settings.load()['tray_drives'] if r in online] if self.window._limits.get('health_enabled',True) else []
        disks = self.window._disks
        for root in [r for r in self.drive_icons if r not in selected]:
            icon = self.drive_icons.pop(root)
            icon.hide()
            icon.deleteLater()
            self.drive_menus.pop(root, None)
        for root in selected:
            disk = disks.get(root)
            temp = disk.get("Temperature") if disk else None
            external = bool(disk) and disk.get("Bus") not in ("NVMe", "SATA", "RAID", "SAS")
            level = settings.heat_class(temp, external, self.window._limits) if temp is not None else "cool"
            color = {"cool": NEON, "warm": ORANGE, "hot": RED}[level]
            if root not in self.drive_icons:
                icon = QSystemTrayIcon(temp_icon(temp, color), self.app)
                # Right-click on a drive icon offers removing it from the tray; the menu lives as long as the icon.
                drive_menu = QMenu()
                drive_menu.setStyleSheet(MENU_STYLE)
                self.drive_menus[root] = drive_menu
                remove_action = QAction("Remove from tray", drive_menu)
                remove_action.triggered.connect(lambda _checked=False, r=root: self._remove_drive(r))
                drive_menu.addAction(remove_action)
                icon.setContextMenu(drive_menu)
                icon.activated.connect(self._activated)
                icon.show()
                self.drive_icons[root] = icon
            tray = self.drive_icons[root]
            tray.setIcon(temp_icon(temp, color))
            tray.setToolTip(f"{root}  {temp} \u00B0C" if temp is not None else f"{root}  no temp data")

    def _remove_drive(self, root):
        # Visibility and monitoring are independent settings.
        self.window._set_tray_drive(root, False)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.refresh()

    def _toggle_pause(self, paused):
        if self.window.pause_btn.isChecked() != paused:
            self.window.pause_btn.setChecked(paused)  # also calls engine.set_paused via the window

    def _sync_pause_from_window(self, paused):
        if self.pause_action.isChecked() != paused:
            self.pause_action.setChecked(paused)


def main() -> int:
    if "--remove-autostart" in sys.argv:
        try:
            startup.remove_if_owned(*startup_target())
            return 0
        except (OSError, subprocess.SubprocessError):
            return 1
    if "--self-test" in sys.argv:
        from selftest import run
        index = sys.argv.index("--self-test")
        if index + 1 >= len(sys.argv):
            return 2
        return run(sys.argv[index + 1])
    if os.name != "nt":
        print("File Burglar runs on Windows only.", file=sys.stderr)
        return 1
    if not is_admin():
        if relaunch_as_admin():
            return 0  # the elevated copy takes over from here
        ctypes.windll.user32.MessageBoxW(
            None, f"{APP_NAME} needs administrator rights to watch every drive.\n\nStart it again and choose Yes on the Windows prompt.",
            APP_NAME, 0x10)  # MB_ICONERROR
        return 1
    if not take_single_instance():
        ctypes.windll.user32.MessageBoxW(
            None, f"{APP_NAME} is already running.\n\nLook for its icon in the system tray (near the clock).",
            APP_NAME, 0x40)  # MB_ICONINFORMATION
        return 0
    lower_process_priority()

    db_path = storage.default_db_path()
    try:
        storage.init(db_path)
    except (OSError, sqlite3.Error) as exc:
        ctypes.windll.user32.MessageBoxW(
            None, f'File Burglar could not open or upgrade its saved index.\n\n{exc}\n\n'
            f'Database: {db_path}\n\n'
            'Monitoring has not started. Keep the database and any safety backups. '
            'If the drive is full, free space and try again.', APP_NAME, 0x10)
        return 1

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FileBurglar.Desktop")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(tray_icon())

    engine = Engine(db_path)
    storage.set_exclusions(settings.load()['exclusions'])
    con=storage.connect(db_path)
    try:
        storage.prune_exclusions(con)
        engine._reload_volumes(con)
    finally:con.close()
    manager = DriveManager(engine.on_events, engine.on_overflow, engine.on_drive,engine=engine)
    window = MainWindow(engine, db_path)
    window.features.manager=manager
    manager.capacity_idle=window.features.measurement_idle
    window.setWindowIcon(tray_icon())
    window.brand_icon.setPixmap(tray_icon().pixmap(QSize(32, 32)))
    tray = Tray(app, window, engine)

    engine.start()
    manager.start()

    def shutdown():
        window.features.stop()
        manager.stop()
        manager.join(timeout=10)
        engine.stop()
        engine.join(timeout=10)
        tray.icon.hide()
        for icon in tray.drive_icons.values():
            icon.hide()
        if window.explorer is not None:
            window.explorer.close()
        window.con.close()

    app.aboutToQuit.connect(shutdown)
    if "--background" not in sys.argv:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
