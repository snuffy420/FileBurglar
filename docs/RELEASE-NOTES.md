# File Burglar 1.5.4

File Burglar indexes your storage, monitors file activity, and shows which folders are consuming or freeing disk space. This release supports **Windows 10/11 x64** and includes everything added since 1.4.5.

## New in 1.5.4: much lower disk, CPU and memory use

- Repeated changes to the same file are merged into one activity row per hour. Programs that rewrite their data files every few seconds previously added millions of rows a day.
- A file created and deleted within the same hour no longer leaves activity rows.
- The activity log is capped at 500,000 rows and 30 days, trimmed in small background chunks.
- A large per-row index on the activity log was removed (about 1 GB on busy systems).
- New databases give freed space back to Windows automatically, and the write-ahead log is truncated after checkpoints.
- At most one pre-upgrade safety backup is kept, reused on retries, and removed 7 days after a completed upgrade.
- Notification bursts are collected briefly so each changed path is checked once.
- Whole-drive recovery scans after lost notifications run at most once every 15 minutes per drive.
- Drives that refuse Windows removal notifications (for example Google Drive's virtual disk) keep their file watcher instead of restarting every 5 seconds.

## Added since 1.4.5

- **Stable drive identity:** saved data follows a volume by its Windows identity, so a drive that changes letters keeps its index, history and settings. Offline drives keep their last letter.
- **Device removal:** File Burglar cooperates with Windows Eject, with Release monitoring, Resume monitoring and optional safe-eject actions.
- **Drive health:** independent health and temperature switches, with one warm alert and one hot escalation per heat episode.
- **Game/work mode:** manual or automatic by application name; heavy catch-up work waits until you are done.
- **Folder exclusions** with clean index and report pruning, and targeted baselines when folders are re-included.
- **What used my space?** view of hour, day and week net growth.
- Optional low-free-space and rapid-folder-growth notifications.
- Explicit monitoring states (Watching, Updating folders, Deferred, Recovering, Paused, Released, Offline, Error) in the window and tray tooltip.
- Copy-full-path actions and full filtered CSV exports.
- Reconnect summaries for known drives, a Refresh drives button, and daily stable-release update checks.
- Dark, readable dialogs and a themed tracking-options editor.

## Downloads

- **Installer:** `FileBurglar-1.5.4-windows-x64-setup.exe`
- **Portable:** `FileBurglar-1.5.4-windows-x64-portable.7z`. Extract the entire folder, then run `FileBurglar.exe`.
- **Source:** `FileBurglar-1.5.4-source.7z`
- **Checksums:** `SHA256SUMS.txt`, with an individual `.sha256` file for each download.

No Python installation is needed for the installer or portable downloads. Administrator access is requested for drive monitoring and counters. Portable data stays in its `Data` folder; installed data stays in `%LOCALAPPDATA%\FileBurglar`. Installing over an earlier version keeps your saved data.

The opening scan can use noticeable resources. After it completes, the app mainly consumes Windows change notifications with low-priority workers. A whole-drive reconciliation may still run after a real notification-buffer loss or drive reconnection. Hardware and driver support determine which temperature counters are available. This release is unsigned and has no macOS or Linux packages.

See [CHANGELOG.md](https://github.com/snuffy420/FileBurglar/blob/main/CHANGELOG.md) for the complete list. [Contact File Burglar](https://snuffy420.github.io/FileBurglar/docs/contact.html).
