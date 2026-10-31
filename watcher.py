"""Live change feed: one ReadDirectoryChangesW watcher per fixed or removable drive.

The kernel reports changed names, so nothing walks the disk and Explorer and
other programs are not touched. Overflows (too many changes in one burst) are
reported so the engine rescans that drive instead of guessing.
"""

import ctypes
import os
import struct
import threading
import time
import uuid
from ctypes import wintypes

import settings

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_ALL = 0x0007
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OVERLAPPED = 0x40000000
# File name, directory name, size and last write. Access times are ignored on purpose.
NOTIFY_FILTER = 0x1 | 0x2 | 0x8 | 0x10
ERROR_IO_PENDING = 997
INFINITE = 0xFFFFFFFF
# Local fixed/removable drives only (network drives are excluded above).
# A modest extra buffer absorbs bursts without a polling thread or disk reads.
BUF_SIZE = 256 * 1024
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
# Notification actions. A rename becomes a removal plus an addition so the engine's accounting stays simple.
ACTIONS = {1: "added", 2: "removed", 3: "changed", 4: "removed", 5: "added"}

if os.name == "nt":
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLE = wintypes.HANDLE

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", HANDLE),
        ]

    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, HANDLE]
    k32.CreateFileW.restype = HANDLE
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    k32.CreateEventW.restype = HANDLE
    k32.ResetEvent.argtypes = [HANDLE]
    k32.SetEvent.argtypes = [HANDLE]
    k32.ReadDirectoryChangesW.argtypes = [HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
                                          wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    k32.ReadDirectoryChangesW.restype = wintypes.BOOL
    k32.GetOverlappedResult.argtypes = [HANDLE, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    k32.GetOverlappedResult.restype = wintypes.BOOL
    k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(HANDLE), wintypes.BOOL, wintypes.DWORD]
    k32.WaitForMultipleObjects.restype = wintypes.DWORD
    k32.CancelIo.argtypes = [HANDLE]
    k32.CloseHandle.argtypes = [HANDLE]
    k32.GetLogicalDrives.restype = wintypes.DWORD
    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k32.GetDriveTypeW.restype = wintypes.UINT
    INVALID_HANDLE = ctypes.c_void_p(-1).value
else:
    k32 = None
    OVERLAPPED = None
    INVALID_HANDLE = None


def list_drives():
    """Roots of every fixed or removable drive that is currently present."""
    if k32 is None:
        return []
    mask = k32.GetLogicalDrives()
    roots = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(ord('A') + i)}:\\"
            if k32.GetDriveTypeW(root) in (DRIVE_FIXED, DRIVE_REMOVABLE):
                roots.append(root)
    return roots


def parse_notifications(data: bytes, root: str):
    """Turn a FILE_NOTIFY_INFORMATION buffer into [(action, full_path)]."""
    out = []
    offset = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Truncated notification header")
        next_off, action, name_len = struct.unpack_from("<III", data, offset)
        if name_len % 2 or offset + 12 + name_len > len(data):
            raise ValueError("Invalid notification name length")
        name = data[offset + 12: offset + 12 + name_len].decode("utf-16-le", errors="replace")
        kind = ACTIONS.get(action)
        if kind:
            out.append((kind, os.path.join(root, name)))
        if not next_off:
            break
        if next_off < 12 + name_len or offset + next_off >= len(data):
            raise ValueError("Invalid notification offset")
        offset += next_off
    return out


