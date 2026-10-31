"""Drive-letter changes are simulated in SQLite; no actual mounts are changed."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ['FILEBURGLAR_TESTING']='1'
import copy
import shutil
import sqlite3
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock,patch
import settings
import storage
import volumes
from engine import Engine


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.base=Path(__file__).resolve().parent/('volume-test-'+uuid.uuid4().hex);self.base.mkdir()
        self.patch=patch.object(storage,'DATA_DIR',str(self.base));self.patch.start()
        self.db=str(self.base/'test.db');storage.init(self.db);self.con=storage.connect(self.db)

    def tearDown(self):
        self.con.close();self.patch.stop()
        assert self.base.parent==Path(__file__).resolve().parent and self.base.name.startswith('volume-test-')
        shutil.rmtree(self.base)

    def seed(self,identity,root,size=10):
        volumes.bind(self.con,identity,root,'Test disk '+identity)
        folder=root+'Folder';fid=storage.folder_id(self.con,folder)
        self.con.execute('INSERT INTO files(folder_id,name,size) VALUES(?,?,?)',(fid,'item.txt',size))
        self.con.execute('UPDATE folders SET file_count=1,byte_count=? WHERE id=?',(size,fid))
        self.con.execute('UPDATE drives SET files=1,bytes=?,baselined=1 WHERE volume_id=?',(size,identity))
        storage.record(self.con,[(time.time(),folder+'\\item.txt','added',size,size)])
        self.con.commit();return fid

    def test_unplug_preserves_last_letter_counts_and_history(self):
        self.seed('disk-a','L:\\',30);volumes.offline(self.con,'disk-a');self.con.commit()
        info=volumes.rows(self.con)[0]
        self.assertEqual(info['last_root'],'L:\\');self.assertFalse(info['present'])
        self.assertEqual(self.con.execute('SELECT bytes FROM drives').fetchone()[0],30)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM file_log').fetchone()[0],1)

    def test_letter_change_moves_one_saved_entry_and_preserves_original_path(self):
        fid=self.seed('disk-a','L:\\',30)
        volumes.offline(self.con,'disk-a');volumes.bind(self.con,'disk-a','J:\\');self.con.commit()
        self.assertEqual(self.con.execute('SELECT root,bytes FROM drives').fetchall(),[('J:\\',30)])
        self.assertEqual(self.con.execute('SELECT id,path FROM folders').fetchall(),[(fid,'J:\\Folder')])
        path,observed,root,identity=self.con.execute('SELECT path,observed_path,observed_root,volume_id FROM file_log').fetchone()
        self.assertEqual(path,'J:\\Folder\\item.txt')
        self.assertEqual(volumes.original_path(path,observed,root,identity,volumes.rows(self.con)),'L:\\Folder\\item.txt')
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM files').fetchone()[0],1)

    def test_two_drives_swapping_letters_keep_separate_statistics(self):
        a=self.seed('disk-a','L:\\',30);b=self.seed('disk-b','J:\\',80)
        volumes.bind(self.con,'disk-a','J:\\');volumes.bind(self.con,'disk-b','L:\\');self.con.commit()
        self.assertEqual(dict(self.con.execute('SELECT root,bytes FROM drives')),{'J:\\':30,'L:\\':80})
        self.assertEqual(self.con.execute('SELECT path FROM folders WHERE id=?',(a,)).fetchone()[0],'J:\\Folder')
        self.assertEqual(self.con.execute('SELECT path FROM folders WHERE id=?',(b,)).fetchone()[0],'L:\\Folder')
        self.assertEqual(dict(self.con.execute('SELECT volume_id,SUM(delta) FROM file_log GROUP BY volume_id')),{'disk-a':30,'disk-b':80})

    def test_new_disk_at_old_letter_archives_old_owner_without_stealing_data(self):
        self.seed('disk-a','L:\\',30);volumes.offline(self.con,'disk-a')
        volumes.bind(self.con,'disk-b','L:\\');self.con.commit()
        records={v['id']:v for v in volumes.rows(self.con)}
        self.assertTrue(records['disk-a']['namespace'].startswith('@'))
        self.assertEqual(records['disk-a']['last_root'],'L:\\')
        self.assertFalse(records['disk-a']['present'])
        self.assertEqual(self.con.execute('SELECT bytes FROM drives WHERE volume_id=?',('disk-b',)).fetchone()[0],0)
        self.assertIn('offline',volumes.display_path(self.con.execute('SELECT path FROM file_log').fetchone()[0],volumes.rows(self.con)))

    def test_failed_remap_rolls_back_displacement_and_all_paths(self):
        self.seed('disk-a','L:\\',30);self.seed('disk-b','J:\\',80)
        before=self.con.execute('SELECT id,path FROM folders ORDER BY id').fetchall()
        self.con.execute("CREATE TRIGGER refuse_path BEFORE UPDATE OF path ON folders WHEN NEW.path LIKE 'J:%' BEGIN SELECT RAISE(ABORT,'simulated failure');END")
        with self.assertRaises(sqlite3.IntegrityError):volumes.bind(self.con,'disk-a','J:\\')
        self.assertEqual(self.con.execute('SELECT id,path FROM folders ORDER BY id').fetchall(),before)
        self.assertEqual(dict(self.con.execute('SELECT root,bytes FROM drives')),{'L:\\':30,'J:\\':80})

    def test_preferences_follow_identity_through_swap(self):
        data=copy.deepcopy(settings.DEFAULTS)
        data.update(tray_drives=['L:'],unmonitored_drives=['J:'],exclusions=['L:\\cache'],space_rules=[dict(drive='L:',value=10,unit='GB')])
        settings.save(data)
        metadata=[dict(id='a',last_root='L:\\',present=1),dict(id='b',last_root='J:\\',present=1)]
        settings.configure_volumes(metadata)
        settings.configure_volumes([dict(id='a',last_root='J:\\',present=1),dict(id='b',last_root='L:\\',present=1)])
        result=settings.load()
        self.assertEqual(result['tray_drives'],['J:']);self.assertEqual(result['unmonitored_drives'],['L:'])
        self.assertEqual(result['exclusions'],['J:\\cache'])
        self.assertEqual(result['space_rules'][0]['drive'],'J:')

    def test_offline_preferences_do_not_apply_to_a_replacement_drive(self):
        data=copy.deepcopy(settings.DEFAULTS);data['exclusions']=['L:\\cache'];settings.save(data)
        settings.configure_volumes([dict(id='a',last_root='L:\\',present=1)])
        settings.configure_volumes([dict(id='a',last_root='L:\\',present=0),dict(id='b',last_root='L:\\',present=1)])
        self.assertEqual(settings.load()['exclusions'],[])

    def test_tray_selection_survives_restart_offline_and_letter_change(self):
        metadata=[dict(id='disk-a',last_root='L:\\',present=1)]
        settings.configure_volumes(metadata)
        settings.set_drive_choice('L:\\','tray_drives',True,'disk-a')
        # Simulate a new process, then initial discovery while disk is absent.
        with patch.object(settings,'_CONTEXT',None),patch.object(settings,'_CONTEXT_DIR',None):
            settings.configure_volumes([dict(id='disk-a',last_root='L:\\',present=0)])
            self.assertEqual(settings.load()['tray_drives'],[])
            self.assertTrue(settings.load()['volume_preferences']['disk-a']['tray'])
            settings.configure_volumes([dict(id='disk-a',last_root='J:\\',present=1)])
            self.assertEqual(settings.load()['tray_drives'],['J:'])

    def test_tray_click_uses_current_mapping_and_preserves_other_preferences(self):
        settings.configure_volumes([dict(id='disk-a',last_root='L:\\',present=1)])
        data=settings.load();data['exclusions']=['L:\\cache'];settings.save(data)
        settings.configure_volumes([dict(id='disk-a',last_root='J:\\',present=1)])
        result=settings.set_drive_choice('J:\\','tray_drives',True,'disk-a')
        self.assertEqual(result['tray_drives'],['J:'])
        self.assertEqual(result['exclusions'],['J:\\cache'])
        self.assertIsNone(settings.set_drive_choice('J:\\','tray_drives',False,'different-disk'))
        self.assertEqual(settings.load()['tray_drives'],['J:'])

    def test_old_watcher_token_cannot_update_the_new_occupant(self):
        self.seed('disk-a','L:\\',30);engine=Engine(self.db)
        engine.mounts['L:\\']=dict(id='disk-b',token='new')
        engine.invalid_tokens.add('old')
        engine.on_events('L:\\',[('changed','L:\\item.txt')],'old')
        self.assertEqual(engine.inbox.qsize(),0)
        with patch.object(engine,'_events') as events:
            engine._handle(self.con,('events','L:\\',('old',[('changed','L:\\item.txt')])),[])
        events.assert_not_called()

    def test_explicit_legacy_link_retains_index_and_merges_history(self):
        fid=self.seed('legacy:old','L:\\',30)
        self.con.execute('UPDATE volumes SET verified=0 WHERE id=?',('legacy:old',))
        self.seed('identified','J:\\',5)
        volumes.link_legacy(self.con,'legacy:old','identified');self.con.commit()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM volumes').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT id,path FROM folders').fetchall(),[(fid,'J:\\Folder')])
        self.assertEqual(self.con.execute('SELECT SUM(delta) FROM file_log').fetchone()[0],35)
        self.assertEqual(self.con.execute('SELECT SUM(events) FROM folder_daily').fetchone()[0],2)

    def test_schema_upgrade_backups_and_marks_legacy_history(self):
        legacy=self.base/'old.db';c=sqlite3.connect(legacy);c.executescript(storage.SCHEMA)
        c.execute("INSERT INTO drives(root,files,bytes) VALUES('L:\\',1,30)")
        c.execute("INSERT INTO volume_identity VALUES('L:\\','known-disk')")
        c.execute("INSERT INTO file_log(ts,path,action,delta,size) VALUES(1,'L:\\item.txt','added',30,30)")
        c.commit();c.close();storage.init(str(legacy))
        c=storage.connect(str(legacy))
        try:
            self.assertEqual(c.execute('SELECT volume_id,legacy_unverified FROM file_log').fetchone(),('known-disk',1))
            self.assertEqual(c.execute('SELECT bytes FROM drives').fetchone()[0],30)
        finally:c.close()
        backups=list(self.base.glob('pre-volume-migration-*.db'));self.assertEqual(len(backups),1)
        storage.init(str(legacy));self.assertEqual(len(list(self.base.glob('pre-volume-migration-*.db'))),1)

    def test_low_space_stops_upgrade_before_backup_or_database_writes(self):
        legacy=self.base/'low-space.db'
        c=sqlite3.connect(legacy);c.executescript(storage.SCHEMA);c.close()
        original=legacy.read_bytes()
        with patch('volumes.shutil.disk_usage',return_value=Mock(free=1)),patch('volumes.backup_before_migration') as backup:
            with self.assertRaisesRegex(OSError,'Not enough free space'):
                storage.init(str(legacy))
            backup.assert_not_called()
        self.assertEqual(legacy.read_bytes(),original)
        self.assertEqual(list(self.base.glob('pre-volume-migration-*.db')),[])

    def test_completed_upgrade_does_not_need_upgrade_space_again(self):
        with patch('volumes.shutil.disk_usage',return_value=Mock(free=1)) as usage:
            storage.init(self.db)
        usage.assert_not_called()

    def test_incomplete_upgrade_checks_space_even_if_volumes_table_exists(self):
        legacy=self.base/'incomplete.db'
        c=sqlite3.connect(legacy);c.executescript(storage.SCHEMA);c.execute(volumes.SCHEMA);c.close()
        with patch('volumes.shutil.disk_usage',return_value=Mock(free=1)):
            with self.assertRaisesRegex(OSError,'Not enough free space'):
                storage.init(str(legacy))

    def test_startup_database_error_is_explained_without_starting_monitoring(self):
        import main
        with patch.object(main,'is_admin',return_value=True),patch.object(main,'take_single_instance',return_value=True),patch.object(main,'lower_process_priority'),patch.object(main.storage,'init',side_effect=sqlite3.OperationalError('database or disk is full')),patch.object(main.ctypes.windll.user32,'MessageBoxW') as message,patch.object(main,'Engine') as engine:
            self.assertEqual(main.main(),1)
            engine.assert_not_called()
            self.assertIn('database or disk is full',message.call_args.args[1])
            self.assertIn('Monitoring has not started',message.call_args.args[1])

    def test_event_batch_rolls_back_if_native_volume_changes_during_stats(self):
        self.seed('disk-a','L:\\',30);engine=Engine(self.db)
        engine.mounts['L:\\']=dict(id='disk-a',token='old');engine._set('L:\\',state='ready')
        result=type('Stat',(),dict(st_size=99,st_mtime_ns=123))()
        with patch('devices.volume_identity',side_effect=['disk-a','different-disk']),patch('engine.os.path.islink',return_value=False),patch('engine.os.path.isjunction',return_value=False),patch('engine.os.path.isdir',return_value=False),patch('engine.os.stat',return_value=result):
            rows=[];engine._events(self.con,'L:\\',[('changed','L:\\Folder\\item.txt')],rows)
        self.assertEqual(self.con.execute('SELECT size FROM files').fetchone()[0],30)
        self.assertEqual(rows,[])

    def test_complete_current_index_survives_explicit_history_link(self):
        self.seed('legacy:old','L:\\',30);self.con.execute("UPDATE volumes SET verified=0 WHERE id='legacy:old'")
        current=self.seed('identified','J:\\',50)
        self.con.execute("UPDATE drives SET last_scan=123 WHERE volume_id='identified'")
        volumes.link_legacy(self.con,'legacy:old','identified')
        self.assertEqual(self.con.execute('SELECT id FROM folders').fetchone()[0],current)
        self.assertEqual(self.con.execute('SELECT bytes FROM drives').fetchone()[0],50)

    def test_engine_rebind_preserves_saved_counters_in_its_snapshot(self):
        self.seed('disk-a','L:\\',30);engine=Engine(self.db)
        with patch.object(engine,'_start_index'):
            engine._handle(self.con,('drive','L:\\',('online',dict(identity='disk-a',token='first'))),[])
            engine._handle(self.con,('drive','L:\\',('offline',dict(token='first'))),[])
            engine._handle(self.con,('drive','J:\\',('online',dict(identity='disk-a',token='second'))),[])
        snapshot=engine.snapshot()
        self.assertNotIn('L:\\',snapshot)
        self.assertEqual(snapshot['J:\\']['bytes'],30)
        self.assertEqual(snapshot['J:\\']['last_root'],'J:\\')

    def test_unmonitored_volume_still_follows_its_letter_without_scanning(self):
        self.seed('disk-a','L:\\',30);engine=Engine(self.db)
        with patch.object(engine,'_start_index') as scan:
            engine._handle(self.con,('drive','J:\\',('online',dict(identity='disk-a',token='passive',monitor=False))),[])
        scan.assert_not_called()
        self.assertEqual(engine.snapshot()['J:\\']['state'],'unmonitored')
        self.assertEqual(engine.snapshot()['J:\\']['bytes'],30)


if __name__=='__main__':unittest.main()
