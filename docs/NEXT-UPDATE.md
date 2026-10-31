# Next update

The 1.5.2 combined test installer includes the 1.4.7 bundle, stable volume ownership,
disconnected-drive display, Refresh drives, and the Tracking-options styling fix.
Version steps: 1.4.8 → 1.4.9 → 1.5.1 → 1.5.2, one per feature update.
The requirements below remain the acceptance checklist. Automated/native-registration
tests do not replace owner testing of physical ejection, real games, sensor hardware,
and long-running monitoring. Optional automatic installer execution remains a future
idea; this build only checks for updates and offers release links.

## Stable volume identity and disconnected-drive display — included in 1.5.2 test

- Owner's external disk currently mounts as L: but commonly mounts as J: when optical/media drive assignments differ. Letters are mount locations, not device identities. The 1.4.7 root-keyed index/history and per-root identity check are insufficient for following an existing volume across letter changes; do not claim that feature is already complete.
- Remove actually disconnected volumes from the top drive-tile strip and remove their temperature icons/current health readings. Retain their saved snapshots/history in the Drives table as offline, clearly identified by volume name and last known letter. A temporarily released but still connected volume must retain an accessible Resume action until removal is confirmed.
- Introduce stable volume records independent of drive letters. Prefer the Windows volume GUID for persistent identity on the same Windows installation, and keep physical-device identity separate for grouping multi-volume removable devices. Treat formatting, ambiguous/duplicate IDs, and unavailable identities conservatively; do not guess a match from size, label, or drive letter.
- Store files/folders and activity/report aggregates against volume identity plus relative path, with a separate current mount mapping. A volume returning at J: after being L: must reuse its existing index/history, perform only the necessary reconnect reconciliation, and update current navigation paths without creating false bulk deletions/additions.
- Owner clarification: unplugging a volume retains its last known drive letter as the displayed association for its saved logs, reports, and offline information. Do not clear that letter on disconnect. When that same identified volume returns at a new letter, update its saved entry and displayed association to the new letter. If another volume occupies the old letter meanwhile, distinguish the offline volume by name/identity and offline status; never transfer its history to the new occupant.
- Preserve the original observed path/letter and timestamp for historical events. Resolve current paths through the volume's current mount mapping for navigation, and show the current volume name/letter alongside historical paths where helpful. Do not globally replace letter strings in old logs or silently rewrite which device an event belonged to.
- If another volume takes L:, its counts, size, history, alerts, exclusions, and reconciliation baseline must remain separate from the former L: volume. Use volume identities in drive filters, reconnect summaries, queued jobs, alert episode keys, monitoring choices, tray selections, and per-volume rules. Existing folder exclusions/rules should follow their explicitly chosen volume rather than a letter now assigned to unrelated media.
- Migrate the existing root-keyed database transactionally without resetting the index. Back up/check the schema migration and preserve legacy records whose historical identity cannot be established. Label ambiguous legacy history as unverified rather than attributing it to whichever disk currently occupies that letter. Prior letter changes cannot always be reconstructed from the old schema.
- Cancel/quiesce jobs associated with a removed/reassigned mount and validate the mount identity again before applying delayed events, asynchronous results, file actions, or eject requests. A queued event for old L: must never be applied to a different disk newly mounted at L:.
- Keep the online indexed size separate from all saved indexed size using volume records, without double-counting one volume that changed letters. Automatic discovery and the planned Refresh drives button must use the same identity/mount update path.

### Acceptance checks

- Disconnect L:, verify its top tile/temperature icon disappears while offline history remains, then reconnect the same volume as J: and verify preserved totals/history, correct navigation, and no duplicate drive record.
- Swap two known volumes' letters and verify every file count, size, report, alert rule, monitoring preference, and historical record remains associated with the correct volume.
- Test a different disk reusing L:, multiple volumes on one device, optical-drive letter interference, unknown/ambiguous IDs, changed/formatted media, and rapid unplug/replug while jobs/events are queued.
- Test migration with old indexes, mixed/ambiguous legacy history, cancellation/failure rollback, and repeated launches. Verify no unnecessary full re-index solely because a letter changed and no background whole-drive scans to identify devices.

