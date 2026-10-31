"""Optional, infrequent process-name matching for automatic work mode."""
import ctypes as C
import os
from ctypes import wintypes as W


def matched_process(names):
    wanted={os.path.basename(x).casefold() for x in names}
    if not wanted or os.name!='nt':return None
    kernel=C.WinDLL('kernel32',use_last_error=True)
    class Entry(C.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),
                  ('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),
                  ('priority',W.LONG),('flags',W.DWORD),('name',W.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    kernel.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(Entry)]
    kernel.Process32NextW.argtypes=[W.HANDLE,C.POINTER(Entry)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle in (None,C.c_void_p(-1).value):return None
    try:
        item=Entry();item.size=C.sizeof(item)
        ok=kernel.Process32FirstW(handle,C.byref(item))
        while ok:
            if item.name.casefold() in wanted:return item.name
            ok=kernel.Process32NextW(handle,C.byref(item))
    finally:kernel.CloseHandle(handle)
    return None
