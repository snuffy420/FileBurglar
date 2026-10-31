"""Settings for File Burglar: temperature limits per drive type and the drives shown in the tray.

Stored as JSON next to the database. A missing or unreadable file falls back to the defaults.
"""

import json
import os
import math
import tempfile
import threading
import copy

import storage

DEFAULTS = {
    "internal": {"warm": 45, "hot": 55},
    "external": {"warm": 40, "hot": 50},
    "tray_drives": [],
    "unmonitored_drives": [],
    "health_enabled": True,
    "temperature_alerts": True,
    "alert_reset_hours": 1,
    "auto_updates": True,
    "reconnect_summaries": True,
    "game_mode": False,
    "auto_game_mode": False,
    "game_processes": [],
    "exclusions": [],
    "space_rules": [],
    "growth_rules": [],
    "volume_preferences": {},
}
_LOCK=threading.RLock()
_CONTEXT=None
_CONTEXT_DIR=None


def _context():
    return _CONTEXT if _CONTEXT_DIR==os.path.abspath(storage.DATA_DIR) else None


def _capture(data,root):
    prefix=root.rstrip('\\')+'\\'
    def relative(path):return path[len(prefix):] if path.lower().startswith(prefix.lower()) else None
    return dict(tray=root[:2] in data['tray_drives'],unmonitored=root[:2] in data['unmonitored_drives'],
        exclusions=[relative(p) for p in data['exclusions'] if relative(p) is not None],
        space=[dict(r) for r in data['space_rules'] if r.get('drive','').upper()==root[:2].upper()],
        growth=[dict(r,relative=relative(r.get('path',''))) for r in data['growth_rules'] if relative(r.get('path','')) is not None])


def _project(data):
    context=_context()
    if context is None:return data
    for key in ('tray_drives','unmonitored_drives','exclusions','space_rules','growth_rules'):data[key]=[]
    for ident,mount in context.items():
        if not mount['present']:continue
        root=mount['root'];policy=data['volume_preferences'].get(ident,{})
        if policy.get('tray'):data['tray_drives'].append(root[:2])
        if policy.get('unmonitored'):data['unmonitored_drives'].append(root[:2])
        data['exclusions'].extend(root+p for p in policy.get('exclusions',[]))
        data['space_rules'].extend(dict(r,drive=root[:2]) for r in policy.get('space',[]))
        data['growth_rules'].extend(dict(r,path=root+r.get('relative','')) for r in policy.get('growth',[]))
    data['_volume_mounts']={ident:m['root'] if m['present'] else None for ident,m in context.items()}
    return data


def configure_volumes(volume_rows):
    global _CONTEXT,_CONTEXT_DIR
    with _LOCK:
        # Read before replacing the context so first migration captures the
        # user's existing letter-based choices exactly once.
        data=load()
        for volume in volume_rows:
            ident=volume['id']
            if ident not in data['volume_preferences']:
                data['volume_preferences'][ident]=_capture(data,volume['last_root']) if _context() is None else {}
        _CONTEXT_DIR=os.path.abspath(storage.DATA_DIR)
        _CONTEXT={v['id']:dict(root=v['last_root'],present=bool(v['present'])) for v in volume_rows}
        data=_project(data)
        _write(data)
        storage.set_exclusions(data['exclusions'])


def monitoring_enabled(data,root,identity):
    if _context() is not None:
        return not data['volume_preferences'].get(identity,{}).get('unmonitored',False)
    return root[:2] not in data['unmonitored_drives']


def link_preferences(source,destination):
    with _LOCK:
        data=load();preferences=data['volume_preferences']
        if source in preferences:preferences[destination]=preferences.pop(source)
        _write(_project(data))


def _path() -> str:
    return os.path.join(storage.data_dir(), "settings.json")


