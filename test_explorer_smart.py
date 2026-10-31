import os
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import storage
import smart
from indexer import Indexer, worker_count


class ExplorerAndSensorTests(unittest.TestCase):
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
        self.base = Path(__file__).resolve().parent / ('browser-test-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.db = str(self.base / 'test.db')
        storage.init(self.db)
        self.con = storage.connect(self.db)

    def tearDown(self):
        self.con.close()
        assert self.base.parent == Path(__file__).resolve().parent
        assert self.base.name.startswith('browser-test-')
        shutil.rmtree(self.base)

    def folder(self, path, entries):
        fid = storage.folder_id(self.con, path)
        self.con.executemany('INSERT INTO files(folder_id,name,size) VALUES(?,?,?)',
                             [(fid, name, size) for name, size in entries])
        self.con.execute('UPDATE folders SET file_count=?,byte_count=? WHERE id=?',
                         (len(entries), sum(size for _, size in entries), fid))
        return fid

    def seed(self):
        self.folder('C:\\', [('root.txt', 3)])
        self.folder('C:\\Alpha', [('a', 10), ('b', 20)])
        self.folder('C:\\Alpha\\Deep', [('c', 40)])
        self.folder('C:\\Alphabet', [('separate', 2)])
        self.con.execute("INSERT INTO drives(root,bytes,files) VALUES('C:\\',75,5)")
        self.con.commit()

    def test_root_uses_drive_totals_without_reading_files(self):
        self.seed()
        queries = []
        self.con.set_trace_callback(queries.append)
        rows, more = storage.explorer_page(self.con)
        self.assertEqual(rows, [('drive', 'C:\\', 'C:\\', 75, 5)])
        self.assertFalse(more)
        self.assertFalse(any('FROM files' in q for q in queries))

    def test_nested_totals_and_direct_file_listing(self):
        self.seed()
        rows, more = storage.explorer_page(self.con, 'C:\\')
        self.assertEqual(rows[0], ('folder', 'C:\\Alpha', 'Alpha', 70, 3))
        self.assertIn(('file', 'C:\\root.txt', 'root.txt', 3, None), rows)
        rows, _ = storage.explorer_page(self.con, 'C:\\Alpha')
        self.assertEqual(rows[0], ('folder', 'C:\\Alpha\\Deep', 'Deep', 40, 1))
        self.assertEqual(sum(r[3] for r in rows), 70)
        self.assertNotIn('separate', [r[2] for r in rows])

    def test_migrated_null_stats_fall_back_to_index(self):
        self.seed()
        self.con.execute('UPDATE folders SET file_count=NULL,byte_count=NULL')
        self.assertEqual(storage.explorer_page(self.con, 'C:\\')[0][0][3:], (70, 3))

    def test_pagination_and_literal_search(self):
        self.folder('C:\\', [(f'{i}.txt', i) for i in range(1100)] + [('100%_real.txt', 2)])
        first, more = storage.explorer_page(self.con, 'C:\\', limit=500)
        second, more2 = storage.explorer_page(self.con, 'C:\\', offset=500, limit=500)
        self.assertEqual(len(first), 500)
        self.assertTrue(more and more2)
        self.assertFalse({r[1] for r in first} & {r[1] for r in second})
        result, _ = storage.explorer_page(self.con, 'C:\\', search='%_')
        self.assertEqual([r[2] for r in result], ['100%_real.txt'])

    def test_zero_and_invalid_temperatures_are_missing(self):
        for value in (None, 0, -1, 65535, '38', True):
            self.assertIsNone(smart.valid_temperature(value))
        self.assertEqual(smart.valid_temperature(38), 38)

    def test_ata_uses_raw_temperature_not_normalized_score(self):
        raw = bytearray(512)
        raw[2] = 194
        raw[5] = 100  # normalized value, not Celsius
        raw[7] = 37
        self.assertEqual(smart.ata_temperature(raw), 37)
        raw[7] = 0
        self.assertIsNone(smart.ata_temperature(raw))
        self.assertIsNone(smart.ata_temperature([194]))

    def test_initial_parallel_then_background_mode(self):
        root = str(self.base / 'watched')
        os.mkdir(root)
        modes = []
        with patch('indexer.storage.is_own_path', return_value=False), \
             patch.object(Indexer, '_walk_parallel', autospec=True, return_value=(0, 0)) as fast, \
             patch.object(Indexer, '_walk', autospec=True, return_value=(0, 0)) as quiet:
            Indexer(self.db, root).run()
            self.assertEqual(fast.call_count, 1)
            self.assertEqual(quiet.call_count, 0)
            Indexer(self.db, root).run()
            self.assertEqual(fast.call_count, 1)
            self.assertEqual(quiet.call_count, 1)
        self.assertGreater(worker_count(), 1)

    def test_fast_scan_really_enumerates_in_parallel(self):
        root = str(self.base / 'watched')
        active = peak = 0
        lock = threading.Lock()
        def listing(path):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.02)
            children = [root + '\\' + str(i) for i in range(12)] if path == root else []
            with lock:
                active -= 1
            return {}, children
        with patch('indexer.list_folder', side_effect=listing):
            Indexer(self.db, root).run()
        self.assertGreater(peak, 1)

    def test_fast_startup_uses_parallel_path_with_saved_baseline(self):
        root = str(self.base / 'watched')
        self.con.execute('INSERT INTO drives(root,baselined) VALUES(?,1)', (root,))
        self.con.commit()
        with patch.object(Indexer, '_walk_parallel', autospec=True, return_value=(0,0)) as fast, \
             patch.object(Indexer, '_walk', autospec=True, return_value=(0,0)) as quiet:
            Indexer(self.db, root, fast_startup=True).run()
            self.assertEqual(fast.call_count, 1)
            self.assertTrue(fast.call_args.kwargs['baselined'])
            quiet.assert_not_called()
            Indexer(self.db, root).run()
            self.assertEqual(quiet.call_count, 1)

    def test_engine_first_online_scan_is_fast_and_later_reconnect_is_quiet(self):
        from engine import Engine
        root = 'C:\\'
        self.con.execute('INSERT INTO drives(root,baselined) VALUES(?,1)', (root,))
        self.con.commit()
        engine = Engine(self.db)
        with patch.object(engine, '_start_index') as start:
            engine._drive(root, 'online', '')
            start.assert_called_with(root, fast_startup=True, reason='Application startup')
            engine._drive(root, 'offline', '')
            engine._drive(root, 'online', '')
            start.assert_called_with(root, fast_startup=False, reason='Drive or watcher reconnected')

    def test_fast_startup_preserves_catchup_change_logging(self):
        root = self.base / 'watched'
        root.mkdir()
        f = root / 'test.txt'
        f.write_text('abc', encoding='utf-8')
        with patch('indexer.storage.is_own_path', return_value=False):
            Indexer(self.db, str(root)).run()
            f.write_text('updated', encoding='utf-8')
            Indexer(self.db, str(root), fast_startup=True).run()
        self.assertEqual(self.con.execute('SELECT action,delta FROM file_log').fetchall(), [('changed',4)])

    def test_file_entries_do_not_get_junction_or_own_path_checks(self):
        from unittest.mock import Mock
        from indexer import list_folder
        entry = Mock()
        entry.name = 'file.txt'
        entry.path = 'C:\\sample\\file.txt'
        entry.is_dir.return_value = False
        entry.is_file.return_value = True
        entry.stat.return_value.st_size = 3
        entry.stat.return_value.st_mtime_ns = 123
        context = Mock()
        context.__enter__ = Mock(return_value=iter([entry]))
        context.__exit__ = Mock(return_value=False)
        with patch('indexer.os.scandir', return_value=context), \
             patch('indexer.storage.is_own_path', return_value=False) as own:
            self.assertEqual(list_folder('C:\\sample')[0], {'file.txt':(3,123)})
        entry.is_junction.assert_not_called()
        self.assertEqual(own.call_count, 1)


if __name__ == '__main__':
    unittest.main()
