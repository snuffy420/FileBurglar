# File Burglar 1.5.3 test installer

Quit File Burglar from its system tray menu, then run this installer over your
installed copy in the same installation folder. Launch File Burglar from its
Start menu shortcut. Do not launch FileBurglar.cmd or main.py from the source
checkout: the source checkout has a separate database and settings.

Fixes included:
- Dark, readable confirmation, warning, error, input and progress dialogs.
- Physical and virtual devices no longer group together just because they
  report the same device number. Shared-device groups require PnP identity.
- Tray choices save against the latest volume mapping and survive restarts,
  disconnects and changed letters without resetting other drive preferences.
- Check free space before the one-time database backup/upgrade and show a
  readable startup error instead of the unhandled crash dialog.

All features from 1.5.2 are retained. Existing installed data is preserved in
%LOCALAPPDATA%\FileBurglar. Known saved volume identities migrate automatically.
Old snapshots that have no device identity stay separate; linking them is
optional and requires confirming that they belong to the same volume.

A legacy upgrade needs temporary room for its safety backup, database expansion
and journal. The space check reserves three times the existing database size
plus 256 MiB free. After successful migration this check is skipped. Existing
backups are kept, and no database or personal files are deleted by this build.

Validation: 130 automated tests passed. Packaged-runtime validation is recorded
in VERIFICATION.json. No live install, physical ejection or drive-letter change
is performed by the build process.
