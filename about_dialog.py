"""On-demand About, Help, and optional Bitcoin donation information."""
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QTabWidget, QTextBrowser,
                             QVBoxLayout, QWidget)

from appinfo import APP_NAME, APP_VERSION
from ui import AppDialog, ORANGE, PANEL, TEXT

# Same owner-supplied address used by the owner's other applications.
DONATION_BTC_ADDRESS = 'bc1qz8877p6vusc22qrctdj4h7w0uk2l9l9h8lwphy'
DONATION_BTC_URI = 'bitcoin:' + DONATION_BTC_ADDRESS
CONTACT_URL = 'https://snuffy420.github.io/FileBurglar/docs/contact.html'
REPOSITORY_URL = 'https://github.com/snuffy420/FileBurglar'

ABOUT_HTML = f'''<h2>File Burglar {escape(APP_VERSION)}</h2>
<p>Folder indexing, file activity tracking, and available drive health information for Windows.</p>
<p>Created by <b>zerØ software / snuffy420</b>.</p>
<p>Your index, settings, and activity history stay on your computer. The application does not upload them.</p>
<p>Application code and original graphics: <b>GPL-3.0-or-later</b>.
Bundled components retain their own licenses; see the included LICENSE and THIRD_PARTY_NOTICES.md files.</p>
<p><a href="{CONTACT_URL}">Contact and feedback</a><br>
<a href="{REPOSITORY_URL}">GitHub repository</a><br>
<a href="{REPOSITORY_URL}/releases">Release downloads</a></p>'''

HELP_HTML = '''<h2>Using File Burglar</h2>
<h3>Index once, then monitor</h3>
<p>The opening scan checks the saved index for changes since the last run. Its first baseline
does not flood your activity log. After the scan, drives show READY and normal operation uses
Windows file-change notifications. New folders receive targeted background updates;
there is no timer that repeatedly indexes entire drives.</p>
<p>A drive/feed reconnection or genuine loss of Windows notifications can require a recovery scan.</p>
<h3>Find where space is going</h3>
<p>Use <b>Folder report</b> for written/freed space and activity by folder. Use <b>Activity log</b>
for file changes, byte deltas, and drive/action/path filters. File sizes are logical sizes;
hard links, compression, and sparse files can differ from physical disk allocation.</p>
<h3>Explorer</h3>
<p>Browse the indexed snapshot using breadcrumbs and search within the current location.
Large locations have 500-item pages. Refresh updates the view. You can open files,
create folders, rename items, and send confirmed deletions to the Recycle Bin.</p>
<h3>Pause, tray, and startup</h3>
<p><b>Pause monitoring</b> holds changes and suspends scan work at safe boundaries. Close the
window to keep monitoring in the tray; choose <b>Quit</b> to stop it. Drive details control
monitoring and temperature-icon visibility independently. <b>Start with Windows</b> is opt-in.</p>
<h3>Temperatures and errors</h3>
<p>Available SMART counters depend on the device, driver, and administrator access. An unavailable
temperature is not a measured zero. Set warm/hot limits in Options. For an ERROR status,
open the drive details to read the message or retry the scan. Diagnostic logs are stored with your index.</p>
<h3>Where your data lives</h3>
<p>Installed app: %LOCALAPPDATA%\\FileBurglar. Portable app: Data beside the executable,
with portable.txt present. Python source: the application folder. Detailed activity is retained
for 30 days and daily folder summaries for 365 days. Keep your private index database out of bug reports.</p>'''


class AboutDialog(AppDialog):
    def __init__(self, parent=None):
        super().__init__(parent, 'About File Burglar', width=600)
        self.setWindowTitle('About File Burglar')
        self.setMinimumSize(600, 440)
        available = self.screen().availableGeometry()
        self.resize(min(680, available.width()-60), min(640, available.height()-60))
        self.tabs = QTabWidget()
        self.about_content = self.browser(ABOUT_HTML)
        self.help_content = self.browser(HELP_HTML)
        self.tabs.addTab(self.about_content, 'About')
        self.tabs.addTab(self.help_content, 'Help')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        donation = QWidget()
        layout = QVBoxLayout(donation)
        layout.setContentsMargins(20,16,20,16)
        layout.setSpacing(12)
        title = QLabel('Support File Burglar')
        title.setStyleSheet(f'font-size: 17pt; font-weight: bold; color: {ORANGE};')
        layout.addWidget(title)
        intro = QLabel('If File Burglar is useful to you, you can support its development with a Bitcoin donation. Donations are optional.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(QLabel('Bitcoin (BTC) · Bitcoin network'))
        self.address = QLineEdit(DONATION_BTC_ADDRESS)
        self.address.setReadOnly(True)
        self.address.setAccessibleName('Bitcoin donation address')
        font = QFont('Consolas');font.setPixelSize(13);self.address.setFont(font)
        self.address.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.address.setMinimumHeight(36)
        layout.addWidget(self.address)
        self.qr_label = QLabel()
        self.qr_label.setAccessibleName('Bitcoin donation QR code')
        qr = QPixmap(str(Path(__file__).resolve().parent/'assets/donation-btc-qr.png'))
        if not qr.isNull():
            self.qr_label.setPixmap(qr.scaled(232,232,Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.FastTransformation))
        else:
            self.qr_label.setText('Use the Bitcoin address above.')
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr_label)
        self.copy_button = QPushButton('Copy BTC address')
        self.copy_button.clicked.connect(self.copy_address)
        layout.addWidget(self.copy_button)
        layout.addStretch()
        scroll.setWidget(donation)
        self.tabs.addTab(scroll, 'Donate BTC')
        self.body.addWidget(self.tabs,1)
        footer = QHBoxLayout()
        self.copy_status = QLabel('')
        self.copy_status.setObjectName('subtitle')
        footer.addWidget(self.copy_status,1)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        self.body.addLayout(footer)

    @staticmethod
    def browser(html):
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(f'QTextBrowser {{ background: {PANEL}; color: {TEXT}; border: none; padding: 14px; }}')
        browser.document().setDefaultStyleSheet(f'a {{ color: {ORANGE}; }} h2,h3 {{ color: {ORANGE}; }}')
        browser.setHtml(html)
        return browser

    def copy_address(self):
        QApplication.clipboard().setText(DONATION_BTC_ADDRESS)
        self.copy_status.setText('BTC address copied.')
