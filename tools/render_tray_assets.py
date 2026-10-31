"""Capture the actual tray icon artwork and Qt menus with demo readings."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from unittest.mock import patch

import main
import settings


def render(app, window, assets):
    # This is an offscreen example, not a capture of the user's Windows desktop.
    limits = settings.load()
    limits['tray_drives'] = ['C:', 'D:', 'E:', 'F:']
    settings.save(limits)
    # These four readings are synthetic, including two extra demo tray drives.
    snapshot=window.engine.snapshot()
    demo_snapshot=dict(snapshot,**{r+'\\':dict(state='ready') for r in ('C:','D:','E:','F:')})
    original_disks = window._disks
    original_limits = window._limits
    window._limits = limits
    window._disks = {
        'C:': {'Bus':'NVMe','Health':'Healthy','Temperature':37},
        'D:': {'Bus':'SATA','Health':'Healthy','Temperature':48},
        'E:': {'Bus':'SATA','Health':'Healthy','Temperature':61},
        'F:': {'Bus':'SATA','Health':'Healthy','Temperature':None},
    }
    tray = None
    preview = QWidget()
    try:
        with patch('main.autostart_enabled',return_value=False),patch.object(window.engine,'snapshot',return_value=demo_snapshot),patch('main.settings.load',return_value=limits):
            tray = main.Tray(app, window, window.engine)
        preview.setStyleSheet('QWidget { background: #161616; color: #E9E9E9; font-family: "Segoe UI"; }')
        layout=QVBoxLayout(preview);layout.setContentsMargins(24,20,24,20);layout.setSpacing(16)
        title=QLabel('File Burglar · system tray icons')
        title.setStyleSheet('color: #FF8A1F; font-size: 17px; font-weight: bold;')
        layout.addWidget(title)
        row=QHBoxLayout();row.setSpacing(30)
        samples=[('App',tray.icon.icon(),'Open File Burglar')]
        for root,caption in [('C:','Cool'),('D:','Warm'),('E:','Hot'),('F:','Unavailable')]:
            samples.append((root,tray.drive_icons[root].icon(),caption))
        for name,icon,caption in samples:
            cell=QVBoxLayout();cell.setSpacing(8)
            mark=QLabel();mark.setPixmap(icon.pixmap(32,32));mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label=QLabel(name);label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail=QLabel(caption);detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail.setStyleSheet('color: #A5A5A5; font-size: 11px;')
            cell.addWidget(mark);cell.addWidget(label);cell.addWidget(detail);row.addLayout(cell)
        layout.addLayout(row)
        note=QLabel('Demo readings · actual app icons enlarged for readability')
        note.setStyleSheet('color: #8F8F8F; font-size: 11px;')
        layout.addWidget(note)
        preview.show();preview.adjustSize();app.processEvents()
        preview.grab().save(str(assets/'screenshots/system-tray.png'))

        tray.menu.ensurePolished();tray.menu.adjustSize();tray.menu.show();app.processEvents()
        tray.menu.grab().save(str(assets/'screenshots/tray-menu.png'))
        tray.menu.hide()
        drive_menu=tray.drive_menus['C:']
        drive_menu.ensurePolished();drive_menu.adjustSize();drive_menu.show();app.processEvents()
        drive_menu.grab().save(str(assets/'screenshots/tray-drive-menu.png'))
        drive_menu.hide()
    finally:
        preview.close()
        if tray is not None:
            tray.icon.hide()
            for icon in tray.drive_icons.values():icon.hide()
            tray.menu.close()
        window._disks=original_disks
        window._limits=original_limits
