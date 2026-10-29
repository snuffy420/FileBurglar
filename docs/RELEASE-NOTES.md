# File Burglar 1.4.5 — initial Windows release

File Burglar indexes your storage, monitors file activity, and makes it easier to see which folders are consuming or freeing disk space. This first public release supports **Windows 10/11 x64**.

## Added in 1.4.5

- Main-window About button and tray access to About / Help / Donate.
- Version/author/license information, practical help, and contact/repository links.
- Optional BTC donations using the maintainer’s existing address, verified QR code, and copy-address button.
- Updated README, contact page, and screenshots for all new tabs.
- The tested 1.4.4 monitoring behavior is retained.

## Included

- Persistent file/folder index and fast, parallel startup scanning.
- Live Windows change monitoring; new folders receive targeted background updates rather than another whole-drive index.
- Own database notifications filtered before queuing, eliminating a self-triggered re-indexing loop.
- Dashboard, folder activity reports, searchable event history, and a responsive indexed Explorer with breadcrumbs, search, and pagination.
- Available drive health/SMART counters, temperature tray icons, customizable warm/hot alerts, and ATA temperature fallback.
- Pause/resume, per-drive monitoring, optional logon startup, local diagnostics, and a dark/orange interface.
- Exact handling of legacy timestamps, compatible saved-index upgrades, and nonblocking controls during busy file activity.

## Downloads

- **Installer:** `FileBurglar-1.4.5-windows-x64-setup.exe`
- **Portable:** `FileBurglar-1.4.5-windows-x64-portable.7z` — extract the entire folder, then run `FileBurglar.exe`.
- **Source:** `FileBurglar-1.4.5-source.7z`.
- **Checksums:** `SHA256SUMS.txt`, with individual `.sha256` files.

No Python installation is needed for the installer or portable downloads. Administrator access is requested for drive monitoring/counters. Portable data stays in its `Data` folder; installed data stays in `%LOCALAPPDATA%\FileBurglar`.

The opening scan can use noticeable resources. After it completes the app primarily consumes Windows change notifications, with one low-priority worker for changed folders. A whole-drive reconciliation may still be required after a real notification-buffer loss or drive reconnection. Hardware/driver support determines which temperature counters are available. This release has no macOS/Linux packages and is unsigned.

See [CHANGELOG.md](https://github.com/snuffy420/FileBurglar/blob/main/CHANGELOG.md) for the complete feature list. [Contact File Burglar](https://snuffy420.github.io/FileBurglar/docs/contact.html).
