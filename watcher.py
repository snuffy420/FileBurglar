"""Live change feed: one ReadDirectoryChangesW watcher per fixed or removable drive.

The kernel reports changed names, so nothing walks the disk and Explorer and
other programs are not touched. Overflows (too many changes in one burst) are
reported so the engine rescans that drive instead of guessing.
"""

import ctypes
import os
import struct
import threading
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
BUF_SIZE = 64 * 1024
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

    def __init__(self, root, on_events, on_overflow, on_error, on_ready=None):
        super().__init__(name=f"fb-watch-{root[:1]}", daemon=True)
        self.root = root
        self.on_events = on_events
        self.on_overflow = on_overflow
        self.on_error = on_error
        self.on_ready = on_ready or (lambda root: None)
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
        announced = False
        try:
            while not self.stop_flag:
                k32.ResetEvent(event)
                ok = k32.ReadDirectoryChangesW(handle, buf, BUF_SIZE, True, NOTIFY_FILTER, None,
                                               ctypes.byref(ov), None)
                if not ok and ctypes.get_last_error() != ERROR_IO_PENDING:
                    self.failed = True
                    self.on_error(self.root, f"watch stopped (error {ctypes.get_last_error()})")
                    return
                if not announced:
                    self.on_ready(self.root)  # The change feed is armed before the scan starts.
                    announced = True
                if k32.WaitForMultipleObjects(2, waits, False, INFINITE) != 0:
                    break  # stop requested
                if not k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(returned), False):
                    self.failed = True
                    self.on_error(self.root, f"watch stopped (error {ctypes.get_last_error()})")
                    return
                if returned.value == 0:
                    self.on_overflow(self.root)  # buffer overflowed: some changes were lost
                    continue
                try:
                    events = parse_notifications(buf.raw[: returned.value], self.root)
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

    def __init__(self, on_events, on_overflow, on_drive, poll_seconds=5.0):
        super().__init__(name="fb-drives", daemon=True)
        self.on_events = on_events
        self.on_overflow = on_overflow
        self.on_drive = on_drive  # callback(root, state) with state online, offline or error
        self.poll_seconds = poll_seconds
        self.watchers = {}
        self.stop_flag = False
        self.stop_event = threading.Event()

    def _on_error(self, root, message):
        self.on_drive(root, "error", message)

    def _start(self, root):
        watcher = DriveWatcher(root, self.on_events, self.on_overflow, self._on_error,
                               lambda r: self.on_drive(r, "online", ""))
        self.watchers[root] = watcher
        watcher.start()

    def run(self):
        while not self.stop_flag:
            # Unmonitored drives get no watcher at all, so they cost nothing while they are connected.
            skipped = set(settings.load()["unmonitored_drives"])
            present = {root for root in list_drives() if root[:2] not in skipped}
            for root in present - set(self.watchers):
                self._start(root)
            for root in set(self.watchers) - present:
                watcher = self.watchers.pop(root)
                watcher.stop()
                watcher.join(timeout=5)
                self.on_drive(root, "offline", "")
            for root, watcher in list(self.watchers.items()):
                if not watcher.is_alive() and root in present:
                    self._start(root)
            self.stop_event.wait(self.poll_seconds)
        for watcher in self.watchers.values():
            watcher.stop()
        for watcher in self.watchers.values():
            watcher.join(timeout=5)

    def stop(self):
        self.stop_flag = True
        self.stop_event.set()
