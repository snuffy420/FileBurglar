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
from PyQt6.QtCore import Qt


class ReportLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unconfigured_tables_can_resize_without_event_filter_errors(self):
        with patch('sys.excepthook') as errors:
            table=ui.make_table(['Drive','Status'])
            table.resize(700,300);table.show()
            for _ in range(5):self.app.processEvents()
            table.resize(900,400)
            for _ in range(5):self.app.processEvents()
            errors.assert_not_called()
            table.hide();table.deleteLater()

    def test_default_reports_fit_after_resize_with_vertical_scrollbar(self):
        for headers,widths,path_column in (
            (['Folder','Changes','Written','Freed','Last change'],[780,110,130,130,180],0),
            (['Time','Drive','Path','Change','Size','Action'],[170,65,760,110,110,100],2)):
            table=ui.make_table(headers)
            table.configure_fit(widths,path_column)
            table.setRowCount(100)
            for width in (1000,1600,850):
                table.resize(width,400);table.show()
                for _ in range(5):self.app.processEvents()
                self.assertEqual(table.horizontalScrollBar().maximum(),0)
                self.assertGreater(table.verticalScrollBar().maximum(),0)
                self.assertLessEqual(sum(table.columnWidth(c) for c in range(len(headers))),table.viewport().width())
            table.setColumnWidth(path_column,1800)
            table.resize(1000,400)
            for _ in range(5):self.app.processEvents()
            self.assertEqual(table.columnWidth(path_column),1800)
            self.assertGreater(table.horizontalScrollBar().maximum(),0)
            table.hide();table.deleteLater()

    def test_paths_keep_full_text_and_tooltip_with_trailing_elision(self):
        table=ui.make_table(['Path'])
        path='C:\\Users\\example\\a very long folder\\filename.txt'
        item=ui.cell(path);table.setRowCount(1);table.setItem(0,0,item)
        self.assertEqual(item.text(),path)
        self.assertEqual(item.toolTip(),path)
        self.assertEqual(table.textElideMode(),Qt.TextElideMode.ElideRight)
        self.assertFalse(table.wordWrap())
        table.deleteLater()

    def test_size_card_separates_connected_index_from_all_saved_drives(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        con=sqlite3.connect(':memory:')
        con.execute('CREATE TABLE folder_daily(day TEXT,events INTEGER)')
        window=SimpleNamespace(con=con,engine=SimpleNamespace(paused=False),
                               **{name:Mock() for name in ('card_drives','card_files','card_size','card_today','card_held','scan_status')})
        snap={
            'C:\\':dict(state='ready',files=10,bytes=3*1024**4,held=0),
            'D:\\':dict(state='indexing',files=5,bytes=2*1024**4,held=0),
            'I:\\':dict(state='offline',files=9,bytes=4*1024**4,held=0),
        }
        ui.MainWindow._refresh_cards(window,snap)
        window.card_size.set.assert_called_with('5.0 TB / 9.0 TB')
        window.card_files.set.assert_called_with('15')
        snap['I:\\']['state']='ready'
        ui.MainWindow._refresh_cards(window,snap)
        window.card_size.set.assert_called_with('9.0 TB / 9.0 TB')
        for info in snap.values():info['state']='offline'
        ui.MainWindow._refresh_cards(window,snap)
        window.card_size.set.assert_called_with('0 B / 9.0 TB')
        con.close()

    def test_small_offline_drive_is_not_hidden_by_tb_rounding(self):
        saved=16910402759833
        connected=saved-9714359307
        self.assertEqual(ui.human_bytes(connected),ui.human_bytes(saved))
        left,right=ui.indexed_size_text(connected,saved).split(' / ')
        self.assertNotEqual(left,right)
        self.assertEqual(ui.indexed_size_text(saved,saved),'15.4 TB / 15.4 TB')


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
