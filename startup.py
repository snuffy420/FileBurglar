"""Opt-in, elevated logon startup through Windows Task Scheduler."""
import csv
import io
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

TASK_NAME = 'FileBurglar'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'


def _run(*args):
    return subprocess.run(args, capture_output=True, creationflags=0x08000000, timeout=15)


def _task():
    result = _run('schtasks.exe', '/Query', '/TN', TASK_NAME, '/XML')
    if result.returncode:
        return None
    try:
        return ET.fromstring(result.stdout)
    except ET.ParseError:
        return None


def enabled():
    task = _task()
    if task is not None:
        return task.findtext('.//t:Settings/t:Enabled', namespaces={'t': NS}) != 'false'
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, TASK_NAME)
        return True
    except OSError:
        return False


def task_xml(sid, executable, arguments):
    ET.register_namespace('', NS)
    root = ET.Element('Task', {'version': '1.2', 'xmlns': NS})
    def add(parent, tag, text=None, **attributes):
        node = ET.SubElement(parent, tag, attributes)
        if text is not None:
            node.text = text
        return node
    registration = add(root, 'RegistrationInfo')
    add(registration, 'Description', 'File Burglar drive monitoring at logon')
    trigger = add(add(root, 'Triggers'), 'LogonTrigger')
    add(trigger, 'Enabled', 'true')
    add(trigger, 'UserId', sid)
    principal = add(add(root, 'Principals'), 'Principal', id='Author')
    add(principal, 'UserId', sid)
    add(principal, 'LogonType', 'InteractiveToken')
    add(principal, 'RunLevel', 'HighestAvailable')
    settings = add(root, 'Settings')
    for tag, value in [('MultipleInstancesPolicy','IgnoreNew'), ('DisallowStartIfOnBatteries','false'),
                       ('StopIfGoingOnBatteries','false'), ('StartWhenAvailable','true'),
                       ('Enabled','true'), ('ExecutionTimeLimit','PT0S')]:
        add(settings, tag, value)
    action = add(add(root, 'Actions', Context='Author'), 'Exec')
    add(action, 'Command', executable)
    if arguments:
        add(action, 'Arguments', subprocess.list2cmdline(arguments))
    add(action, 'WorkingDirectory', os.path.dirname(executable))
    return ET.tostring(root, encoding='utf-16', xml_declaration=True)


def _remove_legacy(command=None):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, TASK_NAME)
            if command is None or value.casefold() == command.casefold():
                winreg.DeleteValue(key, TASK_NAME)
    except FileNotFoundError:
        pass


def set_enabled(value, executable, arguments):
    if value:
        identity = _run('whoami.exe', '/user', '/fo', 'csv', '/nh')
        rows = list(csv.reader(io.StringIO(identity.stdout.decode('utf-8', errors='replace'))))
        if identity.returncode or not rows or len(rows[0]) < 2 or not rows[0][1].startswith('S-1-'):
            raise OSError('Windows could not identify the current user for logon startup.')
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.xml', delete=False) as file:
                path = file.name
                file.write(task_xml(rows[0][1], executable, arguments))
            result = _run('schtasks.exe', '/Create', '/TN', TASK_NAME, '/XML', path, '/F')
            if result.returncode:
                raise OSError('Windows could not save the logon task: ' + result.stderr.decode(errors='replace').strip())
        finally:
            if path and os.path.isfile(path):
                os.unlink(path)
        _remove_legacy()
    else:
        if _task() is not None:
            result = _run('schtasks.exe', '/Delete', '/TN', TASK_NAME, '/F')
            if result.returncode:
                raise OSError('Windows could not remove the logon task.')
        _remove_legacy()


def remove_if_owned(executable, arguments):
    task = _task()
    if task is not None:
        target = task.findtext('.//t:Exec/t:Command', namespaces={'t': NS}) or ''
        if os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(executable)):
            result = _run('schtasks.exe', '/Delete', '/TN', TASK_NAME, '/F')
            if result.returncode:
                raise OSError('Windows could not remove this installation\'s logon task.')
    _remove_legacy(subprocess.list2cmdline([executable] + arguments))