## Refresh drives button — included in 1.5.2 test

- Add a "Refresh drives" button beside Explorer. Automatic discovery remains enabled: DriveManager currently checks connected drives every five seconds, and a visible main window refreshes its display every three seconds.
- The button requests an immediate background device-discovery pass and refreshes the displayed drive list/status. Refresh capacity/health metadata through their existing asynchronous workers only when enabled and permitted.
- This is device discovery and display refresh, not a request to re-index already monitored drives. Newly discovered monitored drives retain the normal initial/reconnect reconciliation behavior.
- Respect monitoring exclusions, game/work mode, disabled health checks, and temporary removal/release states. Refresh must never reopen a deliberately released/ejecting device or defeat Windows safe removal.
- Coalesce repeated clicks, avoid overlapping discovery passes, and keep the UI responsive. Verify a newly connected drive appears without restarting and that existing drives do not acquire extra scan jobs.

## Priority: cooperate with Windows safe drive removal — included in 1.4.7 test

- Owner cannot eject a USB drive through Windows because the device reports it is in use. Code inspection found that DriveWatcher keeps a directory handle open for ReadDirectoryChangesW and closes it only when stopped; 1.4.6 had no Windows query-remove notification handler. This is a likely contributor, not proof that no other process is holding the device.
- Incident clarification: eject reportedly still failed after File Burglar was closed and succeeded after Explorer was also closed, making Explorer a more likely blocker in this instance. File Burglar's role remains unconfirmed. The owner explicitly requires safe-removal cooperation regardless; keep this a priority requirement before publishing the next feature release.
- Treat removal as successful only after Windows confirms it. A stopped watcher, closed app window, elapsed timeout, or completed local database commit is not confirmation that the physical device can be unplugged. Keep failure/cancellation messages distinct from "Safe to remove"; do not offer forced removal as a fallback.
- Support the normal Windows "Safely Remove Hardware" and Explorer Eject paths. Register handle-specific device-removal notifications while each watcher handle is open, and respond to Windows query-remove events by quiescing File Burglar's work and releasing its device handles before acknowledging removal.
- Introduce a per-device release state that prevents DriveManager from reopening watchers while the device is still physically present but Windows is removing it. Stop new work for every monitored volume on the same physical removable device, including active startup/recovery indexers, targeted folder updates, and health queries that could access it. Retain cancellation tokens and wait for handle-owning workers to acknowledge completion; a timed-out join is not proof that the drive is released.
- Keep cancellation and pending overlapped I/O lifetime safe. Do not block the GUI indefinitely, close a handle from an unsafe thread, or report success while an operation still owns it. Resolve Windows' query-remove timing requirements explicitly in the implementation and test them.
- Commit File Burglar's outstanding local index transactions, retain the saved snapshot, and mark the affected volumes as ejecting/offline. Preserve a reconciliation-needed flag for changes not fully applied. Do not scan a drive as part of releasing it.
- Handle removal pending/completed and query-remove failure/cancellation. Resume watching only after a failed/cancelled removal is confirmed or the device genuinely reconnects, with any necessary reconciliation scheduled according to game/work mode.
- Add an optional "Safely eject" action to the drive tile/menu and drive details for eligible removable devices. First release File Burglar's access, then ask Windows to safely eject the correctly identified physical device. Show affected volume letters if one physical device contains multiple volumes.
- Also offer "Release monitoring for removal" so users can release File Burglar's access and then use Windows' own eject UI. This must not require changing their permanent monitoring selection, and must not re-open the device on the next five-second drive poll.
- Release only File Burglar's own workers and handles. Do not stop unrelated services, terminate other applications, force-dismount, or bypass Windows' removal veto. If Windows still reports the drive busy, show the available Windows failure reason and leave it mounted; never claim it is safe to unplug.
- If the executable, portable data, source checkout/database, or current working directory resides on the target device, explain the remaining dependency and require a clean application exit or relocation as appropriate. Do not promise in-app ejection of the drive the app itself is running from.

