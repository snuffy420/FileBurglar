# File Burglar 1.5.2 — combined test installer

This combines the 1.4.7 feature bundle with four updates: 1.4.8 disconnected-drive
display, 1.4.9 stable volume identity, 1.5.1 Refresh drives, and 1.5.2 Options styling.
There is one combined installer; a portable copy is not needed.

Quit File Burglar from its tray menu, then install over the existing copy using
the same installation folder. The data location stays `%LOCALAPPDATA%\FileBurglar`.
Before the first volume-schema migration, the app saves a SQLite backup beside
the index named `pre-volume-migration-*.db`. First launch may take longer while
this database backup and metadata conversion complete. It does not read file
contents or reset the saved index. Allow the normal startup reconciliation to finish.

## Test your external drive

1. If still running 1.4.7, reconnect the external drive while it still receives
   L: and let it reach Watching/Ready. That records a usable stable identity for
   the older L: entry before upgrading.
2. Install 1.5.2 and check that disconnected drives disappear from the top strip
   and temperature icons. Their saved information remains in Drives, labeled
   with their last known letter and offline status.
3. Reconnect the same external volume. Automatic discovery checks every five
   seconds; the circular-arrow button beside Explorer requests an immediate pass.
4. If it now receives J:, its existing verified entry should update to J:,
   keeping its saved index/history together. A different volume occupying L:
   must remain separate. No bulk file additions/removals should be generated
   just by renaming the mount association.
5. Where an older entry has no usable recorded identity, the program will not
   guess. Right-click the connected drive or open its details, choose
   **Link saved drive**, and explicitly select the old saved entry that you know
   belongs to it. This is a one-time legacy association, not a step for every
   reconnect. Already verified volumes follow later letter changes automatically.
   The option only appears while unidentified saved entries remain.

Current paths use the volume's latest/last letter. Activity-log tooltips and CSV
exports retain the original observed path. Old history whose device identity
cannot be established is marked unverified. A legacy link preserves observed
history; it cannot reconstruct unobserved intermediate changes.

The first reconnect reconciliation can still check files for changes made while
the drive was disconnected. Refresh alone does not trigger a full scan of drives
already monitored. Monitoring exclusions, per-volume alert rules, tray choices,
and monitoring selection should follow the volume rather than transfer to
another device that takes its old letter.

## Other included features

All 1.4.7 controls remain: game/work mode, folder exclusions, hour/day/week net
growth, optional space/growth alerts, full filtered CSV export, copy paths,
reconnect summaries, update notifications, safe-removal controls, and independent
health/temperature switches with one/two-hour alert resets.
See [the feature guide](TESTING-1.4.7.md) for their locations.

Tracking Options now uses a dark exclusions editor and styled checkbox indicators.

## Verification and limits

Automated tests simulate unplug/reconnect, letter changes/swaps, replacement
media, transactional failure/rollback, migration backups, legacy links, stable
preferences, stale events/actions, and offline UI behavior. The packaged synthetic
self-test also exercises a volume-letter remap without touching any real mount.

The build does not install/uninstall over your running copy, change real drive
letters, or physically eject a device. Physical USB removal, your real hardware,
and sustained workloads still require your test. Use Windows' safe-removal
confirmation before unplugging; File Burglar never force-dismounts devices.
