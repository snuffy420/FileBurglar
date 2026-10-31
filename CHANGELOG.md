# Changelog

## [1.5.4] - Disk, CPU and memory use fixes, 2026-10-30

- Merge repeated changes to the same file into one activity row per hour. Programs that rewrite their data files every few seconds previously added millions of rows a day (16.7 million in 36 hours on one test PC, about 4.2 GB).
- A file created and deleted within the same hour no longer leaves activity rows, since it made no net change to used space.
- Cap the activity log at 500,000 rows in addition to the 30-day limit. Old history is trimmed in small chunks, so a large existing log shrinks gradually without stalling monitoring.
- Remove the per-row volume index on the activity log (about 1 GB on the test PC). Drive-letter remaps still work by volume ID.
- New databases give freed space back to Windows automatically after trimming. The write-ahead log is truncated after checkpoints instead of keeping its largest size.
- Keep at most one pre-upgrade safety backup. A retried or interrupted upgrade reuses it instead of making another full copy on every launch, and completed-upgrade backups are deleted after 7 days.
- Collect notification bursts for half a second and check each changed path once, cutting repeated file checks and database writes on busy folders.
- Lost-notification recovery scans of a whole drive now run at most once every 15 minutes per drive. Further losses during the cooldown queue a single deferred rescan.
- A drive that refuses Windows removal notifications keeps its file watcher instead of failing and restarting every 5 seconds, and the error is logged once.
- Show the Options tab name "Updates & reconnects" correctly instead of treating "&" as a keyboard-shortcut marker.
- README: describe every feature added since 1.4.5 and show all current screenshots. Fix demo screenshots that showed drives as offline with "checking space".
- Retain all features from 1.5.3.

## [1.5.3] — Upgrade and dialog fixes, 2026-10-30

- Theme all Qt dialogs, labels, details, inputs and progress controls for readable dark backgrounds.
- Validate Windows device type and resolved PnP identity before grouping drive letters for removal; keep Google Drive virtual disks separate from physical disk 0. Explain genuine multi-volume device groups.
- Save tray and monitoring toggles against the latest stable-volume map, preserving choices through restarts, disconnections and changed letters.
- Check available working space before a legacy database backup/upgrade. Handle startup database and disk-space failures with a readable message, close connections on failure, and retain saved data and backups.
- Retain all features from 1.5.2.

## [1.5.2] — Tracking options styling, 2026-10-29

- Theme the exclusions editor and checkbox indicators with the app's dark/orange palette. Add a clear empty-state hint and align the folder action button with the form.
- Combined test installer includes all prior feature builds and the three updates below. Versions advance once per feature update, as requested; after 1.4.9 the sequence continues at 1.5.1.

## [1.5.1] — Refresh drives, 2026-10-29

- Add a compact refresh button beside Explorer, with a Refresh drives tooltip/accessibility label. It wakes background discovery immediately, coalesces clicks, and respects released/ejecting devices and disabled health checks.
- Discovery refresh alone does not re-index existing monitored drives. Automatic five-second discovery remains enabled.

## [1.4.9] — Stable volume ownership, 2026-10-29

- Track saved drive data by Windows volume identity instead of assuming a drive letter identifies a device. A verified volume changing L: to J: retains its file index, history, counts, settings, rules, and current/last-letter association.
- Make transactional remaps by volume ownership, including two drives swapping letters or different media occupying an old letter. Keep disconnected/replaced snapshots separate.
- Preserve the last known letter while offline. Update displayed associations when that same verified volume returns elsewhere; retain the originally observed letter in log details and exports.
- Tag and reject stale watcher events, guard scan commits and Explorer actions against mount reassignment, and carry per-volume preferences independently of letters. Unmonitored volumes can update their mount association without starting a scan.
- Back up pre-migration databases before adding volume ownership metadata. Retain existing indexed files and flag uncertain legacy history. Older saved entries without a usable recorded identity require explicit Link saved drive confirmation; no size/letter guessing.
- Existing 1.4.7 identities are imported automatically. Plugging an old external drive back in under its old letter in 1.4.7 before upgrading lets that recorded association migrate without manual linking.

