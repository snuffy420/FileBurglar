"""One low-priority worker for changed folders; never schedules drive scans."""
import os
import queue
import threading
import time

import diagnostics
import storage
from indexer import list_folder, lower_thread_priority, reconcile_folder


def under(path, parent):
    path = os.path.normcase(path).rstrip('\\')
    parent = os.path.normcase(parent).rstrip('\\')
    return path == parent or path.startswith(parent + '\\')


class FolderUpdater(threading.Thread):
    def __init__(self, db_path, can_work, on_done):
        super().__init__(name='fb-folder-updates', daemon=True)
        self.db_path, self.can_work, self.on_done = db_path, can_work, on_done
        self.condition = threading.Condition()
        self.pending = {}  # (drive root, folder path) -> recursive
        self.active = None
        self.stopped = threading.Event()

    def enqueue(self, root, path, recursive=False):
        if not path or storage.is_ignored_path(path):
            return
        with self.condition:
            for (drive, existing), full in self.pending.items():
                if drive == root and full and under(path, existing):
                    return
            if recursive:
                for key in list(self.pending):
                    if key[0] == root and under(key[1], path):
                        del self.pending[key]
            key = (root, path)
            self.pending[key] = self.pending.get(key, False) or recursive
            same_drive = [key for key in self.pending if key[0] == root]
            if len(same_drive) > 256:
                common = os.path.commonpath([key[1] for key in same_drive])
                for key in same_drive:
                    del self.pending[key]
                self.pending[(root, common)] = True
            self.condition.notify_all()

    def covers(self, root, path):
        active = self.active
        return bool(active and active[0] == root and under(path, active[1]))

    def count(self, root):
        with self.condition:
            return sum(drive == root for drive, _ in self.pending) + int(bool(self.active and self.active[0] == root))

    def wake(self):
        with self.condition:
            self.condition.notify_all()

    def discard_excluded(self):
        with self.condition:
            self.pending={key:value for key,value in self.pending.items() if not storage.is_excluded_path(key[1])}

    def discard_root(self,root):
        with self.condition:self.pending={key:value for key,value in self.pending.items() if key[0]!=root}

    def stop(self):
        self.stopped.set()
        self.wake()

    def run(self):
        lower_thread_priority()
        con = storage.connect(self.db_path)
        try:
            while not self.stopped.is_set():
                with self.condition:
                    available = next((key for key in self.pending if self.can_work(key[0])), None)
                    if available is None:
                        self.condition.wait(.25 if self.pending else 2)
                        continue
                    recursive = self.pending.pop(available)
                    self.active = available
                root, path = available
                error = ''
                try:
                    remaining = self._scan(con, root, [(path, recursive)])
                    if not remaining:storage.completed_baseline(path)
                    for folder, full in remaining:
                        self.enqueue(root, folder, full)
                except Exception as exc:
                    con.rollback()
                    error = str(exc)
                    diagnostics.report(self.db_path, 'folder update', root, error)
                finally:
                    self.active = None
                self.on_done(root, error)
        finally:
            con.close()

    def _scan(self, con, root, stack):
        processed = 0
        while stack and not self.stopped.is_set():
            if not self.can_work(root):
                return stack
            path, recursive = stack.pop()
            if storage.is_excluded_path(path):continue
            files, subdirs = list_folder(path)
            missing = False
            if files is None:
                try:
                    os.stat(path)
                except (FileNotFoundError, NotADirectoryError):
                    missing = True
                except OSError:
                    continue
                if not missing:
                    continue  # Access failure is not evidence of deletion.
            log = []
            with storage.WRITE_LOCK:
                if not self.can_work(root):
                    stack.append((path, recursive))
                    return stack
                if missing:
                    self._remove(con, path, log)
                else:
                    row = con.execute('SELECT gen FROM drives WHERE root=?', (root,)).fetchone()
                    gen = row[0] if row else 1
                    prefix = path.rstrip('\\') + '\\'
                    hi = path.rstrip('\\') + ']'
                    previous = [r[0] for r in con.execute(
                        "SELECT path FROM folders WHERE path>=? AND path<? AND path!=? "
                        "AND instr(substr(path,?), '\\')=0", (prefix, hi, path, len(prefix)+1))]
                    reconcile_folder(con, gen, True, path, files, log)
                    present = {os.path.normcase(p) for p in subdirs}
                    for gone in previous:
                        if os.path.normcase(gone) not in present:
                            self._remove(con, gone, log)
                    for sub in subdirs:
                        known = con.execute('SELECT 1 FROM folders WHERE path=?', (sub,)).fetchone()
                        if recursive or not known:
                            stack.append((sub, True))
                storage.record(con, log)
                storage.update_drive_totals(con, log)
                con.commit()
            processed += 1
            if processed % 32 == 0:
                self.stopped.wait(.025)
        return []

    @staticmethod
    def _remove(con, path, log):
        prefix, hi = path.rstrip('\\') + '\\', path.rstrip('\\') + ']'
        folders = con.execute('SELECT id,path FROM folders WHERE path=? OR (path>=? AND path<?)',
                              (path,prefix,hi)).fetchall()
        ts = time.time()
        for fid, folder in folders:
            for name, size in con.execute('SELECT name,size FROM files WHERE folder_id=?', (fid,)).fetchall():
                log.append((ts,os.path.join(folder,name),'removed',-size,0))
            con.execute('DELETE FROM files WHERE folder_id=?', (fid,))
            con.execute('DELETE FROM folders WHERE id=?', (fid,))
