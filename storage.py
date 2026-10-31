"""SQLite storage for File Burglar.

- drives:       one row per drive root: scan generation, baseline flag, totals
- folders:      folder paths; `scan` is the generation of the last scan that saw them
- files:        (folder_id, name) -> size, the index itself
- file_log:     detected changes (the detailed report). Repeated changes to one
                file within an hour are merged into a single row, and the table is
                capped by age and row count so busy folders cannot grow it unbounded.
- folder_daily: per-day, per-folder totals that feed the Folder report
"""

import os
import sqlite3
import sys
import threading
import time
import json
import volumes
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
CREATE TABLE IF NOT EXISTS volume_identity(root TEXT PRIMARY KEY, identity TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""

DAILY_UPSERT = """
INSERT INTO folder_daily (day, drive, folder, events, added, freed, last_ts, volume_id, relative_path)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (day, drive, folder) DO UPDATE SET
    events = events + excluded.events,
    added = added + excluded.added,
    freed = freed + excluded.freed,
    last_ts = MAX(last_ts, excluded.last_ts),
    volume_id = COALESCE(excluded.volume_id, volume_id), relative_path = excluded.relative_path
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

# Activity log size controls. A program that rewrites the same files every few
# seconds produced millions of rows a day; merging per file and hour keeps the
# history while bounding the table. Set LOG_MERGE_SECONDS to 0 to disable merging.
LOG_MERGE_SECONDS = 3600
LOG_MAX_ROWS = 500_000
_MERGE_PATHS = 20_000  # recent paths remembered for merging (bounded memory)
_merge_lock = threading.Lock()
_merge_state = {}  # database file -> [bucket, {path: [rowid, action]}]
EXCLUSIONS = ()
BASELINE_PATHS = ()


def set_exclusions(paths):
    global EXCLUSIONS
    EXCLUSIONS=tuple(sorted({os.path.normcase(os.path.abspath(p)).rstrip('\\') for p in paths
                             if isinstance(p,str) and os.path.isabs(p)}))


def is_excluded_path(path):
    if not EXCLUSIONS:return False
    candidate=os.path.normcase(os.path.abspath(path)).rstrip('\\')
    return any(candidate==p or candidate.startswith(p+'\\') for p in EXCLUSIONS)


def is_ignored_path(path):
    return is_own_path(path) or is_excluded_path(path)


def prune_exclusions(con):
    """Forget excluded coverage without recording fictitious deletions."""
    global BASELINE_PATHS
    row=con.execute("SELECT value FROM app_meta WHERE key='exclusions'").fetchone()
    encoded=json.dumps(EXCLUSIONS)
    if row and row[0]==encoded:return
    previous=json.loads(row[0]) if row else []
    BASELINE_PATHS=tuple(p for p in previous if not is_excluded_path(p))
    for path in EXCLUSIONS:
        clause='(path=? COLLATE NOCASE OR (path>=? COLLATE NOCASE AND path<? COLLATE NOCASE))'
        args=(path,path+'\\',path+']')
        while True:
            rows=con.execute('SELECT id FROM folders WHERE '+clause+' LIMIT 256',args).fetchall()
            if not rows:break
            con.executemany('DELETE FROM files WHERE folder_id=?',rows)
            con.executemany('DELETE FROM folders WHERE id=?',rows)
        con.execute('DELETE FROM file_log WHERE '+clause,args)
        con.execute('DELETE FROM folder_daily WHERE '+clause.replace('path','folder'),args)
    for (root,) in con.execute('SELECT root FROM drives').fetchall():
        normalized=os.path.normcase(root).rstrip('\\')
        if not any(p==normalized or p.startswith(normalized+'\\') for p in EXCLUSIONS):continue
        lo,hi=drive_range(root)
        count,size=con.execute('SELECT COALESCE(SUM(COALESCE(file_count,(SELECT COUNT(*) FROM files WHERE folder_id=folders.id))),0),COALESCE(SUM(COALESCE(byte_count,(SELECT SUM(size) FROM files WHERE folder_id=folders.id))),0) FROM folders WHERE path>=? AND path<?',(lo,hi)).fetchone()
        con.execute('UPDATE drives SET files=?,bytes=? WHERE root=?',(count,size,root))
    con.execute("INSERT INTO app_meta VALUES('exclusions',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(encoded,))
    con.commit()


def needs_baseline(path):
    path=os.path.normcase(os.path.abspath(path)).rstrip('\\')
    return any(path==p or path.startswith(p+'\\') for p in BASELINE_PATHS)


def completed_baseline(path):
    global BASELINE_PATHS
    path=os.path.normcase(os.path.abspath(path)).rstrip('\\')
    BASELINE_PATHS=tuple(p for p in BASELINE_PATHS if not (p==path or p.startswith(path+'\\')))


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
    # Truncate the write-ahead log after checkpoints so a large scan cannot leave
    # a multi-gigabyte -wal file behind.
    con.execute("PRAGMA journal_size_limit=67108864")
    return con


def init(db_path: str) -> None:
    import volumes
    volumes.check_upgrade_space(db_path)
    volumes.backup_before_migration(db_path)
    if not os.path.exists(db_path) or not os.path.getsize(db_path):
        # New databases return freed pages to Windows after old history is
        # pruned. This must be set before WAL mode and before any table exists.
        fresh = sqlite3.connect(db_path)
        try:
            fresh.execute("PRAGMA auto_vacuum=INCREMENTAL")
            fresh.execute("PRAGMA journal_mode=WAL")
        finally:
            fresh.close()
    con = connect(db_path)
    try:
        _init_schema(con)
    finally:
        con.close()
    volumes.cleanup_migration_backups(db_path)


def _init_schema(con):
    con.executescript(SCHEMA)
    if "mtime_ns" not in {row[1] for row in con.execute("PRAGMA table_info(files)")}:
        con.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0")
    columns = {row[1] for row in con.execute("PRAGMA table_info(folders)")}
    for column in ("file_count", "byte_count"):
        if column not in columns:
            con.execute(f"ALTER TABLE folders ADD COLUMN {column} INTEGER")
    con.commit()
    volumes.migrate(con)


def default_db_path() -> str:
    return os.path.join(data_dir(), DB_FILE)


def filter_root(con,value):
    if not value:return None
    row=con.execute('SELECT namespace FROM volumes WHERE id=?',(value,)).fetchone()
    return row[0] if row else value.rstrip('\\')+'\\'


def filter_identity(con,value):
    if not value:return None
    return value if con.execute('SELECT 1 FROM volumes WHERE id=?',(value,)).fetchone() else None


def drive_range(root: str):
    """All paths on a drive start with its root and sort below root[:-1] + ']'."""
    return root, root.rstrip("\\/") + "]"


def folder_id(con: sqlite3.Connection, path: str) -> int:
    """Id of a folder row, created on demand; its scan generation is left alone."""
    import volumes
    root=os.path.splitdrive(path)[0]+os.sep
    con.execute("INSERT OR IGNORE INTO folders (path,volume_id) VALUES (?,?)", (path,volumes.owner(con,path)))
    return con.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()[0]


def visit_folder(con: sqlite3.Connection, path: str, gen: int) -> int:
    """Id of a folder row, marked as seen by scan generation `gen`."""
    return con.execute(
        "INSERT INTO folders (path, scan,volume_id) VALUES (?, ?, ?) "
        "ON CONFLICT (path) DO UPDATE SET scan = excluded.scan RETURNING id",
        (path, gen, volumes.owner(con,path)),
    ).fetchone()[0]


def merged_action(previous, new):
    """Net action for two changes to one path; None means nothing remains to report."""
    if new == "removed":
        return None if previous == "added" else "removed"
    if new == "added":
        return "changed" if previous == "removed" else previous
    return previous


def _merge_paths(name, bucket):
    state = _merge_state.get(name)
    if state is None:
        if len(_merge_state) >= 4:
            _merge_state.clear()
        state = _merge_state[name] = [bucket, {}]
    if state[0] != bucket:
        state[0] = bucket
        state[1].clear()
    return state[1]


INSERT_LOG = ("INSERT INTO file_log (ts,path,action,delta,size,volume_id,observed_root,legacy_unverified) "
              "VALUES(?,?,?,?,?,?,?,0)")


def _write_log(con, rows, owners):
    if LOG_MERGE_SECONDS <= 0:
        con.executemany(INSERT_LOG, [(ts, path, action, delta, size,
                                      owners.get(os.path.splitdrive(path)[0] + os.sep),
                                      os.path.splitdrive(path)[0] + os.sep)
                                     for ts, path, action, delta, size in rows])
        return
    name = con.execute("PRAGMA database_list").fetchone()[2] or ":memory:"
    with _merge_lock:
        for ts, path, action, delta, size in rows:
            recent = _merge_paths(name, int(ts // LOG_MERGE_SECONDS))
            hit = recent.get(path)
            root = os.path.splitdrive(path)[0] + os.sep
            volume = owners.get(root)
            if hit is not None:
                merged = merged_action(hit[1], action)
                # The id, path and volume must all still match, so a pruned,
                # rolled-back or remapped row is never merged into.
                if merged is None:
                    # Created and deleted within the hour: no net change to report.
                    cursor = con.execute("DELETE FROM file_log WHERE id=? AND path=? AND volume_id IS ?",
                                         (hit[0], path, volume))
                else:
                    cursor = con.execute("UPDATE file_log SET ts=?, action=?, delta=delta+?, size=? "
                                         "WHERE id=? AND path=? AND volume_id IS ?",
                                         (ts, merged, delta, size, hit[0], path, volume))
                if cursor.rowcount:
                    if merged is None:
                        del recent[path]
                    else:
                        hit[1] = merged
                    continue
                del recent[path]  # row was pruned, rolled back or moved; start a new one
            cursor = con.execute(INSERT_LOG, (ts, path, action, delta, size, volume, root))
            if len(recent) < _MERGE_PATHS:
                recent[path] = [cursor.lastrowid, action]


def prune_log(con, max_age_seconds, max_rows=None, chunk=20_000):
    """Delete one bounded chunk of expired or excess history. True means more remains."""
    max_rows = LOG_MAX_ROWS if max_rows is None else max_rows
    cutoff = time.time() - max_age_seconds
    removed = con.execute("DELETE FROM file_log WHERE id IN (SELECT id FROM file_log WHERE ts < ? LIMIT ?)",
                          (cutoff, chunk)).rowcount
    if removed < chunk:
        row = con.execute("SELECT id FROM file_log ORDER BY id DESC LIMIT 1 OFFSET ?", (max_rows,)).fetchone()
        if row:
            removed += con.execute("DELETE FROM file_log WHERE id IN (SELECT id FROM file_log WHERE id <= ? "
                                   "ORDER BY id LIMIT ?)", (row[0], chunk - removed)).rowcount
    with _merge_lock:
        _merge_state.clear()  # merged rows may have been deleted
    return removed >= chunk


def release_free_space(con, pages=4096):
    """Give up to `pages` free database pages back to Windows (incremental-vacuum databases)."""
    if con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
        con.execute(f"PRAGMA incremental_vacuum({int(pages)})").fetchall()


def record(con: sqlite3.Connection, rows) -> None:
    """Store change rows (ts, path, action, delta, size) and roll them into folder_daily."""
    if not rows:
        return
    owners=dict(con.execute('SELECT root,volume_id FROM drives'))
    _write_log(con, rows, owners)
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
        [(day, drive, folder, *vals,owners.get(drive+os.sep),None) for (day, drive, folder), vals in totals.items()],
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