class DriveWatcher(threading.Thread):
    """Blocks on one drive's change notifications until stop() is called or the drive disappears."""

    def __init__(self, root, on_events, on_overflow, on_error, on_ready=None, on_handle=None):
        super().__init__(name=f"fb-watch-{root[:1]}", daemon=True)
        self.root = root
        self.on_events = on_events
        self.on_overflow = on_overflow
        self.on_error = on_error
        self.on_ready = on_ready or (lambda root: None)
        self.on_handle=on_handle
        self.stop_flag = False
        self.failed = False
        self._stop_handle = None
        self._handle_lock = threading.Lock()

    def stop(self):
        self.stop_flag = True
        with self._handle_lock:
            if self._stop_handle:
                k32.SetEvent(self._stop_handle)

    def run(self):
        handle = k32.CreateFileW(self.root, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None, OPEN_EXISTING,
                                 FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED, None)
        if not handle or handle == INVALID_HANDLE:
            self.failed = True
            self.on_error(self.root, f"cannot open drive (error {ctypes.get_last_error()})")
            return
        event = k32.CreateEventW(None, True, False, None)
        self._stop_handle = k32.CreateEventW(None, True, False, None)
        if self.stop_flag:
            k32.SetEvent(self._stop_handle)
        buf = ctypes.create_string_buffer(BUF_SIZE)
        ov = OVERLAPPED()
        ov.hEvent = event
        returned = wintypes.DWORD()
        waits = (HANDLE * 2)(event, self._stop_handle)
        def arm():
            k32.ResetEvent(event)
            ok = k32.ReadDirectoryChangesW(handle, buf, BUF_SIZE, True, NOTIFY_FILTER, None,
                                           ctypes.byref(ov), None)
            if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                self.failed = True
                self.on_error(self.root, f"watch stopped (error {ctypes.get_last_error()})")
                return False
            return True
        try:
            if self.on_handle:
                try:self.on_handle(handle)
                except OSError as error:
                    self.failed=True
                    self.on_error(self.root,str(error));return
            if not arm():
                return
            self.on_ready(self.root)
            while not self.stop_flag:
                if k32.WaitForMultipleObjects(2, waits, False, INFINITE) != 0:
                    break  # stop requested
                if not k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), False):
                    self.failed = True
                    self.on_error(self.root, f"watch stopped (error {ctypes.get_last_error()})")
                    return
                # Copy the completed batch, then immediately arm the next read.
                # Parsing and engine backpressure must not delay draining Windows'
                # notification buffer on busy drives.
                data = ctypes.string_at(buf, returned.value)
                if not arm():
                    return
                if not data:
                    self.on_overflow(self.root)  # buffer overflowed: some changes were lost
                    continue
                try:
                    events = parse_notifications(data, self.root)
                except ValueError:
                    self.on_overflow(self.root)
                    continue
                if events:
                    self.on_events(self.root, events)
        finally:
            k32.CancelIo(handle)
            # Cancellation is asynchronous; keep OVERLAPPED and its buffer alive.
            k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), True)
            with self._handle_lock:
                self._stop_handle, stop_handle = None, self._stop_handle
            k32.CloseHandle(event)
            k32.CloseHandle(stop_handle)
            k32.CloseHandle(handle)


