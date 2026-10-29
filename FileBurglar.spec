from pathlib import Path
import importlib.metadata as metadata
root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[], datas=[(str(root/'assets/donation-btc-qr.png'),'assets')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt6.QtWebEngineCore','PyQt6.QtWebEngineWidgets','PyQt6.QtQml',
              'PyQt6.QtQuick','PyQt6.QtMultimedia','PyQt6.QtBluetooth','tkinter'],
    noarchive=False)
# Python 3.12 can ship older CRT DLLs than Qt needs. The root directory is
# searched first, so use the Qt wheel's matching redistributable pair there.
qt_bin = Path(metadata.distribution('PyQt6').locate_file('PyQt6/Qt6/bin'))
a.binaries = [(dest, str(qt_bin / Path(dest).name), kind)
              if Path(dest).name.upper() in ('VCRUNTIME140.DLL','VCRUNTIME140_1.DLL')
              else (dest, src, kind) for dest, src, kind in a.binaries]
# Qt uses Windows' system ICU. Do not let unrelated PATH tools (for example
# Poppler) substitute a different ICU ABI under the same DLL basename.
a.binaries = [(dest, src, kind) for dest, src, kind in a.binaries
              if Path(dest).name.lower() not in ('icuuc.dll','icuin.dll','icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FileBurglar',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root / 'assets' / 'fileburglar.ico'),
          version=str(root / 'installer' / 'version-info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FileBurglar')