### Acceptance checks

- Test Windows-initiated removal with the app visible and tray-only, during idle monitoring, startup/recovery scans, changed-folder processing, and health polling.
- Test app-initiated safe eject, release-then-Windows-eject, multi-volume USB devices, cancelled/vetoed removal, surprise unplug, and reconnect. Verify no watcher restart races or leaked handles.
- Confirm another application's open file produces an honest failure, system/non-removable volumes are not offered an eject action, private index data is retained, and other drives continue monitoring normally.
- Verify a failed or cancelled release does not silently lose notification coverage; recover gaps explicitly rather than reporting a fully current index.

Windows references: [Processing a request to remove a device](https://learn.microsoft.com/en-us/windows/win32/devio/processing-a-request-to-remove-a-device), [safe device eject API](https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_request_device_ejectw).

## Approved feature additions — included in 1.4.7 test

These seven additions are approved for the next update. Keep background work bounded,
reuse the saved index/event feed, and avoid extra whole-drive scans or frequent polling.

### Game/work mode

- Provide a manual switch and an optional automatic trigger for configured games or demanding applications, using inexpensive Windows notifications or infrequent checks rather than continuous process enumeration.
- Defer recovery scans and heavy catch-up jobs while active, but continue collecting filesystem changes within bounded queues. Let an active scan pause at a safe boundary; resume low-priority processing afterwards.
- Make deferred work visible and preserve a recovery-needed flag if the change feed or queue loses events. Never claim the index is current when reconciliation is deferred. Do not silently change the user's existing fast-startup-scan preference.

### Folder exclusions

- Let users select folders to exclude, including caches, temporary directories, and build output. Offer suggestions without automatically excluding their files.
- Apply exclusions consistently to startup/recovery scans, live notifications, folder updates, reports, and indexed totals; prune excluded paths from queued work.
- Explain that changing exclusions changes tracking coverage. Stop tracking excluded paths without logging their removal as real file deletion. Re-including a folder uses targeted background indexing to establish its baseline.

### What used my space?

- Show folders with the largest net growth over the last hour, day, or week, with written, freed, and net bytes. Reuse existing activity/history rather than walking drives.
- Offer drive filters and links to the folder/report. Avoid double-counting nested folders in combined totals, and explain incomplete history or excluded periods.
- Run bounded queries away from the UI thread, only while this view is used. Verify which hourly data the existing history retains before implementing the hour filter.

### Space alerts

- Optional per-drive free-space thresholds in bytes or percentage, plus optional per-folder growth thresholds over a chosen interval.
- Reuse cached capacity information and activity summaries; any necessary capacity refresh must be infrequent and off the UI thread. Do not recursively measure folders for alerts.
- Notify once per episode, escalate only when meaningfully worse, and reset only after sustained recovery. Include mute/disable controls; avoid repeated alerts around a threshold. Treat missing/offline drive readings as unavailable, not zero free space.

### Monitoring status

- Clearly distinguish Watching, Updating changed folders, Recovery deferred, Startup scan, Recovering missed changes, Paused, Offline, and Error.
- Show the reason and meaningful available progress for recovery and deferred work. Use counts when total work is unknown rather than invented percentages or completion times.
- Make status accessible in the main window and tray without extra scanning. Build on the scan-reason diagnostics already included in the 1.4.6 test installer.

### Copy and export reports

- Add copy-full-path actions for applicable report, activity, and Explorer selections.
- Export the current filters and sort order to CSV, with clear column headings and UTF-8 encoding. State whether export includes the complete filtered result or just the displayed page, and offer the complete result when practical.
- Stream larger exports in the background with cancellation and bounded memory. Quote CSV correctly and handle spreadsheet formula-leading path text safely. Save only to a user-selected destination.

### Reconnect summary

- After a previously indexed external drive reconnects and reconciliation completes, show a concise summary of observed additions, removals, changed files, and net size since its last completed snapshot.
- Match the same physical volume rather than assuming a reused drive letter is the same device. If identity is uncertain or there is no baseline, establish a baseline instead of reporting misleading changes.
- Use the necessary reconnect reconciliation results, not an additional scan. Explain that the summary compares snapshots and cannot reconstruct every intermediate edit made while disconnected.
- Notify once per reconnect and provide a way to mute summaries. Respect game/work mode by deferring heavy reconciliation and making the pending state clear.

### Shared acceptance checks

- Verify idle/tray operation remains lightweight and memory/queues stay bounded under sustained change bursts. No new feature should silently schedule periodic full-drive scans.
- Check exclusions, pause, game/work mode, drive reconnects, notification cooldowns, and recovery together so one feature does not bypass another.
- Validate net-growth accounting, unknown/missing readings, volume identity, CSV cancellation, and UI responsiveness with synthetic fixtures before owner testing.

## Disable temperature and drive-health monitoring — included in 1.4.7 test

- Add a clear, saved Options toggle: "Enable drive-health and temperature monitoring".
- Turning it off stops scheduling SMART/PowerShell health and temperature queries, stops temperature alerts, and removes drive-temperature tray icons. Discard results from an already-running query after disabling; it must not re-enable icons or trigger alerts.
- Keep file indexing, filesystem change monitoring, reports, and the main application tray icon working independently.
- Show monitoring as disabled in drive details/tiles instead of displaying stale temperatures or implying a current health result. Keep basic drive availability and capacity information.
- Disable the temperature threshold controls while monitoring is off; preserve their saved values and tray visibility preferences for re-enabling.
- Also provide a separate "Enable temperature alerts" toggle for users who want health readings and temperature icons without warning notifications. This disables both warm/hot popups while retaining measurements.
- Prevent repeated alerts when a reading bounces across a threshold (owner example: 39/40 degrees C with warm set to 40). During one heat episode, show at most one warm notification and one hot escalation; do not notify again for hot-to-warm transitions or repeated warm/hot boundary crossings. Small temperature increases within the same severity do not create more popups.
- Re-arm alerts only after valid readings stay at least 3 degrees C below the warm threshold for one continuous hour by default. Provide a saved reset-delay option of one or two hours in Options. This is a notification cooldown, not a guarantee that a drive temperature is safe. Brief dips, missing readings, and drive reconnects must not reset the episode. A missing reading breaks the continuous cooling interval without clearing the episode. Persist the highest notified severity across application restarts so restarting does not repeat an existing alert; restart the cooling interval conservatively when observation resumes.
- Keep current temperatures, colors, and severity displays accurate while suppressing duplicate notifications. Alert toggles must not silently clear an active episode or replay suppressed alerts when re-enabled; a later escalation to a higher severity may still notify.
- Apply changes immediately on Save and retain them across restarts. Re-enabling monitoring resumes the existing background polling schedule without starting a drive index scan.
- Verify that disabling monitoring prevents subsequent health-query launches, stale-result alerts, and temperature tray icons; verify that alert-only disabling preserves readings and that filesystem monitoring continues in either mode.
- Test repeated 39/40 oscillation, warm-to-hot escalation, hot/warm oscillation, short cooling dips, sustained cooling/reheating, unavailable readings, reconnects, restarts, and toggles. Notification state must remain bounded and must not require additional health polling.

## Update notifications — included in 1.4.7 test

Start with automatic update checks and user-controlled installation.

- Check published stable GitHub releases for `snuffy420/FileBurglar`. Compare parsed version numbers with the installed version; never suggest a downgrade, draft, or test/prerelease build as a stable upgrade.
- Default to one automatic check per 24 hours, delayed until after startup. Save the last check time across restarts. Run a short-lived background request with a timeout and a bounded response; use conditional HTTP requests where supported. No permanent updater service or extra disk scans.
- Add "Check for updates" to About and the main tray menu, plus an Options setting to disable automatic checks.
- Notify once per new version with a small tray notification and an unobtrusive in-app notice. Offer "View release / download", "Remind me later", and "Skip this version"; retain those choices across restarts.
- Open the official release page so the user can read the changelog and download the Windows installer. Keep existing index/settings during the upgrade. Do not automatically download, run an installer, close the app, or interrupt monitoring/work.
- Offline, timeout, and rate-limit failures stay quiet during automatic checks and defer until a later scheduled check. A manual check shows a clear result or failure message without blocking the UI. Prevent overlapping requests and notification spam.
- Send no index, paths, activity history, diagnostics, or drive details with the request; only request public release metadata using an application User-Agent.
- A future optional download-and-install flow can build on this after the notification-only flow is tested. It would verify the intended release/Windows installer and its integrity/authenticity, request permission to install, and shut down monitoring cleanly before Windows elevation. No silent automatic installation is planned for the first implementation.

### Acceptance checks

- Test newer, equal, older, malformed, draft, and prerelease versions, including numerical ordering such as 1.4.10 versus 1.4.9.
- Verify cached check times, disabled checks, skipped versions, reminder behavior, offline/rate-limit recovery, and one request at a time.
- Confirm checks do not block the UI, start indexing, reset data, or create a resident background process.
- Confirm clicking the notification opens only the expected HTTPS GitHub release page and never executes remote content.

API reference: [GitHub Releases REST API](https://docs.github.com/en/rest/releases/releases?apiVersion=latest).

## Investigate recurring drive scans after startup

- Owner screenshot from 2026-10-29 shows C: returning to INDEXING after the previous night's completed scan, with 346 held paths. Other online drives remain READY.
- Code inspection confirms INDEXING means a whole-drive Indexer is active; targeted FolderUpdater jobs do not set this status. This is not merely ordinary live file updates.
- The engine can initiate a recovery scan after a Windows notification overflow, watcher reconnection, or explicit retry. Overflows during an active scan queue another scan. The original pre-1.4.6 diagnostics did not record successful recovery-scan triggers, so the screenshot alone does not establish which trigger occurred.
- Record bounded, local scan-start reasons and completion information, and show the reason in the UI. Distinguish startup scans, recovery scans, and targeted folder updates.
- Investigate notification overflow frequency and watcher processing/re-arm delays on busy C: drives. Avoid repeated full-drive work while preserving recovery of genuinely missed changes; do not simply discard overflow signals.
- Confirm with a sustained high-change workload that monitoring remains low priority, ordinary changes do not start whole-drive scans, and necessary recovery does not become a repeated scan loop.

## Folder report and Activity log layout

- Fit the default columns within each view's available width, including room for the vertical scrollbar. No horizontal scrolling should be necessary at the default column widths.
- Keep columns manually resizable; allow horizontal scrolling when the user deliberately widens them beyond the available space.
- Give the Folder/Path column the remaining space after sizing the date, drive, action, count, and size columns appropriately. Recalculate the default layout when the window is resized, without overriding manual column adjustments.
- Display as much of the full folder or file path as the cell permits. Truncate only when necessary, with the ellipsis at the end, rather than prematurely showing `C:...` in a wide cell.
- Make the full untruncated path available in a tooltip.

### Acceptance checks

- Check both tabs at normal and maximized window sizes and Windows display scaling settings.
- Confirm all default columns are visible without horizontal scrolling, with enough rows to show a vertical scrollbar.
- Confirm long paths use the full available cell width before the trailing ellipsis, and short paths remain fully visible.
- Confirm manual column resizing still works and horizontal scrolling appears when needed after widening columns.
- Keep the layout changes lightweight; do not add filesystem scans or background polling.