class DriveManager(threading.Thread):
    """Starts a watcher for each drive that appears and stops it when the drive goes away."""

    def __init__(self, on_events, on_overflow, on_drive, poll_seconds=5.0, engine=None):
        super().__init__(name="fb-drives", daemon=True)
        self.on_events = on_events
        self.on_overflow = on_overflow
        self.on_drive = on_drive  # callback(root, state) with state online, offline or error
        self.poll_seconds = poll_seconds
        self.watchers = {}
        self.stop_flag = False
        self.stop_event = threading.Event()
        self.engine=engine
        self.lock=threading.RLock()
        self.blocked=set()
        self.seen_absent=set()
        self.devices={}
        self.registrations=[]
        self.retire=set()
        self.capacity_idle=None
        self.absent=set()
        self.reconnected=set()
        self.available=set()
        self.passive={}
        self.registration_failed=set()

    def _register(self,root,handle):
        if self.engine is None:return
        import devices
        try:
            registration=devices.Registration(handle,lambda action:self._device_event(root,action))
        except OSError as error:
            # Some virtual or unusual volumes refuse removal notifications. Keep
            # watching files instead of failing and restarting every poll.
            if root not in self.registration_failed:
                self.registration_failed.add(root)
                import diagnostics
                diagnostics.report(self.engine.db_path,'watcher',root,
                                   f'{error}; file monitoring continues without Windows removal notifications')
            return
        self.registration_failed.discard(root)
        with self.lock:self.registrations.append((root,registration))

    def group(self,root):
        with self.lock:
            selected=self.devices.get(root,{})
            # A resolved PnP identity proves a shared device; numeric IDs alone
            # can collide with virtual devices or be reused after removal.
            disk_id=selected.get('disk_id')
            pnp_id=selected.get('pnp_id')
            active=self.available|set(self.watchers)|set(self.passive)|{root}
            return {root}|{r for r,d in self.devices.items() if r in active and (
                (disk_id and d.get('disk_id')==disk_id) or
                (pnp_id and d.get('pnp_id')==pnp_id))}

    def release(self,root,timeout=3):
        import devices,smart
        roots=self.group(root)
        if devices.dependencies(roots):
            return False,'File Burglar is running from, storing data on, or using a working directory on this device. Quit File Burglar before ejecting it.'
        deadline=time.monotonic()+timeout
        with self.lock:
            self.blocked.update(roots)
            watchers=[w for r,w in self.watchers.items() if r in roots]
        smart.block('removal')
        for watcher in watchers:watcher.stop()
        if self.engine:
            self.engine.request_release(roots)
        for watcher in watchers:watcher.join(max(0,deadline-time.monotonic()))
        health=smart.wait_idle(max(0,deadline-time.monotonic()))
        capacity=self.capacity_idle is None or self.capacity_idle.wait(max(0,deadline-time.monotonic()))
        workers=not any(w.is_alive() for w in watchers)
        engine_ok=self.engine is None or self.engine.release(roots,max(0,deadline-time.monotonic()))
        if not (health and capacity and workers and engine_ok):
            return False,'File Burglar is still finishing drive operations. The drive has not been declared safe to remove. Retry after operations finish.'
        return True,'File Burglar released its access. Use Windows Eject to confirm safe removal.'

    def resume(self,root):
        import smart
        roots=self.group(root)
        with self.lock:
            self.blocked.difference_update(roots);self.retire.update(roots)
        if self.engine:self.engine.resume_released(roots)
        if not self.blocked:smart.unblock('removal')
        self.stop_event.set()  # wake the manager; it clears this between polls

    def _device_event(self,root,action):
        if action==2:
            ok,_message=self.release(root)
            return 0 if ok else 1223
        if action==3:
            self.resume(root)
        elif action in (4,5):
            if self.engine:
                self.engine.invalidate_mount(root,getattr(self.watchers.get(root),'token',None))
            with self.lock:self.retire.update(self.group(root))
            if action==4:self.release(root)
            if action==5:
                self.seen_absent.update(self.group(root));self.reconnected.update(self.group(root))
        return 0

    def safe_eject(self,root,expected_identity=None):
        import devices
        device=devices.describe(root)
        if expected_identity and device.get('identity')!=expected_identity:return False,'The drive assignment changed. Refresh and select the drive again.'
        self.devices[root]=device
        if not device.get('removable'):return False,'This device is not identified by Windows as safely ejectable.'
        ok,message=self.release(root)
        if not ok:return ok,message
        ok,message=devices.request_eject(device)
        if not ok:self.resume(root)
        return ok,message

    def _cleanup_notifications(self,all_roots=False):
        with self.lock:
            closing=[pair for pair in self.registrations if all_roots or pair[0] in self.retire]
            self.registrations=[pair for pair in self.registrations if pair not in closing]
            self.retire.clear()
        for root,registration in closing:
            if not registration.close():
                # Keep the callback alive until Windows confirms unregistration.
                with self.lock:self.registrations.append((root,registration));self.retire.add(root)

    def _on_error(self, root, message):
        self.on_drive(root, "error", message)

    def _start(self, root):
        import devices
        if self.engine and root in self.engine.indexers:
            if not self.engine.release({root},2):return
        self.devices[root]=devices.describe(root)
        physically_reconnected=root in self.reconnected
        token=uuid.uuid4().hex
        device=self.devices[root].copy()
        def online(r):
            self.reconnected.discard(r)
            self.on_drive(r,'online',{'identity':device.get('identity') or '', 'label':device.get('label',''),
                                      'token':token,'reconnected':physically_reconnected})
        def events(r,batch):
            if self.engine:self.engine.on_events(r,batch,token)
            else:self.on_events(r,batch)
        def overflow(r):
            if self.engine:self.engine.on_overflow(r,token)
            else:self.on_overflow(r)
        def error(r,message):
            if self.engine:self.engine.invalidate_mount(r,token)
            self._on_error(r,message)
        watcher = DriveWatcher(root, events, overflow, error,
                               online,
                               lambda handle:self._register(root,handle))
        watcher.token=token
        with self.lock:
            if root in self.blocked or self.stop_flag:return
            self.watchers[root] = watcher
            watcher.start()

    def run(self):
        while not self.stop_flag:
            self._cleanup_notifications()
            # Unmonitored drives get no watcher at all, so they cost nothing while they are connected.
            preferences=settings.load()
            available=set(list_drives())
            self.available=available
            import devices
            identities={root:devices.volume_identity(root) for root in available}
            for root in self.blocked&available:
                previous=self.devices.get(root,{}).get('identity')
                if identities[root] and previous and identities[root]!=previous:self.resume(root)
            if not self.blocked&available:
                import smart
                smart.unblock('removal')
            self.absent.update(set(self.devices)-available)
            self.reconnected.update(available & self.absent)
            self.absent.difference_update(available)
            present={root for root in available if settings.monitoring_enabled(preferences,root,identities[root])}
            # One watcher per volume even if Windows exposes multiple letters.
            preferred={m['id']:r for r,m in self.engine.mounts.items() if r in present} if self.engine else {}
            seen=set();unique=set()
            for root in sorted(present,key=lambda r:0 if preferred.get(identities[r])==r else 1):
                identity=identities[root]
                if identity and identity in seen:continue
                if identity:seen.add(identity)
                unique.add(root)
            present=unique
            passive={r for r in available if not settings.monitoring_enabled(preferences,r,identities[r])}
            passive_ids=set();unique_passive=set()
            for root in sorted(passive):
                identity=identities[root]
                if identity and identity in passive_ids:continue
                if identity:passive_ids.add(identity)
                unique_passive.add(root)
            passive=unique_passive
            for root in self.blocked-available:self.seen_absent.add(root)
            for root in available & self.seen_absent:
                self.resume(root);self.seen_absent.discard(root)
            changed={root for root in set(self.watchers)&present if identities[root]!=self.devices.get(root,{}).get('identity')}
            if self.engine:
                changed.update(root for root in set(self.watchers)&present-self.blocked if root not in self.engine.pending_mounts and self.engine.mounts.get(root,{}).get('token')!=getattr(self.watchers[root],'token',None))
            for root in (set(self.watchers) - present)|changed:
                watcher = self.watchers.pop(root)
                if self.engine:self.engine.invalidate_mount(root,getattr(watcher,'token',None))
                watcher.stop()
                watcher.join(timeout=5)
                self.on_drive(root, "offline", {'token':getattr(watcher,'token',None)})
                with self.lock:self.retire.add(root)
            for root,(identity,token) in list(self.passive.items()):
                if root not in passive or identity!=identities.get(root):
                    if self.engine:self.engine.invalidate_mount(root,token)
                    self.on_drive(root,'offline',dict(token=token));self.passive.pop(root,None)
            for root in passive-self.blocked-set(self.passive):
                token=uuid.uuid4().hex;device=devices.describe(root);self.devices[root]=device
                self.passive[root]=(identities[root],token)
                self.on_drive(root,'online',dict(identity=identities[root],label=device.get('label',''),token=token,monitor=False))
            for root in present - set(self.watchers) - self.blocked:
                self._start(root)
            for root, watcher in list(self.watchers.items()):
                if not watcher.is_alive() and root in present and root not in self.blocked:
                    with self.lock:self.retire.add(root)
                    self._cleanup_notifications()
                    self._start(root)
            self.stop_event.wait(self.poll_seconds)
            self.stop_event.clear()
        for watcher in self.watchers.values():
            watcher.stop()
        for watcher in self.watchers.values():
            watcher.join(timeout=5)
        self._cleanup_notifications(all_roots=True)

    def stop(self):
        self.stop_flag = True
        self.stop_event.set()

    def refresh(self):
        self.stop_event.set()
