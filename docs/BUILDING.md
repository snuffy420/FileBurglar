# Building the Windows release

Use Windows x64 and Python 3.12 x64. The source checkout is the directory containing `main.py`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
.\.venv\Scripts\python.exe tools\render_release_assets.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean FileBurglar.spec
```

The build uses a folder distribution, keeping startup fast and avoiding a temporary extraction on every launch. Python and Qt are bundled; no source database/settings are collected by the PyInstaller specification.

Install [7-Zip](https://www.7-zip.org/) and [Inno Setup 7](https://jrsoftware.org/isdl.php) for packaging. Set `INNO_ISCC` to the absolute path of `ISCC.exe` if it is not in a standard installation location. The initial release used Inno Setup 7.1.0 and 7-Zip; neither compiler is copied into the application.

To generate the complete upload folder after building:

```powershell
$env:INNO_ISCC = 'C:\Program Files\Inno Setup 7\ISCC.exe'
py -3.12 tools\package_release.py --payload dist\FileBurglar --output ..\GitHub-Upload
```

The packaging script stages an explicit allowlist of source files, builds the installer, creates portable/source 7z archives, validates each archive, writes checksums, and records a build manifest. It refuses to overwrite an existing release folder.

For release verification without monitoring any real drives:

```powershell
.\dist\FileBurglar\FileBurglar.exe --self-test C:\path\to\verification-report.json
```

This hidden test mode uses synthetic data, starts no drive watchers or health queries, and writes its JSON report only to the requested location. Its temporary fixture is removed afterward. The current running source instance can remain open while this smoke test runs.

Before publishing, review `docs/PUBLISHING.md`, include corresponding upstream source access and license materials as required, and use your own signing certificate if signed packages are desired.
