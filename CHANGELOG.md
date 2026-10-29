# Changelog

## [1.4.5] — 2026-10-28

- Added an About button to the main window and About / Help / Donate in the tray menu.
- Added tabbed application information, in-app help, contact/repository links, and license information.
- Added optional Bitcoin donation details using the maintainer's existing address, with a verified QR code and copy-address button.
- Updated the README, contact page, screenshots, release notes, installer, portable packages, and source archives.
- Retains the tested 1.4.4 indexing/monitoring behavior; the About dialog is created only when opened.

## [1.4.4] — 2026-10-28

Windows x64 release preparation, followed by 1.4.5 with the About section. Earlier version numbers were internal development builds.

### Indexing and monitoring

- Persistent SQLite indexing of files, folders, logical sizes, and counts across fixed/removable drives.
- Parallel, higher-priority startup scanning on every launch, including previously indexed drives; lower-resource processing and serialized rescans afterwards.
- Startup reconciliation with saved history, baseline suppression of first-scan activity, and visible scan mode/progress.
- Targeted changed-folder updates; new directories do not trigger whole-drive rescans.
- Own database/WAL notifications filtered before queueing, preventing self-triggered re-indexing.
- Folder-level queue-pressure recovery instead of unnecessary drive scans.
- Live Windows change notifications with rename/add/remove/size/timestamp reconciliation, de-duplication, overflow recovery, and bounded event/path queues.
- Global pause/resume, per-drive monitoring selection, watcher retries, cancellation-aware shutdown, and single-instance protection.
- Correct live drive totals and retained totals during catch-up scanning.
- Unreadable-subtree preservation, junction/symlink avoidance, and cancelled-scan generation handling.
- Exact support for timestamps outside SQLite's signed 64-bit nanosecond range, including legacy Windows dates near 1601.

### Reports and Explorer

- Dashboard cards, drive status/scan dates, folder activity summaries, and searchable per-file activity with byte deltas.
- Period, drive, and action filters; sortable numeric columns; 30-day detailed logs and 365-day daily summaries.
- Responsive Explorer using separate read-only queries, directory summary counters, recent-page caching, stale-query cancellation, breadcrumbs, location search, and 500-item pages.
- File opening, new folders, renaming, and confirmed Recycle Bin operations with drive-root protection.

### Drive health and tray

- Windows storage identity, media/bus type, health, available temperature, wear, and power-on-hour counters.
- ATA SMART temperature fallback, stable disk-to-drive mapping, and honest unavailable readings.
- Main tray menu, individual drive-temperature icons, warm/hot colors and alerts, configurable limits, and independent tray visibility/monitoring choices.
- Opt-in elevated logon startup for the current user, with tray-only background startup.

### Interface and reliability

- Dark/orange UI, orange-at-bottom main gradient, display-size-specific burglar/magnifier icon, and a modern indexed Explorer.
- Nonblocking control queue so Pause does not wait behind a full filesystem-event queue.
- Readable drive error details, retry scans, exact error logging, and small rotated diagnostics.
- Validated settings with atomic saves, compatible schema upgrades, and separate installed/portable data locations.

### Release packages

- Windows x64 installer and standalone portable folder, with ZIP and 7z portable archives.
- Matching ZIP/7z source archives, dependency licenses, build scripts, and SHA-256 checksums.
- GitHub-ready README, synthetic screenshots, release notes, issue template, and File Burglar contact page using the existing maintainer Formspree endpoint.
- Windows only for this release; no macOS/Linux packages.
