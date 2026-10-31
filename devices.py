"""Windows device identity and safe PnP removal. Never force-dismounts."""
import ctypes as C
import os
import sys
import threading
import uuid
from ctypes import wintypes as W

if os.name=='nt':
    kernel=C.WinDLL('kernel32',use_last_error=True)
    setup=C.WinDLL('setupapi',use_last_error=True)
    cm=C.WinDLL('cfgmgr32',use_last_error=True)
    INVALID=C.c_void_p(-1).value
    class GUID(C.Structure):
        _fields_=[('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',C.c_ubyte*8)]
    class Interface(C.Structure):
        _fields_=[('size',W.DWORD),('guid',GUID),('flags',W.DWORD),('reserved',C.c_size_t)]
    class DevInfo(C.Structure):
        _fields_=[('size',W.DWORD),('guid',GUID),('node',W.DWORD),('reserved',C.c_size_t)]
    class DeviceNumber(C.Structure):
        _fields_=[('type',W.DWORD),('number',W.DWORD),('partition',W.DWORD)]
    class FilterUnion(C.Union):
        _fields_=[('handle',W.HANDLE),('instance',W.WCHAR*200),('guid',GUID)]
    class NotifyFilter(C.Structure):
        _fields_=[('size',W.DWORD),('flags',W.DWORD),('type',W.DWORD),('reserved',W.DWORD),('u',FilterUnion)]
    CALLBACK=C.WINFUNCTYPE(W.DWORD,W.HANDLE,C.c_void_p,W.DWORD,C.c_void_p,W.DWORD)
    kernel.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
    kernel.CreateFileW.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE]
    kernel.DeviceIoControl.argtypes=[W.HANDLE,W.DWORD,C.c_void_p,W.DWORD,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p]
    kernel.GetVolumeNameForVolumeMountPointW.argtypes=[W.LPCWSTR,W.LPWSTR,W.DWORD]
    kernel.GetVolumeInformationW.argtypes=[W.LPCWSTR,W.LPWSTR,W.DWORD,C.c_void_p,C.c_void_p,C.c_void_p,W.LPWSTR,W.DWORD]
    setup.SetupDiGetClassDevsW.argtypes=[C.POINTER(GUID),W.LPCWSTR,W.HWND,W.DWORD]
    setup.SetupDiGetClassDevsW.restype=W.HANDLE
    setup.SetupDiEnumDeviceInterfaces.argtypes=[W.HANDLE,C.c_void_p,C.POINTER(GUID),W.DWORD,C.POINTER(Interface)]
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes=[W.HANDLE,C.POINTER(Interface),C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.POINTER(DevInfo)]
    setup.SetupDiDestroyDeviceInfoList.argtypes=[W.HANDLE]
    cm.CM_Get_Parent.argtypes=[C.POINTER(W.DWORD),W.DWORD,W.DWORD]
    cm.CM_Get_Device_IDW.argtypes=[W.DWORD,W.LPWSTR,W.DWORD,W.DWORD]
    cm.CM_Get_DevNode_Registry_PropertyW.argtypes=[W.DWORD,W.DWORD,C.POINTER(W.DWORD),C.c_void_p,C.POINTER(W.DWORD),W.DWORD]
    cm.CM_Register_Notification.argtypes=[C.POINTER(NotifyFilter),C.c_void_p,CALLBACK,C.POINTER(W.HANDLE)]
    cm.CM_Unregister_Notification.argtypes=[W.HANDLE]
    cm.CM_Request_Device_EjectW.argtypes=[W.DWORD,C.POINTER(W.DWORD),W.LPWSTR,W.DWORD,W.DWORD]


def volume_identity(root):
    if os.name!='nt':return None
    name=C.create_unicode_buffer(1024)
    return name.value if kernel.GetVolumeNameForVolumeMountPointW(root,name,len(name)) else None


def disk_number(path):
    handle=kernel.CreateFileW(path,0,7,None,3,0,None)
    if handle in (None,INVALID):return None
    try:
        number=DeviceNumber();returned=W.DWORD()
        if kernel.DeviceIoControl(handle,0x2D1080,None,0,C.byref(number),C.sizeof(number),C.byref(returned),None):
            # Numbers are scoped to device type: a virtual device can report
            # number 0 while a completely separate physical disk is also 0.
            if returned.value>=C.sizeof(number) and number.type==7:
                return number.number
    finally:kernel.CloseHandle(handle)


