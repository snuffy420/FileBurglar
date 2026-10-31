"""Bounded, local notification episodes; no background threads or polling."""
import json
import os
import tempfile
import time
from pathlib import Path

import storage


class Episodes:
    def __init__(self, name='notification-state.json'):
        self.path = Path(storage.DATA_DIR)/name
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.entries = saved if isinstance(saved,dict) else {}
        except (OSError,ValueError):
            self.entries = {}
        self.entries = dict(list(self.entries.items())[-256:])
        self.cooling = {}  # Deliberately not persisted: require observed cooling.
        self.last_seen = {}

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.entries = dict(list(self.entries.items())[-256:])
        name = None
        try:
            with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self.path.parent,delete=False) as f:
                name=f.name;json.dump(self.entries,f,allow_nan=False)
            os.replace(name,self.path)
        finally:
            if name and os.path.exists(name):os.unlink(name)

    def observe(self,key,severity,safely_recovered,reset_seconds=3600,enabled=True,now=None,max_gap=None):
        """Return true once at each upward severity until sustained recovery.

        None means missing readings: break cooling but retain the episode.
        Muted episodes still advance, preventing a flood when unmuted.
        """
        now=time.monotonic() if now is None else now
        if key not in self.last_seen and len(self.last_seen)>=256:
            oldest=next(iter(self.last_seen));self.last_seen.pop(oldest,None);self.cooling.pop(oldest,None)
        previous_time=self.last_seen.get(key)
        self.last_seen[key]=now
        if max_gap is not None and previous_time is not None and now-previous_time>max_gap:
            self.cooling.pop(key,None)
        if severity is None:
            self.cooling.pop(key,None)
            return False
        previous=self.entries.get(key,0)
        if type(previous) is not int:previous=0
        if safely_recovered:
            since=self.cooling.setdefault(key,now)
            if now-since>=reset_seconds:
                if previous:
                    self.entries.pop(key,None);self.save()
                self.cooling.pop(key,None)
        else:
            self.cooling.pop(key,None)
        if severity>previous:
            self.entries[key]=severity;self.save()
            return enabled
        return False


def temperature_observation(episodes,identity,celsius,limits,reset_hours,enabled):
    if celsius is None:
        return episodes.observe('heat:'+identity,None,False)
    severity=2 if celsius>=limits['hot'] else 1 if celsius>=limits['warm'] else 0
    return episodes.observe('heat:'+identity,severity,celsius<=limits['warm']-3,
                            reset_hours*3600,enabled,max_gap=180)