## [1.4.8] — Disconnected-drive display, 2026-10-29

- Remove disconnected volumes from the top tile strip and temperature icons while retaining their saved offline row, index, logs, and last letter.
- Keep a deliberately released but still connected drive accessible for Resume. Restore other drives' health polling after a released device has actually disconnected.

## [1.4.7] — Feature-bundle test build, 2026-10-29

- Windows device-removal notifications release File Burglar's own watchers and workers for normal Windows Eject. Added Release monitoring for removal, Resume monitoring, and optional Windows-confirmed Safely eject actions on eligible devices. Multi-volume devices are grouped; Windows vetoes and application/data dependencies are respected. No forced removal.
- Added independent drive-health/temperature monitoring and temperature-alert switches. Disabled monitoring cancels its own health query and hides temperature icons; file monitoring continues.
- Temperature notifications now use persisted heat episodes: one warm alert and one hot escalation, then a reset after one or two hours observed at least 3°C below warm. Missing readings and restarts do not repeat alerts.
- Added manual game/work mode and optional matching of configured application names every 30 seconds. Heavy recovery/folder catch-up work is deferred while filesystem notifications remain collected within bounded queues.
- Added folder exclusions with consistent live/scan filtering, index/report cleanup without fictitious deletion history, and targeted baselines when folders are re-included.
- Added an asynchronous What used my space? view for hour/day/week net growth, with direct-parent accounting to avoid double-counting files.
- Added optional low-free-space and rapid-folder-growth notifications with per-rule configuration and deduplicated episodes. Capacity checks run off the UI thread at most once per minute when visible or requested by alerts; folder-growth alert queries run at most every five minutes.
- Added explicit Watching, Updating folders, Deferred, Startup scan, Recovering, Paused, Released, Offline, and Error states, with reasons/progress where available and status in the tray tooltip.
- Added copy-full-path context actions and cancellable CSV exports of all filtered report/activity/growth rows, respecting the current sort. Exports stream to a temporary file, protect spreadsheet formula-leading text, and replace the destination only on success.
- Added optional reconnect summaries only for matching known volume identities; unknown/replaced media establish a baseline rather than reporting false changes against another drive.
- Added daily, cached stable GitHub release checks, manual checks in About/tray/main window, and update notices with download/remind/skip choices. Automatic notices do not open modal dialogs or download/run installers.
- Retains the tested 1.4.6 layout, size-card precision, fast startup scan, and low-priority incremental monitoring changes. No automatic periodic whole-drive scan was added.
- New settings and small metadata tables are additive; the installer retains its application identity and existing per-user data location.

## [1.4.6] — Test build, 2026-10-29

- Re-arm Windows change notifications before parsing/delivering each completed batch, reducing delays when drives are busy.
- Increase each local-drive notification buffer from 64 KiB to 256 KiB; copy only the returned bytes. Six watched drives use 1.5 MiB of notification buffers.
- Show startup/recovery scan reasons in the progress line and status tooltip. Record scan starts/completions in a rotating local `fileburglar-scans.log` (512 KiB plus one backup).
- Preserve recovery after genuinely lost change notifications; this build does not disable recovery scans or claim to eliminate all overflows.
- Fit Folder report and Activity log columns to the available viewport, including the previously unbudgeted Action column. Preserve manual column resizing and allow scrolling after manual widening.
- Disable path wrapping, use trailing ellipses, and expose full cell text in tooltips.
- Show connected-drive indexed size / all saved indexed size, including offline drives, in the size card. The denominator is saved file size, not drive capacity.
- Use extra precision when rounding would hide excluded offline-drive data; shorten the caption to "online indexed" and show exact byte totals on hover.
- Windows installer upgrades the existing application in place using the same application identity and data directory. No database format change or index reset.

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

- Windows x64 installer and standalone portable folder, with 7z portable archives.
- Matching 7z source archives, dependency licenses, build scripts, and SHA-256 checksums.
- GitHub-ready README, synthetic screenshots, release notes, issue template, and File Burglar contact page using the existing maintainer Formspree endpoint.
- Windows only for this release; no macOS/Linux packages.
