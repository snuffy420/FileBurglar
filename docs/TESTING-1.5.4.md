# File Burglar 1.5.4 test build

This build fixes File Burglar's disk growth. On the test PC the database had
reached 4.7 GB, plus three 2.5 GB pre-upgrade backups, in about a day and a half.

Portable test (recommended): extract the portable folder anywhere, for example on
K:, and run FileBurglar.exe from it. Portable mode keeps its index, settings and
logs in the Data folder beside the program, so nothing is written to C:.
Quit any installed copy from its tray menu first; only one copy can run at a time.

Installer test: quit File Burglar from its tray menu and run the setup over the
installed copy. Data stays in %LOCALAPPDATA%\FileBurglar.

What to check:
- After the startup scan finishes, the Data folder (or %LOCALAPPDATA%\FileBurglar)
  should grow slowly even while programs rewrite files constantly.
- The Activity log shows one row per file per hour for busy files, with the
  hour's total size change.
- Drive tiles return to Watching after a lost-notification recovery and do not
  start another full rescan for at least 15 minutes.

Existing large databases are trimmed gradually in the background. Their file
size only drops after a fresh start, because older databases cannot release free
pages without a full rebuild. Deleting the old data folder before testing gives
the cleanest result.
