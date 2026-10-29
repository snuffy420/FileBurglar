"""Settings for File Burglar: temperature limits per drive type and the drives shown in the tray.

Stored as JSON next to the database. A missing or unreadable file falls back to the defaults.
"""

import json
import os
import math
import tempfile

import storage

DEFAULTS = {
    "internal": {"warm": 45, "hot": 55},
    "external": {"warm": 40, "hot": 50},
    "tray_drives": [],
    "unmonitored_drives": [],
}


def _path() -> str:
    return os.path.join(storage.data_dir(), "settings.json")


def load() -> dict:
    data = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return data
    if not isinstance(saved, dict):
        return data
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
    return data


def save(data: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=storage.data_dir(),
                                         prefix="settings-", suffix=".tmp", delete=False) as fh:
            temporary = fh.name
            json.dump(data, fh, indent=2, allow_nan=False)
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
