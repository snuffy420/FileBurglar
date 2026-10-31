"""Offscreen integration checks for the feature controls and worker results."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ['FILEBURGLAR_TESTING']='1'
import copy
import json
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication,QMessageBox,QInputDialog,QLabel,QProgressDialog,QFileDialog
import feature_ui
import settings
import smart
import storage
import ui
from engine import Engine


class FeatureUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.base=Path(__file__).resolve().parent/('feature-ui-test-'+uuid.uuid4().hex);self.base.mkdir()
        self.defaults=copy.deepcopy(settings.DEFAULTS);self.defaults['auto_updates']=False
        self.patches=[patch.object(storage,'DATA_DIR',str(self.base)),patch('settings.load',side_effect=lambda:copy.deepcopy(self.defaults)),patch.object(ui.MainWindow,'_poll_smart',lambda self:None)]
        for p in self.patches:p.start()
        self.db=str(self.base/'test.db');storage.init(self.db)
        self.engine=Engine(self.db);self.window=ui.MainWindow(self.engine,self.db)
        self.features=self.window.features

    def tearDown(self):
        self.features.stop();self.window.timer.stop();self.window.smart_timer.stop();self.window.hide()
        deadline=time.monotonic()+5
        while self.features.busy and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
            # stop() suppresses completion signals. No test leaves an active
            # filesystem task; only growth is used and closes its own reader.
            if self.features.busy=={'growth'}:break
        self.window.con.close();self.window.deleteLater();self.app.processEvents()
        smart.unblock('shutdown');smart.unblock('options')
        for p in reversed(self.patches):p.stop()
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('feature-ui-test-')
        shutil.rmtree(self.base)

    def test_options_has_independent_health_alerts_and_two_reset_periods(self):
        dialog=feature_ui.Preferences(self.window,self.window._limits)
        try:
            dialog.checks['health_enabled'].setChecked(False)
            self.assertFalse(dialog.checks['temperature_alerts'].isEnabled())
            self.assertTrue(all(not spin.isEnabled() for spin in dialog.spins.values()))
            dialog.reset.setCurrentIndex(1);dialog.save()
            self.assertFalse(dialog.values['health_enabled'])
            self.assertEqual(dialog.values['alert_reset_hours'],2)
        finally:dialog.deleteLater()

    def test_confirmation_and_input_dialogs_have_readable_dark_backgrounds(self):
        file_dialog=QFileDialog(self.window)
        file_dialog.setOption(QFileDialog.Option.DontUseNativeDialog,True)
        file_dialog.setDirectory(str(self.base))
        dialogs=[QInputDialog(self.window),QProgressDialog('Exporting report…','Cancel',0,100,self.window),file_dialog]
        for icon in (QMessageBox.Icon.Question,QMessageBox.Icon.Warning,QMessageBox.Icon.Critical,QMessageBox.Icon.Information):
            box=QMessageBox(self.window);box.setIcon(icon);box.setDetailedText('Example database error details');dialogs.append(box)
        for dialog in dialogs:
            try:
                if isinstance(dialog,QMessageBox):
                    dialog.setText('Release File Burglar’s access to G:, I:?')
                    dialog.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
                elif isinstance(dialog,QInputDialog):dialog.setLabelText('Enter a folder name:')
                dialog.show();self.app.processEvents()
                image=dialog.grab().toImage()
                self.assertLess(image.pixelColor(5,5).lightness(),60)
                labels=[label for label in dialog.findChildren(QLabel) if label.text()]
                self.assertTrue(labels)
                for label in labels:self.assertGreater(label.palette().color(label.foregroundRole()).lightness(),180)
            finally:dialog.close();dialog.deleteLater()

    def test_master_health_off_discards_inflight_readings_and_hides_stale_tiles(self):
        self.window._limits['health_enabled']=False
        self.window._store_smart((0,{'Z:':dict(Temperature=99,Health='Healthy')}))
        self.assertEqual(self.window._disks,{})
        self.engine._set('Z:\\',state='ready')
        self.window._refresh_strip(self.engine.snapshot())
        self.assertEqual(self.window.tiles['Z:\\'].temp.text(),'Health monitoring off')

    def test_stale_health_generation_cannot_replace_current_map(self):
        self.window._health_generation=3;self.window._disks={'Z:':dict(Temperature=30)}
        self.window._store_smart((2,{'Z:':dict(Temperature=90)}))
        self.assertEqual(self.window._disks['Z:']['Temperature'],30)

    def test_released_tile_never_queries_drive_capacity(self):
        self.engine._set('Z:\\',state='ready');self.engine.released_roots.add('Z:\\')
        with patch('ui.shutil.disk_usage',side_effect=AssertionError('drive must not be opened')):
            self.window._refresh_strip(self.engine.snapshot())
        self.assertEqual(self.window.tiles['Z:\\'].space.text(),'released for removal')

    def test_automatic_update_notice_does_not_open_a_modal_dialog(self):
        self.window._limits['auto_updates']=True
        self.features.update_manual=False;self.features.notify=Mock()
        release=dict(version='9.0.0',url='https://github.com/snuffy420/FileBurglar/releases/tag/v9.0.0')
        with patch('feature_ui.QMessageBox') as dialog:
            self.features.receive('update',(True,(release,'etag')))
        dialog.assert_not_called();self.features.notify.assert_called_once()
        self.assertEqual(self.features.available_release,release)
        self.features.receive('update',(True,(release,'etag')))
        self.features.notify.assert_called_once()

    def test_skipped_update_does_not_notify(self):
        self.window._limits['auto_updates']=True
        self.features.update_manual=False;self.features.update_state['skip']='9.0.0';self.features.notify=Mock()
        release=dict(version='9.0.0',url='https://github.com/snuffy420/FileBurglar/releases/tag/v9.0.0')
        self.features.receive('update',(True,(release,'etag')))
        self.features.notify.assert_not_called()

    def test_export_keeps_filters_and_sort_but_not_display_row_limit(self):
        self.window.log_search.setText('cache')
        self.window.log_table.horizontalHeader().setSortIndicator(3,Qt.SortOrder.AscendingOrder)
        sql,args,headers=self.features.export_query(2)
        self.assertIn('path LIKE ?',sql);self.assertIn('%cache%',args)
        self.assertTrue(sql.endswith('ORDER BY delta ASC'));self.assertNotIn('LIMIT',sql)
        self.window.con.execute(sql,args).fetchall()

    def test_growth_export_query_preserves_selected_period(self):
        self.features.period.setCurrentIndex(2)
        sql,args,headers=self.features.export_query(3)
        self.assertLess(args[0],time.time()-167*3600)
        self.assertNotIn('LIMIT',sql)
        import reports
        reports.prepare(self.window.con);self.window.con.execute(sql,args).fetchall()
        deadline=time.monotonic()+5
        while 'growth' in self.features.busy and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertNotIn('growth',self.features.busy)

    def test_manual_game_mode_is_saved_and_queues_engine_control(self):
        self.features.manual_work(True)
        saved=json.loads((self.base/'settings.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['game_mode'])
        commands=[]
        while not self.engine.controls.empty():commands.append(self.engine.controls.get_nowait())
        self.assertEqual(commands[-1],('work_mode',None,True))

    def test_unplugged_tile_disappears_without_removing_saved_information(self):
        self.engine._set('L:\\',state='ready',files=10,bytes=100)
        self.window._refresh_strip(self.engine.snapshot());self.assertIn('L:\\',self.window.tiles)
        self.engine._set('L:\\',state='offline')
        self.window._refresh_strip(self.engine.snapshot());self.assertNotIn('L:\\',self.window.tiles)
        self.assertEqual(self.engine.snapshot()['L:\\']['bytes'],100)

    def test_refresh_requests_discovery_without_starting_a_scan(self):
        manager=Mock();self.features.manager=manager
        with patch.object(self.engine,'_start_index') as scan:
            self.window._refresh_devices()
        manager.refresh.assert_called_once();scan.assert_not_called()

    def test_stale_explorer_action_is_rejected_after_letter_reassignment(self):
        explorer=ui.ExplorerWindow(self.window.con)
        try:
            explorer._volume_context={'L:\\':('original-volume',True)}
            with patch('devices.volume_identity',return_value='replacement-volume'),patch.object(explorer,'_fail') as fail:
                self.assertFalse(explorer._valid_paths(['L:\\file.txt']))
            fail.assert_called_once()
        finally:explorer.close();explorer.deleteLater()

    def test_tracking_exclusion_editor_uses_dark_theme(self):
        dialog=feature_ui.Preferences(self.window,self.window._limits)
        try:
            self.assertIn('QPlainTextEdit',self.window.styleSheet())
            self.assertIn('#191919',self.window.styleSheet())
            self.assertTrue(dialog.exclusions.placeholderText())
        finally:dialog.deleteLater()


if __name__=='__main__':unittest.main()
