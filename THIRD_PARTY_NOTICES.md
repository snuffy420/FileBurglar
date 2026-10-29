# Third-party software

File Burglar application code and original graphics are GPL-3.0-or-later. Third-party components retain their own copyright and license terms. Copies of the available license texts are in `licenses/` and accompany the executable packages.

| Component | Release build version | License / source |
| --- | --- | --- |
| CPython | 3.12.6, x64 | Python/PSF license; https://www.python.org/downloads/release/python-3126/ |
| SQLite | Supplied by the bundled Python runtime | Public domain; https://www.sqlite.org/copyright.html |
| PyQt6 | 6.11.0 | GPLv3; https://www.riverbankcomputing.com/software/pyqt/download |
| Qt runtime | 6.11.2 | LGPLv3/GPL component terms; https://download.qt.io/archive/qt/6.11/6.11.2/ |
| PyQt6-sip | 13.12.0 | SIP license; https://www.riverbankcomputing.com/software/sip/download |
| PyInstaller bootloader/runtime | 6.18.0 | GPLv2-or-later with bootloader exception; https://github.com/pyinstaller/pyinstaller/tree/v6.18.0 |

The application dynamically loads the bundled, unmodified Qt runtime from `_internal`. The Python wrappers and Qt runtime are supplied by the pinned upstream wheels in `requirements.txt`. Matching upstream source distributions and build information are available from the linked projects and their release archives. The File Burglar source archives contain the application source and packaging scripts; they are not a combined source tree for CPython, Qt, or other upstream projects. Distribution of their binaries remains subject to their respective license terms.

PyInstaller, Pillow, 7-Zip/py7zr, and Inno Setup are packaging tools, not application requirements for end users. Inno Setup adds its own installer/uninstaller runtime under its license. The application does not include OpenLUFS, FFmpeg, audio tools, or dependencies from unrelated projects.

Upstream license references: [PyQt licensing](https://www.riverbankcomputing.com/static/Docs/PyQt6/introduction.html#license), [Qt open-source terms](https://www.qt.io/licensing/open-source-lgpl-obligations), [PyInstaller license](https://pyinstaller.org/en/stable/license.html), [Inno Setup license](https://github.com/jrsoftware/issrc/blob/main/license.txt).
