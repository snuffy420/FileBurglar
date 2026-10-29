"""Offscreen tests for responsive, cancellable Explorer requests."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import uuid
import shutil
from pathlib import Path
import storage
import threading
import time
import unittest
from unittest.mock import patch
from PyQt6.QtWidgets import QApplication
import ui


class ExplorerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.base = Path(__file__).resolve().parent / ('gui-test-' + uuid.uuid4().hex)
        self.base.mkdir()
        self.db = str(self.base / 'test.db')
        storage.init(self.db)
        self.con = sqlite3.connect(self.db)
        self.window = ui.ExplorerWindow(self.con)
        self.window.show()

    def tearDown(self):
        self.window.close()
        self.con.close()
        self.window.deleteLater()
        self.app.processEvents()
        assert self.base.parent == Path(__file__).resolve().parent and self.base.name.startswith('gui-test-')
        shutil.rmtree(self.base)

    def pump_until(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_loading_runs_off_ui_thread_and_reopen_uses_cache(self):
        entered = threading.Event()
        release = threading.Event()
        threads = []
        def query(*args, **kwargs):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return [('drive', 'C:\\', 'C:\\', 20, 2)], False
        with patch('storage.explorer_page', side_effect=query) as load:
            try:
                self.window.load()
                self.pump_until(entered.is_set)
                self.assertTrue(self.window._busy)
                self.assertNotEqual(threads[0], threading.get_ident())
                self.assertEqual(self.window.table.rowCount(), 0)
            finally:
                release.set()
            self.pump_until(lambda: not self.window._busy)
            self.assertEqual(self.window.table.rowCount(), 1)
            self.window.load()
            self.assertEqual(load.call_count, 1)
            self.assertFalse(self.window._busy)

    def test_new_navigation_discards_older_result(self):
        entered = threading.Event()
        release = threading.Event()
        def query(con, path, *args, **kwargs):
            if path == 'C:\\':
                entered.set()
                release.wait(3)
                return [('file', 'C:\\old.txt', 'old.txt', 1, None)], False
            return [('drive', 'D:\\', 'D:\\', 5, 1)], False
        with patch('storage.explorer_page', side_effect=query):
            try:
                self.window._navigate('C:\\')
                self.pump_until(entered.is_set)
                self.window._navigate(None)
            finally:
                release.set()
            self.pump_until(lambda: not self.window._busy and self.window._pending is None)
            self.assertEqual(self.window.table.item(0, 0).text(), 'D:\\')


if __name__ == '__main__':
    unittest.main()
