"""Startup reconcile: index a drive and log what changed while the PC was off.

Each drive's first scan per application launch uses parallel higher-priority
workers, including drives with saved indexes. Later rescans use serialized,
throttled background reconciliation. Each scan
uses one database writer and bounded batches.

A folder is compared as a unit with the saved index and only changed folders
write rows. The first scan of a drive is a baseline: it fills the index
without logging, so a fresh install does not flood the report.
"""

import ctypes
import queue
import os
import threading
import time

import storage

_kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None
THREAD_MODE_BACKGROUND_BEGIN = 0x00010000
THREAD_PRIORITY_LOWEST = -2
BATCH_FOLDERS = 32
SCAN_SLOT = threading.Semaphore(1)
_FAST_LOCK = threading.Lock()
_FAST_COUNT = 0
_OLD_PRIORITY = None


def fast_priority(begin):
    """Temporarily boost the process while fast startup scans are active."""
    global _FAST_COUNT, _OLD_PRIORITY
    if _kernel32 is None:
        return
    _kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    _kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    _kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    handle = _kernel32.GetCurrentProcess()
    with _FAST_LOCK:
        if begin:
            if _FAST_COUNT == 0:
                _OLD_PRIORITY = _kernel32.GetPriorityClass(handle)
                _kernel32.SetPriorityClass(handle, 0x00008000)  # ABOVE_NORMAL_PRIORITY_CLASS
            _FAST_COUNT += 1
        else:
            _FAST_COUNT -= 1
            if _FAST_COUNT == 0 and _OLD_PRIORITY:
                _kernel32.SetPriorityClass(handle, _OLD_PRIORITY)


def raise_thread_priority():
    if _kernel32 is not None:
        _kernel32.GetCurrentThread.restype = ctypes.c_void_p
        _kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _kernel32.SetThreadPriority(_kernel32.GetCurrentThread(), 1)



def lower_thread_priority() -> None:
    """Background mode lowers CPU, memory and disk priority for this thread."""
    if _kernel32 is None:
        return
    _kernel32.GetCurrentThread.restype = ctypes.c_void_p
    _kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    handle = _kernel32.GetCurrentThread()
    if not _kernel32.SetThreadPriority(handle, THREAD_MODE_BACKGROUND_BEGIN):
        _kernel32.SetThreadPriority(handle, THREAD_PRIORITY_LOWEST)


def worker_count() -> int:
    return min(32, max(2, (os.cpu_count() or 2) * 2))


def list_folder(path):
    """Return file (size, modification time) pairs and subfolders.

    None means the directory could not be read completely. Preserve its saved
    subtree instead of interpreting an access failure as deletion.
    """
    files, subdirs = {}, []
    if storage.is_ignored_path(path) or os.path.normcase(os.path.abspath(path)) == os.path.normcase(storage.DATA_DIR):
        return files, subdirs
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not entry.is_junction() and not storage.is_ignored_path(entry.path):
                            subdirs.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        files[entry.name] = (st.st_size, storage.timestamp_key(st.st_mtime_ns))
                except OSError:
                    return None, subdirs
    except OSError:
        return None, []
    return files, subdirs


def preserve_subtree(con, path, gen):
    lo, hi = path.rstrip("\\") + "\\", path.rstrip("\\") + "]"
    con.execute("UPDATE folders SET scan = ? WHERE path = ? OR (path >= ? AND path < ?)",
                (gen, path, lo, hi))


def reconcile_folder(con, gen, baselined, path, files, log):
    """Reconcile files; migrated timestamps establish a baseline without false edits."""
    if storage.is_excluded_path(path):return 0,0
    if storage.needs_baseline(path):baselined=False
    if files is None:
        preserve_subtree(con, path, gen)
        return 0, 0
    fid = storage.visit_folder(con, path, gen)
    con.execute("UPDATE folders SET file_count=?, byte_count=? WHERE id=?",
                (len(files), sum(v[0] for v in files.values()), fid))
    old = {n: (s, m) for n, s, m in con.execute(
        "SELECT name, size, mtime_ns FROM files WHERE folder_id = ?", (fid,))}
    if old == files:
        return sum(v[0] for v in files.values()), len(files)
    if baselined:
        ts = time.time()
        for name, (size, mtime) in files.items():
            prev = old.get(name)
            if prev is not None and prev[0] == size and (prev[1] == 0 or prev[1] == mtime):
                continue
            log.append((ts, os.path.join(path, name), "added" if prev is None else "changed",
                        size - (prev[0] if prev else 0), size))
        for name, (size, _mtime) in old.items():
            if name not in files:
                log.append((ts, os.path.join(path, name), "removed", -size, 0))
    con.execute("DELETE FROM files WHERE folder_id = ?", (fid,))
    con.executemany("INSERT INTO files (folder_id, name, size, mtime_ns) VALUES (?, ?, ?, ?)",
                    [(fid, n, *value) for n, value in files.items()])
    return sum(v[0] for v in files.values()), len(files)


