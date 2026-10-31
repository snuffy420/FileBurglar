"""Regression checks for the pending feature bundle; never eject real devices."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ['FILEBURGLAR_TESTING']='1'
import copy
import ctypes
import json
import shutil
import sqlite3
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import devices
import notifications
import reports
import settings
import smart
import storage
import updates
from engine import Engine
from indexer import Indexer
from watcher import DriveManager,DriveWatcher


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(__file__).resolve().parent/('feature-test-'+uuid.uuid4().hex);self.base.mkdir()
        self.data=self.base/'Data';self.data.mkdir()
        self.patches=[patch.object(storage,'DATA_DIR',str(self.data)),patch.object(storage,'EXCLUSIONS',()),
                      patch.object(storage,'BASELINE_PATHS',()),patch('indexer.fast_priority'),
                      patch('indexer.raise_thread_priority'),patch('indexer.lower_thread_priority')]
        for p in self.patches:p.start()
        self.db=str(self.data/'test.db');storage.init(self.db)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('feature-test-')
        shutil.rmtree(self.base)

    def test_thermal_boundary_oscillation_only_notifies_once(self):
        episodes=notifications.Episodes()
        levels=[]
        for index,temp in enumerate([39,40,39,40,41,39,40,50,49,50,45,40]):
            with patch('notifications.time.monotonic',return_value=index*60):
                if notifications.temperature_observation(episodes,'disk',temp,{'warm':40,'hot':50},1,True):levels.append(temp)
        self.assertEqual(levels,[40,50])

    def test_thermal_reset_requires_one_hour_of_observed_cooling(self):
        e=notifications.Episodes();self.assertTrue(e.observe('heat:a',1,False,now=0))
        self.assertFalse(e.observe('heat:a',0,True,now=100))
        self.assertFalse(e.observe('heat:a',0,True,now=3699))
        self.assertFalse(e.observe('heat:a',0,True,now=3700))
        self.assertTrue(e.observe('heat:a',1,False,now=3800))

    def test_missing_reading_restarts_cooling_but_not_episode(self):
        e=notifications.Episodes();e.observe('heat:a',2,False,now=0)
        e.observe('heat:a',0,True,now=1);e.observe('heat:a',None,False,now=3000)
        e.observe('heat:a',0,True,now=4000)
        self.assertFalse(e.observe('heat:a',2,False,now=5000))

    def test_restart_and_muting_do_not_repeat_a_hot_alert(self):
        e=notifications.Episodes();self.assertFalse(e.observe('heat:a',2,False,enabled=False,now=1))
        again=notifications.Episodes();self.assertFalse(again.observe('heat:a',2,False,now=10000))
        self.assertEqual(again.cooling,{})

    def test_two_hour_reset_does_not_reset_at_one_hour(self):
        e=notifications.Episodes();e.observe('heat:a',1,False,now=0)
        e.observe('heat:a',0,True,7200,now=1)
        e.observe('heat:a',0,True,7200,now=4000)
        self.assertFalse(e.observe('heat:a',1,False,7200,now=4001))

    def test_new_feature_preferences_round_trip_and_keep_old_settings(self):
        values=copy.deepcopy(settings.DEFAULTS)
        values.update(health_enabled=False,temperature_alerts=False,alert_reset_hours=2,
                      auto_game_mode=True,exclusions=[str(self.base/'excluded')],game_processes=['game.exe'],
                      space_rules=[dict(drive='D:',value=5,unit='%')],growth_rules=[dict(path=str(self.base),gb=1,hours=2)])
        settings.save(values);loaded=settings.load()
        for key in values:self.assertEqual(loaded[key],values[key])

    def test_exclusions_prune_without_deletion_history_and_reinclude_baseline(self):
        watched=self.base/'watched';excluded=watched/'cache';excluded.mkdir(parents=True)
        (excluded/'a.txt').write_text('abc');(watched/'keep.txt').write_text('keep')
        Indexer(self.db,str(watched)).run()
        con=storage.connect(self.db)
        try:
            storage.set_exclusions([str(excluded)]);storage.prune_exclusions(con)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM files').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM file_log').fetchone()[0],0)
            Indexer(self.db,str(watched)).run()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM files').fetchone()[0],1)
            storage.set_exclusions([]);storage.prune_exclusions(con)
            Indexer(self.db,str(watched)).run()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM files').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM file_log').fetchone()[0],0)
        finally:con.close()

    def test_game_mode_defers_recovery_and_holds_known_events(self):
        engine=Engine(self.db);root='Z:\\';engine._set(root,state='ready');engine.work_mode=True
        engine._overflow(root)
        self.assertIn(root,engine.rescan_pending)
        con=storage.connect(self.db)
        try:
            engine._events(con,root,[('changed','Z:\\x.txt')],[])
            self.assertIn('Z:\\x.txt',engine.held[root])
            self.assertFalse(engine._can_update_folder(root))
            with patch.object(engine,'_start_index') as start:
                engine._start_deferred(con,[]);start.assert_not_called()
                engine.work_mode=False;engine._start_deferred(con,[]);start.assert_called_once()
        finally:con.close()

    def test_excluded_events_do_not_enter_the_bounded_queue(self):
        storage.set_exclusions([str(self.base/'cache')]);engine=Engine(self.db)
        engine.on_events('Z:\\',[('changed',str(self.base/'cache/a.txt'))])
        self.assertEqual(engine.inbox.qsize(),0)

    def test_growth_report_counts_each_direct_parent_once(self):
        con=storage.connect(self.db)
        try:
            now=time.time()
            storage.record(con,[(now,'Z:\\a\\one','added',10,10),(now,'Z:\\a\\b\\two','changed',7,7),(now,'Z:\\a\\old','removed',-3,0),(now-7200,'Z:\\a\\before','added',100,100)]);con.commit()
            reports.prepare(con);sql,args=reports.growth_query(1)
            rows=con.execute(sql,args).fetchall()
            self.assertEqual(sum(r[4] for r in rows),14)
            self.assertEqual({r[0] for r in rows},{'Z:\\a','Z:\\a\\b'})
        finally:con.close()

    def test_csv_export_quotes_paths_and_does_not_execute_formula_text(self):
        con=storage.connect(self.db)
        con.execute("INSERT INTO file_log(ts,path,action,delta,size) VALUES(1,?,'added',3,3)",('=example,"quoted"',));con.commit();con.close()
        destination=self.base/'out.csv'
        self.assertEqual(reports.export(self.db,'SELECT path,delta FROM file_log',[],['Path','Delta'],destination,threading.Event()),1)
        import csv
        with destination.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.reader(f))
        self.assertEqual(rows[1],["'=example,\"quoted\"",'3'])

    def test_cancelled_export_preserves_existing_destination(self):
        destination=self.base/'out.csv';destination.write_text('original')
        cancel=threading.Event();cancel.set()
        with self.assertRaises((InterruptedError,sqlite3.OperationalError)):
            reports.export(self.db,'SELECT 1',[],['value'],destination,cancel)
        self.assertEqual(destination.read_text(),'original')
        self.assertFalse(destination.with_name('out.csv.partial').exists())

    def test_existing_partial_export_is_not_deleted(self):
        destination=self.base/'out.csv';partial=self.base/'out.csv.partial';partial.write_text('other task')
        with self.assertRaises(FileExistsError):reports.export(self.db,'SELECT 1',[],['value'],destination,threading.Event())
        self.assertEqual(partial.read_text(),'other task')

    def test_versions_compare_numerically_and_ignore_test_builds(self):
        self.assertGreater(updates.version('v1.4.10'),updates.version('1.4.9'))
        self.assertIsNone(updates.version('1.4.10-test'))
        release=dict(tag_name='v1.4.10',html_url='https://github.com/snuffy420/FileBurglar/releases/tag/v1.4.10')
        self.assertEqual(updates.validate_release(release,'1.4.9')['version'],'1.4.10')
        for extra in ({'prerelease':True},{'draft':True},{'tag_name':'v1.4.8'}):
            self.assertIsNone(updates.validate_release(dict(release,**extra),'1.4.9'))

    def test_update_link_must_belong_to_the_official_repository(self):
        release=dict(tag_name='v9.0.0',html_url='https://example.com/installer.exe')
        with self.assertRaises(ValueError):updates.validate_release(release,'1.4.7')

    def manager(self):
        engine=Mock();engine.released_roots=set();engine.indexers={};engine.release.return_value=True
        manager=DriveManager(Mock(),Mock(),Mock(),engine=engine)
        manager.devices={'E:\\':dict(number=10,node=100,disk_id='physical-usb',removable=True),'F:\\':dict(number=10,node=100,disk_id='physical-usb',removable=True),'C:\\':dict(number=0,node=None,removable=False)}
        watcher=Mock();watcher.is_alive.return_value=False;manager.watchers={'E:\\':watcher,'F:\\':watcher}
        return manager,engine,watcher

    def test_virtual_and_physical_device_number_collision_never_groups_drives(self):
        manager,_,_=self.manager()
        manager.devices={'G:\\':dict(number=0,disk_id='physical-disk-0'),
                         'I:\\':dict(number=0,node=None)}
        manager.available={'G:\\','I:\\'}
        self.assertEqual(manager.group('G:\\'),{'G:\\'})
        self.assertEqual(manager.group('I:\\'),{'I:\\'})

    def test_absent_cached_volume_cannot_be_grouped_with_present_disk(self):
        manager,_,_=self.manager()
        manager.watchers={};manager.available={'E:\\'}
        self.assertEqual(manager.group('E:\\'),{'E:\\'})

    def test_native_device_number_requires_disk_type_and_complete_response(self):
        def ioctl(kind,returned):
            def call(handle,code,inp,insize,out,outsize,size,overlapped):
                value=devices.C.cast(out,devices.C.POINTER(devices.DeviceNumber)).contents
                value.type=kind;value.number=0
                devices.C.cast(size,devices.C.POINTER(devices.W.DWORD)).contents.value=returned
                return True
            return call
        for kind,length,expected in [(8,12,None),(7,0,None),(7,12,0)]:
            with patch.object(devices.kernel,'CreateFileW',return_value=123),patch.object(devices.kernel,'CloseHandle'),patch.object(devices.kernel,'DeviceIoControl',side_effect=ioctl(kind,length)):
                self.assertEqual(devices.disk_number('fixture'),expected)

    def test_release_stops_sibling_volume_watchers_without_ejecting(self):
        manager,engine,watcher=self.manager()
        with patch('devices.dependencies',return_value=[]),patch('smart.block'),patch('smart.wait_idle',return_value=True),patch('devices.request_eject') as eject:
            ok,message=manager.release('E:\\')
        self.assertTrue(ok);self.assertEqual(manager.blocked,{'E:\\','F:\\'})
        engine.release.assert_called_once();watcher.stop.assert_called();eject.assert_not_called()

    def test_eject_cannot_report_success_when_worker_did_not_release(self):
        manager,engine,watcher=self.manager();engine.release.return_value=False
        with patch('devices.describe',return_value=manager.devices['E:\\']),patch('devices.dependencies',return_value=[]),patch('smart.block'),patch('smart.wait_idle',return_value=True),patch('devices.request_eject') as eject:
            ok,message=manager.safe_eject('E:\\')
        self.assertFalse(ok);eject.assert_not_called()

    def test_windows_veto_resumes_monitoring_without_forcing_removal(self):
        manager,engine,watcher=self.manager()
        with patch('devices.describe',return_value=manager.devices['E:\\']),patch('devices.dependencies',return_value=[]),patch('smart.block'),patch('smart.unblock'),patch('smart.wait_idle',return_value=True),patch('devices.request_eject',return_value=(False,'Windows refused removal')):
            ok,message=manager.safe_eject('E:\\')
        self.assertFalse(ok);self.assertIn('refused',message);self.assertFalse(manager.blocked)
        engine.resume_released.assert_called_once()

    def test_drive_containing_app_data_is_not_released(self):
        manager,engine,watcher=self.manager()
        with patch('devices.dependencies',return_value=['E:\\Data']):ok,message=manager.release('E:\\')
        self.assertFalse(ok);watcher.stop.assert_not_called()

    def test_windows_removal_callbacks_acknowledge_release_and_resume_on_failure(self):
        manager,engine,watcher=self.manager()
        with patch.object(manager,'release',return_value=(True,'released')) as release:
            self.assertEqual(manager._device_event('E:\\',2),0);release.assert_called_once()
        with patch.object(manager,'release',return_value=(False,'busy')):
            self.assertEqual(manager._device_event('E:\\',2),1223)
        with patch.object(manager,'resume') as resume:
            manager._device_event('E:\\',3);resume.assert_called_once_with('E:\\')

    def test_real_windows_handle_registration_and_cleanup(self):
        registrations=[];ready=threading.Event();errors=[]
        def opened(handle):registrations.append(devices.Registration(handle,lambda action:0))
        watcher=DriveWatcher(str(self.base)+os.sep,lambda *a:None,lambda *a:None,
                             lambda root,error:errors.append(error),lambda root:ready.set(),opened)
        watcher.start()
        try:
            self.assertTrue(ready.wait(3),errors)
            self.assertEqual(len(registrations),1)
            self.assertTrue(registrations[0].handle)
        finally:
            watcher.stop();watcher.join(3)
            for registration in registrations:registration.close()
        self.assertFalse(watcher.is_alive())

    def test_volume_identity_query_is_read_only_and_returns_stable_guid(self):
        root=os.path.splitdrive(str(self.base))[0]+'\\'
        first=devices.volume_identity(root)
        self.assertTrue(first and first.startswith('\\\\?\\Volume{'))
        self.assertEqual(first,devices.volume_identity(root))

    def test_notifications_have_bounded_persisted_state(self):
        episodes=notifications.Episodes()
        episodes.entries={str(i):1 for i in range(400)};episodes.save()
        self.assertEqual(len(episodes.entries),256)

    def test_reconnect_summary_requires_matching_volume_identity(self):
        engine=Engine(self.db);root='Z:\\';con=storage.connect(self.db)
        try:
            con.execute('INSERT INTO drives(root,baselined) VALUES(?,1)',(root,))
            con.execute('INSERT INTO volume_identity VALUES(?,?)',(root,'volume-a'));con.commit()
            engine._set(root,state='offline');engine.startup_roots.add(root)
            with patch.object(engine,'_start_index'):
                engine._handle(con,('drive',root,('online','volume-a')),[])
            self.assertIn(root,engine.reconnect_pending)
            engine.reconnect_pending.clear();engine._set(root,state='offline')
            with patch.object(engine,'_start_index'):
                engine._handle(con,('drive',root,('online','different-volume')),[])
            self.assertNotIn(root,engine.reconnect_pending)
            self.assertEqual(con.execute('SELECT baselined FROM drives WHERE root=?',(root,)).fetchone()[0],0)
        finally:con.close()

    def test_released_drive_cannot_start_a_new_indexer(self):
        engine=Engine(self.db);engine.request_release({'Z:\\'})
        with patch('engine.Indexer') as indexer:
            engine._start_index('Z:\\',fast_startup=True)
        indexer.assert_not_called()

    def test_second_health_query_cannot_claim_first_query_is_idle(self):
        with smart._QUERY_LOCK:
            old_process=smart._PROCESS;old_blocks=set(smart._BLOCKS)
            smart._BLOCKS.clear();smart._PROCESS=Mock();smart._IDLE.clear()
        try:
            self.assertEqual(smart.read_disks(),[])
            self.assertFalse(smart._IDLE.is_set())
        finally:
            with smart._QUERY_LOCK:
                smart._PROCESS=old_process;smart._BLOCKS.clear();smart._BLOCKS.update(old_blocks);smart._IDLE.set()

    def test_update_response_size_is_bounded(self):
        response=Mock();response.read.return_value=b'x'*(updates.MAX_RESPONSE+1)
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('updates.urllib.request.urlopen',return_value=response):
            with self.assertRaises(ValueError):updates.check('1.4.7')

    def test_sleep_or_missing_poll_interval_does_not_count_as_observed_cooling(self):
        e=notifications.Episodes()
        for now,temp in ((0,40),(10,30),(4000,30)):
            with patch('notifications.time.monotonic',return_value=now):
                notifications.temperature_observation(e,'disk',temp,dict(warm=40,hot=50),1,True)
        with patch('notifications.time.monotonic',return_value=4010):
            self.assertFalse(notifications.temperature_observation(e,'disk',40,dict(warm=40,hot=50),1,True))

    def test_cancelled_removal_is_not_reported_as_a_physical_reconnect(self):
        engine=Engine(self.db);root='Z:\\';con=storage.connect(self.db)
        try:
            con.execute('INSERT INTO volume_identity VALUES(?,?)',(root,'volume-a'));con.commit()
            engine._set(root,state='released');engine.startup_roots.add(root)
            with patch.object(engine,'_start_index'):
                engine._handle(con,('drive',root,('online',dict(identity='volume-a',reconnected=False))),[])
            self.assertNotIn(root,engine.reconnect_pending)
        finally:con.close()

    def test_first_launch_without_exclusions_keeps_saved_drive_totals(self):
        con=storage.connect(self.db)
        try:
            con.execute("INSERT INTO drives(root,files,bytes) VALUES('Z:\\',99,12345)");con.commit()
            storage.prune_exclusions(con)
            self.assertEqual(con.execute('SELECT files,bytes FROM drives').fetchone(),(99,12345))
        finally:con.close()


if __name__=='__main__':unittest.main()
