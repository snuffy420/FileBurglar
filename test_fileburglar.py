"""Isolated regression tests. Never open the application's real database."""
import os
import sqlite3
import struct
import shutil
import uuid
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import storage
import settings
from engine import Engine
from indexer import Indexer, list_folder, reconcile_folder
from watcher import DriveWatcher, parse_notifications


class IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify scheduling paths without lowering this test runner's disk priority.
        cls.priority_patches = [patch('indexer.' + name) for name in
                                ('lower_thread_priority', 'raise_thread_priority', 'fast_priority')]
        for item in cls.priority_patches:
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.priority_patches):
            item.stop()

    def setUp(self):
        self.base = Path(__file__).resolve().parent / ("test-work-" + uuid.uuid4().hex)
        self.base.mkdir()
        self.own = patch('storage.DATA_DIR', str(self.base / 'app-data'))
        self.own.start()
        # These tests check change detection row by row; hourly merging is
        # covered separately in test_disk_budget.py.
        self.merge = patch('storage.LOG_MERGE_SECONDS', 0)
        self.merge.start()
        self.root = self.base / 'watched'
        self.root.mkdir()
        self.db = str(self.base / 'test.db')
        storage.init(self.db)
        self.con = storage.connect(self.db)
        self.engine = Engine(self.db)

    def tearDown(self):
        self.con.close()
        self.merge.stop()
        self.own.stop()
        assert self.base.parent == Path(__file__).resolve().parent and self.base.name.startswith('test-work-')
        shutil.rmtree(self.base)

    def scan(self):
        Indexer(self.db, str(self.root)).run()

    def apply(self, path):
        rows = []
        self.engine._apply_path(self.con, str(path), 1, rows)
        self.engine._flush(self.con, rows)

    def actions(self):
        return [r[0] for r in self.con.execute('SELECT action FROM file_log ORDER BY id')]

    def test_baseline_and_offline_same_size_edit(self):
        f = self.root / 'a.txt'
        f.write_text('abc')
        self.scan()
        self.assertEqual(self.actions(), [])
        stamp = f.stat().st_mtime_ns
        f.write_text('xyz')
        os.utime(f, ns=(stamp + 1000000000, stamp + 1000000000))
        self.scan()
        self.assertEqual(self.actions(), ['changed'])
        self.assertEqual(self.con.execute('SELECT delta FROM file_log').fetchone()[0], 0)

    def test_live_edit_duplicate_notifications_rename_delete(self):
        f = self.root / 'a.txt'
        f.write_text('abc')
        self.scan()
        stamp = f.stat().st_mtime_ns
        f.write_text('xyz')
        os.utime(f, ns=(stamp + 1000000000, stamp + 1000000000))
        self.apply(f)
        self.apply(f)
        other = f.with_name('b.txt')
        f.rename(other)
        self.apply(f)
        self.apply(other)
        other.unlink()
        self.apply(other)
        self.assertEqual(self.actions(), ['changed', 'removed', 'added', 'removed'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)

    def test_unreadable_subtree_preserved(self):
        sub = self.root / 'locked'
        sub.mkdir()
        (sub / 'keep.txt').write_text('keep')
        self.scan()
        original = list_folder
        with patch('indexer.list_folder', side_effect=lambda p: (None, []) if p == str(sub) else original(p)):
            self.scan()
        self.assertEqual(self.actions(), [])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM files').fetchone()[0], 1)

    def test_access_denied_live_is_not_delete(self):
        f = self.root / 'keep.txt'
        f.write_text('keep')
        self.scan()
        with patch('engine.os.stat', side_effect=PermissionError):
            self.apply(f)
        self.assertEqual(self.actions(), [])

    def test_offline_deleted_subtree(self):
        sub = self.root / 'gone'
        sub.mkdir()
        f = sub / 'old.txt'
        f.write_text('old')
        self.scan()
        f.unlink()
        sub.rmdir()
        self.scan()
        self.assertEqual(self.actions(), ['removed'])

    def test_pause_replays_once(self):
        root = str(self.root)
        self.scan()
        self.engine._set(root, state='ready')
        self.engine.paused = True
        f = self.root / 'new.txt'
        f.write_text('new')
        self.engine._events(self.con, root, [('added', str(f))], [])
        self.assertEqual(self.actions(), [])
        self.engine._pause(self.con, False, [])
        self.assertEqual(self.actions(), ['added'])

    def test_live_totals_follow_changes(self):
        root = os.path.splitdrive(str(self.root))[0] + os.sep
        self.con.execute('INSERT INTO drives(root, files, bytes) VALUES (?, 0, 0)', (root,))
        f = self.root / 'new.txt'
        f.write_text('abc')
        self.apply(f)
        self.assertEqual(self.con.execute('SELECT files, bytes FROM drives').fetchone(), (1, 3))
        f.unlink()
        self.apply(f)
        self.assertEqual(self.con.execute('SELECT files, bytes FROM drives').fetchone(), (0, 0))

    def test_migrated_mtime_does_not_log_false_edits(self):
        f = self.root / 'a.txt'
        f.write_text('abc')
        self.scan()
        self.con.execute('UPDATE files SET mtime_ns = 0')
        self.con.commit()
        self.scan()
        self.assertEqual(self.actions(), [])
        self.assertGreater(self.con.execute('SELECT mtime_ns FROM files').fetchone()[0], 0)

    def test_old_schema_migration(self):
        old = str(self.base / 'old.db')
        con = sqlite3.connect(old)
        con.execute('CREATE TABLE files(folder_id INTEGER, name TEXT, size INTEGER)')
        con.execute("INSERT INTO files VALUES (1, 'keep', 42)")
        con.commit()
        con.close()
        storage.init(old)
        con = storage.connect(old)
        self.assertEqual(con.execute('SELECT size, mtime_ns FROM files').fetchone(), (42, 0))
        con.close()

    def test_settings_invalid_types_and_atomic_save(self):
        with patch('storage.DATA_DIR', str(self.base)):
            for value in ([], {'internal': None}, {'internal': {'warm': 90, 'hot': 20}}):
                settings.save(value)
                self.assertEqual(settings.load()['internal'], settings.DEFAULTS['internal'])
            settings.save({'tray_drives': ['e:', 'E:', None, '../bad']})
            self.assertEqual(settings.load()['tray_drives'], ['E:'])

    def test_real_windows_notifications_and_stop(self):
        events = []
        watcher = DriveWatcher(str(self.root) + os.sep,
                               lambda root, batch: events.extend(batch),
                               lambda root: None, lambda root, err: events.append(('error', err)))
        watcher.start()
        try:
            time.sleep(.2)
            f = self.root / 'notification.txt'
            f.write_text('hello')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not events:
                time.sleep(.05)
            self.assertTrue(any(p == str(f) for _, p in events), events)
        finally:
            watcher.stop()
            watcher.join(5)
        self.assertFalse(watcher.is_alive())

    def test_queue_is_bounded_and_shutdown_does_not_block(self):
        for _ in range(300):
            self.engine.on_events('C:\\', [('changed', 'C:\\test')])
        self.assertEqual(self.engine.inbox.qsize(), 64)
        self.assertIn('C:\\', self.engine.dropped_roots)
        self.engine.stop()
        self.assertTrue(self.engine.stop_flag)

    def test_scan_can_stop_while_paused(self):
        import threading
        scan = Indexer(self.db, str(self.root))
        scan.pause_check = lambda: True
        thread = threading.Thread(target=scan.run)
        thread.start()
        time.sleep(.1)
        scan.stop_event.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_stale_scan_status_cannot_revive_offline_drive(self):
        root = str(self.root)
        old = Indexer(self.db, root)
        self.engine._set(root, state='offline')
        self.engine._handle(self.con, ('index_status', root, (old, 'ready', '')), [])
        self.assertEqual(self.engine.snapshot()[root]['state'], 'offline')

    def test_failed_watcher_is_retried_without_triggering_scan(self):
        from watcher import DriveManager
        from unittest.mock import Mock
        manager = DriveManager(lambda *a: None, Mock(), Mock(), poll_seconds=0)
        failed = Mock()
        failed.is_alive.return_value = False
        def start(root):
            manager.watchers[root] = failed
            if manager._start.call_count >= 2:
                manager.stop()
        with patch('watcher.list_drives', return_value=['C:\\']), patch.object(manager, '_start', side_effect=start):
            manager.run()
            self.assertEqual(manager._start.call_count, 2)
            manager.on_overflow.assert_not_called()

    def test_notification_parser(self):
        name = 'hello.txt'.encode('utf-16-le')
        self.assertEqual(parse_notifications(struct.pack('<III', 0, 1, len(name)) + name, 'C:\\'),
                         [('added', 'C:\\hello.txt')])
        with self.assertRaises(ValueError):
            parse_notifications(b'bad', 'C:\\')

    def test_extreme_timestamps_round_trip_without_precision_loss(self):
        fid = storage.folder_id(self.con, str(self.root))
        values = [-(1 << 63), (1 << 63)-1, -(1 << 63)-1,
                  -11644443511509811200, 1 << 63, 10**25]
        for i, value in enumerate(values):
            key = storage.timestamp_key(value)
            self.con.execute('INSERT INTO files(folder_id,name,size,mtime_ns) VALUES(?,?,1,?)',
                             (fid,str(i),key))
            stored, kind = self.con.execute('SELECT mtime_ns,typeof(mtime_ns) FROM files WHERE folder_id=? AND name=?',
                                            (fid,str(i))).fetchone()
            self.assertEqual(stored,key)
            self.assertEqual(kind,'integer' if -(1 << 63) <= value < 1 << 63 else 'text')

    def test_live_legacy_timestamp_does_not_abort_or_repeat_events(self):
        from types import SimpleNamespace
        path = str(self.root / 'old.txt')
        st = SimpleNamespace(st_size=3,st_mtime_ns=-11644443511509811200)
        with patch('engine.os.path.islink',return_value=False), \
             patch('engine.os.path.isjunction',return_value=False), \
             patch('engine.os.path.isdir',return_value=False), \
             patch('engine.os.stat',return_value=st):
            self.apply(path)
            self.apply(path)
            self.assertEqual(self.actions(),['added'])
            st.st_mtime_ns += 100
            self.apply(path)
        self.assertEqual(self.actions(),['added','changed'])
        self.assertEqual(self.con.execute('SELECT delta FROM file_log ORDER BY id DESC LIMIT 1').fetchone()[0],0)

    def test_pause_control_does_not_block_on_full_event_queue(self):
        import threading
        for _ in range(64):
            self.engine.on_events('C:\\',[('changed','C:\\test')])
        caller = threading.Thread(target=self.engine.set_paused,args=(True,))
        caller.start()
        caller.join(.5)
        blocked = caller.is_alive()
        if blocked:
            self.engine.inbox.get_nowait()
            caller.join(1)
        self.assertFalse(blocked,'Pause waited for the filesystem-event queue')
        command = self.engine.controls.get_nowait()
        self.engine._handle(self.con,command,[])
        self.assertTrue(self.engine.paused)

    def test_retry_of_failed_startup_keeps_fast_mode(self):
        root = str(self.root)
        self.engine._set(root,state='error',scan_mode='startup')
        self.engine.retry_drive(root)
        with patch.object(self.engine,'_start_index') as start:
            self.engine._handle(self.con,self.engine.controls.get_nowait(),[])
            start.assert_called_once_with(root,fast_startup=True,reason='Manual retry')

    def test_idle_engine_stop_wakes_immediately(self):
        with patch('engine.lower_thread_priority'):
            self.engine.start()
            self.engine.stop()
            self.engine.join(1)
        self.assertFalse(self.engine.is_alive())


if __name__ == '__main__':
    unittest.main()