class Indexer:
    """Walks one drive and reconciles it with the saved index."""

    def __init__(self, db_path, root, on_status=None, fast_startup=False):
        self.db_path = db_path
        self.fast_startup = fast_startup
        self.fast_mode = False
        self.root = root
        self.on_status = on_status or (lambda root, state: None)
        self.stop_event = threading.Event()
        self.pause_check = lambda: False
        self.guard_check = lambda: True
        self.on_progress = lambda files, size, initial: None
        self._progress_at = 0.0

    def run(self) -> None:
        """Startup speed is independent of the persisted history/baseline flag."""
        con = storage.connect(self.db_path)
        try:
            row = con.execute("SELECT baselined FROM drives WHERE root=?", (self.root,)).fetchone()
            initial = self.fast_startup or not (row and row[0])
            self.fast_mode = initial
        finally:
            con.close()
        if initial and not self.stop_event.is_set():
            fast_priority(True)
            try:
                self._run_scan()
            finally:
                fast_priority(False)
            return
        while not self.stop_event.is_set():
            if SCAN_SLOT.acquire(timeout=0.2):
                try:
                    self._run_scan()
                finally:
                    SCAN_SLOT.release()
                return
        self.on_status(self.root, "stopped")

    def _run_scan(self):
        if not self._valid():self.on_status(self.root,'stopped');return
        self.on_status(self.root, "indexing")
        con = storage.connect(self.db_path)
        try:
            row = con.execute("SELECT gen, baselined FROM drives WHERE root = ?", (self.root,)).fetchone()
            gen = (row[0] if row else 0) + 1
            baselined = bool(row and row[1])
            full = not baselined
            with storage.WRITE_LOCK:
                con.execute("INSERT INTO drives(root,gen) VALUES (?,?) "
                            "ON CONFLICT(root) DO UPDATE SET gen=excluded.gen", (self.root,gen))
                con.commit()
            if self.fast_mode:
                raise_thread_priority()
                total_files, total_bytes = self._walk_parallel(con, gen, baselined=baselined)
            else:
                lower_thread_priority()
                total_files, total_bytes = self._walk(con, gen, baselined)
            if self.stop_event.is_set():
                self.on_status(self.root, "stopped")
                return
            with storage.WRITE_LOCK:
                if not self._valid():self.on_status(self.root,'stopped');return
                self._sweep_gone(con, gen, log_changes=baselined)
                lo, hi = storage.drive_range(self.root)
                total_files, total_bytes = con.execute(
                    "SELECT COALESCE(SUM(COALESCE(d.file_count, "
                    "(SELECT COUNT(*) FROM files WHERE folder_id=d.id))),0), "
                    "COALESCE(SUM(COALESCE(d.byte_count, "
                    "(SELECT COALESCE(SUM(size),0) FROM files WHERE folder_id=d.id))),0) "
                    "FROM folders d WHERE d.path >= ? AND d.path < ?", (lo,hi)).fetchone()
                con.execute(
                    "INSERT INTO drives (root, gen, baselined, files, bytes, last_scan) VALUES (?, ?, 1, ?, ?, ?) "
                    "ON CONFLICT (root) DO UPDATE SET gen = excluded.gen, baselined = 1, "
                    "files = excluded.files, bytes = excluded.bytes, last_scan = excluded.last_scan",
                    (self.root, gen, total_files, total_bytes, time.time()),
                )
                if not self._valid():con.rollback();self.on_status(self.root,'stopped');return
                con.commit()
            self.on_status(self.root, "ready")
        finally:
            con.close()

    def _walk(self, con, gen, baselined):
        # Enumerate outside WRITE_LOCK; retain only a small batch of folder data.
        stack = [self.root]
        total_files = total_bytes = 0
        while stack and not self.stop_event.is_set():
            while self.pause_check() and not self.stop_event.wait(0.2):
                pass
            if self.stop_event.is_set():
                break
            batch = []
            batch_files = 0
            for _ in range(BATCH_FOLDERS):
                if not stack or self.stop_event.is_set():
                    break
                path = stack.pop()
                files, subdirs = list_folder(path)
                stack.extend(subdirs)
                batch.append((path, files))
                batch_files += len(files) if files else 0
                if batch_files >= 4096:
                    break
            log = []
            with storage.WRITE_LOCK:
                if not self._valid():break
                for path, files in batch:
                    size, count = reconcile_folder(con, gen, baselined, path, files, log)
                    total_bytes += size
                    total_files += count
                storage.record(con, log)
                if not self._valid():con.rollback();break
                con.commit()
            self._progress(total_files, total_bytes, False)
            self.stop_event.wait(0.025)
        return total_files, total_bytes

    def _progress(self, files, size, initial):
        now = time.monotonic()
        if now - self._progress_at >= 0.5:
            self._progress_at = now
            self.on_progress(files, size, initial)

    def _valid(self):
        if not self.guard_check():self.stop_event.set()
        return not self.stop_event.is_set()

    def _walk_parallel(self, con, gen, baselined=False):
        # Enumeration continues while the single writer reconciles prior results.
        # Backpressure bounds folder payloads instead of queuing the whole index.
        todo = queue.LifoQueue()
        results = queue.Queue(maxsize=128)
        todo.put(self.root)
        outstanding = 1
        lock = threading.Lock()
        finished = threading.Event()
        errors = []

        def worker():
            nonlocal outstanding
            raise_thread_priority()
            try:
                while not finished.is_set() and not self.stop_event.is_set():
                    try:
                        path = todo.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    while self.pause_check() and not self.stop_event.wait(0.1):
                        pass
                    if self.stop_event.is_set():
                        return
                    files, subdirs = list_folder(path)
                    with lock:
                        outstanding += len(subdirs)
                    for sub in subdirs:
                        todo.put(sub)
                    while not self.stop_event.is_set():
                        try:
                            results.put((path, files), timeout=0.1)
                            break
                        except queue.Full:
                            pass
                    with lock:
                        outstanding -= 1
                        if outstanding == 0:
                            finished.set()
            except BaseException as exc:
                with lock:
                    errors.append(exc)
                self.stop_event.set()
                finished.set()

        threads = [threading.Thread(target=worker, name=f"fb-startup-{i}", daemon=True)
                   for i in range(worker_count())]
        for thread in threads:
            thread.start()
        total_files = total_bytes = 0
        try:
            while not self.stop_event.is_set():
                try:
                    item = results.get(timeout=0.1)
                except queue.Empty:
                    if finished.is_set():
                        break
                    continue
                batch = [item]
                batch_files = len(item[1]) if item[1] else 0
                while len(batch) < 128 and batch_files < 16384:
                    try:
                        item = results.get_nowait()
                    except queue.Empty:
                        break
                    batch.append(item)
                    batch_files += len(item[1]) if item[1] else 0
                log = []
                with storage.WRITE_LOCK:
                    if not self._valid():break
                    for path, files in batch:
                        size, count = reconcile_folder(con, gen, baselined, path, files, log)
                        total_files += count
                        total_bytes += size
                    storage.record(con, log)
                    if not self._valid():con.rollback();break
                    con.commit()
                self._progress(total_files, total_bytes, not baselined)
            if errors:
                raise errors[0]
        except BaseException:
            self.stop_event.set()
            raise
        finally:
            finished.set()
            for thread in threads:
                thread.join()
        return total_files, total_bytes

    def _sweep_gone(self, con, gen, log_changes=True):
        """Folders not seen in this scan were deleted while the PC was off: log their files as removed."""
        lo, hi = storage.drive_range(self.root)
        stale = con.execute(
            "SELECT id, path FROM folders WHERE path >= ? AND path < ? AND scan < ?", (lo, hi, gen)
        ).fetchall()
        ts = time.time()
        for fid, path in stale:
            rows = con.execute("SELECT name, size FROM files WHERE folder_id = ?", (fid,)).fetchall()
            if log_changes:
                storage.record(con, [(ts, os.path.join(path, n), "removed", -s, 0) for n, s in rows])
            con.execute("DELETE FROM files WHERE folder_id = ?", (fid,))
            con.execute("DELETE FROM folders WHERE id = ?", (fid,))
        con.commit()
