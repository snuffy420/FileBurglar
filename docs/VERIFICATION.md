# Initial release verification

The release was assembled on Windows 10 x64 using Python 3.12.6, PyInstaller 6.18.0, PyQt6 6.11.0, and the Qt 6.11.2 wheel. Windows 10/11 x64 are the supported targets; no macOS/Linux build is claimed.

Verification includes the regression suite, a separate-database packaged-runtime self-test, archive integrity and content checks, and installer validation. A machine-readable release build manifest and verification reports are supplied with the upload folder. They describe the actual checks completed for these binaries.

Fifty-four automated tests cover the initial release, including new checks that ordinary folder creation/renaming, known-file changes, own database notifications, and queue-pressure recovery do not start another whole-drive scan. A real Windows watcher test verifies return to READY/live monitoring.

Core regressions cover persisted-index startup mode, real parallel enumeration, legacy timestamp overflow, change accounting, rename/delete, same-size edits, denied access, subtree preservation, migrations, scan cancellation, bounded queues, nonblocking pause, watcher retries, stale status messages, and real Windows change notifications. Explorer tests cover paging, counters, caching, asynchronous loading, and stale-result rejection. Release tests cover data placement, frozen launch arguments, and scheduled-startup XML/ownership handling. About tests cover all tabs, version/help/contact content, the exact donation address, copy-button behavior, bundled QR loading, and opening the dialog without starting monitoring. The original and displayed-size QR images were independently decoded against the owner address.

The packaged self-test creates synthetic drive data and checks SQLite, exact legacy timestamps, the app icon, all three main tabs, asynchronous Explorer loading, and the frozen startup command. It intentionally starts no real-drive scan or health query and does not change the user's startup settings.

Actual drive temperatures remain dependent on device support and elevation. A real E: legacy timestamp folder was verified during development, but the release smoke tests do not scan recipients' disks. No game frame-time benchmark, cross-machine hardware certification, or digital signing is claimed.
