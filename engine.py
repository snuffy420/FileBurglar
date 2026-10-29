"""Engine: applies live change events to the index and writes the activity log.

Everything that touches the live index runs on one background thread, so the
engine never competes with the startup indexer for the disk. Each event is
checked against the disk and compared with the index, so a change is counted
once even if the startup scan already saw it. A drive's events are held until
its startup index is ready, and held while the engine is paused.
"""

import os
import queue
import threading
import time
from datetime import date, timedelta

import storage
import diagnostics
from folder_updates import FolderUpdater
from indexer import Indexer, lower_thread_priority

BATCH_ROWS = 500
PRUNE_EVERY = 3600
LOG_DAYS = 30
DAILY_DAYS = 365


class Engine(threading.Thread):
    def __init__(self, db_path):
        super().__init__(name="fb-engine", daemon=True)
        self.db_path = db_path
        self.inbox = queue.Queue(maxsize=64)
        self.controls = queue.SimpleQueue()
        self.wake = threading.Event()
        self.dropped_roots = set()
        self.drop_lock = threading.Lock()
        self.lock = threading.Lock()
        self.state = {}  # root -> status dict for the UI
        self.held = {}  # root -> paths waiting for the drive to be ready
        self.indexers = {}  # root -> running Indexer
        self.scan_threads = set()
        self.rescan_pending = set()
        self.startup_roots = set()
        self.folder_updater = FolderUpdater(db_path, self._can_update_folder,
                                            lambda root,error: self._control("folder_done",root,error))
        self.paused = False
        self.paused_drives = set()
        self.stop_flag = False
        self.last_prune = 0.0
        con = storage.connect(db_path)
        try:
            for root, files, size, last in con.execute("SELECT root,files,bytes,last_scan FROM drives"):
                self._set(root, files=files, bytes=size, last_scan=last)
        finally:
            con.close()

    # Called from watcher, drive and indexer threads. They only enqueue.
    def on_events(self, root, events):
        # Filter database/WAL writes BEFORE the bounded queue, not after overflow.
        events = [(kind,path) for kind,path in events if not storage.is_own_path(path)]
        if not events:
            return
        try:
            self.inbox.put_nowait(("events", root, events))
        except queue.Full:
            with self.drop_lock:
                self.dropped_roots.add(root)
            for _kind,path in events:
                self.folder_updater.enqueue(root,os.path.dirname(path),False)
        self.wake.set()

    def _control(self, kind, root=None, payload=None):
        # GUI commands never wait for a full filesystem-event queue.
        self.controls.put((kind, root, payload))
        self.wake.set()

    def on_overflow(self, root):
        self._control("overflow", root)

    def on_drive(self, root, state, message=""):
        self._control("drive", root, (state, message))

    def on_status(self, root, state, message=""):
        self._control("status", root, (state, message))

    def set_paused(self, value):
        self._control("pause", payload=bool(value))

    def set_drive_paused(self, root, value):
        self._control("drive_pause", root, bool(value))

    def retry_drive(self, root):
        self._control("retry", root)

    def stop(self):
        self.stop_flag = True
        self.folder_updater.stop()
        for indexer in list(self.indexers.values()):
            indexer.stop_event.set()
        self.wake.set()

    def snapshot(self):
        with self.lock:
            return {root: dict(info, folder_updates=self.folder_updater.count(root)) for root, info in self.state.items()}

    def _set(self, root, **info):
        with self.lock:
            entry = self.state.setdefault(
                root, {"state": "offline", "message": "", "files": 0, "bytes": 0,
                       "last_scan": 0.0, "last_event": 0.0, "held": 0})
            entry.update(info)
            entry["held"] = len(self.held.get(root, ()))

    def run(self):
        lower_thread_priority()
        con = storage.connect(self.db_path)
        rows = []
        self.folder_updater.start()
        try:
            while not self.stop_flag:
                self.wake.wait(timeout=2.0)
                self.wake.clear()
                with self.drop_lock:
                    dropped, self.dropped_roots = self.dropped_roots, set()
                # Known events dropped by backpressure are recovered by folder
                # jobs already queued in on_events, not another drive index.
                for root in dropped:
                    if self.state.get(root,{}).get("state") == "ready":
                        self._set(root,message="Catching up changed folders")
                with storage.WRITE_LOCK:
                    while not self.stop_flag:
                        try:
                            control = self.controls.get_nowait()
                        except queue.Empty:
                            break
                        self._handle(con, control, rows)
                    for _ in range(500):
                        if self.stop_flag:
                            break
                        try:
                            item = self.inbox.get_nowait()
                        except queue.Empty:
                            break
                        self._handle(con, item, rows)
                        if len(rows) >= BATCH_ROWS:
                            self._flush(con, rows)
                    self._flush(con, rows)
                    self._maybe_prune(con)
                if not self.inbox.empty() or not self.controls.empty():
                    self.wake.set()
        finally:
            with storage.WRITE_LOCK:
                self._flush(con, rows)
            self.folder_updater.stop()
            if self.folder_updater.ident is not None:
                self.folder_updater.join(timeout=5)
            for indexer in list(self.indexers.values()):
                indexer.stop_event.set()
            deadline = time.monotonic() + 5
            with self.lock:
                threads = list(self.scan_threads)
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            con.close()

    def _flush(self, con, rows):
        # Always commit, even with no log rows: folder and file writes made while handling
        # an event would otherwise stay in an open transaction and block every other writer.
        if rows:
            storage.record(con, rows)
            changes = {}
            folders = {}
            for _ts, path, action, delta, _size in rows:
                root = os.path.splitdrive(path)[0] + os.sep
                count, size = changes.get(root, (0, 0))
                n = 1 if action == "added" else -1 if action == "removed" else 0
                changes[root] = (count + n, size + delta)
                folder = os.path.dirname(path)
                fc, fb = folders.get(folder, (0, 0))
                folders[folder] = (fc + n, fb + delta)
            for path, (count, size) in folders.items():
                con.execute("UPDATE folders SET file_count=file_count+?, byte_count=byte_count+? WHERE path=?",
                            (count, size, path))
            for root, (count, size) in changes.items():
                con.execute("UPDATE drives SET files = files + ?, bytes = bytes + ? WHERE root = ?",
                            (count, size, root))
                row = con.execute("SELECT files, bytes FROM drives WHERE root = ?", (root,)).fetchone()
                if row:
                    self._set(root, files=row[0], bytes=row[1])
            rows.clear()
        con.commit()

    def _handle(self, con, item, rows):
        kind, root, payload = item
        if kind == "events":
            self._events(con, root, payload, rows)
        elif kind == "overflow":
            self._overflow(root)
        elif kind == "drive":
            self._drive(root, *payload)
        elif kind == "status":
            self._status(con, root, *payload)
        elif kind == "progress":
            indexer, count, size, initial = payload
            if self.indexers.get(root) is indexer:
                info = dict(message=f"{'Fast startup scan' if indexer.fast_mode else 'Background scan'}: {count:,} files checked",
                            scanned_files=count, scanned_bytes=size)
                if initial:
                    info.update(files=count, bytes=size)
                self._set(root, **info)
        elif kind == "folder_done":
            row = con.execute("SELECT files,bytes FROM drives WHERE root=?",(root,)).fetchone()
            if row:
                self._set(root,files=row[0],bytes=row[1])
            if payload:
                self._set(root,state="error",message="Folder update: " + payload)
            elif self._can_update_folder(root):
                self._replay_held(con,root,rows)
        elif kind == "retry":
            self._start_index(root, fast_startup=self.state.get(root, {}).get("scan_mode") == "startup")
        elif kind == "index_status":
            indexer, state, message = payload
            if self.indexers.get(root) is indexer:
                self._status(con, root, state, message)
        elif kind == "pause":
            self._pause(con, payload, rows)
        elif kind == "drive_pause":
            self._drive_pause(con, root, payload, rows)

    def _can_update_folder(self, root):
        return (not self.stop_flag and not self.paused and root not in self.paused_drives
                and self.state.get(root,{}).get("state") == "ready")

    def _hold_paths(self, root, paths):
        held = self.held.setdefault(root,set())
        held.update(paths)
        if len(held) > 4096:
            for path in held:
                self.folder_updater.enqueue(root,os.path.dirname(path),False)
            held.clear()
        self._set(root)

    def _events(self, con, root, events, rows):
        info = self.state.get(root,{})
        self._set(root,last_event=time.time())
        paths = {path for _kind,path in events if not storage.is_own_path(path)}
        if self.paused or root in self.paused_drives or info.get("state") != "ready":
            self._hold_paths(root,paths)
            return
        held = {path for path in paths if self.folder_updater.covers(root,path)}
        if held:
            self._hold_paths(root,held)
        gen = self._gen(con,root)
        for path in paths-held:
            self._apply_path(con,path,gen,rows)

    def _overflow(self, root):
        if self.state.get(root, {}).get("state") == "offline":
            return
        if self.paused or root in self.paused_drives:
            self.rescan_pending.add(root)
            return
        # Some changes were lost, so reconcile the whole drive again.
        if self.state.get(root, {}).get("state") == "indexing":
            self.rescan_pending.add(root)
        else:
            self._start_index(root)

    def _drive(self, root, state, message):
        if state == "online":
            startup = root not in self.startup_roots
            self.startup_roots.add(root)
            if root not in self.indexers:
                self._start_index(root, fast_startup=startup)
        elif state == "offline":
            indexer = self.indexers.pop(root, None)
            if indexer:
                indexer.stop_event.set()
            self.held.pop(root, None)
            self.rescan_pending.discard(root)
            with self.drop_lock:
                self.dropped_roots.discard(root)
            self._set(root, state="offline", message="")
        else:
            diagnostics.report(self.db_path, "watcher", root, message)
            self._set(root, state="error", message=message)

    def _status(self, con, root, state, message):
        if root not in self.indexers:
            return
        if state == "indexing":
            self._set(root, state="indexing", message="")
            return
        if state == "error":
            self._set(root, state="error", message=message)
            self.indexers.pop(root, None)
            return
        self.indexers.pop(root, None)
        if state == "stopped":
            return
        row = con.execute("SELECT files, bytes, last_scan FROM drives WHERE root = ?", (root,)).fetchone()
        files, size, last = row if row else (0, 0, 0.0)
        self._set(root, state="ready", message="", files=files, bytes=size, last_scan=last)
        if root in self.rescan_pending and not self.paused and root not in self.paused_drives:
            self.rescan_pending.discard(root)
            self._start_index(root)
            return
        if not self.paused and root not in self.paused_drives:
            self._replay_held(con, root)
        self.folder_updater.wake()

    def _drive_pause(self, con, root, value, rows):
        self.folder_updater.wake()
        if value:
            self.paused_drives.add(root)
        else:
            self.paused_drives.discard(root)
            if not self.paused and self.state.get(root, {}).get("state") == "ready":
                self._replay_held(con, root, rows)
        self._set(root)

    def _pause(self, con, value, rows):
        self.paused = value
        self.folder_updater.wake()
        if not value:
            for root in set(self.held) | self.rescan_pending:
                if self.state.get(root, {}).get("state") == "ready" and root not in self.paused_drives:
                    self._replay_held(con, root, rows)

    def _replay_held(self, con, root, rows=None):
        paths = self.held.pop(root, set())
        deferred = {path for path in paths if self.folder_updater.covers(root,path)}
        if deferred:
            self.held[root] = deferred
            paths -= deferred
        if root in self.rescan_pending:
            self.rescan_pending.discard(root)
            self._start_index(root)
            return
        if not paths:
            return
        rows = [] if rows is None else rows
        gen = self._gen(con, root)
        for path in paths:
            self._apply_path(con, path, gen, rows)
        self._flush(con, rows)
        self._set(root)

    def _start_index(self, root, fast_startup=False):
        if self.stop_flag or root in self.indexers:
            return
        indexer = Indexer(self.db_path, root, fast_startup=fast_startup)
        def progress(count, size, initial):
            try:
                self.inbox.put_nowait(("progress", root, (indexer, count, size, initial)))
                self.wake.set()
            except queue.Full:
                pass
        indexer.on_progress = progress
        indexer.pause_check = lambda: self.paused or root in self.paused_drives
        indexer.on_status = lambda r, state, message="": self._post_index_status(indexer, state, message)
        self.indexers[root] = indexer
        self._set(root, state="indexing", message="Fast startup scan" if fast_startup else "Background scan",
                  scan_mode="startup" if fast_startup else "background", scanned_files=0, scanned_bytes=0)
        thread = threading.Thread(target=self._run_index, args=(indexer,),
                                  name=f"fb-index-{root[:1]}", daemon=True)
        with self.lock:
            self.scan_threads.add(thread)
        thread.start()

    def _post_index_status(self, indexer, state, message):
        if not self.stop_flag:
            self._control("index_status", indexer.root, (indexer, state, message))

    def _run_index(self, indexer):
        try:
            indexer.run()
        except Exception as exc:  # keep the drive visible as an error instead of stuck
            diagnostics.report(self.db_path, "indexer", indexer.root, str(exc))
            indexer.on_status(indexer.root, "error", str(exc))
        finally:
            with self.lock:
                self.scan_threads.discard(threading.current_thread())

    def _gen(self, con, root):
        row = con.execute("SELECT gen FROM drives WHERE root = ?", (root,)).fetchone()
        return row[0] if row else 1

    def _folder_row(self, con, path):
        row = con.execute("SELECT id FROM folders WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None

    def _apply_path(self, con, path, gen, rows):
        ts = time.time()
        if os.path.islink(path) or os.path.isjunction(path):
            return
        if os.path.isdir(path):
            if self._folder_row(con, path) is None:  # a new folder appeared: index its whole subtree
                self._scan_subtree(con, path, gen, rows)
            return
        try:
            stat = os.stat(path)
        except (FileNotFoundError, NotADirectoryError):
            self._remove_subtree(con, path, rows, ts)
            folder, name = os.path.split(path)
            fid = self._folder_row(con, folder)
            if fid is not None:
                old = con.execute("SELECT size FROM files WHERE folder_id = ? AND name = ?", (fid, name)).fetchone()
                if old:
                    con.execute("DELETE FROM files WHERE folder_id = ? AND name = ?", (fid, name))
                    rows.append((ts, path, "removed", -old[0], 0))
            return
        except OSError:
            return  # Access denied is not evidence of deletion.
        size, mtime = stat.st_size, storage.timestamp_key(stat.st_mtime_ns)
        folder, name = os.path.split(path)
        fid = storage.folder_id(con, folder)
        old = con.execute("SELECT size, mtime_ns FROM files WHERE folder_id = ? AND name = ?", (fid, name)).fetchone()
        if old is None:
            con.execute("INSERT INTO files (folder_id, name, size, mtime_ns) VALUES (?, ?, ?, ?)", (fid, name, size, mtime))
            rows.append((ts, path, "added", size, size))
        elif old[0] != size or old[1] != mtime:
            con.execute("UPDATE files SET size = ?, mtime_ns = ? WHERE folder_id = ? AND name = ?",
                        (size, mtime, fid, name))
            if old[0] != size or old[1] != 0:
                rows.append((ts, path, "changed", size - old[0], size))

    def _scan_subtree(self, con, path, gen, rows):
        # New directories need their own subtree indexed, not the whole drive.
        storage.visit_folder(con,path,gen)
        self.folder_updater.enqueue(os.path.splitdrive(path)[0] + os.sep,path,True)

    def _remove_subtree(self, con, path, rows, ts):
        lo = path + "\\"
        hi = path + "]"  # sorts just after every path that starts with lo
        folders = con.execute(
            "SELECT id, path FROM folders WHERE path = ? OR (path >= ? AND path < ?)", (path, lo, hi)
        ).fetchall()
        for fid, fpath in folders:
            for name, size in con.execute("SELECT name, size FROM files WHERE folder_id = ?", (fid,)).fetchall():
                rows.append((ts, os.path.join(fpath, name), "removed", -size, 0))
            con.execute("DELETE FROM files WHERE folder_id = ?", (fid,))
            con.execute("DELETE FROM folders WHERE id = ?", (fid,))

    def _maybe_prune(self, con):
        now = time.time()
        if now - self.last_prune < PRUNE_EVERY:
            return
        self.last_prune = now
        con.execute("DELETE FROM file_log WHERE ts < ?", (now - LOG_DAYS * 86400,))
        cutoff = (date.today() - timedelta(days=DAILY_DAYS)).isoformat()
        con.execute("DELETE FROM folder_daily WHERE day < ?", (cutoff,))
        con.commit()
