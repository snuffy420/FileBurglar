"""SQLite storage for File Burglar.

- drives:       one row per drive root: scan generation, baseline flag, totals
- folders:      folder paths; `scan` is the generation of the last scan that saw them
- files:        (folder_id, name) -> size, the index itself
- file_log:     one row per detected change (the detailed report)
- folder_daily: per-day, per-folder totals that feed the Folder report
"""

import os
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta

from appinfo import APP_FOLDER, DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS drives (
    root TEXT PRIMARY KEY,
    gen INTEGER NOT NULL DEFAULT 0,
    baselined INTEGER NOT NULL DEFAULT 0,
    files INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    last_scan REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    scan INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER,
    byte_count INTEGER
);
CREATE TABLE IF NOT EXISTS files (
    folder_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (folder_id, name)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS file_log (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    delta INTEGER NOT NULL,
    size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_file_log_ts ON file_log (ts);
CREATE TABLE IF NOT EXISTS folder_daily (
    day TEXT NOT NULL,
    drive TEXT NOT NULL,
    folder TEXT NOT NULL,
    events INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    freed INTEGER NOT NULL DEFAULT 0,
    last_ts REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, drive, folder)
) WITHOUT ROWID;
"""

DAILY_UPSERT = """
INSERT INTO folder_daily (day, drive, folder, events, added, freed, last_ts)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (day, drive, folder) DO UPDATE SET
    events = events + excluded.events,
    added = added + excluded.added,
    freed = freed + excluded.freed,
    last_ts = MAX(last_ts, excluded.last_ts)
"""


FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))


def runtime_data_dir(app_dir=None, frozen=None, local_appdata=None):
    app_dir = app_dir or APP_DIR
    frozen = FROZEN if frozen is None else frozen
    if not frozen:
        return app_dir  # Keep existing source-checkout indexes in place.
    if os.path.isfile(os.path.join(app_dir, "portable.txt")):
        return os.path.join(app_dir, "Data")
    return os.path.join(local_appdata or os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_FOLDER)


DATA_DIR = runtime_data_dir()

# Writers in this program take turns on this lock. Each one commits before releasing it,
# so no connection holds the database write lock while another writer waits on it.
WRITE_LOCK = threading.RLock()


def data_dir() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def is_own_path(path: str) -> bool:
    """True for anything inside the program's own folder, so its database writes are never logged."""
    candidate = os.path.normcase(os.path.abspath(path))
    roots = (DATA_DIR, APP_DIR) if FROZEN else (DATA_DIR,)
    for folder in roots:
        folder = os.path.normcase(os.path.abspath(folder))
        if candidate == folder or candidate.startswith(folder + os.sep):
            return True
    return False


def timestamp_key(nanoseconds):
    """Lossless SQLite key for Windows dates outside signed 64-bit nanoseconds.

    SQLite INTEGER-affinity columns can also store text. The nonnumeric prefix
    prevents SQLite from converting extreme dates to an imprecise REAL value.
    Existing ordinary integer timestamps remain unchanged; no index rebuild.
    """
    if -(1 << 63) <= nanoseconds < (1 << 63):
        return nanoseconds
    return "ns:" + str(nanoseconds)


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-4096")  # 4 MiB page cache keeps RAM low
    return con


def init(db_path: str) -> None:
    con = connect(db_path)
    con.executescript(SCHEMA)
    if "mtime_ns" not in {row[1] for row in con.execute("PRAGMA table_info(files)")}:
        con.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0")
    columns = {row[1] for row in con.execute("PRAGMA table_info(folders)")}
    for column in ("file_count", "byte_count"):
        if column not in columns:
            con.execute(f"ALTER TABLE folders ADD COLUMN {column} INTEGER")
    con.commit()
    con.close()


def default_db_path() -> str:
    return os.path.join(data_dir(), DB_FILE)


def drive_range(root: str):
    """All paths on a drive start with its root and sort below root[:-1] + ']'."""
    return root, root.rstrip("\\/") + "]"


def folder_id(con: sqlite3.Connection, path: str) -> int:
    """Id of a folder row, created on demand; its scan generation is left alone."""
    con.execute("INSERT OR IGNORE INTO folders (path) VALUES (?)", (path,))
    return con.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()[0]


def visit_folder(con: sqlite3.Connection, path: str, gen: int) -> int:
    """Id of a folder row, marked as seen by scan generation `gen`."""
    return con.execute(
        "INSERT INTO folders (path, scan) VALUES (?, ?) "
        "ON CONFLICT (path) DO UPDATE SET scan = excluded.scan RETURNING id",
        (path, gen),
    ).fetchone()[0]


def record(con: sqlite3.Connection, rows) -> None:
    """Store change rows (ts, path, action, delta, size) and roll them into folder_daily."""
    if not rows:
        return
    con.executemany(
        "INSERT INTO file_log (ts, path, action, delta, size) VALUES (?, ?, ?, ?, ?)", rows
    )
    totals = {}
    for ts, path, _action, delta, _size in rows:
        folder = os.path.split(path)[0]
        key = (date.fromtimestamp(ts).isoformat(), path[:2], folder)
        vals = totals.get(key)
        if vals is None:
            vals = totals[key] = [0, 0, 0, 0.0]
        vals[0] += 1
        if delta > 0:
            vals[1] += delta
        elif delta < 0:
            vals[2] -= delta
        if ts > vals[3]:
            vals[3] = ts
    con.executemany(
        DAILY_UPSERT,
        [(day, drive, folder, *vals) for (day, drive, folder), vals in totals.items()],
    )


def day_start(days) -> str:
    """Earliest day string for a report period; None means all time."""
    if days is None:
        return "0000-00-00"
    return (date.today() - timedelta(days=days)).isoformat()


def folder_totals(con: sqlite3.Connection):
    """Folder tree from the index: (totals, children, roots).

    totals maps each folder to [bytes, files] including all of its subfolders.
    children maps a folder to its direct subfolders; roots lists the drive roots.
    """
    rows = con.execute(
        "SELECT d.path, COALESCE(SUM(f.size), 0), COUNT(f.name) FROM folders d "
        "LEFT JOIN files f ON f.folder_id = d.id GROUP BY d.id"
    ).fetchall()
    totals = {}
    for path, size, count in rows:
        p = path
        while True:
            vals = totals.get(p)
            if vals is None:
                totals[p] = [size, count]
            else:
                vals[0] += size
                vals[1] += count
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    children = {}
    roots = []
    for p in totals:
        parent = os.path.dirname(p)
        if parent == p:
            roots.append(p)
        else:
            children.setdefault(parent, []).append(p)
    return totals, children, sorted(roots)


def folder_files(con: sqlite3.Connection, path: str):
    """(name, size) for the files directly inside one folder."""
    return con.execute(
        "SELECT f.name, f.size FROM files f JOIN folders d ON d.id = f.folder_id WHERE d.path = ?",
        (path,),
    ).fetchall()


def now() -> float:
    return time.time()


def explorer_page(con, path=None, search="", offset=0, limit=500):
    """One bounded browser page. Root view never aggregates the file index.

    Folder counters are filled by normal scans, so modern indexes only read
    directory summaries. Legacy NULL counters fall back to indexed file rows.
    """
    if path is None:
        rows = con.execute("SELECT root, bytes, files FROM drives ORDER BY bytes DESC, root").fetchall()
        records = [("drive", root, root, size, count) for root, size, count in rows
                   if search.casefold() in root.casefold()]
        return records[offset:offset + limit], len(records) > offset + limit
    prefix = path.rstrip("\\") + "\\"
    hi = path.rstrip("\\") + "]"
    columns = {row[1] for row in con.execute("PRAGMA table_info(folders)")}
    count = "COALESCE(d.file_count, (SELECT COUNT(*) FROM files WHERE folder_id=d.id))" if "file_count" in columns else "(SELECT COUNT(*) FROM files WHERE folder_id=d.id)"
    size = "COALESCE(d.byte_count, (SELECT COALESCE(SUM(size),0) FROM files WHERE folder_id=d.id))" if "byte_count" in columns else "(SELECT COALESCE(SUM(size),0) FROM files WHERE folder_id=d.id)"
    sql = f"""
    WITH descendants AS (
        SELECT substr(d.path, ?) AS relative, {count} AS n, {size} AS bytes
        FROM folders d WHERE d.path >= ? AND d.path < ? AND d.path != ?
    ), branches AS (
        SELECT CASE WHEN instr(relative, '\\')=0 THEN relative
                    ELSE substr(relative, 1, instr(relative, '\\')-1) END AS name,
               SUM(bytes) AS bytes, SUM(n) AS n
        FROM descendants GROUP BY name
    ), entries AS (
        SELECT 'folder' AS kind, ? || name AS path, name, bytes, n FROM branches
        UNION ALL
        SELECT 'file', ? || f.name, f.name, f.size, NULL FROM files f
        WHERE f.folder_id=(SELECT id FROM folders WHERE path=?)
    )
    SELECT kind,path,name,bytes,n FROM entries
    WHERE instr(lower(name), lower(?)) > 0
    ORDER BY bytes DESC, name COLLATE NOCASE, path LIMIT ? OFFSET ?
    """
    rows = con.execute(sql, (len(prefix)+1, prefix, hi, path, prefix, prefix, path,
                             search, limit+1, offset)).fetchall()
    return rows[:limit], len(rows) > limit


def update_drive_totals(con, rows):
    """Apply logged file deltas after either live writes or scoped reconciliation."""
    changes = {}
    for _ts, path, action, delta, _size in rows:
        root = os.path.splitdrive(path)[0] + os.sep
        count, size = changes.get(root,(0,0))
        count += 1 if action == "added" else -1 if action == "removed" else 0
        changes[root] = (count,size+delta)
    for root,(count,size) in changes.items():
        con.execute("UPDATE drives SET files=files+?,bytes=bytes+? WHERE root=?",(count,size,root))
    return changes