def describe(root):
    """Resolve a volume to its disk and first removable PnP ancestor."""
    identity=volume_identity(root)
    number=disk_number('\\\\.\\'+root[:2])
    label=C.create_unicode_buffer(261)
    kernel.GetVolumeInformationW(root,label,len(label),None,None,None,None,0)
    result={'root':root,'identity':identity,'label':label.value,'number':number,'node':None,'removable':False}
    if number is None:return result
    guid=GUID.from_buffer_copy(uuid.UUID('53f56307-b6bf-11d0-94f2-00a0c91efb8b').bytes_le)
    info=setup.SetupDiGetClassDevsW(C.byref(guid),None,None,0x12)
    if info in (None,INVALID):return result
    try:
        for index in range(256):
            interface=Interface();interface.size=C.sizeof(interface)
            if not setup.SetupDiEnumDeviceInterfaces(info,None,C.byref(guid),index,C.byref(interface)):break
            required=W.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(info,C.byref(interface),None,0,C.byref(required),None)
            if not 8<=required.value<=65536:continue
            detail=C.create_string_buffer(required.value)
            C.cast(detail,C.POINTER(W.DWORD))[0]=8 if C.sizeof(C.c_void_p)==8 else 6
            device=DevInfo();device.size=C.sizeof(device)
            if not setup.SetupDiGetDeviceInterfaceDetailW(info,C.byref(interface),detail,len(detail),None,C.byref(device)):continue
            path=C.wstring_at(C.addressof(detail)+4)
            if disk_number(path)!=number:continue
            disk_id=C.create_unicode_buffer(200)
            if cm.CM_Get_Device_IDW(device.node,disk_id,len(disk_id),0):continue
            result['disk_id']=disk_id.value
            node=device.node
            for _ in range(12):
                ident=C.create_unicode_buffer(200)
                if cm.CM_Get_Device_IDW(node,ident,len(ident),0):break
                name=ident.value.upper()
                # A hub/root controller is not an eligible eject target.
                if 'ROOT_HUB' in name or name.startswith(('PCI\\','ACPI\\')):break
                caps=W.DWORD();size=W.DWORD(4);kind=W.DWORD()
                ok=cm.CM_Get_DevNode_Registry_PropertyW(node,0x10,C.byref(kind),C.byref(caps),C.byref(size),0)==0
                if ok and caps.value&4:
                    result.update(node=node,removable=True,pnp_id=ident.value);break
                parent=W.DWORD()
                if cm.CM_Get_Parent(C.byref(parent),node,0):break
                node=parent.value
            break
    finally:setup.SetupDiDestroyDeviceInfoList(info)
    if root[:2].upper()==os.environ.get('SystemDrive','C:').upper():
        result.update(node=None,removable=False)
    return result


def dependencies(roots):
    import storage
    paths=[sys.executable,storage.DATA_DIR,os.getcwd()]
    if not storage.FROZEN:paths.append(storage.APP_DIR)
    letters={r[:2].upper() for r in roots}
    return [p for p in paths if os.path.splitdrive(os.path.abspath(p))[0].upper() in letters]


def request_eject(device):
    if not device.get('removable') or device.get('node') is None:
        return False,'Windows has not identified this device as safely ejectable.'
    if not device.get('identity') or volume_identity(device['root'])!=device['identity']:
        return False,'The selected volume changed or disappeared. No eject request was sent.'
    current=C.create_unicode_buffer(200)
    if cm.CM_Get_Device_IDW(device['node'],current,len(current),0) or current.value!=device.get('pnp_id'):
        return False,'The device identity changed. No eject request was sent.'
    veto=W.DWORD();name=C.create_unicode_buffer(260)
    status=cm.CM_Request_Device_EjectW(device['node'],C.byref(veto),name,len(name),0)
    if status==0:return True,'Windows confirmed safe removal.'
    return False,f'Windows refused removal (status {status}, reason {veto.value})'+(': '+name.value if name.value else '.')


class Registration:
    """Keep callback alive; unregister outside the PnP callback thread."""
    def __init__(self,handle,on_event):
        self.handle=W.HANDLE();self.retire=False
        def callback(notification,context,action,data,size):
            try:return on_event(action)
            except Exception:return 1223 if action==2 else 0
        self.callback=CALLBACK(callback)
        filt=NotifyFilter();filt.size=C.sizeof(filt);filt.type=1;filt.u.handle=handle
        result=cm.CM_Register_Notification(C.byref(filt),None,self.callback,C.byref(self.handle))
        if result:raise OSError(f'Device-removal registration failed ({result})')

    def close(self):
        if self.handle:
            if cm.CM_Unregister_Notification(self.handle):return False
            self.handle=W.HANDLE()
        return True
