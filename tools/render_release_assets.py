"""Render native branding and screenshots using synthetic data only."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['FILEBURGLAR_TESTING'] = '1'
import shutil
import sys
import time
import uuid
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication,QMessageBox,QTabWidget
import main
import storage
import ui
import settings
import volumes
from engine import Engine


def run():
    app = QApplication.instance() or QApplication([])
    for name in ('segoeui.ttf','segoeuib.ttf','seguisym.ttf'):
        font = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setWindowIcon(main.tray_icon())
    assets = ROOT/'assets'
    (assets/'screenshots').mkdir(exist_ok=True)
    side = QPixmap(328,628)
    painter = QPainter(side)
    painter.scale(2,2)
    gradient = QLinearGradient(0,0,0,314)
    gradient.setColorAt(0,QColor('#141414'))
    gradient.setColorAt(1,QColor('#6A3A10'))
    painter.fillRect(QRectF(0,0,164,314),gradient)
    painter.drawPixmap(18,32,main._draw_icon(128))
    painter.setPen(QColor('#FF8A1F'))
    font = QFont('Segoe UI');font.setPixelSize(22);font.setBold(True);painter.setFont(font)
    painter.drawText(QRectF(18,175,146,65),Qt.AlignmentFlag.AlignLeft,'FILE\nBURGLAR')
    font.setPixelSize(10);font.setBold(False);painter.setFont(font)
    painter.setPen(QColor('#EEE4DA'))
    painter.drawText(QRectF(18,245,140,45),Qt.AlignmentFlag.AlignLeft,'Storage activity\nand drive health')
    font.setPixelSize(9);painter.setFont(font)
    painter.drawText(18,300,'zerØ software')
    painter.end()
    side.toImage().convertToFormat(QImage.Format.Format_RGB888).save(str(assets/'installer-side.bmp'))
    small=QPixmap(112,112);small.fill(QColor('#191919'))
    painter=QPainter(small);painter.drawPixmap(4,4,main._draw_icon(104));painter.end()
    small.toImage().convertToFormat(QImage.Format.Format_RGB888).save(str(assets/'installer-small.bmp'))
    base=ROOT/('asset-work-'+uuid.uuid4().hex);base.mkdir()
    old_data=storage.DATA_DIR
    window=explorer=None
    try:
        storage.DATA_DIR=str(base/'Data')
        limits=settings.load();limits['auto_updates']=False;settings.save(limits)
        db=storage.default_db_path();storage.init(db)
        con=storage.connect(db)
        samples=[('C:\\',512*1024**3,180*1024**3,42560,182*1024**3),
                 ('D:\\',2*1024**4,1100*1024**3,128240,680*1024**3)]
        now=time.time()
        for root,total,free,count,size in samples:
            volumes.bind(con,'demo-volume-'+root[0],root,'Demo '+root[:2])
            con.execute('UPDATE drives SET baselined=1,files=?,bytes=?,last_scan=? WHERE root=?',(count,size,now,root))
        storage.record(con,[(now,'C:\\Projects\\Build\\output.bin','changed',64*1024**2,128*1024**2),
                            (now-90,'C:\\Users\\Demo\\Downloads\\archive.zip','added',25*1024**2,25*1024**2),
                            (now-160,'D:\\Media\\cache.tmp','removed',-400*1024**2,0)])
        # A file rewritten repeatedly within the hour shows as one merged row.
        for step in range(12):
            storage.record(con,[(now-30+step,'D:\\Bots\\data\\state.json','changed',96*1024,(step+1)*96*1024)])
        con.commit();con.close()
        Space=namedtuple('Space','total used free')
        capacities={r:Space(t,t-f,f) for r,t,f,_,_ in samples}
        with patch.object(ui.MainWindow,'_poll_smart',lambda self:None), \
             patch('ui.shutil.disk_usage',side_effect=lambda path:capacities[path]):
            engine=Engine(db)
            for root,_,_,count,size in samples:
                engine._set(root,state='ready',present=True,files=count,bytes=size,last_scan=now,last_event=now,volume_id='demo-volume-'+root[0])
            settings.configure_volumes([dict(id='demo-volume-'+r[0],last_root=r,present=1) for r,*_ in samples])
            window=ui.MainWindow(engine,db)
            window._disks={'C:':{'Name':'NVMe SSD','Bus':'NVMe','Media':'SSD','Health':'Healthy','Temperature':37},
                           'D:':{'Name':'Data drive','Bus':'SATA','Media':'HDD','Health':'Healthy','Temperature':35}}
            window.setWindowIcon(main.tray_icon())
            window.brand_icon.setPixmap(main.tray_icon().pixmap(32,32))
            window.resize(1200,780);window.show();app.processEvents();window.refresh();app.processEvents()
            # Show demo free space at once instead of waiting for the background capacity check.
            for root,tile in window.tiles.items():
                tile.capacity=capacities.get(root)
            window.refresh();app.processEvents()
            window.grab().save(str(assets/'screenshots/main.png'))
            for tab,name in [(1,'folder-report'),(2,'activity-log'),(3,'space-growth')]:
                window.tabs.setCurrentIndex(tab);window.refresh()
                if tab==3:window.features.refresh_growth(True)
                deadline=time.monotonic()+5
                while 'growth' in window.features.busy and time.monotonic()<deadline:
                    app.processEvents();time.sleep(.01)
                app.processEvents();window.grab().save(str(assets/'screenshots'/(name+'.png')))
            window.tabs.setCurrentIndex(0)
            from feature_ui import Preferences
            options=Preferences(window,window._limits);options.show()
            tabs=options.findChildren(QTabWidget)[0]
            for tab,name in [(0,'temperature-options'),(1,'tracking-options'),(2,'space-alert-options'),(3,'update-options')]:
                tabs.setCurrentIndex(tab);app.processEvents()
                options.grab().save(str(assets/'screenshots'/(name+'.png')))
            options.reject();options.deleteLater()
            confirmation=QMessageBox(window);confirmation.setWindowTitle('Drive removal')
            confirmation.setIcon(QMessageBox.Icon.Question)
            confirmation.setText('Release File Burglar’s access to D:\\?')
            confirmation.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
            confirmation.setDefaultButton(QMessageBox.StandardButton.No)
            confirmation.show();app.processEvents()
            confirmation.grab().save(str(assets/'screenshots/drive-removal.png'))
            confirmation.close();confirmation.deleteLater()
            from about_dialog import AboutDialog
            about=AboutDialog(window);about.show();app.processEvents()
            for index,name in enumerate(('about.png','help.png','donate.png')):
                about.tabs.setCurrentIndex(index);app.processEvents()
                about.grab().save(str(assets/'screenshots'/name))
            about.accept()
            explorer=ui.ExplorerWindow(window.con);explorer.show();explorer.load()
            deadline=time.monotonic()+5
            while explorer._busy and time.monotonic()<deadline:
                app.processEvents();time.sleep(.01)
            assert explorer.table.rowCount()==2
            explorer.grab().save(str(assets/'screenshots/explorer.png'))
            from render_tray_assets import render as render_tray
            render_tray(app,window,assets)
    finally:
        if explorer:explorer.close()
        if window:
            window.features.stop();window.timer.stop();window.smart_timer.stop();window.hide();window.con.close()
        storage.DATA_DIR=old_data
        assert base.parent==ROOT and base.name.startswith('asset-work-')
        shutil.rmtree(base)
    print('Rendered installer artwork and synthetic screenshots')


if __name__=='__main__':
    run()
