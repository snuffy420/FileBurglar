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
import volumes
import settings
import json
from folder_updates import FolderUpdater
from indexer import Indexer, lower_thread_priority

BATCH_ROWS = 500
PRUNE_EVERY = 3600
PRUNE_BACKLOG_EVERY = 5  # seconds between bounded prune chunks while old history remains
LOG_DAYS = 30
DAILY_DAYS = 365
# Wait briefly for a burst of notifications so a file rewritten many times per
# second is checked once. Keeps CPU and disk work low on busy drives.
EVENT_SETTLE = 0.5
MERGE_PATH_LIMIT = 20000
# Minimum seconds between whole-drive recovery scans caused by lost Windows
# notifications. Overflows during the cooldown queue one deferred rescan.
RECOVERY_COOLDOWN = 15 * 60


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
        self.recovery_after = {}  # root -> monotonic time when another recovery scan may start
        self.startup_roots = set()
        self.work_mode = False
        self.released_roots = set()
        self.release_guard = threading.RLock()
        self.notices = queue.SimpleQueue()
        self.scan_baselines = {}
        self.reconnect_pending={}
        self.mounts={}
        self.invalid_tokens=set()
        self.pending_mounts={}
        self.folder_updater = FolderUpdater(db_path, self._can_update_folder,
                                            lambda root,error: self._control("folder_done",root,error))
        self.paused = False
        self.paused_drives = set()
        self.stop_flag = False
        self.last_prune = 0.0
        con = storage.connect(db_path)
        try:
            con.execute('UPDATE volumes SET present=0');con.commit()
            self._reload_volumes(con)
            metadata=volumes.rows(con)
            if metadata:settings.configure_volumes(metadata)
        finally:
            con.close()

    # Called from watcher, drive and indexer threads. They only enqueue.
    def on_events(self, root, events, token=None):
        if token in self.invalid_tokens:return
        if token and token not in (self.mounts.get(root,{}).get('token'),self.pending_mounts.get(root)):return
        if root in self.released_roots:return
        # Filter database/WAL writes BEFORE the bounded queue, not after overflow.
        events = [(kind,path) for kind,path in events if not storage.is_ignored_path(path)]
        if not events:
            return
        try:
            self.inbox.put_nowait(("events", root, (token,events) if token else events))
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

    def on_overflow(self, root,token=None):
        self._control("overflow", root,token)

    def on_drive(self, root, state, message=""):
        if state=='online' and isinstance(message,dict) and message.get('token'):self.pending_mounts[root]=message['token']
        self._control("drive", root, (state, message))

    def on_status(self, root, state, message=""):
        self._control("status", root, (state, message))

    def set_paused(self, value):
        self._control("pause", payload=bool(value))

    def set_drive_paused(self, root, value):
        self._control("drive_pause", root, bool(value))

    def retry_drive(self, root):
        self._control("retry", root)

    def set_work_mode(self,value):
        self._control('work_mode',payload=bool(value))

    def configure_exclusions(self,paths,previous=()):
        self._control('exclusions',payload=(list(paths),list(previous)))

    def link_saved_volume(self,source,destination):
        from concurrent.futures import Future
        future=Future();self._control('link_volume',payload=(source,destination,future));return future

    def release(self,roots,timeout=3):
        """Block new I/O immediately and acknowledge actual worker completion."""
        deadline=time.monotonic()+timeout
        self.request_release(roots)
        while time.monotonic()<deadline:
            active=self.folder_updater.active
            busy=any(i.root in roots for i in self.indexers.values()) or bool(active and active[0] in roots)
            if not busy:
                # Acquiring the write lock ensures event processing committed and
                # released its filesystem operations. Never hold it while joining.
                remaining=max(0,deadline-time.monotonic())
                if storage.WRITE_LOCK.acquire(timeout=remaining):
                    storage.WRITE_LOCK.release()
                    for root in roots:self._set(root,state='released',message='Released for removal')
                    return True
            time.sleep(.02)
        return False

    def request_release(self,roots):
        with self.release_guard:
            self.released_roots.update(roots)
            for root,indexer in list(self.indexers.items()):
                if root in roots:indexer.stop_event.set()
            self.folder_updater.wake()

    def resume_released(self,roots):
        self.released_roots.difference_update(roots)

    def _io_allowed(self,root):
        return not self.stop_flag and root not in self.released_roots

    def invalidate_mount(self,root,token=None):
        if token:
            self.invalid_tokens.add(token)
            if len(self.invalid_tokens)>256:self.invalid_tokens=set(list(self.invalid_tokens)[-128:])
        if token is None or self.mounts.get(root,{}).get('token')==token:self.request_release({root})

    def _valid_mount(self,root,token=None):
        if token in self.invalid_tokens:return False
        mount=self.mounts.get(root)
        if not mount:return token is None  # unmanaged synthetic fixtures / legacy APIs
        if token and token!=mount.get('token'):return False
        import devices
        return devices.volume_identity(root)==mount['id']

    def _reload_volumes(self,con):
        metadata={v['namespace']:v for v in volumes.rows(con)}
        by_id={s.get('volume_id'):s for s in self.state.values() if s.get('volume_id')}
        fresh={}
        for root,count,size,last,ident in con.execute('SELECT root,files,bytes,last_scan,volume_id FROM drives'):
            info=dict(by_id.get(ident,self.state.get(root,{})))
            info.update(files=count,bytes=size,last_scan=last,volume_id=ident,
                        last_root=metadata.get(root,{}).get('last_root',root),label=metadata.get(root,{}).get('label',''),
                        verified=bool(metadata.get(root,{}).get('verified',True)))
            info['present']=bool(metadata[root]['present']) if root in metadata else None
            info.setdefault('state','offline');info.setdefault('message','');info.setdefault('last_event',0);info.setdefault('held',0)
            if not metadata.get(root,{}).get('present',False):info['state']='offline'
            fresh[root]=info
        with self.lock:self.state=fresh

    def _bind_volume(self,con,root,info):
        ident=info.get('identity','');token=info.get('token')
        if token in self.invalid_tokens:return False
        if not ident:
            self._set(root,state='error',message='Cannot identify the volume; saved drive data was not associated.');return False
        old=con.execute('SELECT namespace,present FROM volumes WHERE id=?',(ident,)).fetchone()
        if old and old[0]!=root and old[0] in self.indexers:
            self.indexers[old[0]].stop_event.set()
            self._set(root,state='error',message='Previous mount is still closing. Refresh drives to retry.');return False
        affected={root}
        if old:affected.add(old[0])
        for path in affected:
            self.held.pop(path,None);self.rescan_pending.discard(path)
            self.folder_updater.discard_root(path)
        previous_metadata=volumes.rows(con)
        volumes.bind(con,ident,root,info.get('label',''))
        current_metadata={v['id']:v for v in volumes.rows(con)}
        relocated=[]
        for path in storage.BASELINE_PATHS:
            owner=next((v for v in previous_metadata if path.lower().startswith(v['namespace'].lower())),None)
            if owner and owner['id'] in current_metadata:
                path=current_metadata[owner['id']]['namespace']+path[len(owner['namespace']):]
            relocated.append(os.path.normcase(path))
        storage.BASELINE_PATHS=tuple(relocated)
        self.mounts[root]=dict(id=ident,token=token)
        for previous,mount in list(self.mounts.items()):
            if previous!=root and mount['id']==ident:self.mounts.pop(previous,None)
        self.released_roots.discard(root)
        self._reload_volumes(con)
        settings.configure_volumes(volumes.rows(con))
        con.execute("INSERT INTO app_meta VALUES('exclusions',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(storage.EXCLUSIONS),))
        self.notices.put(('mounts',root,None))
        if old and not old[1] and ident in self.startup_roots:
            self.reconnect_pending[root]=con.execute('SELECT COALESCE(MAX(id),0) FROM file_log').fetchone()[0]
        return True

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
                self._settle()
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
                    self._drain_inbox(con, rows)
                    self._flush(con, rows)
                    self._maybe_prune(con)
                    self._start_deferred(con,rows)
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

    def _settle(self):
        """Let a notification burst finish arriving before it is processed."""
        if EVENT_SETTLE <= 0 or self.inbox.empty():
            return
        deadline = time.monotonic() + EVENT_SETTLE
        while (not self.stop_flag and self.controls.empty() and not self.inbox.full()
               and time.monotonic() < deadline):
            time.sleep(0.05)

    def _drain_inbox(self, con, rows):
        """Handle queued items, merging event batches so each path is checked once."""
        pending = {}  # (root, token) -> {path: kind}
        merged = 0

        def apply_pending():
            for (root, token), paths in pending.items():
                events = [(kind, path) for path, kind in paths.items()]
                self._handle(con, ("events", root, (token, events) if token else events), rows)
                if len(rows) >= BATCH_ROWS:
                    self._flush(con, rows)
            pending.clear()

        for _ in range(500):
            if self.stop_flag:
                break
            try:
                item = self.inbox.get_nowait()
            except queue.Empty:
                break
            if item[0] != "events":
                self._handle(con, item, rows)
                continue
            payload = item[2]
            token, events = payload if isinstance(payload, tuple) else (None, payload)
            paths = pending.setdefault((item[1], token), {})
            for kind, path in events:
                paths[path] = kind
            merged += len(events)
            if merged >= MERGE_PATH_LIMIT:
                apply_pending()
                merged = 0
            if len(rows) >= BATCH_ROWS:
                self._flush(con, rows)
        apply_pending()

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
            if isinstance(payload,tuple):
                token,payload=payload
                if not self._valid_mount(root,token):return
            self._events(con, root, payload, rows)
        elif kind == "overflow":
            if not self._valid_mount(root,payload):return
            self._overflow(root)
        elif kind == 'work_mode':
            self.work_mode=payload
            self.folder_updater.wake()
            if not payload:
                for drive in list(self.held):
                    if self._can_update_folder(drive):self._replay_held(con,drive,rows)
        elif kind=='link_volume':
            source,destination,future=payload
            try:
                mounted=con.execute('SELECT namespace FROM volumes WHERE id=?',(destination,)).fetchone()
                if not mounted or self.mounts.get(mounted[0],{}).get('id')!=destination or not self._valid_mount(mounted[0]):
                    raise ValueError('The drive assignment changed. Refresh and select the drive again.')
                volumes.link_legacy(con,source,destination)
                settings.link_preferences(source,destination)
                self._reload_volumes(con);settings.configure_volumes(volumes.rows(con))
                storage.prune_exclusions(con);self._reload_volumes(con)
                self.notices.put(('mounts','',None));future.set_result(True)
            except Exception as error:future.set_exception(error)
        elif kind == 'exclusions':
            paths,previous=payload
            storage.set_exclusions(paths)
            storage.prune_exclusions(con)
            self.folder_updater.discard_excluded()
            for drive,held in self.held.items():
                self.held[drive]={p for p in held if not storage.is_excluded_path(p)}
            for path in previous:
                if not storage.is_excluded_path(path):
                    drive=os.path.splitdrive(path)[0]+os.sep
                    self.folder_updater.enqueue(drive,path,True)
            for drive,count,size in con.execute('SELECT root,files,bytes FROM drives'):
                self._set(drive,files=count,bytes=size)
        elif kind == "drive":
            identity_info=payload[1]
            if isinstance(identity_info,dict) and identity_info.get('token'):
                if payload[0]=='online':
                    try:
                        if not self._bind_volume(con,root,identity_info):return
                    except Exception as error:
                        diagnostics.report(self.db_path,'volume identity',root,str(error))
                        self._set(root,state='offline',message='Volume association failed: '+str(error))
                        self.notices.put(('identity_error',root,str(error)));return
                    finally:
                        if self.pending_mounts.get(root)==identity_info['token']:self.pending_mounts.pop(root,None)
                    if identity_info.get('monitor') is False:
                        self._set(root,state='unmonitored',message='Connected; file monitoring is disabled',present=True)
                        return
                elif payload[0]=='offline':
                    if self.mounts.get(root,{}).get('token')!=identity_info['token']:return
                    volumes.offline(con,self.mounts[root]['id'])
                    settings.configure_volumes(volumes.rows(con))
                    con.execute("INSERT INTO app_meta VALUES('exclusions',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(storage.EXCLUSIONS),))
                    self.notices.put(('mounts',root,None))
                self._drive(root,*payload)
                return
            identity=identity_info.get('identity','') if isinstance(identity_info,dict) else identity_info
            reconnected=identity_info.get('reconnected',False) if isinstance(identity_info,dict) else True
            if payload[0]=='online' and identity:
                old=con.execute('SELECT identity FROM volume_identity WHERE root=?',(root,)).fetchone()
                if old and old[0]!=identity:
                    # Different media at the same letter gets a baseline, not
                    # fictional deletion/addition activity against the old disk.
                    con.execute('UPDATE drives SET baselined=0 WHERE root=?',(root,))
                if reconnected and old and old[0]==identity and self.state.get(root,{}).get('state') in ('offline','released') and root in self.startup_roots:
                    self.reconnect_pending[root]=con.execute('SELECT COALESCE(MAX(id),0) FROM file_log').fetchone()[0]
                con.execute('INSERT INTO volume_identity VALUES(?,?) ON CONFLICT(root) DO UPDATE SET identity=excluded.identity',(root,identity))
            self._drive(root, *payload)
        elif kind == "status":
            self._status(con, root, *payload)
        elif kind == "progress":
            indexer, count, size, initial = payload
            if self.indexers.get(root) is indexer:
                reason = self.state.get(root, {}).get('scan_reason', '')
                info = dict(message=f"{'Fast startup scan' if indexer.fast_mode else 'Recovery scan'}: {count:,} files checked; {reason}",
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
            self._start_index(root, fast_startup=self.state.get(root, {}).get("scan_mode") == "startup", reason="Manual retry")
        elif kind == "index_status":
            indexer, state, message = payload
            if self.indexers.get(root) is indexer:
                self._status(con, root, state, message)
        elif kind == "pause":
            self._pause(con, payload, rows)
        elif kind == "drive_pause":
            self._drive_pause(con, root, payload, rows)

    def _can_update_folder(self, root):
        return (self._io_allowed(root) and not self.paused and not self.work_mode and root not in self.paused_drives
                and self.state.get(root,{}).get("state") == "ready" and self._valid_mount(root))

    def _recovery_waiting(self, root):
        return time.monotonic() < self.recovery_after.get(root, 0)

    def _start_deferred(self,con,rows):
        if self.work_mode or self.paused:return
        for root in list(self.rescan_pending):
            if self._can_update_folder(root) and not self._recovery_waiting(root):
                self.rescan_pending.discard(root)
                self._start_index(root,reason='Deferred recovery after lost notifications')

    def _hold_paths(self, root, paths):
        held = self.held.setdefault(root,set())
        held.update(paths)
        if len(held) > 4096:
            for path in held:
                self.folder_updater.enqueue(root,os.path.dirname(path),False)
            held.clear()
        self._set(root)

    def _events(self, con, root, events, rows):
        if not self._valid_mount(root):return
        start=len(rows)
        if not con.in_transaction:con.execute('BEGIN')
        con.execute('SAVEPOINT live_volume')
        try:
            self._events_checked(con,root,events,rows)
            if not self._valid_mount(root):
                con.execute('ROLLBACK TO live_volume');del rows[start:]
                self.invalidate_mount(root,self.mounts.get(root,{}).get('token'))
        finally:con.execute('RELEASE live_volume')

    def _events_checked(self, con, root, events, rows):
        if not self._io_allowed(root):return
        info = self.state.get(root,{})
        self._set(root,last_event=time.time())
        paths = {path for _kind,path in events if not storage.is_ignored_path(path)}
        if self.paused or self.work_mode or root in self.paused_drives or info.get("state") != "ready":
            self._hold_paths(root,paths)
            return
        held = {path for path in paths if self.folder_updater.covers(root,path)}
        if held:
            self._hold_paths(root,held)
        gen = self._gen(con,root)
        for path in paths-held:
            self._apply_path(con,path,gen,rows)

    def _overflow(self, root):
        if not self._io_allowed(root) or self.state.get(root, {}).get("state") == "offline":
            return
        if self.paused or self.work_mode or root in self.paused_drives:
            self.rescan_pending.add(root)
            self._set(root,message='Recovery deferred while paused or in game/work mode')
            return
        # Some changes were lost, so reconcile the whole drive again.
        if self.state.get(root, {}).get("state") == "indexing":
            self.rescan_pending.add(root)
        elif self._recovery_waiting(root):
            # Busy drives can overflow repeatedly; one queued rescan covers them all.
            self.rescan_pending.add(root)
            self._set(root, message="Recovery scan queued (lost notifications)")
        else:
            self._start_index(root, reason="Windows change notifications were lost")

    def _drive(self, root, state, message):
        if state=='online' and not self._io_allowed(root):return
        if state == "online":
            identity=self.mounts.get(root,{}).get('id',root)
            startup = identity not in self.startup_roots
            self.startup_roots.add(identity)
            if root not in self.indexers:
                self._start_index(root, fast_startup=startup,
                                  reason="Application startup" if startup else "Drive or watcher reconnected")
        elif state == "offline":
            indexer = self.indexers.pop(root, None)
            if indexer:
                indexer.stop_event.set()
            self.held.pop(root, None)
            self.rescan_pending.discard(root)
            with self.drop_lock:
                self.dropped_roots.discard(root)
            self._set(root, state="offline", message="",present=False)
        else:
            diagnostics.report(self.db_path, "watcher", root, message)
            self._set(root, state="error", message=message)

    def _status(self, con, root, state, message):
        if root not in self.indexers:
            return
        if state == "indexing":
            self._set(root, state="indexing")
            return
        diagnostics.report(self.db_path, "scan", root,
                           f"{state}: {self.state.get(root, {}).get('scan_reason', '')}; {message}", scan=True)
        if state == "error":
            self._set(root, state="error", message=message)
            self.indexers.pop(root, None)
            return
        self.indexers.pop(root, None)
        if state == "stopped":
            return
        row = con.execute("SELECT files, bytes, last_scan FROM drives WHERE root = ?", (root,)).fetchone()
        files, size, last = row if row else (0, 0, 0.0)
        storage.completed_baseline(root)
        if root in self.reconnect_pending and state=='ready':
            after=self.reconnect_pending.pop(root)
            lo,hi=storage.drive_range(root)
            added,removed,changed,delta=con.execute(
                "SELECT COALESCE(SUM(action='added'),0),COALESCE(SUM(action='removed'),0),"
                "COALESCE(SUM(action='changed'),0),COALESCE(SUM(delta),0) FROM file_log WHERE id>? AND path>=? AND path<?",
                (after,lo,hi)).fetchone()
            self.notices.put(('reconnect',root,(added,removed,changed,delta)))
        self._set(root, state="released" if root in self.released_roots else "ready", message="", files=files, bytes=size, last_scan=last)
        if state == "ready":
            self.recovery_after[root] = time.monotonic() + RECOVERY_COOLDOWN
        if root in self.rescan_pending and not self.paused and root not in self.paused_drives and not self._recovery_waiting(root):
            self.rescan_pending.discard(root)
            self._start_index(root, reason="Notifications lost during the previous scan")
            return
        if root in self.rescan_pending:
            self._set(root, message="Recovery scan queued (lost notifications)")
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
        if self.work_mode or not self._io_allowed(root):return
        paths = self.held.pop(root, set())
        deferred = {path for path in paths if self.folder_updater.covers(root,path)}
        if deferred:
            self.held[root] = deferred
            paths -= deferred
        if root in self.rescan_pending and not self._recovery_waiting(root):
            self.rescan_pending.discard(root)
            self._start_index(root, reason="Deferred recovery after lost notifications")
            return
        if not paths:
            return
        rows = [] if rows is None else rows
        gen = self._gen(con, root)
        for path in paths:
            self._apply_path(con, path, gen, rows)
        self._flush(con, rows)
        self._set(root)

    def _start_index(self, root, fast_startup=False, reason=None):
        with self.release_guard:
            return self._launch_index(root,fast_startup,reason)

    def _launch_index(self, root, fast_startup=False, reason=None):
        if not self._io_allowed(root) or root in self.indexers:
            return
        if self.work_mode and not fast_startup:
            self.rescan_pending.add(root)
            self._set(root,state='ready',message='Recovery deferred in game/work mode')
            return
        indexer = Indexer(self.db_path, root, fast_startup=fast_startup)
        expected=self.mounts.get(root,{}).copy()
        indexer.guard_check=lambda:self._valid_mount(root,expected.get('token')) and self.mounts.get(root,{})==expected
        reason = reason or ("Application startup" if fast_startup else "Recovery requested")
        diagnostics.report(self.db_path, "scan", root, "started: " + reason, scan=True)
        def progress(count, size, initial):
            try:
                self.inbox.put_nowait(("progress", root, (indexer, count, size, initial)))
                self.wake.set()
            except queue.Full:
                pass
        indexer.on_progress = progress
        indexer.pause_check = lambda: self.paused or root in self.paused_drives or (self.work_mode and not fast_startup)
        indexer.on_status = lambda r, state, message="": self._post_index_status(indexer, state, message)
        self.indexers[root] = indexer
        self._set(root, state="indexing", message=reason, scan_reason=reason,
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
        root=os.path.splitdrive(path)[0]+os.sep
        if not self._io_allowed(root) or storage.is_excluded_path(path):return
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
        """Trim history in bounded chunks so the write lock is never held for long."""
        now = time.time()
        if now - self.last_prune < PRUNE_EVERY:
            return
        more = storage.prune_log(con, LOG_DAYS * 86400)
        cutoff = (date.today() - timedelta(days=DAILY_DAYS)).isoformat()
        con.execute("DELETE FROM folder_daily WHERE day < ?", (cutoff,))
        con.commit()
        storage.release_free_space(con)
        con.commit()
        # A large backlog (for example after upgrading) continues in a few seconds.
        self.last_prune = now - PRUNE_EVERY + PRUNE_BACKLOG_EVERY if more else now
