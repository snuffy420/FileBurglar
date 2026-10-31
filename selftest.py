"""Packaged-runtime smoke check using synthetic data only."""
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
import traceback
import uuid
from pathlib import Path


def run(report_path):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication
    import storage
    import main
    from appinfo import APP_VERSION
    from engine import Engine
    from ui import MainWindow, ExplorerWindow
    report = Path(report_path).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    base = report.parent / ('fileburglar-selftest-' + uuid.uuid4().hex)
    base.mkdir()
    old_data = storage.DATA_DIR
    old_poll = MainWindow._poll_smart
    result = {'version': APP_VERSION, 'frozen': bool(getattr(sys,'frozen',False)),
              'architecture': platform.machine(), 'python': platform.python_version(),
              'pyqt': PYQT_VERSION_STR, 'qt': QT_VERSION_STR,
              'default_data_directory': old_data, 'watchers_started': False,
              'health_queries_started': False, 'checks': {}}
    window = explorer = None
    try:
        storage.DATA_DIR = str(base / 'Data')
        db = storage.default_db_path()
        storage.init(db)
        con = storage.connect(db)
        fid = storage.folder_id(con, 'Z:\\')
        key = storage.timestamp_key(-11644443511509811200)
        con.execute('INSERT INTO files(folder_id,name,size,mtime_ns) VALUES(?,?,?,?)',
                    (fid,'sample.txt',3,key))
        con.execute('UPDATE folders SET file_count=1,byte_count=3 WHERE id=?',(fid,))
        con.execute("INSERT INTO drives(root,baselined,files,bytes) VALUES('Z:\\',1,1,3)")
        con.commit()
        assert con.execute('SELECT mtime_ns FROM files').fetchone()[0] == key
        assert con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        con.close()
        result['checks']['sqlite_and_extreme_timestamp'] = True
        app = QApplication.instance() or QApplication([])
        app.setWindowIcon(main.tray_icon())
        assert not main.tray_icon().pixmap(32,32).isNull()
        result['checks']['app_icon'] = True
        MainWindow._poll_smart = lambda self: None
        engine = Engine(db)
        window = MainWindow(engine,db)
        engine._set('Z:\\',state='ready',files=1,bytes=3)
        engine._set('Y:\\',state='offline',files=1,bytes=5)
        window.show()
        app.processEvents()
        for tab in range(window.tabs.count()):
            window.tabs.setCurrentIndex(tab)
            window.refresh()
            for _ in range(5):
                app.processEvents()
            if tab in (1,2):
                table = window.report_table if tab == 1 else window.log_table
                assert table.horizontalScrollBar().maximum() == 0
        result['checks']['main_window_tabs'] = True
        result['checks']['report_columns_fit'] = True
        assert window.card_size.value.text() == '3 B / 8 B'
        result['checks']['online_and_saved_indexed_size'] = True
        from feature_ui import Preferences
        preferences=Preferences(window,window._limits)
        preferences.show();app.processEvents()
        assert 'health_enabled' in preferences.checks and 'auto_updates' in preferences.checks
        assert preferences.reset.count()==2
        preferences.reject();preferences.deleteLater()
        window.features.refresh_growth(True)
        deadline=time.monotonic()+8
        while 'growth' in window.features.busy and time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
        assert 'growth' not in window.features.busy
        result['checks']['feature_options_and_growth_view']=True
        import ctypes
        import devices
        assert ctypes.sizeof(devices.NotifyFilter)==416
        assert devices.cm.CM_Register_Notification and devices.cm.CM_Request_Device_EjectW
        result['checks']['native_removal_api_imports']=True
        from about_dialog import AboutDialog, DONATION_BTC_ADDRESS
        about = AboutDialog(window)
        assert about.tabs.count() == 3 and about.address.text() == DONATION_BTC_ADDRESS
        assert about.qr_label.pixmap() is not None and not about.qr_label.pixmap().isNull()
        for index in range(3):
            about.tabs.setCurrentIndex(index)
            app.processEvents()
        about.accept()
        result['checks']['about_help_and_donation_qr'] = True
        explorer = ExplorerWindow(window.con)
        explorer.show()
        explorer.load()
        deadline = time.monotonic()+8
        while explorer._busy and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(.01)
        assert not explorer._busy and explorer.table.rowCount() == 1
        result['checks']['asynchronous_explorer'] = True
        import volumes
        con=window.con
        volumes.bind(con,'selftest-stable-volume','W:\\')
        fid=storage.folder_id(con,'W:\\Example')
        con.execute('INSERT INTO files(folder_id,name,size) VALUES(?,?,?)',(fid,'demo.txt',7))
        storage.record(con,[(time.time(),'W:\\Example\\demo.txt','added',7,7)])
        volumes.offline(con,'selftest-stable-volume')
        volumes.bind(con,'selftest-stable-volume','V:\\');con.commit()
        assert con.execute('SELECT path FROM folders WHERE id=?',(fid,)).fetchone()[0]=='V:\\Example'
        assert con.execute('SELECT observed_root FROM file_log WHERE volume_id=?',('selftest-stable-volume',)).fetchone()[0]=='W:\\'
        result['checks']['stable_volume_letter_change']=True
        executable, arguments = main.startup_target()
        assert '--background' in arguments
        if result['frozen']:
            assert os.path.normcase(executable) == os.path.normcase(sys.executable)
            assert not any(x.endswith('.py') for x in arguments)
        result['checks']['packaged_startup_command'] = True
        result['passed'] = True
    except Exception:
        result['passed'] = False
        result['error'] = traceback.format_exc()
    finally:
        if explorer is not None:
            explorer.close()
        if window is not None:
            window.features.stop()
            window.timer.stop()
            window.smart_timer.stop()
            window.hide()
            window.con.close()
        MainWindow._poll_smart = old_poll
        storage.DATA_DIR = old_data
        report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        assert base.parent == report.parent and base.name.startswith('fileburglar-selftest-')
        shutil.rmtree(base)
    return 0 if result.get('passed') else 1
