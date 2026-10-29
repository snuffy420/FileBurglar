"""Regression checks: live changes must not repeatedly index whole drives."""
import os
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import storage
from engine import Engine
from indexer import Indexer
from watcher import DriveWatcher


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(__file__).resolve().parent/('monitor-test-'+uuid.uuid4().hex)
        self.base.mkdir()
        self.data=self.base/'Data';self.data.mkdir()
        self.watched=self.base/'watched';self.watched.mkdir()
        self.root=os.path.splitdrive(str(self.base))[0]+os.sep
        self.patches=[patch('storage.DATA_DIR',str(self.data)),
                      patch('indexer.lower_thread_priority'),patch('indexer.raise_thread_priority'),
                      patch('indexer.fast_priority'),patch('engine.lower_thread_priority'),
                      patch('folder_updates.lower_thread_priority')]
        for item in self.patches:item.start()
        self.db=str(self.data/'test.db');storage.init(self.db)
        Indexer(self.db,str(self.watched)).run()
        self.con=storage.connect(self.db)
        self.con.execute('INSERT INTO drives(root,baselined,gen,files,bytes) VALUES(?,1,1,0,0)',(self.root,))
        self.con.commit()
        self.engine=Engine(self.db);self.engine._set(self.root,state='ready',files=0,bytes=0)
        self.scan=patch.object(self.engine,'_start_index');self.scans=self.scan.start()

    def tearDown(self):
        self.engine.stop()
        if self.engine.ident is not None:self.engine.join(5)
        self.assertFalse(self.engine.is_alive())
        self.con.close();self.scan.stop()
        for item in reversed(self.patches):item.stop()
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('monitor-test-')
        shutil.rmtree(self.base)

    def wait(self, condition):
        deadline=time.monotonic()+5
        while not condition() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(condition(),self.engine.snapshot())

    def settled(self,count):
        s=self.engine.snapshot()[self.root]
        return s['files']==count and s['folder_updates']==0 and not self.engine.held.get(self.root)

    def test_database_notifications_never_enter_the_event_queue(self):
        for _ in range(2000):
            self.engine.on_events(self.root,[('changed',str(self.data/'test.db-wal'))])
        self.assertEqual(self.engine.inbox.qsize(),0)
        self.assertFalse(self.engine.dropped_roots)
        self.assertEqual(self.engine.folder_updater.count(self.root),0)
        self.scans.assert_not_called()

    def test_new_directory_indexes_only_its_own_tree(self):
        folder=self.watched/'new';(folder/'nested').mkdir(parents=True)
        (folder/'a.txt').write_text('abc');(folder/'nested'/'b.txt').write_text('hello')
        self.engine.start();self.engine.on_events(self.root,[('added',str(folder))])
        self.wait(lambda:self.settled(2))
        self.assertEqual(self.engine.snapshot()[self.root]['state'],'ready')
        self.assertEqual(self.engine.snapshot()[self.root]['bytes'],8)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM file_log').fetchone()[0],2)
        self.scans.assert_not_called()

    def test_queue_backpressure_recovers_known_folders_without_drive_scan(self):
        for i in range(70):
            file=self.watched/f'{i}.txt';file.write_text('abc')
            self.engine.on_events(self.root,[('added',str(file))])
        self.assertEqual(self.engine.inbox.qsize(),64)
        self.engine.start();self.wait(lambda:self.settled(70))
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM file_log').fetchone()[0],70)
        self.scans.assert_not_called()

    def test_large_paused_backlog_does_not_request_whole_drive_rescan(self):
        self.engine.paused=True
        events=[('added',str(self.watched/f'{i}.txt')) for i in range(4100)]
        self.engine._events(self.con,self.root,events,[])
        self.assertNotIn(self.root,self.engine.rescan_pending)
        self.assertEqual(self.engine.folder_updater.count(self.root),1)
        self.assertFalse(self.engine.held[self.root])
        self.scans.assert_not_called()

    def test_known_file_change_needs_no_folder_or_drive_scan(self):
        file=self.watched/'a.txt';file.write_text('abc')
        self.engine.start();self.engine.on_events(self.root,[('added',str(file))])
        self.wait(lambda:self.settled(1))
        file.write_text('updated');self.engine.on_events(self.root,[('changed',str(file))])
        self.wait(lambda:self.engine.snapshot()[self.root]['bytes']==7)
        self.assertEqual(self.engine.folder_updater.count(self.root),0)
        self.scans.assert_not_called()

    def test_directory_rename_keeps_exact_totals_without_drive_scan(self):
        old=self.watched/'old';old.mkdir();(old/'file.txt').write_text('abc')
        self.engine.start();self.engine.on_events(self.root,[('added',str(old))])
        self.wait(lambda:self.settled(1))
        new=self.watched/'new';old.rename(new)
        self.engine.on_events(self.root,[('removed',str(old)),('added',str(new))])
        self.wait(lambda:self.settled(1) and self.con.execute(
            'SELECT COUNT(*) FROM folders WHERE path=?',(str(new),)).fetchone()[0]==1)
        self.assertEqual(self.engine.snapshot()[self.root]['bytes'],3)
        self.scans.assert_not_called()

    def test_real_kernel_notification_loss_can_still_request_reconciliation(self):
        self.engine._overflow(self.root)
        self.scans.assert_called_once_with(self.root)

    def test_live_windows_notifications_settle_back_to_monitoring(self):
        ready=threading.Event();errors=[]
        watcher=DriveWatcher(str(self.base)+os.sep,
                             lambda root,batch:self.engine.on_events(self.root,batch),
                             lambda root:self.engine.on_overflow(self.root),
                             lambda root,error:errors.append(error),lambda root:ready.set())
        self.engine.start();watcher.start()
        try:
            self.assertTrue(ready.wait(3),errors)
            folder=self.watched/'live';folder.mkdir();(folder/'live.txt').write_text('example')
            self.wait(lambda:self.settled(1))
            time.sleep(.2)
            self.assertEqual(self.engine.snapshot()[self.root]['state'],'ready')
            self.assertFalse(errors)
            self.scans.assert_not_called()
        finally:
            watcher.stop();watcher.join(3)


if __name__=='__main__':unittest.main()
