"""Disk-budget regressions: merged activity, bounded history and compact storage.

Never opens the application's real database.
"""
import os
import shutil
import sqlite3
import sys
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import storage
import volumes
from engine import Engine

HOUR = 3600 * 500_000  # a fixed, hour-aligned timestamp


class DiskBudgetTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent / ('budget-test-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.own = patch('storage.DATA_DIR', str(self.base / 'app-data'))
        self.own.start()
        self.db = str(self.base / 'test.db')
        storage.init(self.db)
        self.con = storage.connect(self.db)

    def tearDown(self):
        self.con.close()
        self.own.stop()
        assert self.base.parent == Path(__file__).resolve().parent and self.base.name.startswith('budget-test-')
        shutil.rmtree(self.base)

    def rows(self):
        return self.con.execute('SELECT path,action,delta,size FROM file_log ORDER BY id').fetchall()

    def test_repeated_rewrites_merge_into_one_row_per_hour(self):
        for i in range(50):
            storage.record(self.con, [(HOUR + i, 'K:\\bot\\data.json', 'changed', 1, 101 + i)])
        storage.record(self.con, [(HOUR + 60, 'K:\\bot\\other.json', 'changed', 0, 7)])
        self.assertEqual(self.rows(), [('K:\\bot\\data.json', 'changed', 50, 150),
                                       ('K:\\bot\\other.json', 'changed', 0, 7)])
        storage.record(self.con, [(HOUR + 3600, 'K:\\bot\\data.json', 'changed', 2, 152)])
        self.assertEqual(len(self.rows()), 3)

    def test_same_batch_duplicates_merge(self):
        storage.record(self.con, [(HOUR, 'K:\\a.log', 'added', 10, 10), (HOUR + 1, 'K:\\a.log', 'changed', 5, 15)])
        self.assertEqual(self.rows(), [('K:\\a.log', 'added', 15, 15)])

    def test_file_created_and_deleted_within_the_hour_leaves_no_row(self):
        storage.record(self.con, [(HOUR, 'C:\\Temp\\x.tmp', 'added', 10, 10)])
        storage.record(self.con, [(HOUR + 5, 'C:\\Temp\\x.tmp', 'changed', 5, 15)])
        storage.record(self.con, [(HOUR + 9, 'C:\\Temp\\x.tmp', 'removed', -15, 0)])
        self.assertEqual(self.rows(), [])

    def test_replaced_file_is_reported_as_one_change(self):
        storage.record(self.con, [(HOUR, 'K:\\r.bin', 'removed', -10, 0)])
        storage.record(self.con, [(HOUR + 1, 'K:\\r.bin', 'added', 12, 12)])
        self.assertEqual(self.rows(), [('K:\\r.bin', 'changed', 2, 12)])

    def test_pruned_or_rolled_back_rows_are_not_merged_into(self):
        storage.record(self.con, [(HOUR, 'K:\\p.txt', 'changed', 1, 1)])
        self.con.execute('DELETE FROM file_log')
        storage.record(self.con, [(HOUR + 1, 'K:\\p.txt', 'changed', 2, 3)])
        self.assertEqual(self.rows(), [('K:\\p.txt', 'changed', 2, 3)])
        self.con.commit()
        storage.record(self.con, [(HOUR + 2, 'K:\\q.txt', 'added', 4, 4)])
        self.con.rollback()
        storage.record(self.con, [(HOUR + 3, 'K:\\q.txt', 'changed', 1, 5)])
        self.assertEqual(self.rows()[-1], ('K:\\q.txt', 'changed', 1, 5))

    def test_merging_can_be_disabled(self):
        with patch('storage.LOG_MERGE_SECONDS', 0):
            for i in range(3):
                storage.record(self.con, [(HOUR + i, 'K:\\d.txt', 'changed', 1, i)])
        self.assertEqual(len(self.rows()), 3)

    def test_daily_folder_totals_still_count_every_change(self):
        for i in range(4):
            storage.record(self.con, [(time.time(), 'K:\\daily\\f.txt', 'changed', 3, 3 * i)])
        added = self.con.execute('SELECT SUM(added) FROM folder_daily').fetchone()[0]
        self.assertEqual(added, 12)

    def test_prune_caps_rows_in_bounded_chunks(self):
        now = time.time()
        storage.record(self.con, [(now, f'K:\\cap\\{i}.txt', 'added', 1, 1) for i in range(250)])
        self.assertTrue(storage.prune_log(self.con, 86400, max_rows=100, chunk=60))
        self.assertTrue(storage.prune_log(self.con, 86400, max_rows=100, chunk=60))
        self.assertFalse(storage.prune_log(self.con, 86400, max_rows=100, chunk=60))
        kept = [r[0] for r in self.con.execute('SELECT path FROM file_log ORDER BY id')]
        self.assertEqual(len(kept), 100)
        self.assertEqual(kept[0], 'K:\\cap\\150.txt')

    def test_prune_removes_expired_history(self):
        storage.record(self.con, [(time.time() - 40 * 86400, 'K:\\old.txt', 'added', 1, 1),
                                  (time.time(), 'K:\\new.txt', 'added', 1, 1)])
        storage.prune_log(self.con, 30 * 86400)
        self.assertEqual([r[0] for r in self.rows()], ['K:\\new.txt'])

    def test_new_database_gives_freed_space_back(self):
        self.assertEqual(self.con.execute('PRAGMA auto_vacuum').fetchone()[0], 2)
        with patch('storage.LOG_MERGE_SECONDS', 0):
            storage.record(self.con, [(HOUR, 'K:\\big\\' + 'x' * 200 + str(i), 'added', 1, 1) for i in range(20000)])
        self.con.commit()
        self.con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        grown = os.path.getsize(self.db)
        self.con.execute('DELETE FROM file_log')
        self.con.commit()
        storage.release_free_space(self.con, 1_000_000)
        self.con.commit()
        self.con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
        self.assertLess(os.path.getsize(self.db), grown / 4)

    def test_activity_log_has_no_per_row_volume_index(self):
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertNotIn('ix_log_volume', names)
        self.assertIn('ix_file_log_ts', names)

    def test_completed_upgrade_backups_are_removed_after_retention(self):
        old = self.base / 'pre-volume-migration-20200101-000000-aaaaaa.db'
        recent = self.base / 'pre-volume-migration-20200102-000000-bbbbbb.db'
        for path in (old, recent):
            path.write_bytes(b'backup')
        stale = time.time() - 8 * 86400
        os.utime(old, (stale, stale))
        storage.init(self.db)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_interrupted_upgrade_reuses_the_existing_backup(self):
        legacy = self.base / 'legacy' / 'fileburglar.db'
        legacy.parent.mkdir()
        con = sqlite3.connect(legacy)
        con.execute('CREATE TABLE drives(root TEXT PRIMARY KEY)')
        con.commit()
        con.close()
        first = volumes.backup_before_migration(str(legacy))
        second = volumes.backup_before_migration(str(legacy))
        self.assertEqual(first, second)
        self.assertEqual(len(list(legacy.parent.glob('pre-volume-migration-*.db'))), 1)


class EngineBudgetTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(__file__).resolve().parent / ('budget-test-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.own = patch('storage.DATA_DIR', str(self.base / 'app-data'))
        self.own.start()
        self.db = str(self.base / 'test.db')
        storage.init(self.db)
        self.con = storage.connect(self.db)
        self.engine = Engine(self.db)

    def tearDown(self):
        self.con.close()
        self.own.stop()
        assert self.base.parent == Path(__file__).resolve().parent and self.base.name.startswith('budget-test-')
        shutil.rmtree(self.base)

    def test_notification_burst_checks_each_path_once(self):
        handled = []
        root = 'Q:\\'
        for _ in range(3):
            self.engine.inbox.put_nowait(('events', root, [('changed', 'Q:\\hot.json'), ('changed', 'Q:\\hot.json')]))
        self.engine.inbox.put_nowait(('events', root, [('added', 'Q:\\new.txt')]))
        with patch.object(self.engine, '_handle', side_effect=lambda con, item, rows: handled.append(item)):
            self.engine._drain_inbox(self.con, [])
        self.assertEqual(len(handled), 1)
        kind, drive, events = handled[0]
        self.assertEqual((kind, drive), ('events', root))
        self.assertEqual(sorted(path for _kind, path in events), ['Q:\\hot.json', 'Q:\\new.txt'])

    def test_tokens_stay_separate_when_merging(self):
        handled = []
        self.engine.inbox.put_nowait(('events', 'Q:\\', ('old', [('changed', 'Q:\\a')])))
        self.engine.inbox.put_nowait(('events', 'Q:\\', ('new', [('changed', 'Q:\\a')])))
        with patch.object(self.engine, '_handle', side_effect=lambda con, item, rows: handled.append(item)):
            self.engine._drain_inbox(self.con, [])
        self.assertEqual(sorted(item[2][0] for item in handled), ['new', 'old'])

    def test_overflow_during_cooldown_queues_one_deferred_rescan(self):
        root = 'Q:\\'
        self.engine._set(root, state='ready')
        self.engine.recovery_after[root] = time.monotonic() + 60
        with patch.object(self.engine, '_start_index') as start:
            self.engine._overflow(root)
            self.engine._overflow(root)
            start.assert_not_called()
        self.assertIn(root, self.engine.rescan_pending)
        self.engine.recovery_after[root] = 0
        with patch.object(self.engine, '_can_update_folder', return_value=True), \
                patch.object(self.engine, '_start_index') as start:
            self.engine._start_deferred(self.con, [])
            start.assert_called_once()

    def test_first_overflow_still_starts_recovery(self):
        root = 'Q:\\'
        self.engine._set(root, state='ready')
        with patch.object(self.engine, '_start_index') as start:
            self.engine._overflow(root)
            start.assert_called_once()

    def test_completed_scan_starts_the_recovery_cooldown(self):
        root = 'Q:\\'
        self.engine.indexers[root] = object()
        with patch.object(self.engine, '_replay_held'):
            self.engine._status(self.con, root, 'ready', '')
        self.assertTrue(self.engine._recovery_waiting(root))

    def test_prune_backlog_continues_soon(self):
        with patch('storage.prune_log', return_value=True):
            self.engine._maybe_prune(self.con)
        import engine
        self.assertLessEqual(time.time() - self.engine.last_prune, engine.PRUNE_EVERY)
        self.assertGreater(time.time() - self.engine.last_prune, engine.PRUNE_EVERY - engine.PRUNE_BACKLOG_EVERY - 1)


class WatcherRegistrationTests(unittest.TestCase):
    def test_refused_removal_registration_keeps_watching_and_logs_once(self):
        import watcher
        fake = types.ModuleType('devices')

        class Refused:
            def __init__(self, *args):
                raise OSError('Device-removal registration failed (31)')
        fake.Registration = Refused
        manager = watcher.DriveManager(None, None, None, engine=MagicMock(db_path='unused.db'))
        with patch.dict(sys.modules, {'devices': fake}), patch('diagnostics.report') as report:
            manager._register('I:\\', 1)
            manager._register('I:\\', 1)
        report.assert_called_once()
        self.assertEqual(manager.registrations, [])


if __name__ == '__main__':
    unittest.main()
