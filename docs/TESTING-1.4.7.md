# File Burglar 1.4.7 — feature-bundle test installer

Quit the existing copy from its system-tray menu, then run the installer using
the existing installation folder. No portable copy is needed. The installer
retains the same application identity and `%LOCALAPPDATA%\FileBurglar` data
location. Existing index/settings stay in place; new metadata tables and settings
are additive. One fast startup reconciliation after launch is still expected.

## Included controls

| Feature | Where to find it |
| --- | --- |
| Release access / safely eject | Right-click a drive tile, or open its drive details. Safe eject appears only when Windows identifies an eligible removable device. |
| Resume a released drive | The same drive menu/details. Release is temporary and does not change the saved monitoring selection. |
| Stop health/temperature monitoring entirely | Options → Temperature → Enable drive-health and temperature monitoring. |
| Keep readings, stop temperature popups | Options → Temperature → Enable temperature notifications. |
| One/two-hour temperature alert reset | Options → Temperature. Reset requires sustained observed cooling at least 3°C below warm; sleep/missing observations do not count as continuous cooling. |
| Game/work mode | Main-window button or tray menu. Automatic matching of application names is configured under Options → Tracking and checked every 30 seconds. |
| Folder exclusions | Options → Tracking. Add full folder paths or use Add excluded folder. These remove tracking coverage, not disk files. |
| Net growth over hour/day/week | What used my space? tab. The view groups activity by each file's direct parent once, avoiding duplicate ancestor totals. |
| Free-space / rapid-growth alerts | Options → Space alerts. Rules are optional; empty lists disable them. Space alerts check at most once per minute, growth alerts at most every five minutes, and both defer during game/work mode. |
| Clear monitoring state | Drive rows/tiles, progress line, and main tray tooltip. Recovery/deferred states differ from ordinary changed-folder updates. |
| Copy full paths | Right-click selected rows in Folder report, Activity log, growth reports, or Explorer. |
| CSV export | Open a report/activity/growth tab and choose Export CSV. All filtered rows are exported in the selected sort order, not just the displayed page. Exports can be cancelled. |
| Reconnect summaries | Options → Updates & reconnects. A summary follows necessary reconciliation for a matching known external volume, not an additional scan. |
| Update notices | Check for updates in the main window, About, or the tray. Options controls daily automatic checks. Notices offer a release link, reminder, or skip choice; installers are not downloaded or run automatically. |

## Drive removal behavior

Windows' normal Eject command remains supported. File Burglar registers for
device-removal requests and releases its own access. It never stops unrelated
services/applications or force-dismounts a device. Multi-volume devices are
handled together. A cancelled/refused Windows removal can resume monitoring.

**Release monitoring** means only that File Burglar has released its own work.
It is not permission to unplug. **Safely eject** reports success only if Windows
confirms removal. If another application still holds the device, Windows may
refuse. A device containing File Burglar, its data, or its working directory
requires closing the application rather than claiming an in-app safe eject.

## What was checked

- 102 automated tests, including notification bursts, thresholds/cooldowns,
  exclusions/re-inclusion, work-mode deferral, reports/CSV cancellation,
  version checks, and simulated removal/refusal/reconnect cases.
- Actual Windows directory-change notifications and actual handle-specific
  device-notification registration/cleanup on a temporary fixture.
- Offscreen full-window/options/growth-view checks and rendered UI previews
  with synthetic data. No private index was used for screenshots.
- The packaged application's hidden synthetic self-test, native imports,
  installer compilation, source/payload audits, and SHA-256 checksum.

No physical drive was ejected and no installation/uninstallation was performed
on your existing installation during the build. Real USB ejection, real sensor
hardware, long-running workloads, and configured games still need owner testing.
The former recurring-scan issue cannot be declared solved solely by these tests;
the available installed scan log contained startup scans and no recorded
recovery loop. Later whole-drive recovery still occurs if Windows genuinely
loses change notifications or a watcher reconnects.

The current source/build README and public GitHub upload folder remain the
previous stable-release materials. This installer is for testing the new bundle
before a new public release is packaged.