def load() -> dict:
    data = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return _project(data)
    if not isinstance(saved, dict):
        return _project(data)
    if isinstance(saved.get('volume_preferences'),dict):
        data['volume_preferences']={k:v for k,v in list(saved['volume_preferences'].items())[-256:] if isinstance(k,str) and isinstance(v,dict)}
    for key in ('health_enabled','temperature_alerts','auto_updates','reconnect_summaries','game_mode','auto_game_mode'):
        if type(saved.get(key)) is bool:
            data[key] = saved[key]
    if saved.get('alert_reset_hours') in (1,2):
        data['alert_reset_hours'] = saved['alert_reset_hours']
    for key in ('game_processes','exclusions'):
        values = saved.get(key)
        if isinstance(values,list):
            data[key] = list(dict.fromkeys(x.strip() for x in values[:256] if isinstance(x,str) and x.strip()))
    for key in ('space_rules','growth_rules'):
        if isinstance(saved.get(key),list):
            data[key] = [x for x in saved[key][:64] if isinstance(x,dict)]
    for kind in ("internal", "external"):
        for key in ("warm", "hot"):
            limits = saved.get(kind)
            value = limits.get(key) if isinstance(limits, dict) else None
            if type(value) in (int, float) and math.isfinite(value):
                data[kind][key] = value
        if data[kind]["warm"] >= data[kind]["hot"]:
            data[kind] = dict(DEFAULTS[kind])
    tray = saved.get("tray_drives")
    if isinstance(tray, list):
        data["tray_drives"] = sorted({x.upper() for x in tray if isinstance(x, str) and len(x) == 2 and x[0].isascii() and x[0].isalpha() and x[1] == ":"})
    unmonitored = saved.get("unmonitored_drives")
    if isinstance(unmonitored, list):
        data["unmonitored_drives"] = sorted({x.upper() for x in unmonitored if isinstance(x, str) and len(x) == 2 and x[0].isascii() and x[0].isalpha() and x[1] == ":"})
    return _project(data)


def save(data: dict) -> None:
    with _LOCK:
        if not isinstance(data,dict):_write(data);return
        incoming=copy.deepcopy(data)
        context=_context()
        if context is not None:
            current=load();policies=current['volume_preferences']
            expected=data.get('_volume_mounts',{})
            for ident,mount in context.items():
                if mount['present'] and expected.get(ident)==mount['root']:
                    policies[ident]=_capture(data,mount['root'])
            incoming['volume_preferences']=policies
            _project(incoming)
        _write(incoming)


def set_drive_choice(root,key,enabled,identity=None):
    """Save one click against the latest volume map, without a stale UI copy.

    Return the current projected settings, or None if the selected volume moved
    or disconnected. Do not recapture unrelated per-volume preferences.
    """
    policy_key={'tray_drives':'tray','unmonitored_drives':'unmonitored'}[key]
    with _LOCK:
        data=load();context=_context()
        if context is not None:
            matches=[ident for ident,mount in context.items() if mount['present'] and mount['root']==root]
            if len(matches)!=1 or (identity and matches[0]!=identity):return None
            policy=data['volume_preferences'].setdefault(matches[0],{})
            policy[policy_key]=bool(enabled)
            _project(data)
        else:
            data[key]=[letter for letter in data[key] if letter!=root[:2]]
            if enabled:data[key].append(root[:2])
        _write(data)
        return data


def _write(data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=storage.data_dir(),
                                         prefix="settings-", suffix=".tmp", delete=False) as fh:
            temporary = fh.name
            json.dump({k:v for k,v in data.items() if not k.startswith('_')} if isinstance(data,dict) else data, fh, indent=2, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, _path())
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def heat_class(celsius, external: bool, data: dict) -> str:
    """'cool', 'warm' or 'hot' for a temperature, using the limits for that drive type."""
    limits = data["external" if external else "internal"]
    if celsius < limits["warm"]:
        return "cool"
    if celsius < limits["hot"]:
        return "warm"
    return "hot"
